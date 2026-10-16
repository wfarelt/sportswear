import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.branches.models import Branch
from apps.products.models import ProductVariant

from .models import (
    InventoryMovement,
    InventoryOperation,
    StockLevel,
    StockTransfer,
    StockTransferEvent,
    StockTransferLine,
)

CENT = Decimal("0.01")


def _fingerprint(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _check_replay(existing, request_hash):
    if existing.request_hash != request_hash:
        raise ValidationError("La clave de operación ya se utilizó con datos diferentes.")
    return existing


def _lock_branch(branch_id):
    try:
        return Branch.objects.select_for_update().get(pk=branch_id, is_active=True)
    except Branch.DoesNotExist as exc:
        raise ValidationError("La sucursal no existe o está inactiva.") from exc


def _lock_variant(variant_id):
    try:
        return ProductVariant.objects.select_for_update().select_related("product").get(
            pk=variant_id, is_active=True, product__is_active=True
        )
    except ProductVariant.DoesNotExist as exc:
        raise ValidationError("La variante no existe o está inactiva.") from exc


def _lock_stock(branch, variant):
    StockLevel.objects.get_or_create(branch=branch, variant=variant)
    return StockLevel.objects.select_for_update().get(branch=branch, variant=variant)


def _weighted_average(stock, quantity, unit_cost):
    unit_cost = Decimal(unit_cost).quantize(CENT, rounding=ROUND_HALF_UP)
    if quantity <= 0:
        raise ValidationError("La cantidad de entrada debe ser mayor que cero.")
    total_quantity = stock.quantity + quantity
    if total_quantity == 0:
        return unit_cost
    value = (Decimal(stock.quantity) * stock.average_cost + Decimal(quantity) * unit_cost)
    return (value / Decimal(total_quantity)).quantize(CENT, rounding=ROUND_HALF_UP)


def _create_operation(*, branch, user, kind, reason, idempotency_key, request_hash, transfer=None):
    return InventoryOperation.objects.create(
        branch=branch,
        transfer=transfer,
        user=user,
        kind=kind,
        reason=reason.strip(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )


def _apply_movement(*, operation, stock, delta, unit_cost):
    if delta == 0:
        raise ValidationError("El movimiento debe modificar las existencias.")
    new_quantity = stock.quantity + delta
    if new_quantity < 0:
        raise ValidationError(
            f"Stock insuficiente para {stock.variant.sku}: disponible {stock.quantity}."
        )
    if delta > 0:
        stock.average_cost = _weighted_average(stock, delta, unit_cost)
    stock.quantity = new_quantity
    stock.save(update_fields=["quantity", "average_cost", "updated_at"])
    return InventoryMovement.objects.create(
        operation=operation,
        branch=stock.branch,
        variant=stock.variant,
        quantity_delta=delta,
        balance_after=new_quantity,
        unit_cost=Decimal(unit_cost).quantize(CENT, rounding=ROUND_HALF_UP),
    )


def record_receipt(*, branch_id, variant_id, quantity, user, reason, idempotency_key, unit_cost=None):
    quantity = int(quantity)
    if quantity <= 0:
        raise ValidationError("La cantidad de entrada debe ser mayor que cero.")
    reason = reason.strip()
    if not reason:
        raise ValidationError("Indica el motivo de la entrada.")
    request_hash = _fingerprint({
        "kind": InventoryOperation.Kind.RECEIPT,
        "branch": branch_id,
        "variant": variant_id,
        "quantity": quantity,
        "reason": reason,
        "unit_cost": unit_cost,
    })
    with transaction.atomic():
        branch = _lock_branch(branch_id)
        variant = _lock_variant(variant_id)
        existing = InventoryOperation.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return _check_replay(existing, request_hash)
        stock = _lock_stock(branch, variant)
        effective_cost = Decimal(unit_cost) if unit_cost is not None else variant.effective_cost
        if effective_cost < 0:
            raise ValidationError("El costo no puede ser negativo.")
        operation = _create_operation(
            branch=branch, user=user, kind=InventoryOperation.Kind.RECEIPT,
            reason=reason, idempotency_key=idempotency_key, request_hash=request_hash,
        )
        _apply_movement(operation=operation, stock=stock, delta=quantity, unit_cost=effective_cost)
        return operation


def record_issue(*, branch_id, variant_id, quantity, user, reason, idempotency_key):
    quantity = int(quantity)
    if quantity <= 0:
        raise ValidationError("La cantidad de salida debe ser mayor que cero.")
    reason = reason.strip()
    if not reason:
        raise ValidationError("Indica el motivo de la salida.")
    request_hash = _fingerprint({
        "kind": InventoryOperation.Kind.ISSUE,
        "branch": branch_id,
        "variant": variant_id,
        "quantity": quantity,
        "reason": reason,
    })
    with transaction.atomic():
        branch = _lock_branch(branch_id)
        variant = _lock_variant(variant_id)
        existing = InventoryOperation.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return _check_replay(existing, request_hash)
        stock = _lock_stock(branch, variant)
        if stock.quantity < quantity:
            raise ValidationError(
                f"Stock insuficiente para {variant.sku}: disponible {stock.quantity}."
            )
        operation = _create_operation(
            branch=branch, user=user, kind=InventoryOperation.Kind.ISSUE,
            reason=reason, idempotency_key=idempotency_key, request_hash=request_hash,
        )
        _apply_movement(
            operation=operation, stock=stock, delta=-quantity, unit_cost=stock.average_cost
        )
        return operation


def adjust_stock_count(*, branch_id, variant_id, counted_quantity, user, reason, idempotency_key):
    counted_quantity = int(counted_quantity)
    if counted_quantity < 0:
        raise ValidationError("El conteo físico no puede ser negativo.")
    reason = reason.strip()
    if not reason:
        raise ValidationError("Indica el motivo del ajuste.")
    with transaction.atomic():
        branch = _lock_branch(branch_id)
        variant = _lock_variant(variant_id)
        request_hash = _fingerprint({
            "kind": "adjustment_count", "branch": branch_id,
            "variant": variant_id, "counted_quantity": counted_quantity,
            "reason": reason,
        })
        existing = InventoryOperation.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return _check_replay(existing, request_hash)
        stock = _lock_stock(branch, variant)
        delta = counted_quantity - stock.quantity
        if delta == 0:
            raise ValidationError("El conteo coincide con el stock registrado; no hay ajuste que guardar.")
        kind = (
            InventoryOperation.Kind.ADJUSTMENT_IN
            if delta > 0
            else InventoryOperation.Kind.ADJUSTMENT_OUT
        )
        operation = _create_operation(
            branch=branch, user=user, kind=kind, reason=reason,
            idempotency_key=idempotency_key, request_hash=request_hash,
        )
        unit_cost = stock.average_cost if delta < 0 else variant.effective_cost
        _apply_movement(operation=operation, stock=stock, delta=delta, unit_cost=unit_cost)
        return operation


def create_transfer(*, origin_id, destination_id, lines, user, notes, creation_key):
    normalized = sorted((int(row["variant_id"]), int(row["quantity"])) for row in lines)
    if not normalized or any(quantity <= 0 for _variant_id, quantity in normalized):
        raise ValidationError("Agrega al menos una variante con cantidad mayor que cero.")
    variant_ids = [variant_id for variant_id, _quantity in normalized]
    if len(set(variant_ids)) != len(variant_ids):
        raise ValidationError("No repitas variantes en una misma transferencia.")
    if origin_id == destination_id:
        raise ValidationError("La sucursal de origen y destino deben ser diferentes.")
    request_hash = _fingerprint({
        "origin": origin_id, "destination": destination_id,
        "lines": normalized, "notes": notes.strip(),
    })
    with transaction.atomic():
        branch_ids = sorted([int(origin_id), int(destination_id)])
        branches = {
            branch.pk: branch
            for branch in Branch.objects.select_for_update()
            .filter(pk__in=branch_ids, is_active=True).order_by("pk")
        }
        if len(branches) != 2:
            raise ValidationError("Ambas sucursales deben existir y estar activas.")
        existing = StockTransfer.objects.select_for_update().filter(creation_key=creation_key).first()
        if existing:
            if existing.created_by_id != user.pk or existing.request_hash != request_hash:
                raise ValidationError("La clave de creación ya se utilizó con otros datos.")
            return existing
        variants = {
            variant.pk: variant
            for variant in ProductVariant.objects.select_for_update().select_related("product")
            .filter(pk__in=variant_ids, is_active=True, product__is_active=True).order_by("pk")
        }
        if len(variants) != len(variant_ids):
            raise ValidationError("Una o más variantes no existen o están inactivas.")
        transfer = StockTransfer.objects.create(
            origin=branches[origin_id], destination=branches[destination_id],
            created_by=user, notes=notes.strip(), creation_key=creation_key,
            request_hash=request_hash,
        )
        StockTransferLine.objects.bulk_create([
            StockTransferLine(
                transfer=transfer, variant=variants[variant_id], quantity_requested=quantity
            )
            for variant_id, quantity in normalized
        ])
        StockTransferEvent.objects.create(
            transfer=transfer, user=user, from_status="",
            to_status=StockTransfer.Status.DRAFT, details="Transferencia preparada.",
        )
        return transfer


def change_transfer_status(*, transfer_id, user, action):
    with transaction.atomic():
        transfer = StockTransfer.objects.select_for_update().get(pk=transfer_id)
        if action == "submit":
            if transfer.status == StockTransfer.Status.PENDING:
                return transfer
            if transfer.status != StockTransfer.Status.DRAFT:
                raise ValidationError("Solo se puede enviar a despacho una transferencia en borrador.")
            new_status = StockTransfer.Status.PENDING
            details = "Transferencia lista para despacho."
        elif action == "cancel":
            if transfer.status == StockTransfer.Status.CANCELLED:
                return transfer
            if transfer.status not in {StockTransfer.Status.DRAFT, StockTransfer.Status.PENDING}:
                raise ValidationError("No se puede cancelar una transferencia ya despachada.")
            new_status = StockTransfer.Status.CANCELLED
            details = "Transferencia cancelada antes del despacho."
        else:
            raise ValidationError("Acción de transferencia no válida.")
        previous = transfer.status
        transfer.status = new_status
        transfer.save(update_fields=["status"])
        StockTransferEvent.objects.create(
            transfer=transfer, user=user, from_status=previous,
            to_status=new_status, details=details,
        )
        return transfer


def _locked_transfer(transfer_id):
    try:
        transfer = StockTransfer.objects.select_for_update().select_related(
            "origin", "destination"
        ).get(pk=transfer_id)
    except StockTransfer.DoesNotExist as exc:
        raise ValidationError("La transferencia no existe.") from exc
    branch_ids = sorted([transfer.origin_id, transfer.destination_id])
    branches = {
        branch.pk: branch
        for branch in Branch.objects.select_for_update()
        .filter(pk__in=branch_ids, is_active=True).order_by("pk")
    }
    if len(branches) != 2:
        raise ValidationError("Las sucursales deben permanecer activas para completar la transferencia.")
    lines = list(
        StockTransferLine.objects.select_for_update().select_related("variant__product")
        .filter(transfer=transfer).order_by("variant_id")
    )
    variants = {
        variant.pk: variant
        for variant in ProductVariant.objects.select_for_update().select_related("product")
        .filter(pk__in=[line.variant_id for line in lines], is_active=True, product__is_active=True)
        .order_by("pk")
    }
    if len(variants) != len(lines):
        raise ValidationError("Una o más variantes de la transferencia están inactivas.")
    return transfer, branches, lines, variants


def dispatch_transfer(*, transfer_id, user, idempotency_key):
    with transaction.atomic():
        transfer, branches, lines, variants = _locked_transfer(transfer_id)
        request_hash = _fingerprint({"kind": "transfer_dispatch", "transfer": transfer.pk})
        existing = InventoryOperation.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            if (
                existing.kind != InventoryOperation.Kind.TRANSFER_OUT
                or existing.transfer_id != transfer.pk
            ):
                raise ValidationError("La clave ya pertenece a otra operación.")
            _check_replay(existing, request_hash)
            return transfer
        if transfer.status != StockTransfer.Status.PENDING:
            raise ValidationError("Solo se pueden despachar transferencias pendientes.")
        if not lines:
            raise ValidationError("La transferencia no tiene productos.")
        origin = branches[transfer.origin_id]
        stocks = {line.pk: _lock_stock(origin, variants[line.variant_id]) for line in lines}
        for line in lines:
            stock = stocks[line.pk]
            if stock.quantity < line.quantity_requested:
                raise ValidationError(
                    f"Stock insuficiente para {line.variant.sku}: disponible {stock.quantity}."
                )
        operation = _create_operation(
            branch=origin, transfer=transfer, user=user,
            kind=InventoryOperation.Kind.TRANSFER_OUT,
            reason=f"Despacho de transferencia TR-{transfer.pk}",
            idempotency_key=idempotency_key, request_hash=request_hash,
        )
        for line in lines:
            stock = stocks[line.pk]
            line.quantity_sent = line.quantity_requested
            line.unit_cost = stock.average_cost
            line.save(update_fields=["quantity_sent", "unit_cost"])
            _apply_movement(
                operation=operation, stock=stock,
                delta=-line.quantity_sent, unit_cost=stock.average_cost,
            )
        previous = transfer.status
        transfer.status = StockTransfer.Status.IN_TRANSIT
        transfer.dispatched_by = user
        transfer.dispatched_at = timezone.now()
        transfer.save(update_fields=["status", "dispatched_by", "dispatched_at"])
        StockTransferEvent.objects.create(
            transfer=transfer, user=user, from_status=previous,
            to_status=transfer.status, details="Mercadería descontada del origen y enviada.",
        )
        return transfer


def receive_transfer(*, transfer_id, user, quantities, finalize, notes, idempotency_key):
    quantities = {int(line_id): int(quantity) for line_id, quantity in quantities.items()}
    if any(quantity < 0 for quantity in quantities.values()):
        raise ValidationError("La cantidad recibida no puede ser negativa.")
    with transaction.atomic():
        transfer, branches, lines, variants = _locked_transfer(transfer_id)
        request_hash = _fingerprint({
            "kind": "transfer_receive", "transfer": transfer.pk,
            "quantities": sorted(quantities.items()), "finalize": bool(finalize),
            "notes": notes.strip(),
        })
        existing = InventoryOperation.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            if (
                existing.kind != InventoryOperation.Kind.TRANSFER_IN
                or existing.transfer_id != transfer.pk
            ):
                raise ValidationError("La clave ya pertenece a otra operación.")
            _check_replay(existing, request_hash)
            return transfer
        if transfer.status not in {StockTransfer.Status.IN_TRANSIT, StockTransfer.Status.PARTIAL}:
            raise ValidationError("La transferencia no está disponible para recepción.")
        line_map = {line.pk: line for line in lines}
        if set(quantities) - set(line_map):
            raise ValidationError("La recepción contiene líneas que no pertenecen a esta transferencia.")
        if not finalize and sum(quantities.values()) == 0:
            raise ValidationError("Indica al menos una cantidad recibida o confirma la recepción final.")
        for line_id, quantity in quantities.items():
            if quantity > line_map[line_id].quantity_sent - line_map[line_id].quantity_received:
                raise ValidationError("La cantidad recibida supera las unidades aún pendientes de recepción.")
        final_missing = sum(
            line.quantity_sent - line.quantity_received - quantities.get(line.pk, 0)
            for line in lines
        )
        if finalize and final_missing and not notes.strip():
            raise ValidationError("Describe la diferencia antes de cerrar la recepción.")

        destination = branches[transfer.destination_id]
        stocks = {
            line_id: _lock_stock(destination, variants[line_map[line_id].variant_id])
            for line_id, quantity in quantities.items() if quantity > 0
        }
        operation = _create_operation(
            branch=destination, transfer=transfer, user=user,
            kind=InventoryOperation.Kind.TRANSFER_IN,
            reason=notes.strip() or f"Recepción de transferencia TR-{transfer.pk}",
            idempotency_key=idempotency_key, request_hash=request_hash,
        )
        for line in lines:
            line_id = line.pk
            quantity = quantities.get(line_id, 0)
            line = line_map[line_id]
            if quantity:
                stock = stocks[line_id]
                unit_cost = line.unit_cost if line.unit_cost is not None else variants[line.variant_id].effective_cost
                line.quantity_received += quantity
                _apply_movement(
                    operation=operation, stock=stock, delta=quantity, unit_cost=unit_cost,
                )
            if finalize:
                line.quantity_missing = line.quantity_sent - line.quantity_received
            line.save(update_fields=["quantity_received", "quantity_missing"])
        previous = transfer.status
        pending = any(
            line.quantity_received + line.quantity_missing < line.quantity_sent
            for line in lines
        )
        if finalize or not pending:
            transfer.status = StockTransfer.Status.RECEIVED
            transfer.received_by = user
            transfer.received_at = timezone.now()
            transfer.save(update_fields=["status", "received_by", "received_at"])
        else:
            transfer.status = StockTransfer.Status.PARTIAL
            transfer.received_by = user
            transfer.save(update_fields=["status", "received_by"])
        details = notes.strip() or "Recepción registrada."
        if transfer.quantity_missing:
            details = f"{details} Diferencia faltante: {transfer.quantity_missing} unidad(es)."
        StockTransferEvent.objects.create(
            transfer=transfer, user=user, from_status=previous,
            to_status=transfer.status, details=details[:255],
        )
        return transfer
