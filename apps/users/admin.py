from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User, UserBranchAccess


class UserBranchAccessInline(admin.TabularInline):
    model = UserBranchAccess
    extra = 0


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    fieldsets = DjangoUserAdmin.fieldsets + (("Perfil comercial", {"fields": ("role",)}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Perfil comercial", {"fields": ("role",)}),
    )
    inlines = [UserBranchAccessInline]


admin.site.site_header = "Administración de Sportswear"
admin.site.site_title = "Sportswear"
admin.site.index_title = "Administración del sistema"
