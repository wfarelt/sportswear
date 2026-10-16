from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.decorators import permission_required_or_superuser

from .forms import BrandForm, CategoryForm, ColorForm, ProductForm, ProductVariantForm, SizeForm
from .models import Brand, Category, Color, Product, ProductVariant, Size

REFERENCE_TYPES = {
    "categorias": (Category, CategoryForm, "Categorías"),
    "marcas": (Brand, BrandForm, "Marcas"),
    "tallas": (Size, SizeForm, "Tallas"),
    "colores": (Color, ColorForm, "Colores"),
}


def _require_reference_permission(request, kind, action):
    if kind not in REFERENCE_TYPES:
        from django.http import Http404

        raise Http404
    model = REFERENCE_TYPES[kind][0]
    permission = f"products.{action}_{model._meta.model_name}"
    if not (request.user.is_superuser or request.user.has_perm(permission)):
        raise PermissionDenied
    return REFERENCE_TYPES[kind]


@permission_required_or_superuser("products.view_product")
def product_list(request):
    products = Product.objects.select_related("category", "brand").annotate(
        variant_count=Count("variants", distinct=True)
    )
    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "")
    brand_id = request.GET.get("brand", "")
    status = request.GET.get("status", "active")
    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(internal_code__icontains=query)
            | Q(variants__sku__icontains=query)
            | Q(variants__barcode__icontains=query)
        ).distinct()
    if category_id.isdigit():
        products = products.filter(category_id=category_id)
    if brand_id.isdigit():
        products = products.filter(brand_id=brand_id)
    if status == "active":
        products = products.filter(is_active=True)
    elif status == "inactive":
        products = products.filter(is_active=False)

    page_obj = Paginator(products, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "products/product_list.html",
        {
            "page_obj": page_obj,
            "products": page_obj.object_list,
            "query": query,
            "selected_category": category_id,
            "selected_brand": brand_id,
            "selected_status": status,
            "categories": Category.objects.filter(is_active=True),
            "brands": Brand.objects.filter(is_active=True),
        },
    )


@permission_required_or_superuser("products.add_product")
def product_create(request):
    form = ProductForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        product = form.save()
        messages.success(request, f"El producto {product.name} se creó. Ahora puedes añadir sus variantes.")
        return redirect("products:detail", pk=product.pk)
    return render(request, "products/product_form.html", {"form": form, "page_title": "Nuevo producto"})


@permission_required_or_superuser("products.view_product")
def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.select_related("category", "brand").prefetch_related(
            "variants__size", "variants__color"
        ),
        pk=pk,
    )
    return render(request, "products/product_detail.html", {"product": product})


@permission_required_or_superuser("products.change_product")
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    if request.method == "POST" and form.is_valid():
        product = form.save()
        messages.success(request, f"El producto {product.name} se actualizó correctamente.")
        return redirect("products:detail", pk=product.pk)
    return render(
        request,
        "products/product_form.html",
        {"form": form, "page_title": f"Editar producto: {product.name}", "product": product},
    )


@permission_required_or_superuser("products.add_productvariant")
def variant_create(request, product_pk):
    product = get_object_or_404(Product, pk=product_pk)
    variant = ProductVariant(product=product)
    form = ProductVariantForm(request.POST or None, instance=variant)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La variante se agregó correctamente.")
        return redirect("products:detail", pk=product.pk)
    return render(
        request,
        "products/variant_form.html",
        {"form": form, "product": product, "page_title": "Agregar variante"},
    )


@permission_required_or_superuser("products.change_productvariant")
def variant_update(request, product_pk, pk):
    product = get_object_or_404(Product, pk=product_pk)
    variant = get_object_or_404(ProductVariant, pk=pk, product=product)
    form = ProductVariantForm(request.POST or None, instance=variant)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La variante se actualizó correctamente.")
        return redirect("products:detail", pk=product.pk)
    return render(
        request,
        "products/variant_form.html",
        {"form": form, "product": product, "variant": variant, "page_title": "Editar variante"},
    )


@permission_required_or_superuser("products.view_product")
def reference_index(request):
    catalogs = []
    for kind, (model, _form, label) in REFERENCE_TYPES.items():
        permission = f"products.view_{model._meta.model_name}"
        if request.user.is_superuser or request.user.has_perm(permission):
            catalogs.append({"kind": kind, "label": label, "count": model.objects.count()})
    return render(request, "products/reference_index.html", {"catalogs": catalogs})


def reference_list(request, kind):
    model, _form, label = _require_reference_permission(request, kind, "view")
    records = model.objects.all()
    query = request.GET.get("q", "").strip()
    if query:
        records = records.filter(name__icontains=query)
    return render(
        request,
        "products/reference_list.html",
        {"records": records, "kind": kind, "label": label, "query": query},
    )


def reference_create(request, kind):
    model, form_class, label = _require_reference_permission(request, kind, "add")
    form = form_class(request.POST or None)
    if request.method == "POST" and form.is_valid():
        record = form.save()
        messages.success(request, f"{label[:-1]} «{record.name}» se creó correctamente.")
        return redirect("products:reference_list", kind=kind)
    return render(
        request,
        "products/reference_form.html",
        {"form": form, "kind": kind, "label": label, "page_title": f"Nuevo registro: {label[:-1].lower()}"},
    )


def reference_update(request, kind, pk):
    model, form_class, label = _require_reference_permission(request, kind, "change")
    record = get_object_or_404(model, pk=pk)
    form = form_class(request.POST or None, instance=record)
    if request.method == "POST" and form.is_valid():
        record = form.save()
        messages.success(request, f"«{record.name}» se actualizó correctamente.")
        return redirect("products:reference_list", kind=kind)
    return render(
        request,
        "products/reference_form.html",
        {"form": form, "kind": kind, "label": label, "page_title": f"Editar: {record.name}"},
    )
