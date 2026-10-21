from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Customer(models.Model):
    class DocumentType(models.TextChoices):
        CI = "ci", "Cédula de identidad"
        NIT = "nit", "NIT"
        PASSPORT = "passport", "Pasaporte"
        OTHER = "other", "Otro"

    full_name = models.CharField("nombre completo", max_length=180, db_index=True)
    document_type = models.CharField(
        "tipo de documento", max_length=12, choices=DocumentType.choices,
        blank=True,
    )
    document_number = models.CharField("número de documento", max_length=40, blank=True)
    phone = models.CharField("teléfono", max_length=40, blank=True)
    email = models.EmailField("correo electrónico", blank=True)
    address = models.CharField("dirección", max_length=255, blank=True)
    notes = models.TextField("observaciones", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customers_created",
        verbose_name="registrado por",
    )
    is_active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        ordering = ["full_name", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(document_type="") & Q(document_number=""))
                | (Q(document_type__gt="") & Q(document_number__gt="")),
                name="customer_document_fields_together",
            ),
            models.UniqueConstraint(
                fields=["document_type", "document_number"],
                condition=~Q(document_number=""),
                name="unique_customer_document",
            ),
        ]
        indexes = [models.Index(fields=["is_active", "full_name"], name="customer_active_name_idx")]
        verbose_name = "cliente"
        verbose_name_plural = "clientes"

    def clean(self):
        self.full_name = (self.full_name or "").strip()
        self.document_number = (self.document_number or "").strip().upper()
        if bool(self.document_type) != bool(self.document_number):
            raise ValidationError("Completa el tipo y el número de documento, o deja ambos vacíos.")
        if self.document_type and self.document_number:
            duplicates = Customer.objects.filter(
                document_type=self.document_type,
                document_number=self.document_number,
            )
            if self.pk:
                duplicates = duplicates.exclude(pk=self.pk)
            if duplicates.exists():
                raise ValidationError({"document_number": "Ya existe un cliente con este documento."})

    def save(self, *args, **kwargs):
        self.full_name = (self.full_name or "").strip()
        self.document_number = (self.document_number or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name
