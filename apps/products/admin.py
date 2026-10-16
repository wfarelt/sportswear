from django.contrib import admin

from .models import Brand, Category, Color, Product, ProductVariant, Size


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Size)
class SizeAdmin(admin.ModelAdmin):
    list_display = ("name", "sort_order", "is_active")
    list_filter = ("is_active",)
    ordering = ("sort_order", "name")


@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):
    list_display = ("name", "hex_code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("internal_code", "name", "category", "brand", "sale_price", "is_active")
    list_filter = ("is_active", "category", "brand")
    search_fields = ("internal_code", "name", "variants__sku", "variants__barcode")
    inlines = [ProductVariantInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("sku", "product", "size", "color", "sale_price", "is_active")
    list_filter = ("is_active", "size", "color")
    search_fields = ("sku", "barcode", "product__name")
