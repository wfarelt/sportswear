import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class StockLevel(models.Model):
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="stock_levels",
        verbose_name="sucursal",
    )
    variant = models.ForeignKey(
        "products.ProductVariant", on_delete=models.PROTECT, related_name="stock_levels",
        verbose_name="variante",
    )
    quantity = models.PositiveIntegerField("cantidad disponible", default=0)
    minimum_quantity = models.PositiveIntegerField("stock mínimo", default=0)
    average_cost = models.DecimalField(
        "costo promedio ponderado", max_digits=12, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    updated_at = models.DateTimeField("actualizado el", auto_now=True)

    class Meta:
        ordering = ["variant__product__name", "variant__sku"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "variant"], name="unique_branch_variant_stock"),
            models.CheckConstraint(condition=Q(quantity__gte=0), name="stock_quantity_nonnegative"),
            models.CheckConstraint(condition=Q(minimum_quantity__gte=0), name="stock_minimum_nonnegative"),
            models.CheckConstraint(condition=Q(average_cost__gte=0), name="stock_average_cost_nonnegative"),
        ]
        indexes = [
            models.Index(fields=["branch", "quantity"], name="stock_branch_quantity_idx"),
            models.Index(fields=["variant", "branch"], name="stock_variant_branch_idx"),
        ]
        verbose_name = "existencia"
        verbose_name_plural = "existencias"

    @property
    def inventory_value(self):
        return self.quantity * self.average_cost

    @property
    def is_low_stock(self):
        return self.minimum_quantity > 0 and self.quantity <= self.minimum_quantity

    def __str__(self):
        return f"{self.branch}: {self.variant} ({self.quantity})"


class StockTransfer(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        PENDING = "pending", "Pendiente de envío"
        IN_TRANSIT = "in_transit", "En tránsito"
        PARTIAL = "partial", "Recibida parcialmente"
        RECEIVED = "received", "Recibida"
        CANCELLED = "cancelled", "Cancelada"

    origin = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="outgoing_transfers",
        verbose_name="sucursal de origen",
    )
    destination = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="incoming_transfers",
        verbose_name="sucursal de destino",
    )
    status = models.CharField("estado", max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField("observaciones", blank=True)
    creation_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_hash = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="prepared_transfers",
        verbose_name="preparada por",
    )
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dispatched_transfers",
        null=True, blank=True, verbose_name="despachada por",
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="received_transfers",
        null=True, blank=True, verbose_name="recibida por",
    )
    created_at = models.DateTimeField("creada el", auto_now_add=True)
    dispatched_at = models.DateTimeField("despachada el", null=True, blank=True)
    received_at = models.DateTimeField("recibida el", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["origin", "status"], name="transfer_origin_status_idx"),
            models.Index(fields=["destination", "status"], name="transfer_dest_status_idx"),
        ]
        verbose_name = "transferencia de mercadería"
        verbose_name_plural = "transferencias de mercadería"

    @property
    def quantity_requested(self):
        return sum(line.quantity_requested for line in self.lines.all())

    @property
    def quantity_sent(self):
        return sum(line.quantity_sent for line in self.lines.all())

    @property
    def quantity_received(self):
        return sum(line.quantity_received for line in self.lines.all())

    @property
    def quantity_missing(self):
        return sum(line.quantity_missing for line in self.lines.all())

    def __str__(self):
        return f"TR-{self.pk or 'nuevo'}: {self.origin} → {self.destination}"


class StockTransferLine(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.PROTECT, related_name="lines")
    variant = models.ForeignKey(
        "products.ProductVariant", on_delete=models.PROTECT, related_name="transfer_lines",
        verbose_name="variante",
    )
    quantity_requested = models.PositiveIntegerField("cantidad solicitada", validators=[MinValueValidator(1)])
    quantity_sent = models.PositiveIntegerField("cantidad enviada", default=0)
    quantity_received = models.PositiveIntegerField("cantidad recibida", default=0)
    quantity_missing = models.PositiveIntegerField("diferencia faltante", default=0)
    unit_cost = models.DecimalField(
        "costo unitario al despacho", max_digits=12, decimal_places=2,
        null=True, blank=True, validators=[MinValueValidator(0)],
    )

    class Meta:
        ordering = ["variant__product__name", "variant__sku"]
        constraints = [
            models.UniqueConstraint(fields=["transfer", "variant"], name="unique_transfer_variant"),
            models.CheckConstraint(condition=Q(quantity_sent__lte=models.F("quantity_requested")), name="transfer_sent_lte_requested"),
            models.CheckConstraint(condition=Q(quantity_received__lte=models.F("quantity_sent")), name="transfer_received_lte_sent"),
            models.CheckConstraint(condition=Q(quantity_missing__lte=models.F("quantity_sent") - models.F("quantity_received")), name="transfer_missing_lte_pending"),
        ]
        verbose_name = "línea de transferencia"
        verbose_name_plural = "líneas de transferencia"

    @property
    def remaining_to_receive(self):
        return self.quantity_sent - self.quantity_received - self.quantity_missing

    def __str__(self):
        return f"{self.variant} × {self.quantity_requested}"


class InventoryOperation(models.Model):
    class Kind(models.TextChoices):
        RECEIPT = "receipt", "Entrada de mercadería"
        ISSUE = "issue", "Salida autorizada"
        ADJUSTMENT_IN = "adjustment_in", "Ajuste positivo"
        ADJUSTMENT_OUT = "adjustment_out", "Ajuste negativo"
        TRANSFER_OUT = "transfer_out", "Despacho entre sucursales"
        TRANSFER_IN = "transfer_in", "Recepción entre sucursales"
        SALE_OUT = "sale_out", "Salida por venta"
        SALE_RETURN = "sale_return", "Devolución de cliente"
        SALE_CANCEL = "sale_cancel", "Reversión por anulación de venta"

    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="inventory_operations",
        verbose_name="sucursal",
    )
    transfer = models.ForeignKey(
        StockTransfer, on_delete=models.PROTECT, related_name="inventory_operations",
        null=True, blank=True, verbose_name="transferencia relacionada",
    )
    source_reference = models.CharField("referencia de origen", max_length=64, blank=True)
    kind = models.CharField("tipo de operación", max_length=24, choices=Kind.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inventory_operations",
        verbose_name="usuario responsable",
    )
    reason = models.CharField("motivo", max_length=255)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField("registrada el", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["branch", "kind", "created_at"], name="operation_branch_kind_idx")]
        verbose_name = "operación de inventario"
        verbose_name_plural = "operaciones de inventario"

    def __str__(self):
        return f"INV-{self.pk or 'nuevo'} · {self.get_kind_display()} · {self.branch}"


class InventoryMovement(models.Model):
    operation = models.ForeignKey(InventoryOperation, on_delete=models.PROTECT, related_name="movements")
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="inventory_movements",
        verbose_name="sucursal",
    )
    variant = models.ForeignKey(
        "products.ProductVariant", on_delete=models.PROTECT, related_name="inventory_movements",
        verbose_name="variante",
    )
    quantity_delta = models.IntegerField("variación de existencias")
    balance_after = models.PositiveIntegerField("saldo posterior")
    unit_cost = models.DecimalField(
        "costo unitario aplicado", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["operation", "branch", "variant"], name="unique_operation_stock_movement"),
            models.CheckConstraint(condition=~Q(quantity_delta=0), name="movement_quantity_nonzero"),
            models.CheckConstraint(condition=Q(balance_after__gte=0), name="movement_balance_nonnegative"),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="movement_cost_nonnegative"),
        ]
        indexes = [
            models.Index(fields=["branch", "created_at"], name="movement_branch_date_idx"),
            models.Index(fields=["variant", "created_at"], name="movement_variant_date_idx"),
        ]
        verbose_name = "movimiento de inventario"
        verbose_name_plural = "movimientos de inventario"

    def __str__(self):
        sign = "+" if self.quantity_delta > 0 else ""
        return f"{self.variant}: {sign}{self.quantity_delta} → {self.balance_after}"


class StockTransferEvent(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.PROTECT, related_name="events")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_transfer_events",
        verbose_name="usuario",
    )
    from_status = models.CharField("estado anterior", max_length=20, blank=True)
    to_status = models.CharField("estado nuevo", max_length=20, choices=StockTransfer.Status.choices)
    details = models.CharField("detalle", max_length=255, blank=True)
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = "evento de transferencia"
        verbose_name_plural = "historial de transferencias"

    def __str__(self):
        return f"{self.transfer}: {self.get_to_status_display()}"
