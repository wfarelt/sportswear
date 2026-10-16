from django.contrib import admin

from .models import (
    PaymentMethod,
    Sale,
    SaleEvent,
    SaleLine,
    SalePayment,
    SaleReturn,
    SaleReturnLine,
)


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "affects_cash", "is_active", "sort_order")
    list_filter = ("is_active", "affects_cash")
    search_fields = ("name", "code")

    def has_delete_permission(self, request, obj=None):
        return False


class SaleLineInline(admin.TabularInline):
    model = SaleLine
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in SaleLine._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


class SalePaymentInline(admin.TabularInline):
    model = SalePayment
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in SalePayment._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


class SaleEventInline(admin.TabularInline):
    model = SaleEvent
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in SaleEvent._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("number", "branch", "customer", "seller", "status", "total", "created_at")
    list_filter = ("status", "branch", "created_at")
    search_fields = ("number", "customer__full_name", "customer__document_number", "seller__username")
    inlines = (SaleLineInline, SalePaymentInline, SaleEventInline)
    readonly_fields = tuple(field.name for field in Sale._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD") and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ("sale", "refund_method", "refund_amount", "created_by", "created_at")
    list_filter = ("refund_method", "created_at")
    search_fields = ("sale__number", "reason")
    readonly_fields = tuple(field.name for field in SaleReturn._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD") and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SaleReturnLine)
class SaleReturnLineAdmin(admin.ModelAdmin):
    list_display = ("sale_return", "sale_line", "quantity", "refund_amount")
    readonly_fields = tuple(field.name for field in SaleReturnLine._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ("GET", "HEAD") and super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False
