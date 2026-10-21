from django.contrib import admin

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, snapshot

from .models import Branch


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "responsible", "phone", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "address")
    prepopulated_fields = {"code": ("name",)}

    def save_model(self, request, obj, form, change):
        before = snapshot(Branch.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_event(
            actor=request.user,
            action=AuditEvent.Action.UPDATE if change else AuditEvent.Action.CREATE,
            instance=obj,
            before_data=before,
        )

    def has_delete_permission(self, request, obj=None):
        return False
