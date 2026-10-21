from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum, Max
from django.shortcuts import get_object_or_404, redirect, render

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, snapshot
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
    visible_customer_ids = visible_sales.values("customer_id").distinct()
    customers = Customer.objects.filter(
        Q(pk__in=visible_customer_ids)
        | Q(created_by=request.user)
        | Q(sales__seller=request.user, sales__customer__isnull=False)
    ).annotate(
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
@transaction.atomic
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save(commit=False)
        customer.created_by = request.user
        customer.save()
        record_event(actor=request.user, action=AuditEvent.Action.CREATE, instance=customer)
        messages.success(request, f"El cliente {customer.full_name} se registró.")
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "customers/customer_form.html", {"form": form, "page_title": "Nuevo cliente"})


@permission_required_or_superuser("customers.view_customer")
def customer_detail(request, pk):
    sales = Sale.objects.filter(
        branch__in=authorized_branches(request.user),
        status__in=[Sale.Status.CONFIRMED, Sale.Status.PARTIALLY_RETURNED],
    )
    if not request.user.is_superuser and not request.user.has_perm("sales.view_all_sales"):
        sales = sales.filter(seller=request.user)
    customer = get_object_or_404(
        Customer.objects.filter(Q(sales__in=sales) | Q(created_by=request.user)), pk=pk
    )
    sales = customer.sales.filter(pk__in=sales.values("pk"))
    sales = sales.select_related("branch", "seller").prefetch_related("payments__method", "returns").order_by("-created_at")
    return render(request, "customers/customer_detail.html", {"customer": customer, "sales": sales})


@permission_required_or_superuser("customers.change_customer")
@transaction.atomic
def customer_update(request, pk):
    visible_sales = Sale.objects.filter(
        branch__in=authorized_branches(request.user),
        status__in=[Sale.Status.CONFIRMED, Sale.Status.PARTIALLY_RETURNED],
    )
    if not request.user.is_superuser and not request.user.has_perm("sales.view_all_sales"):
        visible_sales = visible_sales.filter(seller=request.user)
    customer = get_object_or_404(
        Customer.objects.filter(Q(sales__in=visible_sales) | Q(created_by=request.user)), pk=pk
    )
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        before = snapshot(Customer.objects.get(pk=customer.pk))
        form.save()
        record_event(
            actor=request.user, action=AuditEvent.Action.UPDATE, instance=customer,
            before_data=before,
        )
        messages.success(request, "Los datos del cliente se actualizaron.")
        return redirect("customers:detail", pk=customer.pk)
    return render(request, "customers/customer_form.html", {
        "form": form, "page_title": f"Editar cliente: {customer.full_name}", "customer": customer,
    })
