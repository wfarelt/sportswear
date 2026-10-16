import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class PaymentMethod(models.Model):
    code = models.SlugField("código", max_length=24, unique=True)
    name = models.CharField("nombre", max_length=60, unique=True)
    affects_cash = models.BooleanField("afecta efectivo físico", default=False)
    is_active = models.BooleanField("activo", default=True)
    sort_order = models.PositiveSmallIntegerField("orden", default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "medio de pago"
        verbose_name_plural = "medios de pago"

    def __str__(self):
        return self.name


class Sale(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmada"
        PARTIALLY_RETURNED = "partially_returned", "Devuelta parcialmente"
        RETURNED = "returned", "Devuelta"
        CANCELLED = "cancelled", "Anulada"

    number = models.CharField("número de venta", max_length=24, unique=True, blank=True)
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="sales", verbose_name="sucursal"
    )
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.PROTECT, related_name="sales",
        null=True, blank=True, verbose_name="cliente",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_made",
        verbose_name="vendedor",
    )
    status = models.CharField("estado", max_length=24, choices=Status.choices, default=Status.CONFIRMED)
    subtotal = models.DecimalField("subtotal", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    discount_amount = models.DecimalField("descuento", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    tax_amount = models.DecimalField("impuestos", max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    total = models.DecimalField("total", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField("registrada el", auto_now_add=True)
    cancelled_at = models.DateTimeField("anulada el", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_cancelled",
        null=True, blank=True, verbose_name="anulada por",
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["branch", "created_at"], name="sale_branch_date_idx"),
            models.Index(fields=["seller", "created_at"], name="sale_seller_date_idx"),
            models.Index(fields=["status", "created_at"], name="sale_status_date_idx"),
        ]
        permissions = [
            ("apply_discount", "Puede aplicar descuentos en ventas"),
            ("cancel_sale", "Puede anular ventas confirmadas"),
            ("view_all_sales", "Puede consultar ventas de otros vendedores"),
        ]
        verbose_name = "venta"
        verbose_name_plural = "ventas"

    @property
    def paid_total(self):
        return sum((payment.amount for payment in self.payments.all()), start=0)

    @property
    def refunded_total(self):
        return sum((sale_return.refund_amount for sale_return in self.returns.all()), start=0)

    def __str__(self):
        return self.number or f"Venta pendiente ({self.pk or 'nueva'})"


class SaleLine(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="lines")
    variant = models.ForeignKey(
        "products.ProductVariant", on_delete=models.PROTECT, related_name="sale_lines",
        verbose_name="variante",
    )
    product_name = models.CharField("producto", max_length=180)
    sku = models.CharField("SKU", max_length=64)
    barcode = models.CharField("código de barras", max_length=64, blank=True)
    size_name = models.CharField("talla", max_length=100, blank=True)
    color_name = models.CharField("color", max_length=100, blank=True)
    quantity = models.PositiveIntegerField("cantidad", validators=[MinValueValidator(1)])
    quantity_returned = models.PositiveIntegerField("cantidad devuelta", default=0)
    unit_cost = models.DecimalField("costo unitario histórico", max_digits=12, decimal_places=2)
    unit_price = models.DecimalField("precio unitario", max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField("descuento de línea", max_digits=12, decimal_places=2, default=0)
    tax_rate = models.DecimalField("impuesto (%)", max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField("impuesto de línea", max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField("total de línea", max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["sale", "variant"], name="unique_sale_variant_line"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="sale_line_quantity_positive"),
            models.CheckConstraint(condition=Q(quantity_returned__lte=models.F("quantity")), name="sale_line_returned_lte_quantity"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="sale_line_price_nonnegative"),
            models.CheckConstraint(condition=Q(unit_cost__gte=0), name="sale_line_cost_nonnegative"),
        ]
        verbose_name = "detalle de venta"
        verbose_name_plural = "detalles de venta"

    @property
    def remaining_quantity(self):
        return self.quantity - self.quantity_returned

    @property
    def unit_refund_amount(self):
        gross = self.unit_price - (self.discount_amount / self.quantity)
        return gross + (gross * self.tax_rate / 100)

    def __str__(self):
        return f"{self.sku} × {self.quantity}"


class SalePayment(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="payments")
    method = models.ForeignKey(
        PaymentMethod, on_delete=models.PROTECT, related_name="sale_payments",
        verbose_name="medio de pago",
    )
    amount = models.DecimalField("importe", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    reference = models.CharField("referencia", max_length=100, blank=True)
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="sale_payment_amount_positive")]
        verbose_name = "pago de venta"
        verbose_name_plural = "pagos de venta"

    def __str__(self):
        return f"{self.method}: Bs {self.amount}"


class SaleReturn(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    refund_method = models.ForeignKey(
        PaymentMethod, on_delete=models.PROTECT, related_name="sale_refunds",
        null=True, blank=True, verbose_name="medio de reembolso",
    )
    reason = models.CharField("motivo", max_length=255)
    refund_amount = models.DecimalField("importe a reembolsar", max_digits=12, decimal_places=2)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_hash = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_returned",
        verbose_name="registrada por",
    )
    created_at = models.DateTimeField("registrada el", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        permissions = [("process_salereturn", "Puede procesar devoluciones de ventas")]
        verbose_name = "devolución de venta"
        verbose_name_plural = "devoluciones de ventas"

    def __str__(self):
        return f"Devolución de {self.sale.number} · Bs {self.refund_amount}"


class SaleReturnLine(models.Model):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.PROTECT, related_name="lines")
    sale_line = models.ForeignKey(SaleLine, on_delete=models.PROTECT, related_name="return_lines")
    quantity = models.PositiveIntegerField("cantidad devuelta", validators=[MinValueValidator(1)])
    refund_amount = models.DecimalField("importe devuelto", max_digits=12, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sale_return", "sale_line"], name="unique_return_sale_line"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="sale_return_quantity_positive"),
        ]
        verbose_name = "línea de devolución"
        verbose_name_plural = "líneas de devolución"


class SaleEvent(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="events")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sale_events",
        verbose_name="usuario",
    )
    event_type = models.CharField("operación", max_length=32)
    details = models.CharField("detalle", max_length=255, blank=True)
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = "evento de venta"
        verbose_name_plural = "historial de ventas"
