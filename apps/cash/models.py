import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class CashResource(models.Model):
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="cash_resources",
        verbose_name="sucursal",
    )
    code = models.SlugField("código", max_length=30)
    name = models.CharField("nombre", max_length=100)
    is_active = models.BooleanField("activa", default=True)
    created_at = models.DateTimeField("creada el", auto_now_add=True)

    class Meta:
        ordering = ["branch__name", "name"]
        constraints = [models.UniqueConstraint(fields=["branch", "code"], name="unique_cash_resource_branch_code")]
        verbose_name = "caja"
        verbose_name_plural = "cajas"

    def clean(self):
        if not self.pk:
            return
        previous = CashResource.objects.filter(pk=self.pk).values("branch_id").first()
        if previous and previous["branch_id"] != self.branch_id and self.sessions.exists():
            raise ValidationError({"branch": "No se puede cambiar la sucursal de una caja con historial de turnos."})
        if not self.is_active and self.sessions.filter(status=CashSession.Status.OPEN).exists():
            raise ValidationError({"is_active": "Cierra el turno abierto antes de desactivar esta caja."})

    def __str__(self):
        return f"{self.branch.name} · {self.name}"


class CashSession(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Abierta"
        CLOSED = "closed", "Cerrada"

    resource = models.ForeignKey(
        CashResource, on_delete=models.PROTECT, related_name="sessions", verbose_name="caja"
    )
    status = models.CharField("estado", max_length=10, choices=Status.choices, default=Status.OPEN)
    opening_balance = models.DecimalField(
        "saldo inicial", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)]
    )
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_sessions_opened",
        verbose_name="abierta por",
    )
    opened_at = models.DateTimeField("abierta el", auto_now_add=True)
    opening_notes = models.CharField("observaciones de apertura", max_length=255, blank=True)
    counted_cash = models.DecimalField(
        "efectivo contado", max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    expected_cash = models.DecimalField(
        "efectivo esperado", max_digits=12, decimal_places=2, null=True, blank=True,
    )
    cash_sales_total = models.DecimalField("cobros en efectivo", max_digits=12, decimal_places=2, null=True, blank=True)
    noncash_collections_total = models.DecimalField("cobros no efectivos", max_digits=12, decimal_places=2, null=True, blank=True)
    additional_income_total = models.DecimalField("ingresos adicionales", max_digits=12, decimal_places=2, null=True, blank=True)
    expense_total = models.DecimalField("egresos", max_digits=12, decimal_places=2, null=True, blank=True)
    withdrawal_total = models.DecimalField("retiros", max_digits=12, decimal_places=2, null=True, blank=True)
    refund_total = models.DecimalField("reembolsos en efectivo", max_digits=12, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField("diferencia", max_digits=12, decimal_places=2, null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_sessions_closed",
        null=True, blank=True, verbose_name="cerrada por",
    )
    closed_at = models.DateTimeField("cerrada el", null=True, blank=True)
    closing_notes = models.CharField("observaciones de cierre", max_length=255, blank=True)

    class Meta:
        ordering = ["-opened_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["resource"], condition=Q(status="open"),
                name="unique_open_cash_session_per_resource",
            ),
            models.CheckConstraint(condition=Q(opening_balance__gte=0), name="cash_opening_nonnegative"),
            models.CheckConstraint(condition=Q(counted_cash__isnull=True) | Q(counted_cash__gte=0), name="cash_counted_nonnegative"),
        ]
        indexes = [models.Index(fields=["status", "opened_at"], name="cash_session_status_date_idx")]
        permissions = [
            ("open_cashsession", "Puede abrir turnos de caja"),
            ("close_cashsession", "Puede cerrar turnos de caja"),
            ("adjust_closed_cashsession", "Puede corregir cierres de caja con auditoría"),
        ]
        verbose_name = "turno de caja"
        verbose_name_plural = "turnos de caja"

    @property
    def branch(self):
        return self.resource.branch

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def __str__(self):
        return f"{self.resource} · turno {self.pk or 'nuevo'} · {self.get_status_display()}"


class CashMovement(models.Model):
    class Kind(models.TextChoices):
        SALE_COLLECTION = "sale_collection", "Cobro de venta"
        OTHER_INCOME = "other_income", "Ingreso adicional"
        EXPENSE = "expense", "Egreso"
        WITHDRAWAL = "withdrawal", "Retiro de efectivo"
        SALE_REFUND = "sale_refund", "Reembolso de venta"

    session = models.ForeignKey(
        CashSession, on_delete=models.PROTECT, related_name="movements", verbose_name="turno"
    )
    kind = models.CharField("tipo de movimiento", max_length=20, choices=Kind.choices)
    amount = models.DecimalField(
        "importe", max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)]
    )
    payment_method = models.ForeignKey(
        "sales.PaymentMethod", on_delete=models.PROTECT, related_name="cash_movements",
        null=True, blank=True, verbose_name="medio de pago",
    )
    affects_cash = models.BooleanField("afecta efectivo físico", default=True)
    sale_payment = models.OneToOneField(
        "sales.SalePayment", on_delete=models.PROTECT, related_name="cash_movement",
        null=True, blank=True, verbose_name="pago de venta",
    )
    sale_return = models.ForeignKey(
        "sales.SaleReturn", on_delete=models.PROTECT, related_name="cash_movements",
        null=True, blank=True, verbose_name="devolución relacionada",
    )
    reference = models.CharField("referencia", max_length=100, blank=True)
    reason = models.CharField("motivo", max_length=255, blank=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_hash = models.CharField(max_length=64, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_movements",
        verbose_name="responsable",
    )
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="cash_movement_amount_positive"),
            models.CheckConstraint(
                condition=(Q(kind="sale_collection", sale_payment__isnull=False))
                | (Q(kind="sale_refund", sale_return__isnull=False))
                | Q(kind__in=["other_income", "expense", "withdrawal"]),
                name="cash_movement_source_matches_kind",
            ),
        ]
        indexes = [models.Index(fields=["session", "kind", "created_at"], name="cash_movement_session_kind_idx")]
        verbose_name = "movimiento de caja"
        verbose_name_plural = "movimientos de caja"

    def __str__(self):
        return f"{self.get_kind_display()} · Bs {self.amount} · {self.session}"


class CashSessionEvent(models.Model):
    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name="events")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_session_events",
        verbose_name="usuario",
    )
    event_type = models.CharField("evento", max_length=24)
    details = models.CharField("detalle", max_length=255, blank=True)
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = "evento de caja"
        verbose_name_plural = "historial de caja"
