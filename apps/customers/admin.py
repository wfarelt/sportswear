from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "document_type", "document_number", "phone", "is_active", "created_at")
    list_filter = ("is_active", "document_type")
    search_fields = ("full_name", "document_number", "phone", "email")

    def has_delete_permission(self, request, obj=None):
        return False
