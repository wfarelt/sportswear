import json
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth import get_user_model

from apps.core.decorators import permission_required_or_superuser
from apps.inventory.models import StockLevel
from apps.products.models import ProductVariant

from .forms import (
    SaleCreateForm,
    SaleCancelForm,
    SaleReturnForm,
    SaleReturnLineForm,
    authorized_branches,
)
from .models import PaymentMethod, Sale, SaleEvent, SaleLine
from .services import cancel_sale, confirm_sale, process_return


def _sale_queryset(user):
    queryset = Sale.objects.filter(branch__in=authorized_branches(user)).select_related(
        "branch", "customer", "seller"
    )
    if not user.is_superuser and not user.has_perm("sales.view_all_sales"):
        queryset = queryset.filter(seller=user)
    return queryset


def _get_sale(user, pk):
    return get_object_or_404(
        _sale_queryset(user).prefetch_related("lines", "payments__method", "returns__refund_method", "events__user"),
        pk=pk,
    )


def _selected_branch(request, submitted=None):
    branches = authorized_branches(request.user)
    value = submitted or request.session.get("selected_branch_id")
    if value:
        return get_object_or_404(branches, pk=value)
    if branches.count() == 1:
        return branches.first()
    return None


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@permission_required_or_superuser("sales.view_sale")
def sale_list(request):
    sales = _sale_queryset(request.user)
    branch = _selected_branch(request, request.GET.get("branch")) if request.GET.get("branch") else None
    if branch:
        sales = sales.filter(branch=branch)
    query = request.GET.get("q", "").strip()
    if query:
        sales = sales.filter(
            Q(number__icontains=query)
            | Q(customer__full_name__icontains=query)
            | Q(customer__document_number__icontains=query)
        )
    status = request.GET.get("status", "")
    if status in Sale.Status.values:
        sales = sales.filter(status=status)
    from_date = _parse_date(request.GET.get("from", ""))
    to_date = _parse_date(request.GET.get("to", ""))
    if from_date:
        sales = sales.filter(created_at__date__gte=from_date)
    if to_date:
        sales = sales.filter(created_at__date__lte=to_date)
    seller_id = request.GET.get("seller", "")
    can_view_all = request.user.is_superuser or request.user.has_perm("sales.view_all_sales")
    if can_view_all and seller_id.isdigit():
        sales = sales.filter(seller_id=seller_id)
    page_obj = Paginator(sales.order_by("-created_at", "-pk"), 30).get_page(request.GET.get("page"))
    visible_seller_ids = sales.order_by().values_list("seller_id", flat=True).distinct()
    return render(request, "sales/sale_list.html", {
        "page_obj": page_obj,
        "sales": page_obj.object_list,
        "branches": authorized_branches(request.user),
        "selected_branch": branch,
        "query": query,
        "status": status,
        "statuses": Sale.Status.choices,
        "from_date": request.GET.get("from", ""),
        "to_date": request.GET.get("to", ""),
        "seller_id": seller_id,
        "can_view_all": can_view_all,
        "sellers": get_user_model().objects.filter(pk__in=visible_seller_ids).order_by("first_name", "username") if can_view_all else [],
        "can_create": request.user.is_superuser or request.user.has_perm("sales.add_sale"),
    })


@permission_required_or_superuser("sales.add_sale")
def sale_create(request):
    branch = _selected_branch(request, request.POST.get("branch") if request.method == "POST" else None)
    form = SaleCreateForm(
        request.POST or None, user=request.user, initial_branch=branch
    )
    if request.method == "POST" and form.is_valid():
        try:
            sale = confirm_sale(
                branch_id=form.cleaned_data["branch"].pk,
                customer_id=form.cleaned_data["customer"].pk if form.cleaned_data["customer"] else None,
                lines=form.cleaned_data["cart_json"],
                discount_amount=form.cleaned_data["discount_amount"] or Decimal("0.00"),
                payments=form.cleaned_data["payments_json"],
                user=request.user,
                idempotency_key=form.cleaned_data["idempotency_key"],
                cash_session_id=form.cleaned_data["cash_session"].pk,
            )
            messages.success(request, f"La venta {sale.number} se confirmó correctamente.")
            return redirect("sales:receipt", pk=sale.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(request, "sales/point_of_sale.html", {
        "form": form,
        "payment_methods": PaymentMethod.objects.filter(is_active=True),
        "branches": authorized_branches(request.user),
        "selected_branch": branch,
        "can_discount": request.user.is_superuser or request.user.has_perm("sales.apply_discount"),
    })


@permission_required_or_superuser("sales.add_sale")
@require_GET
def product_search(request):
    branch = _selected_branch(request, request.GET.get("branch"))
    if branch is None:
        return JsonResponse({"results": [], "error": "Selecciona una sucursal autorizada."}, status=400)
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"results": []})
    variants = ProductVariant.objects.filter(
        is_active=True, product__is_active=True
    ).filter(
        Q(product__name__icontains=query)
        | Q(product__internal_code__icontains=query)
        | Q(sku__icontains=query)
        | Q(barcode__icontains=query)
    ).filter(
        stock_levels__branch=branch, stock_levels__quantity__gt=0
    ).select_related("product", "size", "color").order_by("product__name", "sku").distinct()[:20]
    stocks = {
        stock.variant_id: stock.quantity
        for stock in StockLevel.objects.filter(branch=branch, variant__in=variants)
    }
    results = []
    for variant in variants:
        if not stocks.get(variant.pk, 0):
            continue
        price = variant.promotional_price
        if price is None:
            price = variant.product.promotional_price
        if price is None:
            price = variant.sale_price if variant.sale_price is not None else variant.product.sale_price
        results.append({
            "id": variant.pk,
            "name": variant.product.name,
            "sku": variant.sku,
            "barcode": variant.barcode or "",
            "size": variant.size.name if variant.size_id else "—",
            "color": variant.color.name if variant.color_id else "—",
            "price": str(price),
            "tax_rate": str(variant.product.tax_rate),
            "stock": stocks[variant.pk],
        })
    return JsonResponse({"results": results})


@permission_required_or_superuser("sales.view_sale")
def sale_detail(request, pk):
    sale = _get_sale(request.user, pk)
    cancel_form = SaleCancelForm(user=request.user, branch_id=sale.branch_id)
    return render(request, "sales/sale_detail.html", {
        "sale": sale,
        "can_cancel": sale.status == Sale.Status.CONFIRMED and (
            request.user.is_superuser or request.user.has_perm("sales.cancel_sale")
        ),
        "can_return": sale.status in {Sale.Status.CONFIRMED, Sale.Status.PARTIALLY_RETURNED}
        and (request.user.is_superuser or request.user.has_perm("sales.process_salereturn")),
        "cancel_form": cancel_form,
    })


@permission_required_or_superuser("sales.view_sale")
def sale_receipt(request, pk):
    sale = _get_sale(request.user, pk)
    return render(request, "sales/receipt.html", {"sale": sale})


@permission_required_or_superuser("sales.cancel_sale")
@require_POST
def sale_cancel(request, pk):
    sale = _get_sale(request.user, pk)
    form = SaleCancelForm(request.POST, user=request.user, branch_id=sale.branch_id)
    try:
        if not form.is_valid():
            raise ValidationError("Completa el motivo y selecciona el turno de caja.")
        cancel_sale(
            sale_id=sale.pk,
            user=request.user,
            reason=form.cleaned_data["reason"],
            cash_session_id=form.cleaned_data["cash_session"].pk,
        )
        messages.success(request, f"La venta {sale.number} fue anulada y el inventario fue restituido.")
    except ValidationError as error:
        messages.error(request, str(error))
    return redirect("sales:detail", pk=pk)


@permission_required_or_superuser("sales.process_salereturn")
def sale_return(request, pk):
    sale = _get_sale(request.user, pk)
    if sale.status not in {Sale.Status.CONFIRMED, Sale.Status.PARTIALLY_RETURNED}:
        messages.error(request, "Esta venta no admite devoluciones.")
        return redirect("sales:detail", pk=pk)
    lines = list(SaleLine.objects.filter(sale=sale).order_by("pk"))
    active_lines = [line for line in lines if line.remaining_quantity]
    form = SaleReturnForm(request.POST or None, user=request.user, branch_id=sale.branch_id)
    if request.method == "POST":
        line_forms = []
        for index, sale_line in enumerate(active_lines):
            line_form = SaleReturnLineForm(
                request.POST,
                prefix=f"lines-{index}",
                sale_line=sale_line,
            )
            line_forms.append(line_form)
        line_valid = all(line_form.is_valid() for line_form in line_forms)
        if form.is_valid() and line_valid:
            try:
                sale_return = process_return(
                    sale_id=sale.pk,
                    lines=[{
                        "sale_line_id": line_form.cleaned_data["sale_line_id"],
                        "quantity": line_form.cleaned_data["quantity"],
                    } for line_form in line_forms],
                    reason=form.cleaned_data["reason"],
                    refund_method_id=form.cleaned_data["refund_method"].pk,
                    user=request.user,
                    idempotency_key=form.cleaned_data["idempotency_key"],
                    cash_session_id=form.cleaned_data["cash_session"].pk,
                )
                messages.success(request, f"Devolución registrada por Bs {sale_return.refund_amount:.2f}.")
                return redirect("sales:detail", pk=sale.pk)
            except ValidationError as error:
                form.add_error(None, error)
    else:
        line_forms = [
            SaleReturnLineForm(prefix=f"lines-{index}", sale_line=line)
            for index, line in enumerate(active_lines)
        ]
    return render(request, "sales/return_form.html", {
        "sale": sale,
        "form": form,
        "line_forms": line_forms,
        "line_count": len(line_forms),
    })
