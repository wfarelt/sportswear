from django.contrib import admin

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, snapshot

from .models import Brand, Category, Color, Product, ProductVariant, Size


class NoDeleteModelAdmin(admin.ModelAdmin):
    """Keep catalog records available for future historical references."""

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        before = snapshot(obj.__class__.objects.get(pk=obj.pk)) if change else None
        super().save_model(request, obj, form, change)
        record_event(
            actor=request.user,
            action=AuditEvent.Action.UPDATE if change else AuditEvent.Action.CREATE,
            instance=obj,
            before_data=before,
        )


@admin.register(Category)
class CategoryAdmin(NoDeleteModelAdmin):
    list_display = ("name", "parent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Brand)
class BrandAdmin(NoDeleteModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Size)
class SizeAdmin(NoDeleteModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_filter = ("is_active",)
    ordering = ("sort_order", "name")


@admin.register(Color)
class ColorAdmin(NoDeleteModelAdmin):
    list_display = ("name", "hex_code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in ProductVariant._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(NoDeleteModelAdmin):
    list_display = ("internal_code", "name", "category", "brand", "sale_price", "is_active")
    list_filter = ("is_active", "category", "brand")
    search_fields = ("internal_code", "name", "variants__sku", "variants__barcode")
    inlines = [ProductVariantInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(NoDeleteModelAdmin):
    list_display = ("sku", "product", "size", "color", "sale_price", "is_active")
    list_filter = ("is_active", "size", "color")
    search_fields = ("sku", "barcode", "product__name")
