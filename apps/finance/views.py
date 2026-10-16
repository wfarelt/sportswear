from decimal import Decimal
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.decorators import permission_required_or_superuser

from .forms import (
    ApproveFinanceOperationForm,
    FinanceOperationForm,
    RejectFinanceOperationForm,
    ReverseFinanceOperationForm,
)
from .models import FinanceAccount, FinanceCategory, FinanceOperation
from .services import (
    approve_operation,
    create_operation,
    reject_operation,
    reverse_operation,
    visible_operations,
)


def _can(user, permission):
    return user.is_superuser or user.has_perm(permission)


def _operation_queryset(user):
    return visible_operations(user).prefetch_related("events__user")


def _get_operation(user, pk):
    return get_object_or_404(_operation_queryset(user), pk=pk)


def _account_balances(user):
    accounts = FinanceAccount.objects.filter(is_active=True).select_related("branch")
    if not user.has_global_branch_access:
        account_scope = Q(branch__in=user.branches.filter(is_active=True))
        if user.has_perm("finance.view_general_finance_accounts"):
            account_scope |= Q(branch__isnull=True, kind=FinanceAccount.Kind.BANK)
        accounts = accounts.filter(account_scope)
    income_totals = FinanceOperation.objects.filter(
        account_id=OuterRef("pk"),
        status__in=[FinanceOperation.Status.POSTED, FinanceOperation.Status.REVERSED],
        kind=FinanceOperation.Kind.INCOME,
    ).values("account_id").annotate(total=Sum("amount")).values("total")[:1]
    expense_totals = FinanceOperation.objects.filter(
        account_id=OuterRef("pk"),
        status__in=[FinanceOperation.Status.POSTED, FinanceOperation.Status.REVERSED],
        kind=FinanceOperation.Kind.EXPENSE,
    ).values("account_id").annotate(total=Sum("amount")).values("total")[:1]
    return accounts.annotate(
        income=Subquery(income_totals, output_field=DecimalField(max_digits=14, decimal_places=2)),
        expense=Subquery(expense_totals, output_field=DecimalField(max_digits=14, decimal_places=2)),
    ).annotate(
        balance=ExpressionWrapper(
            F("opening_balance")
            + Coalesce(F("income"), Value(Decimal("0.00")))
            - Coalesce(F("expense"), Value(Decimal("0.00"))),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    ).order_by("branch__name", "name")


@login_required
def operation_list(request):
    if not (_can(request.user, "finance.view_financeoperation") or _can(request.user, "finance.add_financeoperation")):
        raise PermissionDenied
    operations = _operation_queryset(request.user)
    kind = request.GET.get("kind", "")
    if kind in FinanceOperation.Kind.values:
        operations = operations.filter(kind=kind)
    status = request.GET.get("status", "")
    if status in FinanceOperation.Status.values:
        operations = operations.filter(status=status)
    query = request.GET.get("q", "").strip()
    if query:
        operations = operations.filter(
            Q(number__icontains=query)
            | Q(concept__icontains=query)
            | Q(account__name__icontains=query)
            | Q(category__name__icontains=query)
        )
    page_obj = Paginator(operations.order_by("-operation_date", "-pk"), 30).get_page(request.GET.get("page"))
    return render(request, "finance/operation_list.html", {
        "page_obj": page_obj, "operations": page_obj.object_list,
        "kind": kind, "status": status, "query": query,
        "kinds": FinanceOperation.Kind.choices, "statuses": FinanceOperation.Status.choices,
        "accounts": _account_balances(request.user) if _can(request.user, "finance.view_financeoperation") else [],
        "can_add": _can(request.user, "finance.add_financeoperation"),
    })


def _operation_create(request, kind):
    form = FinanceOperationForm(
        request.POST or None, request.FILES or None,
        user=request.user, kind=kind,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            operation = create_operation(
                kind=kind,
                category_id=data["category"].pk,
                branch_id=data["branch"].pk,
                account_id=data["account"].pk,
                concept=data["concept"],
                amount=data["amount"],
                operation_date=data["operation_date"],
                notes=data["notes"],
                user=request.user,
                idempotency_key=data["idempotency_key"],
                cash_session_id=data["cash_session"].pk if data["cash_session"] else None,
                payment_method_id=data["payment_method"].pk if data["payment_method"] else None,
                attachment=data["attachment"],
            )
            if operation.status == FinanceOperation.Status.POSTED:
                messages.success(request, f"La operación {operation.number} fue confirmada.")
            else:
                messages.info(request, f"La operación {operation.number} quedó pendiente de autorización.")
            return redirect("finance:detail", pk=operation.pk)
        except (ValidationError, PermissionDenied) as error:
            form.add_error(None, error)
    return render(request, "finance/operation_form.html", {
        "form": form,
        "kind": kind,
        "page_title": "Registrar ingreso" if kind == FinanceOperation.Kind.INCOME else "Registrar egreso",
    })


@permission_required_or_superuser("finance.add_financeoperation")
def income_create(request):
    return _operation_create(request, FinanceOperation.Kind.INCOME)


@permission_required_or_superuser("finance.add_financeoperation")
def expense_create(request):
    return _operation_create(request, FinanceOperation.Kind.EXPENSE)


@login_required
def operation_detail(request, pk):
    if not any(_can(request.user, permission) for permission in (
        "finance.view_financeoperation", "finance.add_financeoperation",
        "finance.approve_financeoperation", "finance.reject_financeoperation",
        "finance.reverse_financeoperation",
    )):
        raise PermissionDenied
    operation = _get_operation(request.user, pk)
    approver_form = ApproveFinanceOperationForm(
        user=request.user, operation=operation
    )
    reversal_form = ReverseFinanceOperationForm(
        user=request.user, operation=operation
    )
    return render(request, "finance/operation_detail.html", {
        "operation": operation,
        "can_approve": operation.status == FinanceOperation.Status.PENDING and _can(request.user, "finance.approve_financeoperation") and operation.created_by_id != request.user.pk,
        "can_reject": operation.status == FinanceOperation.Status.PENDING and _can(request.user, "finance.reject_financeoperation") and operation.created_by_id != request.user.pk,
        "can_reverse": operation.status == FinanceOperation.Status.POSTED and _can(request.user, "finance.reverse_financeoperation"),
        "approve_form": approver_form,
        "reject_form": RejectFinanceOperationForm(),
        "reverse_form": reversal_form,
    })


@permission_required_or_superuser("finance.approve_financeoperation")
@require_POST
def operation_approve(request, pk):
    operation = _get_operation(request.user, pk)
    form = ApproveFinanceOperationForm(request.POST, user=request.user, operation=operation)
    if not form.is_valid():
        messages.error(request, "Selecciona un turno de caja válido para aprobar la operación.")
        return redirect("finance:detail", pk=pk)
    try:
        approved = approve_operation(
            operation_id=operation.pk,
            user=request.user,
            cash_session_id=form.cleaned_data["cash_session"].pk if form.cleaned_data["cash_session"] else None,
        )
        messages.success(request, f"La operación {approved.number} quedó confirmada.")
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, str(error))
    return redirect("finance:detail", pk=pk)


@permission_required_or_superuser("finance.reject_financeoperation")
@require_POST
def operation_reject(request, pk):
    operation = _get_operation(request.user, pk)
    form = RejectFinanceOperationForm(request.POST)
    if form.is_valid():
        try:
            reject_operation(operation_id=operation.pk, user=request.user, reason=form.cleaned_data["reason"])
            messages.success(request, f"La operación {operation.number} fue rechazada.")
        except (ValidationError, PermissionDenied) as error:
            messages.error(request, str(error))
    else:
        messages.error(request, "Indica un motivo para rechazar la operación.")
    return redirect("finance:detail", pk=pk)


@permission_required_or_superuser("finance.reverse_financeoperation")
@require_POST
def operation_reverse(request, pk):
    operation = _get_operation(request.user, pk)
    form = ReverseFinanceOperationForm(request.POST, user=request.user, operation=operation)
    if form.is_valid():
        try:
            reversal = reverse_operation(
                operation_id=operation.pk,
                category_id=form.cleaned_data["category"].pk,
                reason=form.cleaned_data["reason"],
                user=request.user,
                idempotency_key=form.cleaned_data["idempotency_key"],
                cash_session_id=form.cleaned_data["cash_session"].pk if form.cleaned_data["cash_session"] else None,
            )
            messages.success(request, f"Reversión {reversal.number} registrada ({reversal.get_status_display().lower()}).")
        except (ValidationError, PermissionDenied) as error:
            messages.error(request, str(error))
    else:
        messages.error(request, "Completa categoría, motivo y turno de caja cuando corresponda.")
    return redirect("finance:detail", pk=pk)
