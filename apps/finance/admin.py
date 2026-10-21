from django.contrib import admin

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, snapshot

from .models import FinanceAccount, FinanceApprovalLimit, FinanceCategory, FinanceEvent, FinanceOperation


class AuditedFinanceAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        before = snapshot(obj.__class__.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_event(
            actor=request.user,
            action=AuditEvent.Action.UPDATE if change else AuditEvent.Action.CREATE,
            instance=obj,
            before_data=before,
        )


@admin.register(FinanceCategory)
class FinanceCategoryAdmin(AuditedFinanceAdmin):
    list_display = ("name", "kind", "is_active", "created_at")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "description")

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(FinanceAccount)
class FinanceAccountAdmin(AuditedFinanceAdmin):
    list_display = ("name", "kind", "branch", "cash_resource", "currency", "is_active")
    list_filter = ("kind", "branch", "is_active")
    search_fields = ("name", "account_reference", "branch__name")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FinanceApprovalLimit)
class FinanceApprovalLimitAdmin(AuditedFinanceAdmin):
    list_display = ("user", "role", "branch", "maximum_amount", "is_active")
    list_filter = ("role", "branch", "is_active")
    search_fields = ("user__username", "user__first_name", "user__last_name")

    def has_delete_permission(self, request, obj=None):
        return False


class FinanceEventInline(admin.TabularInline):
    model = FinanceEvent
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in FinanceEvent._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FinanceOperation)
class FinanceOperationAdmin(admin.ModelAdmin):
    list_display = ("number", "kind", "category", "branch", "account", "amount", "status", "created_by", "created_at")
    list_filter = ("kind", "status", "category", "branch", "operation_date")
    search_fields = ("number", "concept", "created_by__username", "account__name")
    readonly_fields = tuple(field.name for field in FinanceOperation._meta.fields)
    inlines = (FinanceEventInline,)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD") and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FinanceEvent)
class FinanceEventAdmin(admin.ModelAdmin):
    list_display = ("operation", "event_type", "user", "created_at", "details")
    list_filter = ("event_type", "created_at")
    search_fields = ("operation__number", "details", "user__username")
    readonly_fields = tuple(field.name for field in FinanceEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD") and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
