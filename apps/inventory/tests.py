import uuid
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.branches.models import Branch
from apps.products.models import Brand, Category, Product, ProductVariant, Size
from apps.users.models import User, UserBranchAccess

from .models import InventoryMovement, InventoryOperation, StockLevel, StockTransfer
from .services import (
    adjust_stock_count,
    change_transfer_status,
    create_transfer,
    dispatch_transfer,
    receive_transfer,
    record_issue,
    record_receipt,
)


class InventoryTestDataMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            username="stockkeeper", password="Secure-stock-password-123",
            role=User.Role.STOCKKEEPER,
        )
        self.origin = Branch.objects.create(name="Central", code="central")
        self.destination = Branch.objects.create(name="Norte", code="norte")
        self.category = Category.objects.create(name="Poleras")
        self.brand = Brand.objects.create(name="Marca Uno")
        self.product = Product.objects.create(
            internal_code="POL-001", name="Polera deportiva", category=self.category,
            brand=self.brand, cost=Decimal("10.00"), sale_price=Decimal("30.00"),
        )
        self.size = Size.objects.create(name="M")
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="POL-001-M", size=self.size
        )
        self.assign(self.user, self.origin)
        self.grant(
            self.user, "add_inventoryoperation", "add_stocktransfer", "change_stocktransfer"
        )

    def grant(self, user, *codenames):
        permissions = Permission.objects.filter(
            content_type__app_label="inventory", codename__in=codenames
        )
        user.user_permissions.add(*permissions)

    def assign(self, user, *branches):
        for branch in branches:
            UserBranchAccess.objects.get_or_create(user=user, branch=branch)

    def receive(self, quantity, *, key=None, reason="Compra de proveedor", unit_cost="10.00"):
        return record_receipt(
            branch_id=self.origin.pk, variant_id=self.variant.pk, quantity=quantity,
            user=self.user, reason=reason,
            idempotency_key=key or uuid.uuid4(), unit_cost=Decimal(unit_cost),
        )


class InventoryServiceTests(InventoryTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.assign(self.user, self.destination)

    def test_inventory_service_rejects_missing_permission_and_unassigned_branch(self):
        unauthorized = User.objects.create_user(
            username="unauthorized", password="Secure-stock-password-123"
        )
        with self.assertRaises(PermissionDenied):
            record_receipt(
                branch_id=self.origin.pk, variant_id=self.variant.pk, quantity=1,
                user=unauthorized, reason="Intento", idempotency_key=uuid.uuid4(),
            )

        assigned_user = User.objects.create_user(
            username="assigned", password="Secure-stock-password-123"
        )
        self.assign(assigned_user, self.origin)
        self.grant(assigned_user, "add_inventoryoperation")
        with self.assertRaises(PermissionDenied):
            record_receipt(
                branch_id=self.destination.pk, variant_id=self.variant.pk, quantity=1,
                user=assigned_user, reason="Intento", idempotency_key=uuid.uuid4(),
            )

    def test_receipts_update_stock_and_weighted_average_cost(self):
        self.receive(10, unit_cost="10.00")
        self.receive(10, unit_cost="20.00")
        stock = StockLevel.objects.get(branch=self.origin, variant=self.variant)
        self.assertEqual(stock.quantity, 20)
        self.assertEqual(stock.average_cost, Decimal("15.00"))
        self.assertEqual(stock.inventory_value, Decimal("300.00"))
        self.assertEqual(InventoryMovement.objects.count(), 2)

    def test_replaying_same_receipt_is_idempotent_and_changed_payload_rejected(self):
        key = uuid.uuid4()
        first = self.receive(5, key=key)
        replay = self.receive(5, key=key)
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(StockLevel.objects.get(branch=self.origin, variant=self.variant).quantity, 5)
        with self.assertRaises(ValidationError):
            self.receive(6, key=key)
        self.assertEqual(InventoryMovement.objects.count(), 1)

    def test_issue_rejects_insufficient_stock_without_partial_write(self):
        self.receive(3)
        with self.assertRaises(ValidationError):
            record_issue(
                branch_id=self.origin.pk, variant_id=self.variant.pk, quantity=4,
                user=self.user, reason="Salida", idempotency_key=uuid.uuid4(),
            )
        self.assertEqual(StockLevel.objects.get(branch=self.origin, variant=self.variant).quantity, 3)
        self.assertEqual(InventoryOperation.objects.count(), 1)

    def test_adjustment_records_signed_difference_and_replays_safely(self):
        self.receive(8)
        key = uuid.uuid4()
        operation = adjust_stock_count(
            branch_id=self.origin.pk, variant_id=self.variant.pk, counted_quantity=5,
            user=self.user, reason="Conteo físico", idempotency_key=key,
        )
        self.assertEqual(operation.kind, InventoryOperation.Kind.ADJUSTMENT_OUT)
        movement = operation.movements.get()
        self.assertEqual(movement.quantity_delta, -3)
        replay = adjust_stock_count(
            branch_id=self.origin.pk, variant_id=self.variant.pk, counted_quantity=5,
            user=self.user, reason="Conteo físico", idempotency_key=key,
        )
        self.assertEqual(replay.pk, operation.pk)
        self.assertEqual(StockLevel.objects.get(branch=self.origin, variant=self.variant).quantity, 5)

    def test_adjustment_requires_reason_and_actual_difference(self):
        self.receive(4)
        with self.assertRaises(ValidationError):
            adjust_stock_count(
                branch_id=self.origin.pk, variant_id=self.variant.pk, counted_quantity=4,
                user=self.user, reason="", idempotency_key=uuid.uuid4(),
            )
        with self.assertRaises(ValidationError):
            adjust_stock_count(
                branch_id=self.origin.pk, variant_id=self.variant.pk, counted_quantity=4,
                user=self.user, reason="Conteo coincide", idempotency_key=uuid.uuid4(),
            )

    def test_stock_level_is_unique_per_branch_and_variant(self):
        self.receive(2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StockLevel.objects.create(branch=self.origin, variant=self.variant)

    def test_transfer_deducts_source_on_dispatch_and_adds_destination_only_on_receipt(self):
        self.receive(12)
        transfer = create_transfer(
            origin_id=self.origin.pk, destination_id=self.destination.pk,
            lines=[{"variant_id": self.variant.pk, "quantity": 10}],
            user=self.user, notes="Reposición", creation_key=uuid.uuid4(),
        )
        line = transfer.lines.get()
        self.assertEqual(transfer.status, StockTransfer.Status.DRAFT)
        self.assertEqual(StockLevel.objects.get(branch=self.origin, variant=self.variant).quantity, 12)
        change_transfer_status(transfer_id=transfer.pk, user=self.user, action="submit")
        dispatch_key = uuid.uuid4()
        dispatch_transfer(transfer_id=transfer.pk, user=self.user, idempotency_key=dispatch_key)
        dispatch_transfer(transfer_id=transfer.pk, user=self.user, idempotency_key=dispatch_key)
        self.assertEqual(StockLevel.objects.get(branch=self.origin, variant=self.variant).quantity, 2)
        self.assertFalse(StockLevel.objects.filter(branch=self.destination, variant=self.variant).exists())
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.Status.IN_TRANSIT)
        self.assertEqual(InventoryOperation.objects.filter(kind=InventoryOperation.Kind.TRANSFER_OUT).count(), 1)

        partial_key = uuid.uuid4()
        receive_transfer(
            transfer_id=transfer.pk, user=self.user, quantities={line.pk: 6},
            finalize=False, notes="Primera entrega", idempotency_key=partial_key,
        )
        receive_transfer(
            transfer_id=transfer.pk, user=self.user, quantities={line.pk: 6},
            finalize=False, notes="Primera entrega", idempotency_key=partial_key,
        )
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.Status.PARTIAL)
        self.assertEqual(StockLevel.objects.get(branch=self.destination, variant=self.variant).quantity, 6)

        receive_transfer(
            transfer_id=transfer.pk, user=self.user, quantities={line.pk: 2},
            finalize=True, notes="Diferencia de dos unidades", idempotency_key=uuid.uuid4(),
        )
        transfer.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.Status.RECEIVED)
        self.assertEqual(line.quantity_received, 8)
        self.assertEqual(line.quantity_missing, 2)
        self.assertEqual(StockLevel.objects.get(branch=self.destination, variant=self.variant).quantity, 8)
        self.assertEqual(transfer.events.count(), 5)

    def test_transfer_dispatch_insufficient_stock_keeps_all_stock_and_state_unchanged(self):
        self.receive(2)
        transfer = create_transfer(
            origin_id=self.origin.pk, destination_id=self.destination.pk,
            lines=[{"variant_id": self.variant.pk, "quantity": 3}],
            user=self.user, notes="", creation_key=uuid.uuid4(),
        )
        change_transfer_status(transfer_id=transfer.pk, user=self.user, action="submit")
        with self.assertRaises(ValidationError):
            dispatch_transfer(transfer_id=transfer.pk, user=self.user, idempotency_key=uuid.uuid4())
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.Status.PENDING)
        self.assertEqual(StockLevel.objects.get(branch=self.origin, variant=self.variant).quantity, 2)
        self.assertFalse(InventoryOperation.objects.filter(transfer=transfer).exists())

    def test_transfer_creation_rejects_same_branch_and_duplicate_variant(self):
        with self.assertRaises(ValidationError):
            create_transfer(
                origin_id=self.origin.pk, destination_id=self.origin.pk,
                lines=[{"variant_id": self.variant.pk, "quantity": 1}],
                user=self.user, notes="", creation_key=uuid.uuid4(),
            )
        with self.assertRaises(ValidationError):
            create_transfer(
                origin_id=self.origin.pk, destination_id=self.destination.pk,
                lines=[
                    {"variant_id": self.variant.pk, "quantity": 1},
                    {"variant_id": self.variant.pk, "quantity": 2},
                ],
                user=self.user, notes="", creation_key=uuid.uuid4(),
            )


class InventoryViewTests(InventoryTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.assign(self.user, self.origin)
        self.grant(
            self.user,
            "view_stocklevel", "change_stocklevel", "view_inventoryoperation",
            "add_inventoryoperation", "view_stocktransfer", "add_stocktransfer",
            "change_stocktransfer",
        )
        self.client.force_login(self.user)

    def test_stock_view_rejects_an_unauthorized_branch_identifier(self):
        response = self.client.get(reverse("inventory:stock_list"), {"branch": self.destination.pk})
        self.assertEqual(response.status_code, 404)

    def test_stock_view_lists_only_selected_authorized_branch(self):
        self.receive(5)
        response = self.client.get(reverse("inventory:stock_list"), {"branch": self.origin.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "POL-001-M")
        self.assertContains(response, "Central")
        self.assertNotContains(response, "Norte")

    def test_entry_view_creates_transactional_ledger_operation(self):
        import re

        page = self.client.get(reverse("inventory:receipt_create"))
        self.assertEqual(page.status_code, 200)
        key = re.search(
            rb'name="idempotency_key" value="([0-9a-f-]+)"', page.content
        ).group(1).decode()
        response = self.client.post(reverse("inventory:receipt_create"), {
            "branch": self.origin.pk,
            "variant": self.variant.pk,
            "quantity": 4,
            "unit_cost": "11.50",
            "reason": "Compra recibida",
            "idempotency_key": key,
        })
        self.assertEqual(response.status_code, 302)
        stock = StockLevel.objects.get(branch=self.origin, variant=self.variant)
        self.assertEqual(stock.quantity, 4)
        self.assertEqual(stock.average_cost, Decimal("11.50"))
        self.assertEqual(InventoryMovement.objects.count(), 1)

    def test_user_cannot_post_manual_entry_for_unassigned_branch(self):
        response = self.client.post(reverse("inventory:receipt_create"), {
            "branch": self.destination.pk,
            "variant": self.variant.pk,
            "quantity": 4,
            "reason": "Intento no autorizado",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StockLevel.objects.exists())

    def test_transfer_can_be_viewed_by_user_authorized_for_either_end_but_not_created_for_other_branch(self):
        self.assign(self.user, self.destination)
        transfer = create_transfer(
            origin_id=self.origin.pk, destination_id=self.destination.pk,
            lines=[{"variant_id": self.variant.pk, "quantity": 1}],
            user=self.user, notes="", creation_key=uuid.uuid4(),
        )
        response = self.client.get(reverse("inventory:transfer_detail", args=[transfer.pk]))
        self.assertEqual(response.status_code, 200)
        limited = User.objects.create_user(username="one-branch", password="secure-password-123")
        UserBranchAccess.objects.create(user=limited, branch=self.origin)
        limited.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="inventory", codename__in=("view_stocktransfer", "add_stocktransfer")
        ))
        self.client.force_login(limited)
        response = self.client.get(reverse("inventory:transfer_detail", args=[transfer.pk]))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("inventory:transfer_create"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Norte")

    def test_destination_user_can_receive_transfer_and_finalize_a_shortage(self):
        self.assign(self.user, self.destination)
        self.receive(5)
        transfer = create_transfer(
            origin_id=self.origin.pk,
            destination_id=self.destination.pk,
            lines=[{"variant_id": self.variant.pk, "quantity": 4}],
            user=self.user,
            notes="Reposición",
            creation_key=uuid.uuid4(),
        )
        change_transfer_status(transfer_id=transfer.pk, user=self.user, action="submit")
        dispatch_transfer(transfer_id=transfer.pk, user=self.user, idempotency_key=uuid.uuid4())
        line = transfer.lines.get()

        page = self.client.get(reverse("inventory:transfer_receive", args=[transfer.pk]))
        self.assertEqual(page.status_code, 200)
        response = self.client.post(
            reverse("inventory:transfer_receive", args=[transfer.pk]),
            {
                "idempotency_key": uuid.uuid4(),
                "notes": "Una unidad no llegó",
                "finalize": "on",
                "receipt-TOTAL_FORMS": "1",
                "receipt-INITIAL_FORMS": "1",
                "receipt-MIN_NUM_FORMS": "1",
                "receipt-MAX_NUM_FORMS": "1000",
                "receipt-0-line": line.pk,
                "receipt-0-quantity": "3",
            },
        )
        self.assertRedirects(response, reverse("inventory:transfer_detail", args=[transfer.pk]))
        transfer.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(transfer.status, StockTransfer.Status.RECEIVED)
        self.assertEqual(line.quantity_received, 3)
        self.assertEqual(line.quantity_missing, 1)
        self.assertEqual(StockLevel.objects.get(branch=self.destination, variant=self.variant).quantity, 3)
