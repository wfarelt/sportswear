import hashlib
import json
import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.branches.models import Branch
from apps.sales.models import SalePayment, SaleReturn

from .models import CashMovement, CashResource, CashSession, CashSessionEvent

ZERO = Decimal("0.00")


def _fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _authorized_branch(user, branch_id):
    branches = Branch.objects.filter(pk=branch_id, is_active=True)
    if not user.has_global_branch_access:
        branches = branches.filter(authorized_users=user)
    branch = branches.first()
    if branch is None:
        raise PermissionDenied("No tienes acceso a esta sucursal.")
    return branch


def _require_permission(user, permission):
    if not (user.is_superuser or user.has_perm(permission)):
        raise PermissionDenied("No tienes permiso para realizar esta operación de caja.")


def _locked_open_session(session_id, user, *, permission="cash.add_cashmovement"):
    _require_permission(user, permission)
    try:
        session = CashSession.objects.select_for_update().select_related("resource__branch").get(
            pk=session_id, status=CashSession.Status.OPEN, resource__is_active=True,
            resource__branch__is_active=True,
        )
    except CashSession.DoesNotExist as exc:
        raise ValidationError("El turno no existe, está cerrado o su caja está inactiva.") from exc
    _authorized_branch(user, session.resource.branch_id)
    return session


def get_open_sessions(user, branch_id=None):
    branches = Branch.objects.filter(is_active=True)
    if not user.has_global_branch_access:
        branches = branches.filter(authorized_users=user)
    sessions = CashSession.objects.filter(
        resource__branch__in=branches,
        resource__is_active=True,
        status=CashSession.Status.OPEN,
    ).select_related("resource", "resource__branch", "opened_by")
    if branch_id:
        sessions = sessions.filter(resource__branch_id=branch_id)
    return sessions.order_by("resource__branch__name", "resource__name")


def resolve_open_session(user, branch_id, session_id=None):
    sessions = get_open_sessions(user, branch_id)
    if session_id:
        session = sessions.filter(pk=session_id).first()
        if session is None:
            raise ValidationError("Selecciona un turno abierto de la sucursal autorizada.")
        return session
    sessions = list(sessions[:2])
    if len(sessions) == 1:
        return sessions[0]
    if not sessions:
        raise ValidationError("No hay una caja abierta para esta sucursal. Abre un turno antes de vender.")
    raise ValidationError("Hay varias cajas abiertas; selecciona el turno que recibirá el pago.")


def open_session(*, resource_id, opening_balance, user, notes=""):
    _require_permission(user, "cash.open_cashsession")
    try:
        opening_balance = Decimal(opening_balance).quantize(Decimal("0.01"))
    except Exception as exc:
        raise ValidationError("El saldo inicial no es válido.") from exc
    if opening_balance < ZERO:
        raise ValidationError("El saldo inicial no puede ser negativo.")
    with transaction.atomic():
        try:
            resource = CashResource.objects.select_for_update().select_related("branch").get(
                pk=resource_id, is_active=True, branch__is_active=True
            )
        except CashResource.DoesNotExist as exc:
            raise ValidationError("La caja no existe o está inactiva.") from exc
        _authorized_branch(user, resource.branch_id)
        if CashSession.objects.filter(resource=resource, status=CashSession.Status.OPEN).exists():
            raise ValidationError("Esta caja ya tiene un turno abierto.")
        try:
            with transaction.atomic():
                session = CashSession.objects.create(
                    resource=resource,
                    opening_balance=opening_balance,
                    opened_by=user,
                    opening_notes=notes.strip(),
                )
        except IntegrityError as exc:
            raise ValidationError("Esta caja ya tiene un turno abierto.") from exc
        CashSessionEvent.objects.create(
            session=session, user=user, event_type="opened",
            details=f"Apertura con saldo inicial Bs {opening_balance:.2f}.",
        )
        return session


def _add_movement(*, session, kind, amount, user, payment_method=None,
                  affects_cash=True, sale_payment=None, sale_return=None,
                  reference="", reason="", idempotency_key=None, request_hash=""):
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= ZERO:
        raise ValidationError("El importe debe ser mayor que cero.")
    return CashMovement.objects.create(
        session=session,
        kind=kind,
        amount=amount,
        payment_method=payment_method,
        affects_cash=affects_cash,
        sale_payment=sale_payment,
        sale_return=sale_return,
        reference=reference.strip()[:100],
        reason=reason.strip()[:255],
        idempotency_key=idempotency_key or uuid.uuid4(),
        request_hash=request_hash,
        user=user,
    )


def record_sale_collections(*, session_id=None, sale, payments, user):
    """Link every payment from a sale to the open shift that accepted it."""
    session = resolve_open_session(user, sale.branch_id, session_id)
    created = []
    with transaction.atomic():
        session = _locked_open_session(session.pk, user)
        if session.resource.branch_id != sale.branch_id:
            raise ValidationError("El turno de caja debe pertenecer a la sucursal de la venta.")
        payment_ids = [payment.pk for payment in payments]
        locked_payments = {
            payment.pk: payment
            for payment in SalePayment.objects.select_for_update().select_related(
                "method", "sale__branch"
            ).filter(pk__in=payment_ids, sale=sale).order_by("pk")
        }
        if len(locked_payments) != len(payment_ids):
            raise ValidationError("No se pudieron asociar todos los pagos de la venta con caja.")
        for payment in payments:
            if hasattr(payment, "cash_movement"):
                movement = payment.cash_movement
                if movement.session_id != session.pk:
                    raise ValidationError("El pago ya está asociado a otro turno de caja.")
                created.append(movement)
                continue
            created.append(_add_movement(
                session=session,
                kind=CashMovement.Kind.SALE_COLLECTION,
                amount=payment.amount,
                user=user,
                payment_method=payment.method,
                affects_cash=payment.method.affects_cash,
                sale_payment=payment,
                reference=sale.number,
                reason=f"Cobro de venta {sale.number}",
            ))
        return created


def record_sale_refund(*, sale_return, payment_method, amount, user, session_id=None):
    """Record one refund tender; cash refunds require an open shift, non-cash are tracked too."""
    branch_id = sale_return.sale.branch_id
    session = resolve_open_session(user, branch_id, session_id)
    with transaction.atomic():
        session = _locked_open_session(session.pk, user)
        return _add_movement(
            session=session,
            kind=CashMovement.Kind.SALE_REFUND,
            amount=amount,
            user=user,
            payment_method=payment_method,
            affects_cash=bool(payment_method and payment_method.affects_cash),
            sale_return=sale_return,
            reference=sale_return.sale.number,
            reason=f"Reembolso de venta {sale_return.sale.number}",
        )


def record_manual_movement(*, session_id, kind, amount, reason, reference, user, idempotency_key):
    if kind not in {
        CashMovement.Kind.OTHER_INCOME,
        CashMovement.Kind.EXPENSE,
        CashMovement.Kind.WITHDRAWAL,
    }:
        raise ValidationError("Tipo de movimiento manual no válido.")
    if not reason.strip():
        raise ValidationError("Indica el motivo del movimiento.")
    _require_permission(user, "cash.add_cashmovement")
    payload_hash = _fingerprint({
        "session": session_id, "kind": kind, "amount": str(amount),
        "reason": reason.strip(), "reference": reference.strip(),
    })
    with transaction.atomic():
        existing = CashMovement.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.request_hash != payload_hash:
                raise ValidationError("La clave de movimiento ya se utilizó con datos diferentes.")
            _authorized_branch(user, existing.session.resource.branch_id)
            return existing
        session = _locked_open_session(session_id, user)
        movement = _add_movement(
            session=session, kind=kind, amount=amount, user=user,
            affects_cash=True, reason=reason, reference=reference,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
        )
        CashSessionEvent.objects.create(
            session=session, user=user, event_type="movement",
            details=f"{movement.get_kind_display()}: Bs {movement.amount:.2f} · {movement.reason}"[:255],
        )
        return movement


def close_session(*, session_id, counted_cash, user, notes=""):
    _require_permission(user, "cash.close_cashsession")
    counted_cash = Decimal(counted_cash).quantize(Decimal("0.01"))
    if counted_cash < ZERO:
        raise ValidationError("El efectivo contado no puede ser negativo.")
    with transaction.atomic():
        session = _locked_open_session(
            session_id, user, permission="cash.close_cashsession"
        )
        movements = session.movements.all()
        sums = {
            row["kind"]: row["total"] or ZERO
            for row in movements.values("kind").annotate(total=Sum("amount"))
        }
        cash_sales = movements.filter(
            kind=CashMovement.Kind.SALE_COLLECTION, affects_cash=True
        ).aggregate(total=Sum("amount"))["total"] or ZERO
        noncash = movements.filter(
            kind=CashMovement.Kind.SALE_COLLECTION, affects_cash=False
        ).aggregate(total=Sum("amount"))["total"] or ZERO
        incomes = sums.get(CashMovement.Kind.OTHER_INCOME, ZERO)
        expenses = sums.get(CashMovement.Kind.EXPENSE, ZERO)
        withdrawals = sums.get(CashMovement.Kind.WITHDRAWAL, ZERO)
        refunds = movements.filter(
            kind=CashMovement.Kind.SALE_REFUND, affects_cash=True
        ).aggregate(total=Sum("amount"))["total"] or ZERO
        expected = session.opening_balance + cash_sales + incomes - expenses - withdrawals - refunds
        difference = counted_cash - expected
        session.status = CashSession.Status.CLOSED
        session.counted_cash = counted_cash
        session.expected_cash = expected
        session.cash_sales_total = cash_sales
        session.noncash_collections_total = noncash
        session.additional_income_total = incomes
        session.expense_total = expenses
        session.withdrawal_total = withdrawals
        session.refund_total = refunds
        session.difference = difference
        session.closed_by = user
        session.closed_at = timezone.now()
        session.closing_notes = notes.strip()
        session.save(update_fields=[
            "status", "counted_cash", "expected_cash", "cash_sales_total",
            "noncash_collections_total", "additional_income_total", "expense_total",
            "withdrawal_total", "refund_total", "difference", "closed_by",
            "closed_at", "closing_notes",
        ])
        CashSessionEvent.objects.create(
            session=session, user=user, event_type="closed",
            details=(
                f"Cierre: esperado Bs {expected:.2f}; contado Bs {counted_cash:.2f}; "
                f"diferencia Bs {difference:.2f}."
            )[:255],
        )
        return session


def get_session_totals(session):
    movements = session.movements.all()
    cash_sales = movements.filter(kind=CashMovement.Kind.SALE_COLLECTION, affects_cash=True).aggregate(total=Sum("amount"))["total"] or ZERO
    noncash = movements.filter(kind=CashMovement.Kind.SALE_COLLECTION, affects_cash=False).aggregate(total=Sum("amount"))["total"] or ZERO
    income = movements.filter(kind=CashMovement.Kind.OTHER_INCOME).aggregate(total=Sum("amount"))["total"] or ZERO
    expenses = movements.filter(kind=CashMovement.Kind.EXPENSE).aggregate(total=Sum("amount"))["total"] or ZERO
    withdrawals = movements.filter(kind=CashMovement.Kind.WITHDRAWAL).aggregate(total=Sum("amount"))["total"] or ZERO
    refunds = movements.filter(kind=CashMovement.Kind.SALE_REFUND, affects_cash=True).aggregate(total=Sum("amount"))["total"] or ZERO
    expected = session.opening_balance + cash_sales + income - expenses - withdrawals - refunds
    return {
        "cash_sales": cash_sales,
        "noncash_collections": noncash,
        "additional_income": income,
        "expenses": expenses,
        "withdrawals": withdrawals,
        "cash_refunds": refunds,
        "expected_cash": expected,
    }
