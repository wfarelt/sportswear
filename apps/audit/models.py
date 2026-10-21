from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Creación"
        UPDATE = "update", "Modificación"
        LOGIN = "login", "Inicio de sesión"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name="usuario",
    )
    action = models.CharField("acción", max_length=10, choices=Action.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField("identificador", max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")
    object_repr = models.CharField("registro", max_length=255)
    before_data = models.JSONField("datos anteriores", null=True, blank=True)
    after_data = models.JSONField("datos posteriores")
    created_at = models.DateTimeField("fecha y hora", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["content_type", "object_id"], name="audit_object_idx")]
        verbose_name = "evento de auditoría"
        verbose_name_plural = "eventos de auditoría"

    def __str__(self):
        return f"{self.get_action_display()} · {self.object_repr}"