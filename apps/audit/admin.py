from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "content_type", "object_repr")
    list_filter = ("action", "content_type", "created_at")
    search_fields = ("object_repr", "object_id", "actor__username")
    readonly_fields = tuple(field.name for field in AuditEvent._meta.fields)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False