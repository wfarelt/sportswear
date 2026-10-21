import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from .storage import PrivateFinanceStorage


class FinanceCategory(models.Model):
    class Kind(models.TextChoices):
        INCOME = "income", "Ingreso"
        EXPENSE = "expense", "Egreso"

    name = models.CharField("nombre", max_length=100)
    kind = models.CharField("tipo", max_length=10, choices=Kind.choices)
    description = models.CharField("descripción", max_length=255, blank=True)
    is_active = models.BooleanField("activa", default=True)
    created_at = models.DateTimeField("creada el", auto_now_add=True)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [models.UniqueConstraint(fields=["kind", "name"], name="unique_finance_category_kind_name")]
        verbose_name = "categoría financiera"
        verbose_name_plural = "categorías financieras"

    def __str__(self):
        return f"{self.get_kind_display()} · {self.name}"


class FinanceAccount(models.Model):
    class Kind(models.TextChoices):
        CASH = "cash", "Efectivo"
        BANK = "bank", "Cuenta bancaria"
        OTHER = "other", "Otra cuenta"

    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="finance_accounts",
        null=True, blank=True, verbose_name="sucursal (vacío = cuenta general)",
    )
    name = models.CharField("nombre", max_length=120)
    kind = models.CharField("tipo de cuenta", max_length=10, choices=Kind.choices)
    cash_resource = models.OneToOneField(
        "cash.CashResource", on_delete=models.PROTECT, related_name="finance_account",
        null=True, blank=True, verbose_name="recurso de caja asociado",
    )
    account_reference = models.CharField("referencia de cuenta", max_length=100, blank=True)
    currency = models.CharField("moneda", max_length=3, default="BOB")
    opening_balance = models.DecimalField(
        "saldo inicial contable", max_digits=14, decimal_places=2, default=0,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField("activa", default=True)
    created_at = models.DateTimeField("creada el", auto_now_add=True)

    class Meta:
        ordering = ["branch__name", "name"]
        permissions = [
            ("use_general_finance_accounts", "Puede utilizar cuentas financieras generales"),
            ("view_general_finance_accounts", "Puede consultar saldos de cuentas generales"),
        ]
        verbose_name = "cuenta financiera"
        verbose_name_plural = "cuentas financieras"

    def __str__(self):
        branch = self.branch.name if self.branch_id else "General"
        return f"{branch} · {self.name}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.kind == self.Kind.CASH and not self.cash_resource_id:
            raise ValidationError({"cash_resource": "Una cuenta de efectivo debe vincularse a una caja física."})
        if self.kind != self.Kind.CASH and self.cash_resource_id:
            raise ValidationError({"cash_resource": "Solo las cuentas de efectivo pueden vincular una caja física."})
        if self.cash_resource_id and self.branch_id and self.cash_resource.branch_id != self.branch_id:
            raise ValidationError({"cash_resource": "La caja física pertenece a otra sucursal."})
        if self.kind == self.Kind.CASH and self.opening_balance:
            raise ValidationError({"opening_balance": "El saldo inicial de efectivo se registra al abrir cada turno de caja."})


class FinanceApprovalLimit(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="finance_approval_limits",
        null=True, blank=True, verbose_name="usuario específico",
    )
    role = models.CharField(
        "rol", max_length=20,
        choices=(
            ("admin", "Administrador"),
            ("manager", "Gerente"),
            ("seller", "Vendedor"),
            ("stockkeeper", "Almacenero"),
            ("cashier", "Cajero"),
        ),
        blank=True,
    )
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.CASCADE, related_name="finance_approval_limits",
        null=True, blank=True, verbose_name="sucursal (vacío = todas las autorizadas)",
    )
    maximum_amount = models.DecimalField(
        "límite máximo (Bs)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField("activo", default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(user__isnull=False) | Q(role__gt=""), name="finance_limit_has_user_or_role"),
            models.UniqueConstraint(
                fields=["user", "branch"],
                condition=Q(user__isnull=False, branch__isnull=False),
                name="finance_limit_user_branch_uniq",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(user__isnull=False, branch__isnull=True),
                name="finance_limit_user_global_uniq",
            ),
            models.UniqueConstraint(
                fields=["role", "branch"],
                condition=Q(user__isnull=True, role__gt="", branch__isnull=False),
                name="finance_limit_role_branch_uniq",
            ),
            models.UniqueConstraint(
                fields=["role"],
                condition=Q(user__isnull=True, role__gt="", branch__isnull=True),
                name="finance_limit_role_global_uniq",
            ),
            models.CheckConstraint(condition=Q(maximum_amount__gte=0), name="finance_limit_nonnegative"),
        ]
        verbose_name = "límite de autorización financiera"
        verbose_name_plural = "límites de autorización financiera"

    def __str__(self):
        subject = self.user.get_username() if self.user_id else self.get_role_display()
        return f"{subject} · Bs {self.maximum_amount}"


class FinanceOperation(models.Model):
    class Kind(models.TextChoices):
        INCOME = "income", "Ingreso"
        EXPENSE = "expense", "Egreso"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente de autorización"
        POSTED = "posted", "Confirmada"
        REJECTED = "rejected", "Rechazada"
        REVERSED = "reversed", "Revertida"

    number = models.CharField("número de operación", max_length=24, unique=True, blank=True)
    kind = models.CharField("tipo", max_length=10, choices=Kind.choices)
    category = models.ForeignKey(
        FinanceCategory, on_delete=models.PROTECT, related_name="operations",
        verbose_name="categoría",
    )
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.PROTECT, related_name="finance_operations",
        null=True, blank=True, verbose_name="sucursal",
    )
    account = models.ForeignKey(
        FinanceAccount, on_delete=models.PROTECT, related_name="operations",
        verbose_name="cuenta destino/origen",
    )
    cash_session = models.ForeignKey(
        "cash.CashSession", on_delete=models.PROTECT, related_name="finance_operations",
        null=True, blank=True, verbose_name="turno de caja relacionado",
    )
    cash_movement = models.OneToOneField(
        "cash.CashMovement", on_delete=models.PROTECT, related_name="finance_operation",
        null=True, blank=True, verbose_name="movimiento de caja vinculado",
    )
    payment_method = models.ForeignKey(
        "sales.PaymentMethod", on_delete=models.PROTECT, related_name="finance_operations",
        null=True, blank=True, verbose_name="medio de pago",
    )
    concept = models.CharField("concepto", max_length=180)
    amount = models.DecimalField(
        "importe (Bs)", max_digits=12, decimal_places=2,
        validators=[MinValueValidator(0.01)],
    )
    operation_date = models.DateField("fecha de operación")
    notes = models.TextField("observaciones", blank=True)
    attachment = models.FileField(
        "comprobante adjunto",
        upload_to="finance/",
        storage=PrivateFinanceStorage(),
        blank=True,
    )
    status = models.CharField("estado", max_length=12, choices=Status.choices, default=Status.PENDING)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_hash = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finance_operations_created",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField("creada el", auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finance_operations_approved",
        null=True, blank=True, verbose_name="autorizada por",
    )
    approved_at = models.DateTimeField("autorizada el", null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finance_operations_rejected",
        null=True, blank=True, verbose_name="rechazada por",
    )
    rejected_at = models.DateTimeField("rechazada el", null=True, blank=True)
    reversal_of = models.OneToOneField(
        "self", on_delete=models.PROTECT, related_name="reversal",
        null=True, blank=True, verbose_name="operación revertida",
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finance_operations_reversed",
        null=True, blank=True, verbose_name="revertida por",
    )

    class Meta:
        ordering = ["-operation_date", "-pk"]
        indexes = [
            models.Index(fields=["branch", "kind", "operation_date"], name="finance_branch_kind_date_idx"),
            models.Index(fields=["status", "created_at"], name="finance_status_created_idx"),
        ]
        permissions = [
            ("approve_financeoperation", "Puede autorizar operaciones financieras"),
            ("reject_financeoperation", "Puede rechazar operaciones financieras"),
            ("reverse_financeoperation", "Puede revertir operaciones financieras confirmadas"),
            ("view_all_financeoperations", "Puede ver operaciones financieras de otros usuarios"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="finance_operation_amount_positive"),
        ]
        verbose_name = "operación financiera"
        verbose_name_plural = "operaciones financieras"

    def __str__(self):
        return self.number or f"Operación financiera #{self.pk or 'nueva'}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.category_id and self.kind != self.category.kind:
            raise ValidationError({"category": "La categoría debe corresponder al tipo de operación."})
        if self.account_id:
            if self.account.kind == FinanceAccount.Kind.CASH and not self.account.cash_resource_id:
                raise ValidationError({"account": "Asocia una caja física a esta cuenta de efectivo."})
            if self.account.kind != FinanceAccount.Kind.CASH and self.cash_session_id:
                raise ValidationError({"cash_session": "Solo las operaciones en efectivo usan un turno de caja."})
            if self.branch_id and self.account.branch_id and self.branch_id != self.account.branch_id:
                raise ValidationError({"account": "La cuenta pertenece a otra sucursal."})
        if self.cash_session_id and self.branch_id:
            if self.cash_session.resource.branch_id != self.branch_id:
                raise ValidationError({"cash_session": "El turno pertenece a otra sucursal."})


class FinanceEvent(models.Model):
    operation = models.ForeignKey(FinanceOperation, on_delete=models.PROTECT, related_name="events")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finance_events",
        verbose_name="usuario",
    )
    event_type = models.CharField("evento", max_length=24)
    details = models.CharField("detalle", max_length=255, blank=True)
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        verbose_name = "evento financiero"
        verbose_name_plural = "historial financiero"
