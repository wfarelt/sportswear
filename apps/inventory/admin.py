from django.contrib import admin

from .models import (
    InventoryMovement,
    InventoryOperation,
    StockLevel,
    StockTransfer,
    StockTransferEvent,
    StockTransferLine,
)


class ReadOnlyHistoryAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockLevel)
class StockLevelAdmin(ReadOnlyHistoryAdmin):
    list_display = ("branch", "variant", "quantity", "minimum_quantity", "average_cost", "updated_at")
    list_filter = ("branch", "variant__product__category")
    search_fields = ("variant__sku", "variant__product__name", "variant__barcode")
    readonly_fields = tuple(field.name for field in StockLevel._meta.fields)


class StockTransferLineInline(admin.TabularInline):
    model = StockTransferLine
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in StockTransferLine._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


class StockTransferEventInline(admin.TabularInline):
    model = StockTransferEvent
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in StockTransferEvent._meta.fields)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(StockTransfer)
class StockTransferAdmin(ReadOnlyHistoryAdmin):
    list_display = ("id", "origin", "destination", "status", "created_by", "created_at")
    list_filter = ("status", "origin", "destination")
    search_fields = ("id", "origin__name", "destination__name")
    inlines = (StockTransferLineInline, StockTransferEventInline)
    readonly_fields = tuple(field.name for field in StockTransfer._meta.fields)


@admin.register(InventoryOperation)
class InventoryOperationAdmin(ReadOnlyHistoryAdmin):
    list_display = ("id", "kind", "branch", "user", "reason", "created_at")
    list_filter = ("kind", "branch", "created_at")
    search_fields = ("reason", "user__username", "transfer__id")
    readonly_fields = tuple(field.name for field in InventoryOperation._meta.fields)


@admin.register(InventoryMovement)
class InventoryMovementAdmin(ReadOnlyHistoryAdmin):
    list_display = ("created_at", "operation", "branch", "variant", "quantity_delta", "balance_after")
    list_filter = ("branch", "operation__kind", "created_at")
    search_fields = ("variant__sku", "variant__product__name", "operation__reason")
    readonly_fields = tuple(field.name for field in InventoryMovement._meta.fields)


@admin.register(StockTransferEvent)
class StockTransferEventAdmin(ReadOnlyHistoryAdmin):
    list_display = ("transfer", "from_status", "to_status", "user", "created_at")
    list_filter = ("to_status", "created_at")
    search_fields = ("transfer__id", "details", "user__username")
    readonly_fields = tuple(field.name for field in StockTransferEvent._meta.fields)
