from django.contrib import admin

from .models import Branch


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "responsible", "phone", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "address")
    prepopulated_fields = {"code": ("name",)}

    def has_delete_permission(self, request, obj=None):
        return False
