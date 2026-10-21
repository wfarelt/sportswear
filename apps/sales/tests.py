import json
import uuid
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.branches.models import Branch
from apps.cash.models import CashResource, CashSession, CashMovement
from apps.cash.services import close_session, open_session
from apps.customers.models import Customer
from apps.inventory.models import InventoryMovement, InventoryOperation, StockLevel
from apps.products.models import Brand, Category, Product, ProductVariant, Size
from apps.users.models import User, UserBranchAccess

from .models import PaymentMethod, Sale, SaleLine, SalePayment, SaleReturn
from .services import cancel_sale, confirm_sale, process_return


class SalesTestDataMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            username="seller", password="Secure-sale-password-123", role=User.Role.SELLER
        )
        self.admin = User.objects.create_user(
            username="manager", password="Secure-sale-password-123", role=User.Role.ADMIN
        )
        self.branch = Branch.objects.create(name="Central", code="central")
        UserBranchAccess.objects.create(user=self.user, branch=self.branch)
        self.category = Category.objects.create(name="Poleras")
        self.brand = Brand.objects.create(name="Marca Uno")
        self.product = Product.objects.create(
            internal_code="POL-001", name="Polera deportiva", category=self.category,
            brand=self.brand, cost=Decimal("10.00"), sale_price=Decimal("20.00"),
            tax_rate=Decimal("13.00"),
        )
        self.size = Size.objects.create(name="M")
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="POL-001-M", size=self.size,
        )
        self.stock = StockLevel.objects.create(
            branch=self.branch, variant=self.variant, quantity=10,
            average_cost=Decimal("10.00"),
        )
        self.cash = PaymentMethod.objects.get(code="cash")
        self.qr = PaymentMethod.objects.get(code="qr")
        self.customer = Customer.objects.create(full_name="Cliente Ejemplo")
        self.grant(self.user, "process_salereturn")
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="cash", codename__in=("open_cashsession", "add_cashmovement")
        ))
        self.cash_resource = CashResource.objects.create(
            branch=self.branch, code="main", name="Caja Principal"
        )
        self.cash_session = open_session(
            resource_id=self.cash_resource.pk, opening_balance=Decimal("50.00"),
            user=self.user, notes="Apertura de prueba",
        )

    def grant(self, user, *codenames):
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="sales", codename__in=codenames
        ))
        for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            user.__dict__.pop(cache_name, None)

    def confirm(self, *, quantity=1, amount=None, customer_id=None, discount="0.00", payments=None, key=None, user=None):
        total = Decimal(quantity) * Decimal("20.00")
        tax = (Decimal("20.00") * Decimal("0.13")).quantize(Decimal("0.01")) * quantity
        total += tax
        payment_rows = payments if payments is not None else [{"method_id": self.cash.pk, "amount": amount or total}]
        return confirm_sale(
            branch_id=self.branch.pk,
            customer_id=customer_id,
            lines=[{"variant_id": self.variant.pk, "quantity": quantity}],
            discount_amount=Decimal(discount),
            payments=payment_rows,
            user=user or self.user,
            idempotency_key=key or uuid.uuid4(),
            cash_session_id=self.cash_session.pk,
        )


class SaleServiceTests(SalesTestDataMixin, TestCase):
    def test_confirmed_sale_records_price_snapshots_payment_and_stock_movement(self):
        sale = self.confirm(customer_id=self.customer.pk)
        self.stock.refresh_from_db()
        line = sale.lines.get()
        self.assertEqual(sale.number, "V-00000001")
        self.assertEqual(sale.status, Sale.Status.CONFIRMED)
        self.assertEqual(sale.subtotal, Decimal("20.00"))
        self.assertEqual(sale.tax_amount, Decimal("2.60"))
        self.assertEqual(sale.total, Decimal("22.60"))
        self.assertEqual(sale.paid_total, Decimal("22.60"))
        self.assertEqual(self.stock.quantity, 9)
        self.assertEqual(line.unit_price, Decimal("20.00"))
        self.assertEqual(line.unit_cost, Decimal("10.00"))
        self.assertEqual(line.product_name, "Polera deportiva")
        self.assertEqual(InventoryMovement.objects.get().quantity_delta, -1)
        operation = InventoryOperation.objects.get(kind=InventoryOperation.Kind.SALE_OUT)
        self.assertEqual(operation.source_reference, sale.number)

        self.product.sale_price = Decimal("40.00")
        self.product.save()
        line.refresh_from_db()
        self.assertEqual(line.unit_price, Decimal("20.00"))

    def test_mixed_payments_must_reconcile_and_methods_keep_cash_semantics(self):
        sale = self.confirm(payments=[
            {"method_id": self.cash.pk, "amount": Decimal("10.00")},
            {"method_id": self.qr.pk, "amount": Decimal("12.60")},
        ])
        self.assertEqual(sale.payments.count(), 2)
        self.assertTrue(sale.payments.get(method=self.cash).method.affects_cash)
        self.assertFalse(sale.payments.get(method=self.qr).method.affects_cash)
        with self.assertRaises(ValidationError):
            self.confirm(payments=[{"method_id": self.cash.pk, "amount": Decimal("1.00")}])
        self.assertEqual(Sale.objects.count(), 1)

    def test_sale_rejects_insufficient_stock_and_does_not_write_partial_records(self):
        with self.assertRaises(ValidationError):
            self.confirm(quantity=11)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(InventoryOperation.objects.count(), 0)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 10)

    def test_sale_retry_is_idempotent_but_same_key_with_different_payload_is_rejected(self):
        key = uuid.uuid4()
        sale = self.confirm(key=key)
        replay = self.confirm(key=key)
        self.assertEqual(replay.pk, sale.pk)
        self.assertEqual(Sale.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 9)
        with self.assertRaises(ValidationError):
            self.confirm(quantity=2, key=key)

    def test_discount_requires_permission_and_is_allocated_and_taxed_server_side(self):
        with self.assertRaises(PermissionDenied):
            self.confirm(discount="2.00")
        self.grant(self.user, "apply_discount")
        sale = self.confirm(discount="2.00", amount=Decimal("20.34"))
        line = sale.lines.get()
        self.assertEqual(sale.discount_amount, Decimal("2.00"))
        self.assertEqual(line.discount_amount, Decimal("2.00"))
        self.assertEqual(sale.tax_amount, Decimal("2.34"))
        self.assertEqual(sale.total, Decimal("20.34"))

    def test_sale_rejects_inactive_customer_and_wrong_branch(self):
        self.customer.is_active = False
        self.customer.save()
        with self.assertRaises(ValidationError):
            self.confirm(customer_id=self.customer.pk)
        with self.assertRaises(PermissionDenied):
            confirm_sale(
                branch_id=Branch.objects.create(name="Norte", code="norte").pk,
                customer_id=None,
                lines=[{"variant_id": self.variant.pk, "quantity": 1}],
                discount_amount=Decimal("0"),
                payments=[{"method_id": self.cash.pk, "amount": Decimal("22.60")}],
                user=self.user,
                idempotency_key=uuid.uuid4(),
            )

    def test_return_replenishes_stock_and_cannot_return_more_than_sold(self):
        sale = self.confirm(quantity=2)
        line = sale.lines.get()
        sale_return = process_return(
            sale_id=sale.pk,
            lines=[{"sale_line_id": line.pk, "quantity": 1}],
            reason="Talla incorrecta", refund_method_id=self.cash.pk,
            user=self.user, idempotency_key=uuid.uuid4(),
            cash_session_id=self.cash_session.pk,
        )
        self.stock.refresh_from_db()
        line.refresh_from_db()
        sale.refresh_from_db()
        self.assertEqual(sale_return.refund_amount, Decimal("22.60"))
        self.assertEqual(self.stock.quantity, 9)
        self.assertEqual(line.quantity_returned, 1)
        self.assertEqual(sale.status, Sale.Status.PARTIALLY_RETURNED)
        refund_movement = CashMovement.objects.get(sale_return=sale_return)
        self.assertEqual(refund_movement.session_id, self.cash_session.pk)
        self.assertEqual(refund_movement.amount, sale_return.refund_amount)
        self.assertTrue(refund_movement.affects_cash)
        with self.assertRaises(ValidationError):
            process_return(
                sale_id=sale.pk,
                lines=[{"sale_line_id": line.pk, "quantity": 2}],
                reason="Exceso", refund_method_id=self.cash.pk,
                user=self.user, idempotency_key=uuid.uuid4(),
            )

    def test_repeated_partial_returns_reconcile_to_line_total(self):
        self.product.tax_rate = Decimal("0")
        self.product.sale_price = Decimal("10.00")
        self.product.save()
        self.stock.quantity = 5
        self.stock.save()
        sale = self.confirm(quantity=3, amount=Decimal("30.00"))
        line = sale.lines.get()
        first = process_return(
            sale_id=sale.pk, lines=[{"sale_line_id": line.pk, "quantity": 1}],
            reason="Devolución parcial", refund_method_id=self.cash.pk,
            user=self.user, idempotency_key=uuid.uuid4(),
            cash_session_id=self.cash_session.pk,
        )
        second = process_return(
            sale_id=sale.pk, lines=[{"sale_line_id": line.pk, "quantity": 2}],
            reason="Resto de devolución", refund_method_id=self.cash.pk,
            user=self.user, idempotency_key=uuid.uuid4(),
            cash_session_id=self.cash_session.pk,
        )
        self.assertEqual(first.refund_amount + second.refund_amount, line.line_total)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.RETURNED)

    def test_return_request_replay_does_not_add_stock_twice(self):
        sale = self.confirm()
        line = sale.lines.get()
        key = uuid.uuid4()
        payload = {
            "sale_id": sale.pk, "lines": [{"sale_line_id": line.pk, "quantity": 1}],
            "reason": "Cambio", "refund_method_id": self.cash.pk,
            "user": self.user, "idempotency_key": key,
        }
        first = process_return(**payload)
        replay = process_return(**payload)
        self.stock.refresh_from_db()
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(self.stock.quantity, 10)

    def test_return_requires_authorized_role_permission(self):
        self.user.user_permissions.remove(Permission.objects.get(
            content_type__app_label="sales", codename="process_salereturn"
        ))
        self.user.__dict__.pop("_perm_cache", None)
        self.user.__dict__.pop("_user_perm_cache", None)
        sale = self.confirm()
        with self.assertRaises(PermissionDenied):
            process_return(
                sale_id=sale.pk, lines=[{"sale_line_id": sale.lines.get().pk, "quantity": 1}],
                reason="No autorizado", refund_method_id=self.cash.pk,
                user=self.user, idempotency_key=uuid.uuid4(),
            )

    def test_cancel_sale_reverses_inventory_once_and_preserves_sale(self):
        self.grant(self.user, "cancel_sale")
        sale = self.confirm(quantity=2)
        cancel_sale(sale_id=sale.pk, user=self.user, reason="Error de cobro", cash_session_id=self.cash_session.pk)
        cancel_sale(sale_id=sale.pk, user=self.user, reason="Reintento", cash_session_id=self.cash_session.pk)
        sale.refresh_from_db()
        self.stock.refresh_from_db()
        self.assertEqual(sale.status, Sale.Status.CANCELLED)
        self.assertEqual(self.stock.quantity, 10)
        self.assertEqual(InventoryOperation.objects.filter(kind=InventoryOperation.Kind.SALE_CANCEL).count(), 1)
        self.assertEqual(SaleReturn.objects.filter(sale=sale).count(), 1)
        self.assertEqual(
            CashMovement.objects.filter(
                sale_return__sale=sale, kind=CashMovement.Kind.SALE_REFUND
            ).count(), 1,
        )

    def test_sale_requires_open_cash_shift_and_shift_id_is_part_of_idempotency(self):
        sale = self.confirm()
        another_resource = CashResource.objects.create(
            branch=self.branch, code="second", name="Caja secundaria"
        )
        other_session = open_session(
            resource_id=another_resource.pk, opening_balance=0, user=self.user
        )
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label="cash", codename="close_cashsession"
        ))
        self.user.__dict__.pop("_perm_cache", None)
        self.user.__dict__.pop("_user_perm_cache", None)
        with self.assertRaises(ValidationError):
            confirm_sale(
                branch_id=self.branch.pk, customer_id=None,
                lines=[{"variant_id": self.variant.pk, "quantity": 1}],
                discount_amount=Decimal("0"),
                payments=[{"method_id": self.cash.pk, "amount": Decimal("22.60")}],
                user=self.user, idempotency_key=sale.idempotency_key,
                cash_session_id=other_session.pk,
            )
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(other_session.status, CashSession.Status.OPEN)
        closed = close_session(
            session_id=self.cash_session.pk, counted_cash=Decimal("72.60"), user=self.user
        )
        self.assertEqual(closed.status, CashSession.Status.CLOSED)
        with self.assertRaises(ValidationError):
            confirm_sale(
                branch_id=self.branch.pk, customer_id=None,
                lines=[{"variant_id": self.variant.pk, "quantity": 1}],
                discount_amount=Decimal("0"),
                payments=[{"method_id": self.cash.pk, "amount": Decimal("22.60")}],
                user=self.user, idempotency_key=uuid.uuid4(),
                cash_session_id=self.cash_session.pk,
            )

    def test_cancel_requires_permission_reason_and_no_prior_return(self):
        sale = self.confirm()
        with self.assertRaises(PermissionDenied):
            cancel_sale(sale_id=sale.pk, user=self.user, reason="Motivo", cash_session_id=self.cash_session.pk)
        self.grant(self.user, "cancel_sale")
        with self.assertRaises(ValidationError):
            cancel_sale(sale_id=sale.pk, user=self.user, reason="", cash_session_id=self.cash_session.pk)
        line = sale.lines.get()
        process_return(
            sale_id=sale.pk, lines=[{"sale_line_id": line.pk, "quantity": 1}],
            reason="Devuelta", refund_method_id=self.cash.pk,
            user=self.user, idempotency_key=uuid.uuid4(),
            cash_session_id=self.cash_session.pk,
        )
        with self.assertRaises(ValidationError):
            cancel_sale(sale_id=sale.pk, user=self.user, reason="Anular")


class SalesViewTests(SalesTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.grant(self.user, "view_sale", "add_sale", "process_salereturn")
        self.client.force_login(self.user)

    def test_point_of_sale_and_barcode_search_are_branch_scoped(self):
        response = self.client.get(reverse("sales:create"))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("sales:product_search"), {"q": "POL-001-M", "branch": self.branch.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["stock"], 10)
        other = Branch.objects.create(name="Norte", code="norte")
        response = self.client.get(reverse("sales:product_search"), {"q": "POL-001-M", "branch": other.pk})
        self.assertEqual(response.status_code, 404)

    def test_sale_create_confirms_from_server_data_and_receipt_is_printable(self):
        total = "22.60"
        response = self.client.post(reverse("sales:create"), {
            "branch": self.branch.pk,
            "customer": self.customer.pk,
            "cash_session": self.cash_session.pk,
            "discount_amount": "0.00",
            "cart_json": json.dumps([{"variant_id": self.variant.pk, "quantity": 1}]),
            "payments_json": json.dumps([{"method_id": self.cash.pk, "amount": total}]),
            "idempotency_key": str(uuid.uuid4()),
        })
        sale = Sale.objects.get()
        self.assertRedirects(response, reverse("sales:receipt", args=[sale.pk]))
        response = self.client.get(reverse("sales:receipt", args=[sale.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SPORTWEAR")
        self.assertContains(response, sale.number)
        self.assertContains(response, "Imprimir")

    def test_sale_url_and_history_do_not_expose_other_branch_sale(self):
        other = Branch.objects.create(name="Norte", code="norte")
        StockLevel.objects.create(branch=other, variant=self.variant, quantity=2)
        self.admin.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="cash", codename__in=("open_cashsession", "add_cashmovement")
        ))
        other_resource = CashResource.objects.create(branch=other, code="main", name="Caja Norte")
        other_session = open_session(
            resource_id=other_resource.pk, opening_balance=Decimal("0"), user=self.admin
        )
        sale = confirm_sale(
            branch_id=other.pk, customer_id=None,
            lines=[{"variant_id": self.variant.pk, "quantity": 1}],
            discount_amount=Decimal("0"), payments=[{"method_id": self.cash.pk, "amount": Decimal("22.60")}],
            user=self.admin, idempotency_key=uuid.uuid4(),
            cash_session_id=other_session.pk,
        )
        response = self.client.get(reverse("sales:detail", args=[sale.pk]))
        self.assertEqual(response.status_code, 404)
        response = self.client.get(reverse("sales:list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, sale.number)

    def test_customer_recording_is_optional_for_sale(self):
        sale = self.confirm(customer_id=None)
        self.assertIsNone(sale.customer)

    def test_customer_purchase_history_does_not_show_sales_from_other_branches(self):
        self.grant(self.user, "view_all_sales")
        self.user.user_permissions.add(Permission.objects.get(
            content_type__app_label="customers", codename="view_customer"
        ))
        self.user.__dict__.pop("_perm_cache", None)
        other_branch = Branch.objects.create(name="Norte", code="norte")
        StockLevel.objects.create(branch=other_branch, variant=self.variant, quantity=2)
        self.admin.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="cash", codename__in=("open_cashsession", "add_cashmovement")
        ))
        other_resource = CashResource.objects.create(branch=other_branch, code="main", name="Caja Norte")
        other_session = open_session(
            resource_id=other_resource.pk, opening_balance=Decimal("0"), user=self.admin
        )
        other_sale = confirm_sale(
            branch_id=other_branch.pk,
            customer_id=self.customer.pk,
            lines=[{"variant_id": self.variant.pk, "quantity": 1}],
            discount_amount=Decimal("0.00"),
            payments=[{"method_id": self.cash.pk, "amount": Decimal("22.60")}],
            user=self.admin,
            idempotency_key=uuid.uuid4(),
            cash_session_id=other_session.pk,
        )
        response = self.client.get(reverse("customers:detail", args=[self.customer.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(other_sale.number.encode(), response.content)
