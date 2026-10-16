import hashlib
import json
import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.branches.models import Branch
from apps.cash.models import CashMovement, CashSession
from apps.cash.services import _authorized_branch, _locked_open_session, record_finance_movement

from .models import (
    FinanceAccount,
    FinanceApprovalLimit,
    FinanceCategory,
    FinanceEvent,
    FinanceOperation,
)

CENT = Decimal("0.01")


def _money(value):
    try:
        amount = Decimal(value).quantize(CENT)
    except Exception as exc:
        raise ValidationError("El importe no es válido.") from exc
    if amount <= 0:
        raise ValidationError("El importe debe ser mayor que cero.")
    return amount


def _fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require(user, permission):
    if not (user.is_superuser or user.has_perm(permission)):
        raise PermissionDenied("No tienes permiso para esta operación financiera.")


def _branch_scope(user, branch_id):
    if not branch_id:
        if user.has_global_branch_access:
            return None
        raise ValidationError("Selecciona una sucursal autorizada.")
    return _authorized_branch(user, branch_id)


def _limit_for(user, branch):
    if user.is_superuser:
        return Decimal("999999999999.99")
    scopes = (
        Q(user=user, branch=branch),
        Q(user=user, branch__isnull=True),
        Q(user__isnull=True, role=user.role, branch=branch),
        Q(user__isnull=True, role=user.role, branch__isnull=True),
    )
    for scope in scopes:
        limit = FinanceApprovalLimit.objects.filter(scope, is_active=True).first()
        if limit is not None:
            return limit.maximum_amount
    return Decimal("0.00")


def _authorized_account(user, account_id, branch):
    try:
        account = FinanceAccount.objects.select_for_update().select_related(
            "branch", "cash_resource", "cash_resource__branch"
        ).get(pk=account_id, is_active=True)
    except FinanceAccount.DoesNotExist as exc:
        raise ValidationError("La cuenta financiera no existe o está inactiva.") from exc
    if account.branch_id:
        _authorized_branch(user, account.branch_id)
        if branch and branch.pk != account.branch_id:
            raise ValidationError("La cuenta seleccionada pertenece a otra sucursal.")
    elif not user.has_global_branch_access and not user.has_perm("finance.use_general_finance_accounts"):
        raise PermissionDenied("No tienes permiso para utilizar cuentas financieras generales.")
    if account.kind == FinanceAccount.Kind.CASH:
        if not account.cash_resource_id or not account.cash_resource.is_active:
            raise ValidationError("La cuenta de efectivo debe estar asociada a una caja activa.")
        if branch and account.cash_resource.branch_id != branch.pk:
            raise ValidationError("La caja asociada a la cuenta pertenece a otra sucursal.")
    elif account.cash_resource_id:
        raise ValidationError("Solo las cuentas de efectivo pueden vincular un recurso de caja.")
    return account


def _get_session_for_cash(user, session_id, account, branch):
    if account.kind != FinanceAccount.Kind.CASH:
        if session_id:
            raise ValidationError("Las operaciones bancarias no deben afectar un turno de caja.")
        return None
    if not session_id:
        raise ValidationError("Selecciona un turno abierto para esta operación en efectivo.")
    session = _locked_open_session(session_id, user)
    if session.resource_id != account.cash_resource_id:
        raise ValidationError("El turno abierto no corresponde a la caja de la cuenta seleccionada.")
    if branch and session.resource.branch_id != branch.pk:
        raise ValidationError("El turno de caja pertenece a otra sucursal.")
    return session


def _event(operation, user, event_type, details=""):
    FinanceEvent.objects.create(
        operation=operation, user=user, event_type=event_type, details=details[:255]
    )


def _record_cash_effect(operation, session, user):
    if operation.account.kind != FinanceAccount.Kind.CASH:
        return
    if session is None:
        raise ValidationError("Una operación de efectivo confirmada requiere un turno de caja abierto.")
    cash_kind = (
        CashMovement.Kind.OTHER_INCOME
        if operation.kind == FinanceOperation.Kind.INCOME
        else CashMovement.Kind.EXPENSE
    )
    movement = record_finance_movement(
        session_id=session.pk,
        kind=cash_kind,
        amount=operation.amount,
        reason=operation.concept,
        reference=operation.number,
        user=user,
        finance_operation=operation,
        payment_method=operation.payment_method,
    )
    operation.cash_movement = movement
    operation.cash_session = session


def _post(operation, user, session_id=None):
    branch = _branch_scope(user, operation.branch_id)
    account = _authorized_account(user, operation.account_id, branch)
    session = _get_session_for_cash(user, session_id, account, branch)
    if operation.kind == FinanceOperation.Kind.INCOME:
        _record_cash_effect(operation, session, user)
    elif account.kind == FinanceAccount.Kind.CASH:
        _record_cash_effect(operation, session, user)
    operation.status = FinanceOperation.Status.POSTED
    operation.approved_by = user
    operation.approved_at = timezone.now()
    operation.cash_session = session
    operation.save(update_fields=[
        "status", "approved_by", "approved_at", "cash_session", "cash_movement"
    ])
    if operation.reversal_of_id:
        original = FinanceOperation.objects.select_for_update().get(pk=operation.reversal_of_id)
        if original.status != FinanceOperation.Status.POSTED:
            raise ValidationError("La operación original ya no está disponible para revertir.")
        original.status = FinanceOperation.Status.REVERSED
        original.reversed_by = user
        original.save(update_fields=["status", "reversed_by"])
        _event(original, user, "reversed", f"Revertida por {operation.number}.")
    _event(operation, user, "posted", f"Operación confirmada por Bs {operation.amount:.2f}.")
    return operation


def create_operation(*, kind, category_id, branch_id, account_id, concept, amount,
                     operation_date, notes, user, idempotency_key, cash_session_id=None,
                     payment_method_id=None, attachment=None):
    _require(user, "finance.add_financeoperation")
    if kind not in FinanceOperation.Kind.values:
        raise ValidationError("Tipo de operación financiera no válido.")
    amount = _money(amount)
    concept = concept.strip()
    if not concept:
        raise ValidationError("Indica el concepto de la operación.")
    branch = _branch_scope(user, branch_id)
    payload_hash = _fingerprint({
        "kind": kind, "category": category_id, "branch": branch_id,
        "account": account_id, "concept": concept, "amount": str(amount),
        "date": operation_date.isoformat(), "notes": notes.strip(),
        "cash_session": cash_session_id, "payment_method": payment_method_id,
        "attachment": (
            {"name": attachment.name, "size": attachment.size}
            if attachment is not None else None
        ),
    })
    with transaction.atomic():
        existing = FinanceOperation.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            if existing.request_hash != payload_hash:
                raise ValidationError("La clave ya se usó con datos distintos.")
            _branch_scope(user, existing.branch_id)
            return existing
        try:
            category = FinanceCategory.objects.select_for_update().get(
                pk=category_id, is_active=True, kind=kind
            )
        except FinanceCategory.DoesNotExist as exc:
            raise ValidationError("La categoría no existe, está inactiva o no corresponde al tipo.") from exc
        account = _authorized_account(user, account_id, branch)
        payment_method = None
        if payment_method_id:
            from apps.sales.models import PaymentMethod

            try:
                payment_method = PaymentMethod.objects.get(pk=payment_method_id, is_active=True)
            except PaymentMethod.DoesNotExist as exc:
                raise ValidationError("El medio de pago está inactivo o no existe.") from exc
        if account.kind == FinanceAccount.Kind.CASH and payment_method and not payment_method.affects_cash:
            raise ValidationError("El medio seleccionado no representa efectivo físico.")
        if account.kind != FinanceAccount.Kind.CASH and payment_method and payment_method.affects_cash:
            raise ValidationError("El medio de pago en efectivo debe utilizar una cuenta de caja.")
        if account.kind != FinanceAccount.Kind.CASH and cash_session_id:
            raise ValidationError("Las operaciones no efectivas no usan turnos de caja.")

        operation = FinanceOperation.objects.create(
            kind=kind,
            category=category,
            branch=branch,
            account=account,
            concept=concept,
            amount=amount,
            operation_date=operation_date,
            notes=notes.strip(),
            attachment=attachment,
            status=FinanceOperation.Status.PENDING,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            created_by=user,
            payment_method=payment_method,
        )
        operation.number = f"F-{operation.pk:08d}"
        operation.save(update_fields=["number"])
        _event(operation, user, "created", f"{operation.get_kind_display()} creada por Bs {amount:.2f}.")

        if _limit_for(user, branch) >= amount:
            _post(operation, user, cash_session_id)
        else:
            _event(operation, user, "pending", "Pendiente de autorización por límite de importe.")
        return operation


def approve_operation(*, operation_id, user, cash_session_id=None):
    _require(user, "finance.approve_financeoperation")
    with transaction.atomic():
        try:
            operation = FinanceOperation.objects.select_for_update().select_related(
                "branch", "account", "category"
            ).get(pk=operation_id)
        except FinanceOperation.DoesNotExist as exc:
            raise ValidationError("La operación financiera no existe.") from exc
        _branch_scope(user, operation.branch_id)
        if operation.status != FinanceOperation.Status.PENDING:
            raise ValidationError("Solo se autorizan operaciones pendientes.")
        if operation.created_by_id == user.pk:
            raise PermissionDenied("Quien registra una operación pendiente no puede autorizarla.")
        if _limit_for(user, operation.branch) < operation.amount:
            raise PermissionDenied("Tu límite de autorización es inferior al importe de esta operación.")
        return _post(operation, user, cash_session_id)


def reject_operation(*, operation_id, user, reason):
    _require(user, "finance.reject_financeoperation")
    reason = reason.strip()
    if not reason:
        raise ValidationError("Indica el motivo del rechazo.")
    with transaction.atomic():
        operation = FinanceOperation.objects.select_for_update().select_related("branch").get(pk=operation_id)
        _branch_scope(user, operation.branch_id)
        if operation.status != FinanceOperation.Status.PENDING:
            raise ValidationError("Solo se pueden rechazar operaciones pendientes.")
        if operation.created_by_id == user.pk:
            raise PermissionDenied("Quien registra una operación no puede rechazarla.")
        operation.status = FinanceOperation.Status.REJECTED
        operation.rejected_by = user
        operation.rejected_at = timezone.now()
        operation.save(update_fields=["status", "rejected_by", "rejected_at"])
        _event(operation, user, "rejected", reason)
        return operation


def reverse_operation(*, operation_id, category_id, reason, user, idempotency_key, cash_session_id=None):
    _require(user, "finance.reverse_financeoperation")
    reason = reason.strip()
    if not reason:
        raise ValidationError("Indica el motivo de la reversión.")
    with transaction.atomic():
        original = FinanceOperation.objects.select_for_update().select_related(
            "branch", "account", "payment_method"
        ).get(pk=operation_id)
        _branch_scope(user, original.branch_id)
        request_hash = _fingerprint({
            "reverses": original.pk, "category": category_id,
            "reason": reason, "cash_session": cash_session_id,
        })
        existing = FinanceOperation.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            if existing.request_hash != request_hash:
                raise ValidationError("La clave de reversión ya se usó con otros datos.")
            return existing
        if original.status != FinanceOperation.Status.POSTED:
            raise ValidationError("Solo se pueden revertir operaciones confirmadas.")
        if hasattr(original, "reversal"):
            raise ValidationError("La operación ya tiene una reversión registrada.")
        reverse_kind = (
            FinanceOperation.Kind.EXPENSE
            if original.kind == FinanceOperation.Kind.INCOME
            else FinanceOperation.Kind.INCOME
        )
        try:
            category = FinanceCategory.objects.get(pk=category_id, is_active=True, kind=reverse_kind)
        except FinanceCategory.DoesNotExist as exc:
            raise ValidationError("Selecciona una categoría activa del tipo opuesto.") from exc
        request_hash = _fingerprint({
            "reverses": original.pk, "category": category.pk, "reason": reason,
            "cash_session": cash_session_id,
        })
        reversal = FinanceOperation.objects.create(
            kind=reverse_kind,
            category=category,
            branch=original.branch,
            account=original.account,
            cash_session=None,
            payment_method=original.payment_method,
            concept=f"Reversión {original.number}: {reason}"[:180],
            amount=original.amount,
            operation_date=timezone.localdate(),
            notes=reason,
            status=FinanceOperation.Status.PENDING,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_by=user,
            reversal_of=original,
        )
        reversal.number = f"F-{reversal.pk:08d}"
        reversal.save(update_fields=["number"])
        _event(reversal, user, "created", f"Reversión de {original.number}: {reason}")
        if _limit_for(user, original.branch) >= reversal.amount:
            _post(reversal, user, cash_session_id)
        else:
            _event(reversal, user, "pending", "Reversión pendiente de autorización.")
        return reversal


def visible_operations(user):
    branches = Branch.objects.filter(is_active=True)
    if not user.has_global_branch_access:
        branches = branches.filter(authorized_users=user)
    operations = FinanceOperation.objects.filter(
        Q(branch__in=branches) | Q(branch__isnull=True, created_by=user)
    ).select_related("branch", "category", "account", "created_by", "approved_by")
    may_review = any(user.has_perm(f"finance.{permission}") for permission in (
        "approve_financeoperation", "reject_financeoperation", "reverse_financeoperation"
    ))
    if not user.is_superuser and not user.has_perm("finance.view_all_financeoperations") and not may_review:
        operations = operations.filter(created_by=user)
    return operations
