from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import AuditEvent
from .services import snapshot


class AuditEventTests(TestCase):
    def test_login_is_audited_without_password_or_sensitive_values(self):
        user = get_user_model().objects.create_user(
            username="audited-user", password="Never-log-this-secret-123"
        )
        self.client.post("/accounts/login/", {
            "username": "audited-user", "password": "Never-log-this-secret-123",
        })
        event = AuditEvent.objects.get(action=AuditEvent.Action.LOGIN)
        self.assertEqual(event.actor, user)
        self.assertEqual(event.after_data, {"username": "audited-user"})
        self.assertNotIn("password", event.after_data)
        self.assertNotIn("Never-log-this-secret-123", str(event.after_data))

    def test_user_snapshot_never_contains_password_or_last_login(self):
        user = get_user_model().objects.create_user(
            username="snapshot-user", password="Never-log-this-secret-123"
        )
        data = snapshot(user)
        self.assertNotIn("password", data)
        self.assertNotIn("last_login", data)
        self.assertNotIn("Never-log-this-secret-123", str(data))

    def test_audit_admin_is_read_only(self):
        from django.contrib import admin

        model_admin = admin.site._registry[AuditEvent]
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))