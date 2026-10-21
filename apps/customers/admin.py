from django.contrib import admin

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, snapshot

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "document_type", "document_number", "phone", "is_active", "created_at")
    list_filter = ("is_active", "document_type")
    search_fields = ("full_name", "document_number", "phone", "email")

    def save_model(self, request, obj, form, change):
        before = snapshot(Customer.objects.get(pk=obj.pk)) if change else None
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        record_event(
            actor=request.user,
            action=AuditEvent.Action.UPDATE if change else AuditEvent.Action.CREATE,
            instance=obj,
            before_data=before,
        )

    def has_delete_permission(self, request, obj=None):
        return False
