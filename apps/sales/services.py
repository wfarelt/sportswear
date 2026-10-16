import hashlib
import json
import uuid
from collections import defaultdict
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.branches.models import Branch
from apps.customers.models import Customer
from apps.inventory.models import InventoryMovement, InventoryOperation, StockLevel
from apps.products.models import ProductVariant

from .models import (
    PaymentMethod,
    Sale,
    SaleEvent,
    SaleLine,
    SalePayment,
    SaleReturn,
    SaleReturnLine,
)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _fingerprint(data):
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _authorized_branch(user, branch_id):
    if user.has_global_branch_access:
        query = Branch.objects.filter(is_active=True)
    else:
        query = Branch.objects.filter(is_active=True, authorized_users=user)
    try:
        return query.get(pk=branch_id)
    except Branch.DoesNotExist as exc:
        raise PermissionDenied("No tienes acceso a la sucursal seleccionada.") from exc


def _effective_sale_price(variant):
    base_price = (
        variant.sale_price
        if variant.sale_price is not None
        else variant.product.sale_price
    )
    if variant.promotional_price is not None:
        if variant.promotional_price > base_price:
            raise ValidationError(f"El precio promocional de {variant.sku} supera su precio de venta.")
        return variant.promotional_price
    if variant.product.promotional_price is not None:
        if variant.product.promotional_price > base_price:
            raise ValidationError(f"El precio promocional de {variant.sku} supera su precio de venta.")
        return variant.product.promotional_price
    return base_price


def _allocate_discount(gross_lines, discount):
    """Allocate a sale discount in cents using deterministic largest remainders."""
    gross_total = sum(gross_lines, ZERO)
    if discount == ZERO or gross_total == ZERO:
        return [ZERO for _ in gross_lines]
    allocations = []
    remainders = []
    allocated = ZERO
    for index, gross in enumerate(gross_lines):
        exact = discount * gross / gross_total
        rounded_down = exact.quantize(CENT, rounding=ROUND_DOWN)
        allocations.append(rounded_down)
        remainders.append((exact - rounded_down, index))
        allocated += rounded_down
    cents_left = int((discount - allocated) / CENT)
    for _remainder, index in sorted(remainders, reverse=True)[:cents_left]:
        allocations[index] += CENT
    return allocations


def _normalize_sale_lines(lines):
    normalized = []
    seen = set()
    for item in lines:
        variant_id = int(item["variant_id"])
        quantity = int(item["quantity"])
        if quantity <= 0:
            raise ValidationError("Las cantidades de venta deben ser mayores que cero.")
        if variant_id in seen:
            raise ValidationError("No repitas una variante en el carrito.")
        seen.add(variant_id)
        normalized.append((variant_id, quantity))
    if not normalized:
        raise ValidationError("Agrega al menos un producto a la venta.")
    return sorted(normalized)


def _normalize_payments(payments):
    normalized = []
    seen_methods = set()
    for payment in payments:
        if not payment:
            continue
        method_id = int(payment["method_id"])
        amount = _money(payment["amount"])
        if amount <= ZERO:
            raise ValidationError("Cada pago debe tener un importe mayor que cero.")
        if method_id in seen_methods:
            raise ValidationError("Combina los importes del mismo medio de pago en una sola línea.")
        seen_methods.add(method_id)
        normalized.append({
            "method_id": method_id,
            "amount": amount,
            "reference": str(payment.get("reference", "")).strip()[:100],
        })
    if not normalized:
        raise ValidationError("Registra al menos un medio de pago.")
    return sorted(normalized, key=lambda row: row["method_id"])


def _hash_sale(branch_id, customer_id, normalized_lines, discount, normalized_payments, seller_id, cash_session_id):
    return _fingerprint({
        "branch": int(branch_id),
        "customer": int(customer_id) if customer_id else None,
        "lines": normalized_lines,
        "discount": str(discount),
        "payments": normalized_payments,
        "seller": int(seller_id),
        "cash_session": int(cash_session_id) if cash_session_id else None,
    })


def _create_inventory_operation(*, branch, user, kind, reason, source_reference):
    return InventoryOperation.objects.create(
        branch=branch,
        kind=kind,
        user=user,
        reason=reason,
        idempotency_key=uuid.uuid4(),
        request_hash=_fingerprint({"kind": kind, "source": source_reference}),
        source_reference=source_reference,
    )


def confirm_sale(*, branch_id, customer_id, lines, discount_amount, payments, user, idempotency_key, cash_session_id=None):
    normalized_lines = _normalize_sale_lines(lines)
    normalized_payments = _normalize_payments(payments)
    discount = _money(discount_amount)
    if discount < ZERO:
        raise ValidationError("El descuento no puede ser negativo.")
    if discount and not (user.is_superuser or user.has_perm("sales.apply_discount")):
        raise PermissionDenied("No tienes permiso para aplicar descuentos.")
    request_hash = _hash_sale(
        branch_id, customer_id, normalized_lines, discount, normalized_payments,
        user.pk, cash_session_id,
    )

    with transaction.atomic():
        existing = Sale.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.request_hash != request_hash:
                raise ValidationError("La clave de venta ya se utilizó con datos diferentes.")
            _authorized_branch(user, existing.branch_id)
            return existing

        branch = _authorized_branch(user, branch_id)
        customer = None
        if customer_id:
            try:
                customer = Customer.objects.get(pk=customer_id, is_active=True)
            except Customer.DoesNotExist as exc:
                raise ValidationError("El cliente no existe o está inactivo.") from exc

        variant_ids = [variant_id for variant_id, _quantity in normalized_lines]
        variants = {
            variant.pk: variant
            for variant in ProductVariant.objects.select_for_update().select_related(
                "product", "size", "color"
            ).filter(pk__in=variant_ids, is_active=True, product__is_active=True).order_by("pk")
        }
        if len(variants) != len(variant_ids):
            raise ValidationError("Uno o más productos ya no están disponibles para la venta.")

        methods = {
            method.pk: method
            for method in PaymentMethod.objects.select_for_update()
            .filter(pk__in=[row["method_id"] for row in normalized_payments], is_active=True)
            .order_by("pk")
        }
        if len(methods) != len(normalized_payments):
            raise ValidationError("Uno o más medios de pago están inactivos o no existen.")

        stocks = {}
        for variant_id, quantity in normalized_lines:
            stock = StockLevel.objects.select_for_update().filter(
                branch=branch, variant_id=variant_id
            ).first()
            available = stock.quantity if stock else 0
            if available < quantity:
                raise ValidationError(
                    f"Stock insuficiente para {variants[variant_id].sku}: disponible {available}."
                )
            stocks[variant_id] = stock

        gross_lines = [
            _money(_effective_sale_price(variants[variant_id]) * quantity)
            for variant_id, quantity in normalized_lines
        ]
        subtotal = sum(gross_lines, ZERO)
        if discount > subtotal:
            raise ValidationError("El descuento no puede superar el subtotal de la venta.")
        discount_lines = _allocate_discount(gross_lines, discount)

        prepared_lines = []
        tax_total = ZERO
        total = ZERO
        for (variant_id, quantity), gross, line_discount in zip(
            normalized_lines, gross_lines, discount_lines
        ):
            variant = variants[variant_id]
            unit_price = _money(_effective_sale_price(variant))
            rate = variant.product.tax_rate
            per_unit_discount = line_discount / quantity
            taxable_per_unit = unit_price - per_unit_discount
            tax_per_unit = _money(taxable_per_unit * rate / Decimal("100"))
            line_tax = tax_per_unit * quantity
            line_total = (taxable_per_unit + tax_per_unit) * quantity
            tax_total += line_tax
            total += line_total
            prepared_lines.append({
                "variant": variant,
                "quantity": quantity,
                "unit_price": unit_price,
                "unit_cost": _money(variant.effective_cost),
                "discount_amount": line_discount,
                "tax_rate": rate,
                "tax_amount": line_tax,
                "line_total": line_total,
                "gross": gross,
            })

        total = _money(total)
        if total <= ZERO:
            raise ValidationError("El total de la venta debe ser mayor que cero.")
        payments_total = sum((row["amount"] for row in normalized_payments), ZERO)
        if payments_total != total:
            raise ValidationError(f"Los pagos (Bs {payments_total}) deben coincidir con el total (Bs {total}).")

        sale = Sale.objects.create(
            branch=branch,
            customer=customer,
            seller=user,
            subtotal=subtotal,
            discount_amount=discount,
            tax_amount=tax_total,
            total=total,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        sale.number = f"V-{sale.pk:08d}"
        sale.save(update_fields=["number"])

        sale_lines = []
        for prepared in prepared_lines:
            variant = prepared["variant"]
            sale_lines.append(SaleLine(
                sale=sale,
                variant=variant,
                product_name=variant.product.name,
                sku=variant.sku,
                barcode=variant.barcode or "",
                size_name=variant.size.name if variant.size_id else "",
                color_name=variant.color.name if variant.color_id else "",
                quantity=prepared["quantity"],
                unit_cost=prepared["unit_cost"],
                unit_price=prepared["unit_price"],
                discount_amount=prepared["discount_amount"],
                tax_rate=prepared["tax_rate"],
                tax_amount=prepared["tax_amount"],
                line_total=prepared["line_total"],
            ))
        SaleLine.objects.bulk_create(sale_lines)
        SalePayment.objects.bulk_create([
            SalePayment(sale=sale, method=methods[row["method_id"]], amount=row["amount"], reference=row["reference"])
            for row in normalized_payments
        ])
        sale_payments = list(sale.payments.select_related("method").order_by("pk"))
        from apps.cash.services import record_sale_collections

        record_sale_collections(
            session_id=cash_session_id, sale=sale, payments=sale_payments, user=user
        )

        operation = _create_inventory_operation(
            branch=branch,
            user=user,
            kind=InventoryOperation.Kind.SALE_OUT,
            reason=f"Salida por venta {sale.number}",
            source_reference=sale.number,
        )
        for prepared in prepared_lines:
            variant = prepared["variant"]
            stock = stocks[variant.pk]
            stock.quantity -= prepared["quantity"]
            stock.save(update_fields=["quantity", "updated_at"])
            InventoryMovement.objects.create(
                operation=operation,
                branch=branch,
                variant=variant,
                quantity_delta=-prepared["quantity"],
                balance_after=stock.quantity,
                unit_cost=stock.average_cost,
            )
        SaleEvent.objects.create(
            sale=sale, user=user, event_type="confirmed",
            details=f"Venta confirmada por Bs {total}.",
        )
        return sale


def _lock_sale(sale_id):
    try:
        return Sale.objects.select_for_update().get(pk=sale_id)
    except Sale.DoesNotExist as exc:
        raise ValidationError("La venta no existe.") from exc


def _normalize_return_lines(lines):
    normalized = []
    seen = set()
    for item in lines:
        line_id = int(item["sale_line_id"])
        quantity = int(item["quantity"])
        if quantity <= 0:
            continue
        if line_id in seen:
            raise ValidationError("No repitas una línea de venta en una devolución.")
        seen.add(line_id)
        normalized.append((line_id, quantity))
    if not normalized:
        raise ValidationError("Indica al menos una unidad para devolver.")
    return sorted(normalized)


def process_return(*, sale_id, lines, reason, refund_method_id, user, idempotency_key, cash_session_id=None):
    if not (user.is_superuser or user.has_perm("sales.process_salereturn")):
        raise PermissionDenied("No tienes permiso para registrar devoluciones.")
    normalized = _normalize_return_lines(lines)
    reason = reason.strip()
    if not reason:
        raise ValidationError("Indica el motivo de la devolución.")
    request_hash = _fingerprint({
        "sale": sale_id, "lines": normalized, "reason": reason,
        "refund_method": refund_method_id,
    })
    with transaction.atomic():
        existing = SaleReturn.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
        if existing:
            if existing.request_hash != request_hash:
                raise ValidationError("La clave de devolución ya se utilizó con datos diferentes.")
            _authorized_branch(user, existing.sale.branch_id)
            return existing
        sale = _lock_sale(sale_id)
        if sale.status not in {Sale.Status.CONFIRMED, Sale.Status.PARTIALLY_RETURNED}:
            raise ValidationError("Esta venta no admite nuevas devoluciones.")
        try:
            refund_method = PaymentMethod.objects.select_for_update().get(
                pk=refund_method_id, is_active=True
            )
        except PaymentMethod.DoesNotExist as exc:
            raise ValidationError("El medio de reembolso no está activo.") from exc
        line_map = {
            line.pk: line
            for line in SaleLine.objects.select_for_update().filter(sale=sale).order_by("variant_id")
        }
        if set(line_id for line_id, _quantity in normalized) - set(line_map):
            raise ValidationError("Una línea seleccionada no pertenece a esta venta.")
        branch = _authorized_branch(user, sale.branch_id)
        line_quantities = defaultdict(int)
        for line_id, quantity in normalized:
            line = line_map[line_id]
            if quantity > line.remaining_quantity:
                raise ValidationError(f"La línea {line.sku} solo tiene {line.remaining_quantity} unidad(es) pendientes de devolución.")
            line_quantities[line_id] = quantity

        refund_rows = []
        refund_total = ZERO
        variants = {
            variant.pk: variant
            for variant in ProductVariant.objects.select_for_update().filter(
                pk__in=[line_map[line_id].variant_id for line_id, _ in normalized]
            ).order_by("pk")
        }
        stocks = {}
        for line_id, quantity in normalized:
            line = line_map[line_id]
            stock, _created = StockLevel.objects.select_for_update().get_or_create(
                branch=branch,
                variant=variants[line.variant_id],
                defaults={"quantity": 0, "average_cost": line.unit_cost},
            )
            stocks[line_id] = stock
            previous_refund = line.return_lines.aggregate(total=Sum("refund_amount"))["total"] or ZERO
            cumulative_quantity = line.quantity_returned + quantity
            target_refund = _money(line.line_total * cumulative_quantity / line.quantity)
            line_refund = target_refund - previous_refund
            refund_total += line_refund
            refund_rows.append((line, quantity, line_refund))

        sale_return = SaleReturn.objects.create(
            sale=sale,
            refund_method=refund_method,
            reason=reason,
            refund_amount=refund_total,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            created_by=user,
        )
        from apps.cash.services import record_sale_refund

        record_sale_refund(
            sale_return=sale_return,
            payment_method=refund_method,
            amount=refund_total,
            user=user,
            session_id=cash_session_id,
        )
        for line, quantity, line_refund in refund_rows:
            line.quantity_returned += quantity
            line.save(update_fields=["quantity_returned"])
            SaleReturnLine.objects.create(
                sale_return=sale_return, sale_line=line,
                quantity=quantity, refund_amount=line_refund,
            )

        operation = _create_inventory_operation(
            branch=branch,
            user=user,
            kind=InventoryOperation.Kind.SALE_RETURN,
            reason=f"Devolución de venta {sale.number}: {reason}",
            source_reference=sale.number,
        )
        for line, quantity, _line_refund in refund_rows:
            stock = stocks[line.pk]
            stock.average_cost = _weighted_average_cost(stock, quantity, line.unit_cost)
            stock.quantity += quantity
            stock.save(update_fields=["quantity", "average_cost", "updated_at"])
            InventoryMovement.objects.create(
                operation=operation, branch=branch, variant=variants[line.variant_id],
                quantity_delta=quantity, balance_after=stock.quantity,
                unit_cost=line.unit_cost,
            )
        if all(line.remaining_quantity == 0 for line in line_map.values()):
            sale.status = Sale.Status.RETURNED
        else:
            sale.status = Sale.Status.PARTIALLY_RETURNED
        sale.save(update_fields=["status"])
        SaleEvent.objects.create(
            sale=sale, user=user, event_type="returned",
            details=f"Devolución por Bs {refund_total}: {reason}"[:255],
        )
        return sale_return


def _weighted_average_cost(stock, quantity, unit_cost):
    if quantity <= 0:
        return stock.average_cost
    total_quantity = stock.quantity + quantity
    if total_quantity <= 0:
        return Decimal(unit_cost).quantize(CENT, rounding=ROUND_HALF_UP)
    value = Decimal(stock.quantity) * stock.average_cost + Decimal(quantity) * Decimal(unit_cost)
    return (value / Decimal(total_quantity)).quantize(CENT, rounding=ROUND_HALF_UP)


def cancel_sale(*, sale_id, user, reason, cash_session_id=None):
    if not (user.is_superuser or user.has_perm("sales.cancel_sale")):
        raise PermissionDenied("No tienes permiso para anular ventas.")
    reason = reason.strip()
    if not reason:
        raise ValidationError("Indica el motivo de la anulación.")
    with transaction.atomic():
        sale = _lock_sale(sale_id)
        branch = _authorized_branch(user, sale.branch_id)
        if sale.status == Sale.Status.CANCELLED:
            return sale
        if sale.status != Sale.Status.CONFIRMED:
            raise ValidationError("Solo se puede anular una venta confirmada sin devoluciones previas.")
        lines = list(SaleLine.objects.select_for_update().filter(sale=sale).order_by("variant_id"))
        variants = {
            variant.pk: variant
            for variant in ProductVariant.objects.select_for_update().filter(
                pk__in=[line.variant_id for line in lines]
            ).order_by("pk")
        }
        stocks = {}
        for line in lines:
            stock, _created = StockLevel.objects.select_for_update().get_or_create(
                branch=branch, variant=variants[line.variant_id],
                defaults={"average_cost": line.unit_cost},
            )
            stocks[line.pk] = stock
        operation = _create_inventory_operation(
            branch=branch,
            user=user,
            kind=InventoryOperation.Kind.SALE_CANCEL,
            reason=f"Anulación de venta {sale.number}: {reason}",
            source_reference=sale.number,
        )
        sale_return = SaleReturn.objects.create(
            sale=sale, refund_method=None, refund_amount=sale.total, reason=f"Anulación: {reason}",
            created_by=user, request_hash=_fingerprint({"cancel": sale.pk, "reason": reason}),
        )
        from apps.cash.services import record_sale_refund

        for payment in sale.payments.select_related("method").order_by("pk"):
            record_sale_refund(
                sale_return=sale_return,
                payment_method=payment.method,
                amount=payment.amount,
                user=user,
                session_id=cash_session_id,
            )
        for line in lines:
            quantity = line.quantity
            SaleReturnLine.objects.create(
                sale_return=sale_return, sale_line=line, quantity=quantity,
                refund_amount=line.line_total,
            )
            line.quantity_returned = quantity
            line.save(update_fields=["quantity_returned"])
            stock = stocks[line.pk]
            stock.average_cost = _weighted_average_cost(stock, quantity, line.unit_cost)
            stock.quantity += quantity
            stock.save(update_fields=["quantity", "average_cost", "updated_at"])
            InventoryMovement.objects.create(
                operation=operation, branch=branch, variant=variants[line.variant_id],
                quantity_delta=quantity, balance_after=stock.quantity, unit_cost=line.unit_cost,
            )
        sale.status = Sale.Status.CANCELLED
        sale.cancelled_at = timezone.now()
        sale.cancelled_by = user
        sale.save(update_fields=["status", "cancelled_at", "cancelled_by"])
        SaleEvent.objects.create(
            sale=sale, user=user, event_type="cancelled", details=f"Venta anulada: {reason}"[:255]
        )
        return sale
