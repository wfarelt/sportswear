from django.contrib.contenttypes.models import ContentType

from .models import AuditEvent

_SENSITIVE_FIELDS = {"password", "last_login"}


def snapshot(instance):
    """Return JSON-safe concrete field values without credentials or secrets."""
    result = {}
    for field in instance._meta.concrete_fields:
        if field.name in _SENSITIVE_FIELDS or field.name == "id":
            continue
        value = getattr(instance, field.attname)
        if value is None or isinstance(value, (bool, int, float, str)):
            result[field.name] = value
        else:
            result[field.name] = str(value)
    return result


def record_event(*, actor, action, instance, before_data=None, after_data=None):
    """Append an audit event for a persisted business or security record."""
    if instance.pk is None:
        raise ValueError("No se pueden auditar registros que aún no se guardaron.")
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        content_type=ContentType.objects.get_for_model(instance, for_concrete_model=False),
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        before_data=before_data,
        after_data=after_data if after_data is not None else snapshot(instance),
    )

