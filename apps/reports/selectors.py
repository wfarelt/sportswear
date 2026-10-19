from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.core.exceptions import PermissionDenied
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.cash.models import CashMovement, CashSession
from apps.customers.models import Customer
from apps.finance.models import FinanceAccount, FinanceOperation
from apps.inventory.models import (
    InventoryMovement,
    InventoryOperation,
    StockLevel,
    StockTransfer,
)
from apps.sales.models import Sale, SaleLine, SalePayment, SaleReturn

ZERO = Decimal("0.00")


NET_SALE_STATUSES = [
    Sale.Status.CONFIRMED,
    Sale.Status.PARTIALLY_RETURNED,
    Sale.Status.RETURNED,
]


def authorized_branch_queryset(user):
    from apps.branches.models import Branch

    branches = Branch.objects.filter(is_active=True)
    if not user.has_global_branch_access:
        branches = branches.filter(authorized_users=user)
    return branches


def scoped_sales(user, branch=None, date_from=None, date_to=None, *, visible_sellers=True):
    sales = Sale.objects.filter(
        branch__in=authorized_branch_queryset(user),
        status__in=NET_SALE_STATUSES,
    )
    if not user.is_superuser and not user.has_perm("sales.view_all_sales"):
        sales = sales.filter(seller=user)
    if branch:
        sales = sales.filter(branch=branch)
    if date_from:
        sales = sales.filter(created_at__date__gte=date_from)
    if date_to:
        sales = sales.filter(created_at__date__lte=date_to)
    return sales


def scoped_all_sales(user, branch=None, date_from=None, date_to=None):
    sales = Sale.objects.filter(branch__in=authorized_branch_queryset(user))
    if not user.is_superuser and not user.has_perm("sales.view_all_sales"):
        sales = sales.filter(seller=user)
    if branch:
        sales = sales.filter(branch=branch)
    if date_from:
        sales = sales.filter(created_at__date__gte=date_from)
    if date_to:
        sales = sales.filter(created_at__date__lte=date_to)
    return sales


def scoped_stock(user, branch=None):
    stock = StockLevel.objects.filter(branch__in=authorized_branch_queryset(user))
    if branch:
        stock = stock.filter(branch=branch)
    return stock.select_related("branch", "variant__product__category", "variant__product__brand", "variant__size", "variant__color")


def scoped_inventory_movements(user, branch=None, date_from=None, date_to=None):
    movements = InventoryMovement.objects.filter(branch__in=authorized_branch_queryset(user))
    if branch:
        movements = movements.filter(branch=branch)
    if date_from:
        movements = movements.filter(created_at__date__gte=date_from)
    if date_to:
        movements = movements.filter(created_at__date__lte=date_to)
    return movements.select_related("branch", "variant__product", "variant__product__category", "variant__product__brand", "variant__size", "variant__color", "operation", "operation__user")


def scoped_transfers(user, branch=None, date_from=None, date_to=None):
    branches = authorized_branch_queryset(user)
    transfers = StockTransfer.objects.filter(Q(origin__in=branches) | Q(destination__in=branches))
    if branch:
        transfers = transfers.filter(Q(origin=branch) | Q(destination=branch))
    if date_from:
        transfers = transfers.filter(created_at__date__gte=date_from)
    if date_to:
        transfers = transfers.filter(created_at__date__lte=date_to)
    return transfers.select_related("origin", "destination").prefetch_related("lines__variant__product")


def scoped_finance_operations(user, branch=None, date_from=None, date_to=None):
    branches = authorized_branch_queryset(user)
    if user.is_superuser or user.has_perm("finance.view_all_financeoperations"):
        general_account_scope = Q(branch__isnull=True)
    else:
        general_account_scope = Q(branch__isnull=True, created_by=user)
    operations = FinanceOperation.objects.filter(Q(branch__in=branches) | general_account_scope)
    if not user.is_superuser and not user.has_perm("finance.view_all_financeoperations"):
        operations = operations.filter(created_by=user)
    elif not user.is_superuser and not user.has_perm("finance.view_financeoperation"):
        operations = operations.none()
    if branch:
        operations = operations.filter(branch=branch)
    if date_from:
        operations = operations.filter(operation_date__gte=date_from)
    if date_to:
        operations = operations.filter(operation_date__lte=date_to)
    return operations.select_related("branch", "category", "account", "cash_session")


def scoped_cash_sessions(user, branch=None, date_from=None, date_to=None):
    sessions = CashSession.objects.filter(resource__branch__in=authorized_branch_queryset(user))
    if branch:
        sessions = sessions.filter(resource__branch=branch)
    if date_from:
        sessions = sessions.filter(opened_at__date__gte=date_from)
    if date_to:
        sessions = sessions.filter(opened_at__date__lte=date_to)
    return sessions.select_related("resource__branch", "opened_by", "closed_by")


def _refund_total_for_sales(sales):
    return SaleReturn.objects.filter(
        sale__in=sales, sale__status__in=NET_SALE_STATUSES
    ).aggregate(total=Sum("refund_amount"))["total"] or ZERO


def sale_summary(user, *, branch=None, date_from=None, date_to=None):
    sales = scoped_sales(user, branch, date_from, date_to)
    aggregate = sales.aggregate(
        gross=Sum("total"),
        count=Count("pk"),
        discounts=Sum("discount_amount"),
        taxes=Sum("tax_amount"),
    )
    gross = aggregate["gross"] or ZERO
    refunds = _refund_total_for_sales(sales)
    count = aggregate["count"] or 0
    return {
        "gross_sales": gross,
        "refunds": refunds,
        "net_sales": gross - refunds,
        "sale_count": count,
        "ticket_average": (gross / count if count else ZERO),
        "discounts": aggregate["discounts"] or ZERO,
        "taxes": aggregate["taxes"] or ZERO,
    }


def top_products(user, *, branch=None, date_from=None, date_to=None, limit=8):
    sales = scoped_sales(user, branch, date_from, date_to)
    totals = {}
    lines = SaleLine.objects.filter(sale__in=sales).values(
        "product_name", "sku", "quantity", "quantity_returned", "line_total"
    )
    for line in lines.iterator(chunk_size=1000):
        key = (line["product_name"], line["sku"])
        record = totals.setdefault(key, {"product_name": key[0], "sku": key[1], "units": 0, "revenue": ZERO})
        remaining = line["quantity"] - line["quantity_returned"]
        record["units"] += remaining
        if line["quantity"]:
            record["revenue"] += (
                line["line_total"] * Decimal(remaining) / Decimal(line["quantity"])
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return sorted(totals.values(), key=lambda row: (-row["units"], row["product_name"], row["sku"]))[:limit]


def sales_grouped(user, *, group_by, branch=None, date_from=None, date_to=None):
    sales = scoped_sales(user, branch, date_from, date_to)
    if group_by == "day":
        return list(sales.annotate(label=TruncDate("created_at")).values("label").annotate(
            count=Count("pk"), amount=Sum("total")
        ).order_by("label"))
    if group_by == "branch":
        return list(sales.values("branch__name").annotate(
            label=F("branch__name"), count=Count("pk"), amount=Sum("total")
        ).order_by("label"))
    if group_by == "seller":
        if not (user.is_superuser or user.has_perm("sales.view_all_sales")):
            raise PermissionDenied
        return list(sales.annotate(
            label=F("seller__username")
        ).values("seller_id", "label").annotate(
            count=Count("pk"), amount=Sum("total")
        ).order_by("label"))
    line_query = SaleLine.objects.filter(sale__in=sales)
    grouping = {
        "product": ("product_name",),
        "category": ("category_name",),
        "brand": ("brand_name",),
        "variant": ("size_name", "color_name"),
    }
    if group_by in grouping:
        keys = grouping[group_by]
        totals = {}
        values = line_query.values(
            *keys, "quantity", "quantity_returned", "line_total"
        ).iterator(chunk_size=1000)
        for line in values:
            key = tuple(line.get(name) for name in keys)
            record = totals.setdefault(key, {name: value for name, value in zip(keys, key)})
            remaining = line["quantity"] - line["quantity_returned"]
            record["units"] = record.get("units", 0) + remaining
            returned_revenue = (
                line["line_total"] * Decimal(line["quantity_returned"]) / Decimal(line["quantity"])
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if line["quantity"] else ZERO
            record["revenue"] = record.get("revenue", ZERO) + line["line_total"] - returned_revenue
        result = []
        for key, record in totals.items():
            record["label"] = " / ".join(str(value or "—") for value in key)
            result.append(record)
        return sorted(result, key=lambda row: (-row["units"], row["label"]))
    if group_by == "status":
        return list(scoped_all_sales(user, branch, date_from, date_to).values("status").annotate(
            label=F("status"), count=Count("pk"), amount=Sum("total")
        ).order_by("label"))
    if group_by == "payment":
        if not (user.is_superuser or user.has_perm("sales.view_all_sales") or user.has_perm("reports.view_payment_reports")):
            raise PermissionDenied
        return [
            {
                "label": row["method__name"],
                "count": row["count"],
                "amount": row["amount"],
                "affects_cash": row["method__affects_cash"],
            }
            for row in payment_grouped(user, branch=branch, date_from=date_from, date_to=date_to)
        ]
    return []


def payment_grouped(user, *, branch=None, date_from=None, date_to=None):
    sales = scoped_sales(user, branch, date_from, date_to)
    return list(SalePayment.objects.filter(sale__in=sales).values(
        "method__name", "method__affects_cash"
    ).annotate(count=Count("pk"), amount=Sum("amount")).order_by("method__name"))


def discount_report(user, *, branch=None, date_from=None, date_to=None):
    sales = scoped_sales(user, branch, date_from, date_to).filter(discount_amount__gt=0)
    return list(sales.values("number", "created_at", "branch__name", "seller__username", "discount_amount").order_by("-created_at"))


def stock_summary(user, *, branch=None):
    stock = scoped_stock(user, branch)
    aggregate = stock.aggregate(
        variants=Count("pk"),
        units=Sum("quantity"),
        low=Count("pk", filter=Q(minimum_quantity__gt=0, quantity__lte=F("minimum_quantity"))),
        out=Count("pk", filter=Q(quantity=0)),
        value=Sum(ExpressionWrapper(
            F("quantity") * F("average_cost"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )),
    )
    return {key: value or 0 for key, value in aggregate.items()}


def low_stock_rows(user, *, branch=None, out_only=False, limit=100):
    stock = scoped_stock(user, branch)
    if out_only:
        stock = stock.filter(quantity=0)
    else:
        stock = stock.filter(minimum_quantity__gt=0, quantity__lte=F("minimum_quantity"))
    return stock.order_by("quantity", "variant__product__name", "variant__sku")[:limit]


def inventory_grouped(user, *, branch=None, date_from=None, date_to=None):
    movements = scoped_inventory_movements(user, branch, date_from, date_to)
    return list(movements.values("operation__kind").annotate(
        label=F("operation__kind"), lines=Count("pk"), units=Sum("quantity_delta")
    ).order_by("label"))


def finance_summary(user, *, branch=None, date_from=None, date_to=None):
    base_operations = scoped_finance_operations(user, branch, date_from, date_to)
    pending_operations = base_operations.filter(status=FinanceOperation.Status.PENDING)
    if not user.is_superuser and not user.has_perm("finance.view_all_financeoperations"):
        may_review = any(user.has_perm(f"finance.{permission}") for permission in (
            "approve_financeoperation", "reject_financeoperation"
        ))
        if not may_review:
            pending_operations = pending_operations.filter(created_by=user)
    pending = pending_operations.count()
    operations = base_operations.filter(
        status=FinanceOperation.Status.POSTED
    )
    totals = operations.aggregate(
        income=Sum("amount", filter=Q(kind=FinanceOperation.Kind.INCOME)),
        expense=Sum("amount", filter=Q(kind=FinanceOperation.Kind.EXPENSE)),
        pending=Count("pk", filter=Q(status=FinanceOperation.Status.PENDING)),
    )
    return {**{key: value or 0 for key, value in totals.items()}, "pending": pending}


def finance_grouped(user, *, group_by, branch=None, date_from=None, date_to=None):
    operations = scoped_finance_operations(user, branch, date_from, date_to).filter(
        status=FinanceOperation.Status.POSTED
    )
    if group_by == "category":
        key = "category__name"
    elif group_by == "branch":
        key = "branch__name"
    else:
        key = "kind"
    return list(operations.values(key, "kind").annotate(
        label=F(key), count=Count("pk"), amount=Sum("amount")
    ).order_by("label"))


def cash_summary(user, *, branch=None, date_from=None, date_to=None):
    sessions = scoped_cash_sessions(user, branch, date_from, date_to)
    closed = sessions.filter(status=CashSession.Status.CLOSED)
    open_count = sessions.filter(status=CashSession.Status.OPEN).count()
    return {
        "open_count": open_count,
        "closed_count": closed.count(),
        "cash_sales": closed.aggregate(total=Sum("cash_sales_total"))["total"] or ZERO,
        "noncash": closed.aggregate(total=Sum("noncash_collections_total"))["total"] or ZERO,
        "income": closed.aggregate(total=Sum("additional_income_total"))["total"] or ZERO,
        "expenses": closed.aggregate(total=Sum("expense_total"))["total"] or ZERO,
        "withdrawals": closed.aggregate(total=Sum("withdrawal_total"))["total"] or ZERO,
        "refunds": closed.aggregate(total=Sum("refund_total"))["total"] or ZERO,
        "differences": closed.aggregate(total=Sum("difference"))["total"] or ZERO,
        "sessions": closed.order_by("-closed_at")[:30],
    }


def branch_sales_comparison(user, *, date_from=None, date_to=None):
    sales = scoped_sales(user, None, date_from, date_to)
    rows = list(sales.values("branch_id", "branch__name").annotate(
        label=F("branch__name"), count=Count("pk"), gross=Sum("total")
    ).order_by("-gross", "label"))
    refund_totals = {
        row["sale__branch_id"]: row["amount"] or ZERO
        for row in SaleReturn.objects.filter(
            sale__in=sales, sale__status__in=NET_SALE_STATUSES
        ).values("sale__branch_id").annotate(amount=Sum("refund_amount"))
    }
    for row in rows:
        row["refunds"] = refund_totals.get(row["branch_id"], ZERO)
        row["net"] = (row["gross"] or ZERO) - row["refunds"]
    counts = Sale.objects.filter(
        branch_id__in=[row["branch_id"] for row in rows],
        status=Sale.Status.CANCELLED,
        branch__in=authorized_branch_queryset(user),
    )
    if date_from:
        counts = counts.filter(created_at__date__gte=date_from)
    if date_to:
        counts = counts.filter(created_at__date__lte=date_to)
    if not user.is_superuser and not user.has_perm("sales.view_all_sales"):
        counts = counts.filter(seller=user)
    cancelled_by_branch = {
        row["branch_id"]: row["count"]
        for row in counts.values("branch_id").annotate(count=Count("pk"))
    }
    for row in rows:
        row["cancelled_count"] = cancelled_by_branch.get(row["branch_id"], 0)
    return sorted(rows, key=lambda row: (-row["net"], row["label"]))
