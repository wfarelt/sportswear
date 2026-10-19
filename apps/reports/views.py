from datetime import timedelta
from decimal import Decimal
import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.cash.models import CashMovement, CashSession
from apps.finance.models import FinanceOperation
from apps.inventory.models import InventoryOperation, StockLevel, StockTransfer
from apps.products.models import Category
from apps.sales.models import Sale, SaleLine, SalePayment, SaleReturn

from .exports import export_csv, export_pdf, export_xlsx
from .forms import ReportFilterForm, authorized_branches
from . import selectors

ZERO = Decimal("0.00")


def _allowed(user, permission):
    return user.is_superuser or user.has_perm(permission)


def _require(user, permission):
    if not _allowed(user, permission):
        raise PermissionDenied


def _filters(request):
    form = ReportFilterForm(request.GET or None, user=request.user)
    if form.is_bound and not form.is_valid():
        today = timezone.localdate()
        return form, None, today - timedelta(days=29), today
    if form.is_valid():
        start, end = form.date_range()
        return form, form.cleaned_data.get("branch"), start, end
    today = timezone.localdate()
    return form, None, today - timedelta(days=29), today


def _period_label(start, end):
    if start == end:
        return start.strftime("%d/%m/%Y")
    return f"{start:%d/%m/%Y} – {end:%d/%m/%Y}"


def _dashboard_context(request, form, branch, start, end):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    branch_filter = branch
    can_sales = _allowed(request.user, "reports.view_sales_reports") and _allowed(request.user, "sales.view_sale")
    sales_today = selectors.sale_summary(request.user, branch=branch_filter, date_from=today, date_to=today) if can_sales else None
    sales_month = selectors.sale_summary(request.user, branch=branch_filter, date_from=month_start, date_to=today) if can_sales else None
    context = {
        "form": form,
        "branches": authorized_branches(request.user),
        "selected_branch": branch,
        "date_from": start,
        "date_to": end,
        "period_label": _period_label(start, end),
        "sales_today": sales_today,
        "sales_month": sales_month,
        "top_products": selectors.top_products(
            request.user, branch=branch_filter, date_from=month_start, date_to=today, limit=5
        ) if can_sales else [],
        "branch_comparison": selectors.branch_sales_comparison(
            request.user, date_from=month_start, date_to=today
        ) if branch is None and can_sales else [],
        "can_sales": can_sales,
        "can_inventory": _allowed(request.user, "reports.view_inventory_reports") and _allowed(request.user, "inventory.view_stocklevel"),
        "can_finance": _allowed(request.user, "reports.view_finance_reports") and (
            _allowed(request.user, "finance.view_financeoperation")
            or _allowed(request.user, "finance.view_all_financeoperations")
        ),
        "can_review_finance": _allowed(request.user, "reports.view_finance_reports") and (
            _allowed(request.user, "finance.approve_financeoperation")
            or _allowed(request.user, "finance.reject_financeoperation")
        ),
        "can_cash": _allowed(request.user, "reports.view_finance_reports") and _allowed(request.user, "cash.view_cashsession"),
    }
    if context["can_inventory"]:
        context["stock_summary"] = selectors.stock_summary(request.user, branch=branch_filter)
        context["low_stock"] = selectors.low_stock_rows(request.user, branch=branch_filter, limit=6)
        context["out_of_stock"] = selectors.low_stock_rows(request.user, branch=branch_filter, out_only=True, limit=6)
    if context["can_finance"]:
        context["finance_summary"] = selectors.finance_summary(
            request.user, branch=branch_filter, date_from=month_start, date_to=today
        )
    elif context["can_review_finance"]:
        context["pending_finance_count"] = selectors.scoped_finance_operations(
            request.user, branch_filter, month_start, today
        ).filter(status=FinanceOperation.Status.PENDING).count()
    if context["can_cash"]:
        context["cash_summary"] = selectors.cash_summary(
            request.user, branch=branch_filter, date_from=month_start, date_to=today
        )
    return context


@login_required
def dashboard(request):
    _require(request.user, "reports.view_reports")
    form, branch, start, end = _filters(request)
    context = _dashboard_context(request, form, branch, start, end)
    context["has_dashboard_data"] = True
    return render(request, "core/dashboard.html", context)


@login_required
def sales_report(request):
    _require(request.user, "reports.view_sales_reports")
    _require(request.user, "sales.view_sale")
    form, branch, start, end = _filters(request)
    group_by = form.cleaned_data.get("group_by") or "day" if form.is_valid() else "day"
    if group_by == "seller" and not (request.user.is_superuser or request.user.has_perm("sales.view_all_sales")):
        raise PermissionDenied
    summary = selectors.sale_summary(request.user, branch=branch, date_from=start, date_to=end)
    grouped = selectors.sales_grouped(
        request.user, group_by=group_by, branch=branch, date_from=start, date_to=end
    )
    can_view_payment_summary = _allowed(request.user, "sales.view_all_sales") or _allowed(request.user, "reports.view_payment_reports")
    payments = selectors.payment_grouped(request.user, branch=branch, date_from=start, date_to=end) if can_view_payment_summary else []
    discounts = selectors.discount_report(request.user, branch=branch, date_from=start, date_to=end)[:100]
    all_sales = selectors.scoped_all_sales(request.user, branch, start, end)
    returns = SaleReturn.objects.filter(sale__in=all_sales).exclude(
        sale__status=Sale.Status.CANCELLED
    ).select_related("sale", "refund_method", "created_by").order_by("-created_at")[:100]
    cancelled = all_sales.filter(status=Sale.Status.CANCELLED).select_related("seller").order_by("-created_at")[:100]
    export_rows = [{
        "label": row.get("label", row.get("status", "")),
        "count": row.get("count", 0),
        "units": row.get("units", 0),
        "amount": row.get("amount", row.get("revenue", ZERO)),
    } for row in grouped]
    return render(request, "reports/sales_report.html", {
        "form": form,
        "branches": authorized_branches(request.user),
        "selected_branch": branch,
        "date_from": start,
        "date_to": end,
        "period_label": _period_label(start, end),
        "summary": summary,
        "group_by": group_by,
        "grouped": grouped,
        "payments": payments,
        "can_view_payment_summary": can_view_payment_summary,
        "discounts": discounts,
        "returns": returns,
        "cancelled": cancelled,
        "export_rows": export_rows,
    })


@login_required
def inventory_report(request):
    _require(request.user, "reports.view_inventory_reports")
    _require(request.user, "inventory.view_stocklevel")
    form, branch, start, end = _filters(request)
    stock = selectors.scoped_stock(request.user, branch)
    status = request.GET.get("status", "all")
    if status == "low":
        stock = stock.filter(minimum_quantity__gt=0, quantity__lte=F("minimum_quantity"))
    elif status == "out":
        stock = stock.filter(quantity=0)
    query = request.GET.get("q", "").strip()
    if query:
        stock = stock.filter(
            Q(variant__sku__icontains=query)
            | Q(variant__barcode__icontains=query)
            | Q(variant__product__name__icontains=query)
            | Q(variant__product__internal_code__icontains=query)
        )
    category = request.GET.get("category", "")
    if category.isdigit():
        stock = stock.filter(variant__product__category_id=category)
    page_obj = Paginator(
        stock.order_by("branch__name", "variant__product__name", "variant__sku"), 40
    ).get_page(request.GET.get("page"))
    summary = selectors.stock_summary(request.user, branch=branch)
    movements = selectors.scoped_inventory_movements(
        request.user, branch, start, end
    ).order_by("-created_at")[:100]
    transfers = selectors.scoped_transfers(
        request.user, branch, start, end
    ).order_by("-created_at")[:100]
    return render(request, "reports/inventory_report.html", {
        "form": form,
        "branches": authorized_branches(request.user),
        "selected_branch": branch,
        "date_from": start,
        "date_to": end,
        "summary": summary,
        "stock_levels": page_obj.object_list,
        "page_obj": page_obj,
        "status": status,
        "query": query,
        "category": category,
        "categories": Category.objects.filter(is_active=True),
        "movements": movements,
        "transfers": transfers,
    })


@login_required
def finance_report(request):
    _require(request.user, "reports.view_finance_reports")
    _require(request.user, "finance.view_financeoperation")
    form, branch, start, end = _filters(request)
    summary = selectors.finance_summary(request.user, branch=branch, date_from=start, date_to=end)
    operations = selectors.scoped_finance_operations(request.user, branch, start, end).filter(
        status=FinanceOperation.Status.POSTED
    )
    by_category = selectors.finance_grouped(
        request.user, group_by="category", branch=branch, date_from=start, date_to=end
    )
    cash_permission = _allowed(request.user, "cash.view_cashsession")
    cash_summary = selectors.cash_summary(request.user, branch=branch, date_from=start, date_to=end) if cash_permission else None
    payment_summary = selectors.payment_grouped(request.user, branch=branch, date_from=start, date_to=end) if (_allowed(request.user, "reports.view_sales_reports") and _allowed(request.user, "sales.view_sale")) else []
    page_obj = Paginator(
        operations.order_by("-operation_date", "-pk"), 30
    ).get_page(request.GET.get("page"))
    export_rows = [{
        "number": operation.number,
        "date": operation.operation_date,
        "kind": operation.get_kind_display(),
        "category": operation.category.name,
        "concept": operation.concept,
        "branch": operation.branch.name if operation.branch_id else "General",
        "account": operation.account.name,
        "amount": operation.amount,
        "status": operation.get_status_display(),
    } for operation in page_obj.object_list]
    return render(request, "reports/finance_report.html", {
        "form": form,
        "branches": authorized_branches(request.user),
        "selected_branch": branch,
        "date_from": start,
        "date_to": end,
        "summary": summary,
        "operations": page_obj.object_list,
        "page_obj": page_obj,
        "by_category": by_category,
        "cash_summary": cash_summary,
        "payment_summary": payment_summary,
        "can_view_cash": cash_permission,
        "export_rows": export_rows,
    })


def _export_data(user, report_type, branch, start, end, query_params):
    if report_type == "sales":
        _require(user, "reports.view_sales_reports")
        _require(user, "sales.view_sale")
        group_by = query_params.get("group_by", "day")
        if group_by == "seller" and not (user.is_superuser or user.has_perm("sales.view_all_sales")):
            raise PermissionDenied
        if group_by == "payment" and not (user.is_superuser or user.has_perm("sales.view_all_sales") or user.has_perm("reports.view_payment_reports")):
            raise PermissionDenied
        grouped = selectors.sales_grouped(user, group_by=group_by, branch=branch, date_from=start, date_to=end)
        rows = [{
            "label": item.get("label", ""),
            "count": item.get("count", 0),
            "units": item.get("units", 0),
            "amount": item.get("amount", item.get("revenue", ZERO)),
        } for item in grouped]
        return "Ventas", [("label", "Grupo"), ("count", "Ventas"), ("units", "Unidades"), ("amount", "Importe")], rows
    if report_type == "inventory":
        _require(user, "reports.view_inventory_reports")
        _require(user, "inventory.view_stocklevel")
        stock = selectors.scoped_stock(user, branch).order_by("branch__name", "variant__product__name", "variant__sku")
        rows = [{
            "branch": item.branch.name,
            "product": item.variant.product.name,
            "sku": item.variant.sku,
            "size": item.variant.size.name if item.variant.size_id else "—",
            "color": item.variant.color.name if item.variant.color_id else "—",
            "quantity": item.quantity,
            "minimum": item.minimum_quantity,
            "average_cost": item.average_cost,
            "value": item.inventory_value,
        } for item in stock[:5000]]
        return "Inventario", [("branch", "Sucursal"), ("product", "Producto"), ("sku", "SKU"), ("size", "Talla"), ("color", "Color"), ("quantity", "Existencias"), ("minimum", "Mínimo"), ("average_cost", "Costo promedio"), ("value", "Valorización")], rows
    if report_type == "finance":
        _require(user, "reports.view_finance_reports")
        _require(user, "finance.view_financeoperation")
        operations = selectors.scoped_finance_operations(user, branch, start, end).filter(
            status=FinanceOperation.Status.POSTED
        ).order_by("-operation_date", "-pk")[:5000]
        rows = [{
            "number": operation.number,
            "date": operation.operation_date,
            "kind": operation.get_kind_display(),
            "category": operation.category.name,
            "concept": operation.concept,
            "branch": operation.branch.name if operation.branch_id else "General",
            "account": operation.account.name,
            "amount": operation.amount,
            "status": operation.get_status_display(),
        } for operation in operations]
        return "Finanzas", [("number", "N.º operación"), ("date", "Fecha"), ("kind", "Tipo"), ("category", "Categoría"), ("concept", "Concepto"), ("branch", "Sucursal"), ("account", "Cuenta"), ("amount", "Importe"), ("status", "Estado")], rows
    raise PermissionDenied


@login_required
@require_GET
def export_report(request, report_type, file_format):
    if file_format not in {"csv", "xlsx", "pdf"}:
        raise Http404
    form, branch, start, end = _filters(request)
    if not form.is_valid():
        raise Http404("Filtros no válidos.")
    title, columns, rows = _export_data(
        request.user, report_type, branch, start, end, request.GET
    )
    title = f"Reporte de {title} · {start:%d/%m/%Y} – {end:%d/%m/%Y}"
    if file_format == "csv":
        return export_csv(report_type, title, columns, rows)
    if file_format == "xlsx":
        return export_xlsx(report_type, title, columns, rows)
    return export_pdf(report_type, title, columns, rows)
