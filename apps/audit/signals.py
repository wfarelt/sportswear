from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .models import AuditEvent
from .services import record_event


@receiver(user_logged_in, dispatch_uid="audit_user_login")
def audit_user_login(sender, request, user, **kwargs):
    record_event(
        actor=user,
        action=AuditEvent.Action.LOGIN,
        instance=user,
        after_data={"username": user.get_username()},
    )