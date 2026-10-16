from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import DecimalField, F, ExpressionWrapper, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.branches.models import Branch
from apps.core.decorators import permission_required_or_superuser

from .forms import (
    ActionKeyForm,
    StockAdjustmentForm,
    StockMinimumForm,
    StockMovementForm,
    TransferCreateForm,
    TransferLineFormSet,
    TransferReceiptForm,
    TransferReceiptLineFormSet,
    authorized_branches,
)
from .models import InventoryMovement, InventoryOperation, StockLevel, StockTransfer
from .services import (
    adjust_stock_count,
    change_transfer_status,
    create_transfer,
    dispatch_transfer,
    receive_transfer,
    record_issue,
    record_receipt,
)


def _selected_branch(request, raw_branch_id=None):
    branches = authorized_branches(request.user)
    branch_id = raw_branch_id or request.session.get("selected_branch_id")
    if branch_id:
        return get_object_or_404(branches, pk=branch_id)
    if branches.count() == 1:
        return branches.first()
    return None


def _user_can_access_branch(user, branch_id):
    return authorized_branches(user).filter(pk=branch_id).exists()


def _transfer_queryset(user):
    branches = authorized_branches(user)
    return StockTransfer.objects.filter(
        Q(origin__in=branches) | Q(destination__in=branches)
    ).select_related("origin", "destination", "created_by", "dispatched_by", "received_by")


def _get_transfer(request, pk):
    return get_object_or_404(
        _transfer_queryset(request.user).prefetch_related(
            "lines__variant__product", "lines__variant__size", "lines__variant__color", "events__user"
        ),
        pk=pk,
    )


def _add_validation_error(form, error):
    if hasattr(error, "error_dict"):
        form.add_error(None, error)
    else:
        form.add_error(None, ValidationError(str(error)))


@permission_required_or_superuser("inventory.view_stocklevel")
def stock_list(request):
    branch = _selected_branch(request, request.GET.get("branch"))
    records = StockLevel.objects.none()
    if branch:
        records = StockLevel.objects.filter(branch=branch).select_related(
            "variant__product__category", "variant__product__brand",
            "variant__size", "variant__color", "branch",
        )
        query = request.GET.get("q", "").strip()
        if query:
            records = records.filter(
                Q(variant__sku__icontains=query)
                | Q(variant__barcode__icontains=query)
                | Q(variant__product__internal_code__icontains=query)
                | Q(variant__product__name__icontains=query)
            )
        category_id = request.GET.get("category", "")
        if category_id.isdigit():
            records = records.filter(variant__product__category_id=category_id)
        stock_status = request.GET.get("status", "all")
        if stock_status == "out":
            records = records.filter(quantity=0)
        elif stock_status == "low":
            records = records.filter(minimum_quantity__gt=0, quantity__lte=F("minimum_quantity"))
    else:
        query = request.GET.get("q", "").strip()
        category_id = request.GET.get("category", "")
        stock_status = request.GET.get("status", "all")

    page_obj = Paginator(records.order_by("variant__product__name", "variant__sku", "pk"), 30).get_page(
        request.GET.get("page")
    )
    inventory_value = records.aggregate(
        total=Sum(ExpressionWrapper(
            F("quantity") * F("average_cost"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ))
    )["total"] or Decimal("0.00")
    return render(request, "inventory/stock_list.html", {
        "page_obj": page_obj,
        "stock_levels": page_obj.object_list,
        "branches": authorized_branches(request.user),
        "selected_branch": branch,
        "query": query,
        "category_id": category_id,
        "status": stock_status,
        "categories": _categories_for_stock(),
        "inventory_value": inventory_value,
        "can_change_stock": request.user.is_superuser or request.user.has_perm("inventory.change_stocklevel"),
        "can_add_operations": request.user.is_superuser or request.user.has_perm("inventory.add_inventoryoperation"),
        "can_view_movements": request.user.is_superuser or request.user.has_perm("inventory.view_inventoryoperation"),
        "can_manage_transfers": request.user.is_superuser or request.user.has_perm("inventory.view_stocktransfer"),
    })


def _categories_for_stock():
    from apps.products.models import Category

    return Category.objects.filter(is_active=True).order_by("name")


@permission_required_or_superuser("inventory.change_stocklevel")
@require_POST
def update_stock_minimum(request, pk):
    stock = get_object_or_404(
        StockLevel.objects.filter(branch__in=authorized_branches(request.user))
        .select_related("branch", "variant"),
        pk=pk,
    )
    form = StockMinimumForm(request.POST, instance=stock)
    if form.is_valid():
        form.save()
        messages.success(request, "El stock mínimo se actualizó; las existencias no fueron modificadas.")
    else:
        messages.error(request, "No se pudo actualizar el stock mínimo.")
    return redirect("inventory:stock_list")


def _movement_form(request, kind):
    include_cost = kind == "receipt"
    branch = _selected_branch(request)
    form = StockMovementForm(
        request.POST or None,
        user=request.user,
        initial_branch=branch,
        include_cost=include_cost,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            if include_cost:
                operation = record_receipt(
                    branch_id=data["branch"].pk,
                    variant_id=data["variant"].pk,
                    quantity=data["quantity"],
                    unit_cost=data.get("unit_cost"),
                    user=request.user,
                    reason=data["reason"],
                    idempotency_key=data["idempotency_key"],
                )
            else:
                operation = record_issue(
                    branch_id=data["branch"].pk,
                    variant_id=data["variant"].pk,
                    quantity=data["quantity"],
                    user=request.user,
                    reason=data["reason"],
                    idempotency_key=data["idempotency_key"],
                )
            messages.success(request, "La entrada de inventario se registró correctamente." if include_cost else "La salida se registró correctamente.")
            return redirect("inventory:operation_detail", pk=operation.pk)
        except ValidationError as error:
            _add_validation_error(form, error)
    return render(request, "inventory/movement_form.html", {
        "form": form,
        "movement_title": "Registrar entrada" if include_cost else "Registrar salida",
        "is_receipt": include_cost,
    })


@permission_required_or_superuser("inventory.add_inventoryoperation")
def receipt_create(request):
    return _movement_form(request, "receipt")


@permission_required_or_superuser("inventory.add_inventoryoperation")
def issue_create(request):
    return _movement_form(request, "issue")


@permission_required_or_superuser("inventory.add_inventoryoperation")
def adjustment_create(request):
    branch = _selected_branch(request)
    form = StockAdjustmentForm(request.POST or None, user=request.user, initial_branch=branch)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            operation = adjust_stock_count(
                branch_id=data["branch"].pk,
                variant_id=data["variant"].pk,
                counted_quantity=data["counted_quantity"],
                user=request.user,
                reason=data["reason"],
                idempotency_key=data["idempotency_key"],
            )
            messages.success(request, "El ajuste de inventario quedó registrado en el historial.")
            return redirect("inventory:operation_detail", pk=operation.pk)
        except ValidationError as error:
            _add_validation_error(form, error)
    return render(request, "inventory/adjustment_form.html", {"form": form})


@permission_required_or_superuser("inventory.view_inventoryoperation")
def movement_list(request):
    branches = authorized_branches(request.user)
    records = InventoryMovement.objects.filter(branch__in=branches).select_related(
        "branch", "variant__product", "variant__size", "variant__color",
        "operation__user", "operation__transfer",
    )
    branch = _selected_branch(request, request.GET.get("branch"))
    if branch:
        records = records.filter(branch=branch)
    query = request.GET.get("q", "").strip()
    if query:
        records = records.filter(
            Q(variant__sku__icontains=query)
            | Q(variant__product__name__icontains=query)
            | Q(variant__product__internal_code__icontains=query)
        )
    kind = request.GET.get("kind", "")
    if kind in InventoryOperation.Kind.values:
        records = records.filter(operation__kind=kind)
    page_obj = Paginator(records.order_by("-created_at", "-pk"), 40).get_page(request.GET.get("page"))
    return render(request, "inventory/movement_list.html", {
        "page_obj": page_obj,
        "movements": page_obj.object_list,
        "branches": branches,
        "selected_branch": branch,
        "query": query,
        "selected_kind": kind,
        "kinds": InventoryOperation.Kind.choices,
    })


@permission_required_or_superuser("inventory.view_inventoryoperation")
def operation_detail(request, pk):
    operation = get_object_or_404(
        InventoryOperation.objects.select_related("branch", "user", "transfer")
        .prefetch_related("movements__variant__product", "movements__variant__size", "movements__variant__color"),
        pk=pk,
    )
    if not _user_can_access_branch(request.user, operation.branch_id):
        raise PermissionDenied
    return render(request, "inventory/operation_detail.html", {"operation": operation})


@permission_required_or_superuser("inventory.view_stocktransfer")
def transfer_list(request):
    transfers = _transfer_queryset(request.user).prefetch_related("lines")
    status = request.GET.get("status", "")
    if status in StockTransfer.Status.values:
        transfers = transfers.filter(status=status)
    query = request.GET.get("q", "").strip()
    if query.isdigit():
        transfers = transfers.filter(pk=int(query))
    page_obj = Paginator(transfers.order_by("-created_at", "-pk"), 30).get_page(request.GET.get("page"))
    return render(request, "inventory/transfer_list.html", {
        "page_obj": page_obj,
        "transfers": page_obj.object_list,
        "status": status,
        "statuses": StockTransfer.Status.choices,
        "query": query,
        "can_create": request.user.is_superuser or request.user.has_perm("inventory.add_stocktransfer"),
    })


@permission_required_or_superuser("inventory.add_stocktransfer")
def transfer_create(request):
    form = TransferCreateForm(request.POST or None, user=request.user)
    line_formset = TransferLineFormSet(
        request.POST or None,
        prefix="lines",
    )
    if request.method == "POST" and form.is_valid() and line_formset.is_valid():
        data = form.cleaned_data
        try:
            transfer = create_transfer(
                origin_id=data["origin"].pk,
                destination_id=data["destination"].pk,
                lines=[{
                    "variant_id": line.cleaned_data["variant"].pk,
                    "quantity": line.cleaned_data["quantity"],
                } for line in line_formset.forms if line.cleaned_data],
                user=request.user,
                notes=data["notes"],
                creation_key=data["creation_key"],
            )
            messages.success(request, f"La transferencia TR-{transfer.pk} se creó como borrador.")
            return redirect("inventory:transfer_detail", pk=transfer.pk)
        except ValidationError as error:
            _add_validation_error(form, error)
    return render(request, "inventory/transfer_form.html", {
        "form": form,
        "line_formset": line_formset,
    })


@permission_required_or_superuser("inventory.view_stocktransfer")
def transfer_detail(request, pk):
    transfer = _get_transfer(request, pk)
    can_change = request.user.is_superuser or request.user.has_perm("inventory.change_stocktransfer")
    can_add_operations = request.user.is_superuser or request.user.has_perm("inventory.add_inventoryoperation")
    origin_access = _user_can_access_branch(request.user, transfer.origin_id)
    destination_access = _user_can_access_branch(request.user, transfer.destination_id)
    return render(request, "inventory/transfer_detail.html", {
        "transfer": transfer,
        "can_submit": can_change and origin_access and transfer.status == StockTransfer.Status.DRAFT,
        "can_cancel": can_change and origin_access and transfer.status in {StockTransfer.Status.DRAFT, StockTransfer.Status.PENDING},
        "can_dispatch": can_change and can_add_operations and origin_access and transfer.status == StockTransfer.Status.PENDING,
        "can_receive": can_change and can_add_operations and destination_access and transfer.status in {StockTransfer.Status.IN_TRANSIT, StockTransfer.Status.PARTIAL},
        "submit_form": ActionKeyForm(),
        "dispatch_form": ActionKeyForm(),
    })


def _require_transfer_branch(request, transfer, branch_id):
    if not _user_can_access_branch(request.user, branch_id):
        raise PermissionDenied
    if not (request.user.is_superuser or request.user.has_perm("inventory.add_inventoryoperation")):
        raise PermissionDenied
    return transfer


@permission_required_or_superuser("inventory.change_stocktransfer")
@require_POST
def transfer_submit(request, pk):
    transfer = _get_transfer(request, pk)
    if not _user_can_access_branch(request.user, transfer.origin_id):
        raise PermissionDenied
    try:
        change_transfer_status(transfer_id=transfer.pk, user=request.user, action="submit")
        messages.success(request, "La transferencia está pendiente de despacho.")
    except ValidationError as error:
        messages.error(request, str(error))
    return redirect("inventory:transfer_detail", pk=pk)


@permission_required_or_superuser("inventory.change_stocktransfer")
@require_POST
def transfer_cancel(request, pk):
    transfer = _get_transfer(request, pk)
    if not _user_can_access_branch(request.user, transfer.origin_id):
        raise PermissionDenied
    try:
        change_transfer_status(transfer_id=transfer.pk, user=request.user, action="cancel")
        messages.success(request, "La transferencia fue cancelada.")
    except ValidationError as error:
        messages.error(request, str(error))
    return redirect("inventory:transfer_detail", pk=pk)


@permission_required_or_superuser("inventory.change_stocktransfer")
@require_POST
def transfer_dispatch_view(request, pk):
    transfer = _get_transfer(request, pk)
    _require_transfer_branch(request, transfer, transfer.origin_id)
    form = ActionKeyForm(request.POST)
    if form.is_valid():
        try:
            dispatch_transfer(
                transfer_id=transfer.pk,
                user=request.user,
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
            messages.success(request, "La mercadería salió de la sucursal de origen.")
        except ValidationError as error:
            messages.error(request, str(error))
    else:
        messages.error(request, "No se pudo validar la solicitud de despacho.")
    return redirect("inventory:transfer_detail", pk=pk)


@permission_required_or_superuser("inventory.change_stocktransfer")
def transfer_receive_view(request, pk):
    transfer = _get_transfer(request, pk)
    _require_transfer_branch(request, transfer, transfer.destination_id)
    initial = [{"line": line, "quantity": 0} for line in transfer.lines.all()]
    form = TransferReceiptForm(request.POST or None)
    line_formset = TransferReceiptLineFormSet(
        request.POST or None,
        prefix="receipt",
        initial=initial,
        transfer=transfer,
    )
    if request.method == "POST" and form.is_valid() and line_formset.is_valid():
        quantities = {
            item.cleaned_data["line"].pk: item.cleaned_data["quantity"]
            for item in line_formset.forms
        }
        try:
            updated = receive_transfer(
                transfer_id=transfer.pk,
                user=request.user,
                quantities=quantities,
                finalize=form.cleaned_data["finalize"],
                notes=form.cleaned_data["notes"],
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
            messages.success(request, "Recepción registrada." + (f" Diferencia faltante: {updated.quantity_missing}." if updated.quantity_missing else ""))
            return redirect("inventory:transfer_detail", pk=transfer.pk)
        except ValidationError as error:
            _add_validation_error(form, error)
    return render(request, "inventory/transfer_receive.html", {
        "transfer": transfer,
        "form": form,
        "line_formset": line_formset,
    })
