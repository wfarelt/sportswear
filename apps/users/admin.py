from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, snapshot

from .models import User, UserBranchAccess


class UserBranchAccessInline(admin.TabularInline):
    model = UserBranchAccess
    extra = 0

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            if deleted.pk:
                before = snapshot(UserBranchAccess.objects.get(pk=deleted.pk))
                record_event(
                    actor=request.user,
                    action=AuditEvent.Action.UPDATE,
                    instance=deleted,
                    before_data=before,
                    after_data={"deleted": True, **before},
                )
            deleted.delete()
        for instance in instances:
            before = snapshot(UserBranchAccess.objects.get(pk=instance.pk)) if instance.pk else None
            instance.save()
            record_event(
                actor=request.user,
                action=AuditEvent.Action.UPDATE if before else AuditEvent.Action.CREATE,
                instance=instance,
                before_data=before,
            )
        formset.save_m2m()


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    fieldsets = DjangoUserAdmin.fieldsets + (("Perfil comercial", {"fields": ("role",)}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Perfil comercial", {"fields": ("role",)}),
    )
    inlines = [UserBranchAccessInline]

    @transaction.atomic
    @transaction.atomic
    def save_model(self, request, obj, form, change):
        request.audit_user_before = None
        if change:
            persisted_user = User.objects.get(pk=obj.pk)
            request.audit_user_before = snapshot(persisted_user)
            request.audit_user_before["authorized_branch_ids"] = list(
                persisted_user.branches.order_by("pk").values_list("pk", flat=True)
            )
            request.audit_user_before["permission_ids"] = list(
                persisted_user.user_permissions.order_by("pk").values_list("pk", flat=True)
            )
            request.audit_user_before["group_ids"] = list(
                persisted_user.groups.order_by("pk").values_list("pk", flat=True)
            )
        super().save_model(request, obj, form, change)

    @transaction.atomic
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        after = snapshot(form.instance)
        after["authorized_branch_ids"] = list(
            form.instance.branches.order_by("pk").values_list("pk", flat=True)
        )
        after["permission_ids"] = list(
            form.instance.user_permissions.order_by("pk").values_list("pk", flat=True)
        )
        after["group_ids"] = list(
            form.instance.groups.order_by("pk").values_list("pk", flat=True)
        )
        record_event(
            actor=request.user,
            action=AuditEvent.Action.UPDATE if change else AuditEvent.Action.CREATE,
            instance=form.instance,
            before_data=request.audit_user_before,
            after_data=after,
        )


@admin.register(UserBranchAccess)
class UserBranchAccessAdmin(admin.ModelAdmin):
    list_display = ("user", "branch", "is_default", "assigned_at")
    list_filter = ("branch", "is_default")
    search_fields = ("user__username", "user__first_name", "user__last_name", "branch__name")

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        before = snapshot(obj) if change else None
        super().save_model(request, obj, form, change)
        record_event(
            actor=request.user,
            action=AuditEvent.Action.UPDATE if change else AuditEvent.Action.CREATE,
            instance=obj,
            before_data=before,
        )

    @transaction.atomic
    def delete_model(self, request, obj):
        before = snapshot(obj)
        record_event(
            actor=request.user,
            action=AuditEvent.Action.UPDATE,
            instance=obj,
            before_data=before,
            after_data={"deleted": True, **before},
        )
        super().delete_model(request, obj)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        for obj in queryset.iterator():
            before = snapshot(obj)
            record_event(
                actor=request.user,
                action=AuditEvent.Action.UPDATE,
                instance=obj,
                before_data=before,
                after_data={"deleted": True, **before},
            )
        super().delete_queryset(request, queryset)

    def has_delete_permission(self, request, obj=None):
        return request.method == "POST" and super().has_delete_permission(request, obj)


admin.site.site_header = "Administración de Sportswear"
admin.site.site_title = "Sportswear"
admin.site.index_title = "Administración del sistema"
