from django.contrib import admin

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, snapshot

from .models import CashMovement, CashResource, CashSession, CashSessionEvent


@admin.register(CashResource)
class CashResourceAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "branch", "is_active")
    list_filter = ("branch", "is_active")
    search_fields = ("name", "code", "branch__name")

    def save_model(self, request, obj, form, change):
        before = snapshot(CashResource.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_event(
            actor=request.user,
            action=AuditEvent.Action.UPDATE if change else AuditEvent.Action.CREATE,
            instance=obj,
            before_data=before,
        )

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.sessions.exists():
            return ("branch",)
        return ()

    def has_delete_permission(self, request, obj=None):
        return False


class CashMovementInline(admin.TabularInline):
    model = CashMovement
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in CashMovement._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


class CashSessionEventInline(admin.TabularInline):
    model = CashSessionEvent
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in CashSessionEvent._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CashSession)
class CashSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "resource", "status", "opening_balance", "expected_cash", "difference", "opened_by", "opened_at", "closed_at")
    list_filter = ("status", "resource__branch", "opened_at")
    search_fields = ("resource__name", "resource__branch__name", "opened_by__username", "closed_by__username")
    readonly_fields = tuple(field.name for field in CashSession._meta.fields)
    inlines = (CashMovementInline, CashSessionEventInline)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD") and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashMovement)
class CashMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "session", "kind", "amount", "payment_method", "affects_cash", "user")
    list_filter = ("kind", "affects_cash", "session__resource__branch", "created_at")
    search_fields = ("reason", "reference", "session__id", "user__username")
    readonly_fields = tuple(field.name for field in CashMovement._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD") and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CashSessionEvent)
class CashSessionEventAdmin(admin.ModelAdmin):
    list_display = ("session", "event_type", "user", "created_at", "details")
    list_filter = ("event_type", "created_at")
    readonly_fields = tuple(field.name for field in CashSessionEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD") and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
