from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.decorators import permission_required_or_superuser

from .forms import (
    CashSessionFilterForm,
    CloseSessionForm,
    ManualMovementForm,
    OpenSessionForm,
    authorized_branches,
)
from .models import CashMovement, CashResource, CashSession
from .services import close_session, get_open_sessions, get_session_totals, open_session, record_manual_movement


def _session_queryset(user):
    branches = authorized_branches(user)
    return CashSession.objects.filter(resource__branch__in=branches).select_related(
        "resource__branch", "opened_by", "closed_by"
    )


def _get_session(user, pk):
    return get_object_or_404(_session_queryset(user), pk=pk)


def _can(user, permission):
    return user.is_superuser or user.has_perm(permission)


@permission_required_or_superuser("cash.view_cashsession")
def session_list(request):
    form = CashSessionFilterForm(request.GET or None, user=request.user)
    sessions = _session_queryset(request.user).prefetch_related("movements")
    if form.is_valid():
        branch = form.cleaned_data.get("branch")
        status = form.cleaned_data.get("status")
        if branch:
            sessions = sessions.filter(resource__branch=branch)
        if status:
            sessions = sessions.filter(status=status)
    page_obj = Paginator(sessions.order_by("-opened_at", "-pk"), 30).get_page(request.GET.get("page"))
    return render(request, "cash/session_list.html", {
        "form": form,
        "page_obj": page_obj,
        "sessions": page_obj.object_list,
        "can_open": _can(request.user, "cash.open_cashsession"),
        "can_move": _can(request.user, "cash.add_cashmovement"),
    })


@permission_required_or_superuser("cash.open_cashsession")
def session_open(request):
    form = OpenSessionForm(request.POST or None, user=request.user)
    resources_available = form.fields["resource"].queryset.exists()
    has_authorized_resources = form.authorized_resources.exists()
    if request.method == "POST" and form.is_valid():
        try:
            session = open_session(
                resource_id=form.cleaned_data["resource"].pk,
                opening_balance=form.cleaned_data["opening_balance"],
                user=request.user,
                notes=form.cleaned_data["notes"],
            )
            messages.success(request, f"Turno de caja {session.pk} abierto.")
            return redirect("cash:session_detail", pk=session.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(request, "cash/session_open.html", {
        "form": form,
        "resources_available": resources_available,
        "has_authorized_resources": has_authorized_resources,
        "can_add_cash_resource": request.user.is_staff and _can(request.user, "cash.add_cashresource"),
    })


@permission_required_or_superuser("cash.view_cashsession")
def session_detail(request, pk):
    session = _get_session(request.user, pk)
    totals = get_session_totals(session)
    can_close = session.is_open and _can(request.user, "cash.close_cashsession")
    can_move = session.is_open and _can(request.user, "cash.add_cashmovement")
    movements = session.movements.select_related(
        "user", "payment_method", "sale_payment__sale", "sale_return__sale"
    ).order_by("-created_at", "-pk")
    return render(request, "cash/session_detail.html", {
        "session": session,
        "totals": totals,
        "movements": movements,
        "can_close": can_close,
        "can_move": can_move,
    })


@permission_required_or_superuser("cash.add_cashmovement")
def movement_create(request):
    branch_id = request.GET.get("branch") or request.session.get("selected_branch_id")
    initial_session = None
    if branch_id and str(branch_id).isdigit():
        initial_session = get_open_sessions(request.user, branch_id).first()
    form = ManualMovementForm(
        request.POST or None, user=request.user, initial_session=initial_session
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            movement = record_manual_movement(
                session_id=data["session"].pk,
                kind=data["kind"],
                amount=data["amount"],
                reason=data["reason"],
                reference=data["reference"],
                user=request.user,
                idempotency_key=data["idempotency_key"],
            )
            messages.success(request, "El movimiento de caja quedó registrado.")
            return redirect("cash:session_detail", pk=movement.session_id)
        except ValidationError as error:
            form.add_error(None, error)
    return render(request, "cash/movement_form.html", {"form": form})


@permission_required_or_superuser("cash.close_cashsession")
def session_close(request, pk):
    session = _get_session(request.user, pk)
    if not session.is_open:
        messages.error(request, "El turno ya está cerrado y no se puede modificar.")
        return redirect("cash:session_detail", pk=session.pk)
    form = CloseSessionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            closed = close_session(
                session_id=session.pk,
                counted_cash=form.cleaned_data["counted_cash"],
                user=request.user,
                notes=form.cleaned_data["notes"],
            )
            messages.success(request, f"Turno cerrado. Diferencia: Bs {closed.difference:.2f}.")
            return redirect("cash:session_detail", pk=closed.pk)
        except ValidationError as error:
            form.add_error(None, error)
    return render(request, "cash/session_close.html", {
        "session": session,
        "totals": get_session_totals(session),
        "form": form,
    })


@permission_required_or_superuser("cash.view_cashmovement")
def movement_list(request):
    branches = authorized_branches(request.user)
    movements = CashMovement.objects.filter(
        session__resource__branch__in=branches
    ).select_related("session__resource__branch", "payment_method", "user")
    query = request.GET.get("q", "").strip()
    if query:
        movements = movements.filter(
            Q(reason__icontains=query)
            | Q(reference__icontains=query)
            | Q(session__id__icontains=query)
        )
    page_obj = Paginator(movements.order_by("-created_at", "-pk"), 40).get_page(request.GET.get("page"))
    return render(request, "cash/movement_list.html", {
        "page_obj": page_obj, "movements": page_obj.object_list, "query": query,
    })
