import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.branches.models import Branch
from apps.cash.models import CashResource
from apps.cash.services import open_session
from apps.customers.models import Customer
from apps.finance.models import FinanceAccount, FinanceApprovalLimit, FinanceCategory, FinanceOperation
from apps.finance.services import create_operation
from apps.inventory.models import StockLevel
from apps.products.models import Brand, Category, Product, ProductVariant, Size
from apps.sales.models import PaymentMethod, Sale, SaleLine
from apps.sales.services import confirm_sale, process_return
from apps.users.models import User, UserBranchAccess
from apps.reports import selectors


class ReportsTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            username="manager", password="Secure-reports-123", role=User.Role.MANAGER
        )
        self.seller = User.objects.create_user(
            username="seller", password="Secure-reports-123", role=User.Role.SELLER
        )
        self.branch = Branch.objects.create(name="Central", code="central")
        self.other_branch = Branch.objects.create(name="Norte", code="norte")
        UserBranchAccess.objects.create(user=self.manager, branch=self.branch)
        UserBranchAccess.objects.create(user=self.seller, branch=self.branch)
        self.category = Category.objects.create(name="Poleras")
        self.brand = Brand.objects.create(name="Marca Uno")
        self.product = Product.objects.create(
            internal_code="POL-1", name="Polera", category=self.category, brand=self.brand,
            cost=Decimal("10.00"), sale_price=Decimal("20.00"), tax_rate=0,
        )
        self.size = Size.objects.create(name="M")
        self.variant = ProductVariant.objects.create(product=self.product, sku="POL-1-M", size=self.size)
        StockLevel.objects.create(branch=self.branch, variant=self.variant, quantity=6, minimum_quantity=2, average_cost=Decimal("10.00"))
        StockLevel.objects.create(branch=self.other_branch, variant=self.variant, quantity=0, minimum_quantity=1, average_cost=Decimal("10.00"))
        self.cash_method = PaymentMethod.objects.get(code="cash")
        self.qr_method = PaymentMethod.objects.get(code="qr")
        self.customer = Customer.objects.create(full_name="Cliente reporte")
        self.report_permissions(self.manager,
            "view_reports", "view_sales_reports", "view_inventory_reports", "view_finance_reports", "view_payment_reports",
        )
        self.manager.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="inventory", codename="view_stocklevel"
        ))
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="sales", codename="view_all_sales"
        ))
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="reports", codename="view_payment_reports"
        ))
        self.manager.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="sales", codename__in=("view_sale", "process_salereturn")
        ))
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="inventory", codename="view_stocklevel"
        ))
        self.manager.__dict__.pop("_perm_cache", None)
        self.manager.__dict__.pop("_user_perm_cache", None)
        self.report_permissions(self.seller, "view_reports", "view_sales_reports")
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label="sales", codename="view_sale"
        ))
        self.seller.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="sales", codename__in=("apply_discount", "process_salereturn")
        ))
        self.seller.__dict__.pop("_perm_cache", None)
        self.seller.__dict__.pop("_user_perm_cache", None)
        self.cash_session = None
        self.sales = []
        self.sale_sessions = {}
        self._make_sale(branch=self.branch, user=self.seller, quantity=2, price="20.00", cash=Decimal("20.00"), qr=Decimal("20.00"))
        self._make_sale(branch=self.branch, user=self.seller, quantity=1, price="15.00", cash=Decimal("15.00"), qr=Decimal("0.00"))
        other_admin = User.objects.create_user(
            username="admin-other", password="Secure-reports-123", role=User.Role.ADMIN
        )
        self._make_sale(branch=self.other_branch, user=other_admin, quantity=1, price="100.00", cash=Decimal("100.00"), qr=Decimal("0.00"))

    def report_permissions(self, user, *codenames):
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="reports", codename__in=codenames
        ))
        user.__dict__.pop("_perm_cache", None)
        user.__dict__.pop("_user_perm_cache", None)

    def _make_sale(self, *, branch, user, quantity, price, cash, qr):
        resource = CashResource.objects.create(
            branch=branch, code=f"register-{user.pk}-{len(self.sales)}", name=f"Caja {len(self.sales)}"
        )
        cash_perms = Permission.objects.filter(content_type__app_label="cash", codename__in=("open_cashsession", "add_cashmovement", "close_cashsession"))
        user.user_permissions.add(*cash_perms)
        user.__dict__.pop("_perm_cache", None)
        session = open_session(resource_id=resource.pk, opening_balance=0, user=user)
        self.cash_session = session
        self.product.sale_price = Decimal(price)
        self.product.save()
        stock = StockLevel.objects.get(branch=branch, variant=self.variant)
        if stock.quantity < quantity:
            stock.quantity = quantity
            stock.save()
        total = Decimal(quantity) * Decimal(price)
        payment_data = []
        if cash:
            payment_data.append({"method_id": self.cash_method.pk, "amount": cash})
        if qr:
            payment_data.append({"method_id": self.qr_method.pk, "amount": qr})
        sale = confirm_sale(
            branch_id=branch.pk, customer_id=self.customer.pk,
            lines=[{"variant_id": self.variant.pk, "quantity": quantity}],
            discount_amount=Decimal("0.00"), payments=payment_data,
            user=user, idempotency_key=uuid.uuid4(), cash_session_id=session.pk,
        )
        self.sales.append(sale)
        self.sale_sessions[sale.pk] = session
        return sale, session

    def test_dashboard_calculates_live_sales_and_keeps_branch_isolation(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DATOS REGISTRADOS")
        self.assertNotContains(response, "Norte")
        self.assertEqual(response.context["sales_month"]["sale_count"], 2)
        self.assertEqual(response.context["sales_month"]["gross_sales"], Decimal("55.00"))
        self.assertTrue(response.context["can_inventory"])

    def test_dashboard_sensitive_widgets_are_not_queried_or_shown_without_specific_permission(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_finance"])
        self.assertNotIn("finance_summary", response.context)
        self.assertNotContains(response, "Ingresos confirmados")

    def test_sales_report_supports_category_brand_variant_and_payment_grouping(self):
        self.client.force_login(self.manager)
        for group_by, expected in (("category", "Poleras"), ("brand", "Marca Uno"), ("variant", "M / —"), ("payment", "Efectivo")):
            with self.subTest(group_by=group_by):
                response = self.client.get(reverse("reports:sales"), {"group_by": group_by})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected)

    def test_net_sales_and_product_revenue_are_reduced_by_partial_return(self):
        sale = self.sales[0]
        line = sale.lines.get()
        returned = process_return(
            sale_id=sale.pk,
            lines=[{"sale_line_id": line.pk, "quantity": 1}],
            reason="Talla", refund_method_id=self.cash_method.pk,
            user=self.seller,
            idempotency_key=uuid.uuid4(),
            cash_session_id=self.sale_sessions[sale.pk].pk,
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse("reports:sales"), {"group_by": "product"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["refunds"], returned.refund_amount)
        self.assertEqual(response.context["summary"]["net_sales"], Decimal("35.00"))
        self.assertEqual(response.context["grouped"][0]["units"], 2)
        self.assertEqual(response.context["grouped"][0]["revenue"], Decimal("35.00"))

    def test_seller_grouping_is_blocked_without_view_all_sales(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("reports:sales"), {"group_by": "seller"})
        self.assertEqual(response.status_code, 403)

    def test_payment_report_requires_explicit_sensitive_payment_permission(self):
        self.client.force_login(self.seller)
        response = self.client.get(reverse("reports:sales"), {"group_by": "payment"})
        self.assertEqual(response.status_code, 403)
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label="reports", codename="view_payment_reports"
        ))
        self.seller.__dict__.pop("_perm_cache", None)
        response = self.client.get(reverse("reports:sales"), {"group_by": "payment"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["payments"]), 2)

    def test_dashboard_branch_comparison_uses_net_sales(self):
        sale = self.sales[0]
        line = sale.lines.get()
        process_return(
            sale_id=sale.pk,
            lines=[{"sale_line_id": line.pk, "quantity": 1}],
            reason="Devolución de comparación",
            refund_method_id=self.cash_method.pk,
            user=self.seller,
            idempotency_key=uuid.uuid4(),
            cash_session_id=self.sale_sessions[sale.pk].pk,
        )
        manager = User.objects.create_user(
            username="global-admin", password="Secure-reports-123", role=User.Role.ADMIN
        )
        manager.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="reports", codename__in=("view_reports", "view_sales_reports")
        ))
        manager.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="sales", codename__in=("view_sale", "view_all_sales")
        ))
        manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="reports", codename="view_payment_reports"
        ))
        manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="inventory", codename="view_stocklevel"
        ))
        manager.__dict__.pop("_perm_cache", None)
        self.client.force_login(manager)
        response = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(response.status_code, 200)
        comparison = {item["label"]: item for item in response.context["branch_comparison"]}
        self.assertEqual(comparison["Central"]["net"], Decimal("35.00"))
        self.assertEqual(comparison["Norte"]["net"], Decimal("100.00"))

    def test_dashboard_reviewer_without_finance_read_permission_sees_only_pending_count(self):
        reviewer = User.objects.create_user(
            username="reviewer", password="Secure-reports-123", role=User.Role.MANAGER
        )
        UserBranchAccess.objects.create(user=reviewer, branch=self.branch)
        reviewer.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="reports", codename__in=("view_reports", "view_finance_reports")
        ))
        reviewer.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="approve_financeoperation"
        ))
        reviewer.__dict__.pop("_perm_cache", None)
        reviewer.__dict__.pop("_user_perm_cache", None)
        self.client.force_login(reviewer)
        response = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_review_finance"])
        self.assertNotIn("finance_summary", response.context)

    def test_inventory_report_is_limited_to_authorized_branch_even_on_export(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("reports:inventory"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["units"], 3)
        period = {"date_from": timezone.localdate().isoformat(), "date_to": timezone.localdate().isoformat()}
        export = self.client.get(reverse("reports:export", args=["inventory", "csv"]), period)
        self.assertEqual(export.status_code, 200)
        self.assertIn(b"Central", export.content)
        self.assertNotIn(b"Norte", export.content)

    def test_finance_report_does_not_leak_sensitive_data_without_finance_permission(self):
        self.client.force_login(self.seller)
        period = {"date_from": timezone.localdate().isoformat(), "date_to": timezone.localdate().isoformat()}
        response = self.client.get(reverse("reports:finance"), period)
        self.assertEqual(response.status_code, 403)

    def test_pending_finance_summary_only_counts_operations_visible_to_viewer(self):
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="view_financeoperation"
        ))
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="view_all_financeoperations"
        ))
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="add_financeoperation"
        ))
        self.manager.__dict__.pop("_perm_cache", None)
        self.client.force_login(self.manager)
        category = FinanceCategory.objects.create(name="Pendiente", kind=FinanceCategory.Kind.INCOME)
        account = FinanceAccount.objects.create(name="Banco", kind=FinanceAccount.Kind.BANK, branch=self.branch)
        FinanceApprovalLimit.objects.create(
            user=self.seller, branch=self.branch, maximum_amount=Decimal("0.00")
        )
        self.seller.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="add_financeoperation"
        ))
        self.seller.__dict__.pop("_perm_cache", None)
        self.seller.__dict__.pop("_user_perm_cache", None)
        pending = create_operation(
            kind=FinanceOperation.Kind.INCOME, category_id=category.pk,
            branch_id=self.branch.pk, account_id=account.pk, concept="Pendiente de revisar",
            amount=Decimal("20.00"), operation_date=timezone.localdate(), notes="",
            user=self.seller, idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(pending.status, FinanceOperation.Status.PENDING)
        response = self.client.get(reverse("reports:finance"), {
            "date_from": timezone.localdate().isoformat(),
            "date_to": timezone.localdate().isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["pending"], 1)

    def test_finance_report_and_csv_export_scope_to_branch_and_posted_operations(self):
        income_category = FinanceCategory.objects.create(name="Aporte", kind=FinanceCategory.Kind.INCOME)
        account = FinanceAccount.objects.create(name="Banco", kind=FinanceAccount.Kind.BANK, branch=self.branch)
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="view_all_financeoperations"
        ))
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="view_financeoperation"
        ))
        FinanceApprovalLimit.objects.create(
            user=self.manager, branch=self.branch, maximum_amount=Decimal("1000.00")
        )
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="add_financeoperation"
        ))
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="use_general_finance_accounts"
        ))
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="finance", codename="view_general_finance_accounts"
        ))
        self.manager.__dict__.pop("_perm_cache", None)
        self.client.force_login(self.manager)
        create_operation(
            kind=FinanceOperation.Kind.INCOME,
            category_id=income_category.pk, branch_id=self.branch.pk, account_id=account.pk,
            concept="Aporte", amount=Decimal("30.00"), operation_date=timezone.localdate(),
            notes="", user=self.manager, idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(
            FinanceOperation.objects.get().status,
            FinanceOperation.Status.POSTED,
        )
        period = {"date_from": timezone.localdate().isoformat(), "date_to": timezone.localdate().isoformat()}
        response = self.client.get(reverse("reports:finance"), period)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["income"], Decimal("30.00"))
        export = self.client.get(reverse("reports:export", args=["finance", "csv"]), period)
        self.assertIn(b"F-00000001", export.content)

    def test_excel_and_pdf_exports_have_expected_headers(self):
        self.manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="inventory", codename="view_stocklevel"
        ))
        self.manager.__dict__.pop("_perm_cache", None)
        self.client.force_login(self.manager)
        for file_format, content_type in (("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), ("pdf", "application/pdf")):
            with self.subTest(file_format=file_format):
                period = {"date_from": timezone.localdate().isoformat(), "date_to": timezone.localdate().isoformat()}
                response = self.client.get(reverse("reports:export", args=["inventory", file_format]), period)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], content_type)
                self.assertIn(b"attachment; filename=", response["Content-Disposition"].encode())

    def test_product_aggregations_are_net_of_returned_units(self):
        sale = self.sales[0]
        line = sale.lines.get()
        line.quantity_returned = 1
        line.save(update_fields=["quantity_returned"])
        expected_units = sum(
            item.quantity - item.quantity_returned
            for item in SaleLine.objects.filter(sale__branch=self.branch)
        )
        today = timezone.localdate()
        summary = selectors.top_products(
            self.manager, branch=self.branch, date_from=today, date_to=today,
        )
        self.assertEqual(summary[0]["units"], expected_units)
        grouped = selectors.sales_grouped(
            self.manager, group_by="product", branch=self.branch,
            date_from=today, date_to=today,
        )
        self.assertEqual(grouped[0]["units"], expected_units)

    def test_seller_dashboard_shows_own_real_sales_without_consolidated_report_permission(self):
        self.seller.user_permissions.remove(*Permission.objects.filter(
            content_type__app_label="reports"
        ))
        self.seller.__dict__.pop("_perm_cache", None)
        self.seller.__dict__.pop("_user_perm_cache", None)
        self.client.force_login(self.seller)

        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DATOS REGISTRADOS")
        self.assertContains(response, "Ventas netas · hoy")
        self.assertContains(response, "Bs 55,00")
        self.assertContains(response, self.product.name)
        self.assertNotContains(response, "Vista de demostración")
        self.assertNotContains(response, "EJEMPLO · NO REAL")

    def test_seller_without_report_permissions_cannot_open_consolidated_reports(self):
        self.seller.user_permissions.remove(*Permission.objects.filter(
            content_type__app_label="reports"
        ))
        self.seller.__dict__.pop("_perm_cache", None)
        self.seller.__dict__.pop("_user_perm_cache", None)
        self.client.force_login(self.seller)

        response = self.client.get(reverse("reports:dashboard"))

        self.assertEqual(response.status_code, 403)
