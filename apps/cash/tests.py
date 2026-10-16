import uuid
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.branches.models import Branch
from apps.customers.models import Customer
from apps.inventory.models import StockLevel
from apps.products.models import Brand, Category, Product, ProductVariant, Size
from apps.sales.models import PaymentMethod, Sale
from apps.sales.services import confirm_sale, process_return
from apps.users.models import User, UserBranchAccess

from .models import CashMovement, CashResource, CashSession
from .services import close_session, open_session, record_manual_movement


class CashTestMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            username="cashier", password="Secure-cash-password-123", role=User.Role.CASHIER
        )
        self.branch = Branch.objects.create(name="Central", code="central")
        UserBranchAccess.objects.create(user=self.user, branch=self.branch)
        self.resource = CashResource.objects.create(
            branch=self.branch, code="main", name="Caja Principal"
        )
        self.permissions = Permission.objects.filter(content_type__app_label="cash")
        self.grant(self.user, "view_cashsession", "view_cashmovement", "open_cashsession", "close_cashsession", "add_cashmovement")

    def grant(self, user, *codenames):
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="cash", codename__in=codenames
        ))
        for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            user.__dict__.pop(cache_name, None)


class CashServiceTests(CashTestMixin, TestCase):
    def test_open_and_close_capture_reconciliation_difference_and_history(self):
        session = open_session(
            resource_id=self.resource.pk, opening_balance=Decimal("100.00"),
            user=self.user, notes="Cambio inicial",
        )
        cash = PaymentMethod.objects.get(code="cash")
        qr = PaymentMethod.objects.get(code="qr")
        customer = Customer.objects.create(full_name="Venta cliente")
        category = Category.objects.create(name="Poleras")
        brand = Brand.objects.create(name="Marca Uno")
        product = Product.objects.create(
            internal_code="POL-1", name="Polera", category=category, brand=brand,
            cost=Decimal("10.00"), sale_price=Decimal("20.00"), tax_rate=Decimal("13.00"),
        )
        size = Size.objects.create(name="M")
        variant = ProductVariant.objects.create(product=product, sku="POL-1-M", size=size)
        StockLevel.objects.create(branch=self.branch, variant=variant, quantity=4, average_cost=Decimal("10.00"))
        sale = confirm_sale(
            branch_id=self.branch.pk, customer_id=customer.pk,
            lines=[{"variant_id": variant.pk, "quantity": 1}], discount_amount=Decimal("0"),
            payments=[{"method_id": cash.pk, "amount": Decimal("10.00")}, {"method_id": qr.pk, "amount": Decimal("12.60")}],
            user=self.user, idempotency_key=uuid.uuid4(), cash_session_id=session.pk,
        )
        self.assertEqual(CashMovement.objects.filter(sale_payment__sale=sale).count(), 2)
        record_manual_movement(
            session_id=session.pk, kind=CashMovement.Kind.OTHER_INCOME,
            amount=Decimal("5.00"), reason="Aporte de cambio", reference="REC-1",
            user=self.user, idempotency_key=uuid.uuid4(),
        )
        record_manual_movement(
            session_id=session.pk, kind=CashMovement.Kind.EXPENSE,
            amount=Decimal("2.00"), reason="Compra de bolsas", reference="REC-2",
            user=self.user, idempotency_key=uuid.uuid4(),
        )
        record_manual_movement(
            session_id=session.pk, kind=CashMovement.Kind.WITHDRAWAL,
            amount=Decimal("3.00"), reason="Retiro autorizado", reference="REC-3",
            user=self.user, idempotency_key=uuid.uuid4(),
        )
        closed = close_session(
            session_id=session.pk, counted_cash=Decimal("110.00"),
            user=self.user, notes="Arqueo correcto",
        )
        self.assertEqual(closed.status, CashSession.Status.CLOSED)
        self.assertEqual(closed.cash_sales_total, Decimal("10.00"))
        self.assertEqual(closed.noncash_collections_total, Decimal("12.60"))
        self.assertEqual(closed.additional_income_total, Decimal("5.00"))
        self.assertEqual(closed.expense_total, Decimal("2.00"))
        self.assertEqual(closed.withdrawal_total, Decimal("3.00"))
        self.assertEqual(closed.expected_cash, Decimal("110.00"))
        self.assertEqual(closed.difference, Decimal("0.00"))
        self.assertEqual(closed.closed_by, self.user)
        self.assertEqual(closed.events.count(), 5)
        self.assertEqual(CashMovement.objects.filter(sale_return__sale=sale).count(), 0)

    def test_only_one_open_shift_per_resource_and_a_closed_session_cannot_reopen(self):
        session = open_session(resource_id=self.resource.pk, opening_balance=0, user=self.user)
        with self.assertRaises(ValidationError):
            open_session(resource_id=self.resource.pk, opening_balance=0, user=self.user)
        self.assertEqual(CashSession.objects.filter(status=CashSession.Status.OPEN).count(), 1)
        close_session(session_id=session.pk, counted_cash=0, user=self.user)
        reopened = open_session(resource_id=self.resource.pk, opening_balance=0, user=self.user)
        self.assertNotEqual(reopened.pk, session.pk)

    def test_manual_movements_require_open_shift_and_idempotency(self):
        session = open_session(resource_id=self.resource.pk, opening_balance=0, user=self.user)
        key = uuid.uuid4()
        payload = {
            "session_id": session.pk, "kind": CashMovement.Kind.OTHER_INCOME,
            "amount": Decimal("7.50"), "reason": "Aporte", "reference": "A-1",
            "user": self.user, "idempotency_key": key,
        }
        first = record_manual_movement(**payload)
        replay = record_manual_movement(**payload)
        self.assertEqual(first.pk, replay.pk)
        with self.assertRaises(ValidationError):
            record_manual_movement(**{**payload, "amount": Decimal("8.50")})
        close_session(session_id=session.pk, counted_cash=Decimal("7.50"), user=self.user)
        with self.assertRaises(ValidationError):
            record_manual_movement(
                session_id=session.pk, kind=CashMovement.Kind.OTHER_INCOME,
                amount=1, reason="Tarde", reference="", user=self.user,
                idempotency_key=uuid.uuid4(),
            )

    def test_open_and_movement_enforce_permissions_and_branch_access(self):
        unauthorized = User.objects.create_user(username="limited", password="Secure-cash-password-123")
        with self.assertRaises(PermissionDenied):
            open_session(resource_id=self.resource.pk, opening_balance=0, user=unauthorized)
        other = Branch.objects.create(name="Norte", code="norte")
        resource = CashResource.objects.create(branch=other, code="main", name="Caja Norte")
        with self.assertRaises(PermissionDenied):
            open_session(resource_id=resource.pk, opening_balance=0, user=self.user)

    def test_resource_branch_cannot_be_changed_after_a_session_and_cannot_be_disabled_open(self):
        session = open_session(resource_id=self.resource.pk, opening_balance=0, user=self.user)
        other_branch = Branch.objects.create(name="Norte", code="norte")
        self.resource.branch = other_branch
        with self.assertRaises(ValidationError):
            self.resource.full_clean()
        self.resource.refresh_from_db()
        self.resource.is_active = False
        with self.assertRaises(ValidationError):
            self.resource.full_clean()
        self.assertEqual(session.status, CashSession.Status.OPEN)


class CashViewTests(CashTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_open_form_and_session_detail_are_branch_scoped(self):
        response = self.client.get(reverse("cash:session_open"))
        self.assertEqual(response.status_code, 200)
        session = open_session(resource_id=self.resource.pk, opening_balance=5, user=self.user)
        response = self.client.get(reverse("cash:session_detail", args=[session.pk]))
        self.assertEqual(response.status_code, 200)
        other_branch = Branch.objects.create(name="Norte", code="norte")
        other_resource = CashResource.objects.create(branch=other_branch, code="main", name="Caja Norte")
        other_session = CashSession.objects.create(
            resource=other_resource, opening_balance=0, opened_by=self.user
        )
        self.assertEqual(self.client.get(reverse("cash:session_detail", args=[other_session.pk])).status_code, 404)

    def test_manual_movement_form_post_creates_history(self):
        session = open_session(resource_id=self.resource.pk, opening_balance=0, user=self.user)
        response = self.client.post(reverse("cash:movement_create"), {
            "session": session.pk,
            "kind": CashMovement.Kind.OTHER_INCOME,
            "amount": "12.50",
            "reason": "Ingreso adicional",
            "reference": "REC-10",
            "idempotency_key": str(uuid.uuid4()),
        })
        self.assertRedirects(response, reverse("cash:session_detail", args=[session.pk]))
        self.assertTrue(CashMovement.objects.filter(session=session, amount=Decimal("12.50")).exists())

    def test_closed_session_cannot_be_closed_or_moved_again(self):
        session = open_session(resource_id=self.resource.pk, opening_balance=0, user=self.user)
        close_session(session_id=session.pk, counted_cash=0, user=self.user)
        response = self.client.post(reverse("cash:session_close", args=[session.pk]), {"counted_cash": "0"})
        self.assertRedirects(response, reverse("cash:session_detail", args=[session.pk]))
        response = self.client.post(reverse("cash:movement_create"), {
            "session": session.pk, "kind": CashMovement.Kind.OTHER_INCOME,
            "amount": "5", "reason": "Tarde", "reference": "",
            "idempotency_key": str(uuid.uuid4()),
        })
        self.assertEqual(CashMovement.objects.filter(session=session).count(), 0)
