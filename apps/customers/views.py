from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum, Max
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.decorators import permission_required_or_superuser
from apps.sales.forms import authorized_branches
from apps.sales.models import Sale

from .forms import CustomerForm
from .models import Customer


@permission_required_or_superuser("customers.view_customer")
def customer_list(request):
    visible_sales = Sale.objects.filter(
        branch__in=authorized_branches(request.user),
        status__in=[Sale.Status.CONFIRMED, Sale.Status.PARTIALLY_RETURNED],
    )
    if not request.user.is_superuser and not request.user.has_perm("sales.view_all_sales"):
        visible_sales = visible_sales.filter(seller=request.user)
    customers = Customer.objects.all().annotate(
        purchase_total=Sum("sales__total", filter=Q(sales__in=visible_sales)),
        last_purchase=Max("sales__created_at", filter=Q(sales__in=visible_sales)),
        purchase_count=Count("sales", filter=Q(sales__in=visible_sales), distinct=True),
    )
    query = request.GET.get("q", "").strip()
    if query:
        customers = customers.filter(
            Q(full_name__icontains=query)
            | Q(document_number__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
        )
    page_obj = Paginator(customers.order_by("full_name", "pk"), 30).get_page(request.GET.get("page"))
    return render(request, "customers/customer_list.html", {
        "page_obj": page_obj, "customers": page_obj.object_list, "query": query,
        "can_add": request.user.is_superuser or request.user.has_perm("customers.add_customer"),
    })


@permission_required_or_superuser("customers.add_customer")
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, f"El cliente {customer.full_name} se registró.")
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "customers/customer_form.html", {"form": form, "page_title": "Nuevo cliente"})


@permission_required_or_superuser("customers.view_customer")
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    sales = customer.sales.filter(branch__in=authorized_branches(request.user))
    if not request.user.is_superuser and not request.user.has_perm("sales.view_all_sales"):
        sales = sales.filter(seller=request.user)
    sales = sales.select_related("branch", "seller").prefetch_related("payments__method", "returns").order_by("-created_at")
    return render(request, "customers/customer_detail.html", {"customer": customer, "sales": sales})


@permission_required_or_superuser("customers.change_customer")
def customer_update(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Los datos del cliente se actualizaron.")
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "customers/customer_form.html", {
        "form": form, "page_title": f"Editar cliente: {customer.full_name}", "customer": customer,
    })
