import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.branches.models import Branch
from apps.cash.models import CashMovement, CashResource, CashSession
from apps.cash.services import open_session
from apps.sales.models import PaymentMethod
from apps.users.models import User, UserBranchAccess

from .models import (
    FinanceAccount,
    FinanceApprovalLimit,
    FinanceCategory,
    FinanceOperation,
)
from .services import (
    approve_operation,
    create_operation,
    reject_operation,
    reverse_operation,
)


class FinanceTestMixin:
    def setUp(self):
        self.creator = User.objects.create_user(
            username="clerk", password="Secure-finance-123", role=User.Role.CASHIER
        )
        self.approver = User.objects.create_user(
            username="manager", password="Secure-finance-123", role=User.Role.MANAGER
        )
        self.branch = Branch.objects.create(name="Central", code="central")
        for user in (self.creator, self.approver):
            UserBranchAccess.objects.create(user=user, branch=self.branch)
        self.grant_cash(self.creator)
        self.grant_cash(self.approver)
        self.resource = CashResource.objects.create(
            branch=self.branch, code="main", name="Caja Principal"
        )
        self.session = open_session(
            resource_id=self.resource.pk, opening_balance=Decimal("100.00"),
            user=self.creator,
        )
        self.cash_method = PaymentMethod.objects.get(code="cash")
        self.bank_method = PaymentMethod.objects.get(code="bank-transfer")
        self.income_category = FinanceCategory.objects.create(name="Aportes", kind=FinanceCategory.Kind.INCOME)
        self.expense_category = FinanceCategory.objects.create(name="Servicios", kind=FinanceCategory.Kind.EXPENSE)
        self.cash_account = FinanceAccount.objects.create(
            name="Caja Principal", kind=FinanceAccount.Kind.CASH, branch=self.branch,
            cash_resource=self.resource,
        )
        self.bank_account = FinanceAccount.objects.create(
            name="Banco Nacional", kind=FinanceAccount.Kind.BANK, branch=self.branch,
            account_reference="****1234",
        )
        self.grant(self.creator, "add_financeoperation")
        self.grant(self.approver, "add_financeoperation", "approve_financeoperation", "reject_financeoperation", "reverse_financeoperation", "view_all_financeoperations")
        FinanceApprovalLimit.objects.create(user=self.creator, branch=self.branch, maximum_amount=Decimal("100.00"))
        FinanceApprovalLimit.objects.create(user=self.approver, branch=self.branch, maximum_amount=Decimal("10000.00"))

    def grant(self, user, *codenames):
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="finance", codename__in=codenames
        ))
        for cache in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            user.__dict__.pop(cache, None)

    def grant_cash(self, user):
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="cash", codename__in=("add_cashmovement", "view_cashsession", "open_cashsession", "close_cashsession")
        ))
        for cache in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            user.__dict__.pop(cache, None)

    def create(self, *, kind=FinanceOperation.Kind.INCOME, account=None, amount="50.00", category=None, session=None, user=None, key=None):
        return create_operation(
            kind=kind,
            category_id=(category or (self.income_category if kind == FinanceOperation.Kind.INCOME else self.expense_category)).pk,
            branch_id=self.branch.pk,
            account_id=(account or self.cash_account).pk,
            concept="Operación de prueba",
            amount=Decimal(amount),
            operation_date=date(2026, 10, 16),
            notes="Nota de prueba",
            user=user or self.creator,
            idempotency_key=key or uuid.uuid4(),
            cash_session_id=session.pk if session else None,
            payment_method_id=self.cash_method.pk if (account or self.cash_account).kind == FinanceAccount.Kind.CASH else self.bank_method.pk,
        )


class FinanceServiceTests(FinanceTestMixin, TestCase):
    def test_cash_income_posts_to_finance_and_cash_once_and_changes_close_expected(self):
        operation = self.create(amount="25.50", session=self.session)
        self.assertEqual(operation.status, FinanceOperation.Status.POSTED)
        self.assertEqual(operation.number, "F-00000001")
        self.assertIsNotNone(operation.cash_movement_id)
        movement = operation.cash_movement
        self.assertEqual(movement.kind, CashMovement.Kind.OTHER_INCOME)
        self.assertEqual(movement.amount, operation.amount)
        self.assertEqual(movement.session_id, self.session.pk)
        replay = self.create(amount="25.50", session=self.session, key=operation.idempotency_key)
        self.assertEqual(replay.pk, operation.pk)
        self.assertEqual(CashMovement.objects.filter(finance_operation=operation).count(), 1)

    def test_bank_operation_posts_without_cash_session_or_cash_movement(self):
        operation = self.create(account=self.bank_account, amount="70.00")
        self.assertEqual(operation.status, FinanceOperation.Status.POSTED)
        self.assertIsNone(operation.cash_movement_id)
        self.assertIsNone(operation.cash_session_id)
        self.assertFalse(CashMovement.objects.filter(reference=operation.number).exists())

    def test_account_balance_includes_opening_and_nets_posted_reversal(self):
        self.bank_account.opening_balance = Decimal("500.00")
        self.bank_account.save(update_fields=["opening_balance"])
        operation = self.create(account=self.bank_account, amount="70.00")
        from apps.finance.views import _account_balances

        balance = _account_balances(self.creator).get(pk=self.bank_account.pk)
        self.assertEqual(balance.balance, Decimal("570.00"))
        reverse_operation(
            operation_id=operation.pk,
            category_id=self.expense_category.pk,
            reason="Pago duplicado",
            user=self.approver,
            idempotency_key=uuid.uuid4(),
        )
        balance = _account_balances(self.creator).get(pk=self.bank_account.pk)
        self.assertEqual(balance.balance, Decimal("500.00"))

    def test_amount_above_creator_limit_stays_pending_without_cash_effect_until_approval(self):
        operation = self.create(amount="150.00", session=None)
        self.assertEqual(operation.status, FinanceOperation.Status.PENDING)
        self.assertIsNone(operation.cash_movement_id)
        self.assertFalse(CashMovement.objects.filter(reference=operation.number).exists())
        with self.assertRaises(PermissionDenied):
            approve_operation(operation_id=operation.pk, user=self.creator, cash_session_id=self.session.pk)
        approved = approve_operation(
            operation_id=operation.pk, user=self.approver, cash_session_id=self.session.pk
        )
        self.assertEqual(approved.status, FinanceOperation.Status.POSTED)
        self.assertEqual(approved.approved_by, self.approver)
        self.assertEqual(approved.cash_movement.session_id, self.session.pk)

    def test_creator_cannot_approve_own_pending_operation(self):
        operation = self.create(amount="150.00")
        self.grant(self.creator, "approve_financeoperation")
        limit = FinanceApprovalLimit.objects.get(user=self.creator, branch=self.branch)
        limit.maximum_amount = Decimal("1000.00")
        limit.save(update_fields=["maximum_amount"])
        with self.assertRaises(PermissionDenied):
            approve_operation(operation_id=operation.pk, user=self.creator, cash_session_id=self.session.pk)

    def test_rejected_operation_never_posts_cash(self):
        operation = self.create(amount="150.00")
        rejected = reject_operation(
            operation_id=operation.pk, user=self.approver, reason="Falta comprobante"
        )
        self.assertEqual(rejected.status, FinanceOperation.Status.REJECTED)
        self.assertIsNone(rejected.cash_movement_id)
        self.assertFalse(CashMovement.objects.filter(reference=operation.number).exists())

    def test_legacy_cash_manual_income_and_expense_are_redirected_to_finance(self):
        with self.assertRaises(ValidationError):
            from apps.cash.services import record_manual_movement

            record_manual_movement(
                session_id=self.session.pk, kind=CashMovement.Kind.OTHER_INCOME,
                amount=10, reason="Duplicated route", reference="", user=self.creator,
                idempotency_key=uuid.uuid4(),
            )

    def test_posted_operation_reverses_with_opposite_kind_and_traceable_cash_entry(self):
        original = self.create(amount="25.50", session=self.session)
        reverse_category = self.expense_category
        reversal = reverse_operation(
            operation_id=original.pk, category_id=reverse_category.pk,
            reason="Registro duplicado", user=self.approver,
            idempotency_key=uuid.uuid4(), cash_session_id=self.session.pk,
        )
        original.refresh_from_db()
        self.assertEqual(original.status, FinanceOperation.Status.REVERSED)
        self.assertEqual(reversal.status, FinanceOperation.Status.POSTED)
        self.assertEqual(reversal.kind, FinanceOperation.Kind.EXPENSE)
        self.assertEqual(reversal.amount, original.amount)
        self.assertEqual(reversal.reversal_of_id, original.pk)
        self.assertEqual(reversal.cash_movement.kind, CashMovement.Kind.EXPENSE)
        self.assertEqual(reversal.cash_movement.session_id, self.session.pk)
        with self.assertRaises(ValidationError):
            reverse_operation(
                operation_id=original.pk, category_id=reverse_category.pk,
                reason="Segunda reversión", user=self.approver,
                idempotency_key=uuid.uuid4(), cash_session_id=self.session.pk,
            )

    def test_reversal_retry_with_same_key_returns_same_compensation(self):
        original = self.create(amount="25.50", session=self.session)
        key = uuid.uuid4()
        kwargs = {
            "operation_id": original.pk,
            "category_id": self.expense_category.pk,
            "reason": "Registro equivocado",
            "user": self.approver,
            "idempotency_key": key,
            "cash_session_id": self.session.pk,
        }
        first = reverse_operation(**kwargs)
        replay = reverse_operation(**kwargs)
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(FinanceOperation.objects.filter(reversal_of=original).count(), 1)

    def test_bank_account_rejects_physical_cash_payment_method(self):
        with self.assertRaises(ValidationError):
            create_operation(
                kind=FinanceOperation.Kind.INCOME,
                category_id=self.income_category.pk,
                branch_id=self.branch.pk,
                account_id=self.bank_account.pk,
                concept="Prueba cuenta bancaria",
                amount=Decimal("30.00"),
                operation_date=date(2026, 10, 16),
                notes="",
                user=self.creator,
                idempotency_key=uuid.uuid4(),
                payment_method_id=self.cash_method.pk,
            )

    def test_general_bank_account_requires_explicit_permission(self):
        general_bank = FinanceAccount.objects.create(
            name="Banco General", kind=FinanceAccount.Kind.BANK,
            account_reference="****5678",
        )
        with self.assertRaises(PermissionDenied):
            self.create(account=general_bank, amount="30.00")
        self.grant(self.creator, "use_general_finance_accounts")
        operation = self.create(account=general_bank, amount="30.00")
        self.assertEqual(operation.status, FinanceOperation.Status.POSTED)

    def test_operation_rejects_wrong_category_account_branch_and_negative_values(self):
        with self.assertRaises(ValidationError):
            self.create(kind=FinanceOperation.Kind.EXPENSE, category=self.income_category)
        with self.assertRaises(ValidationError):
            self.create(amount="-1.00", session=self.session)
        other = Branch.objects.create(name="Norte", code="norte")
        account = FinanceAccount.objects.create(name="Banco Norte", kind=FinanceAccount.Kind.BANK, branch=other)
        with self.assertRaises(PermissionDenied):
            self.create(account=account)

    def test_finance_create_requires_permission(self):
        limited = User.objects.create_user(username="limited", password="Secure-finance-123")
        with self.assertRaises(PermissionDenied):
            self.create(user=limited)


class FinanceViewTests(FinanceTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.creator.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="finance", codename__in=("view_financeoperation", "add_financeoperation")
        ))
        self.creator.__dict__.pop("_perm_cache", None)
        self.client.force_login(self.creator)

    def test_income_form_posts_cash_operation_to_selected_shift(self):
        response = self.client.post(reverse("finance:income_create"), {
            "branch": self.branch.pk,
            "category": self.income_category.pk,
            "account": self.cash_account.pk,
            "amount": "40.00",
            "concept": "Aporte de capital",
            "operation_date": "2026-10-16",
            "payment_method": self.cash_method.pk,
            "cash_session": self.session.pk,
            "notes": "Aporte socio",
            "idempotency_key": str(uuid.uuid4()),
        })
        operation = FinanceOperation.objects.get()
        self.assertRedirects(response, reverse("finance:detail", args=[operation.pk]))
        self.assertEqual(operation.status, FinanceOperation.Status.POSTED)
        self.assertEqual(operation.cash_movement.amount, Decimal("40.00"))

    def test_pending_operation_detail_does_not_expose_other_branch(self):
        operation = self.create(amount="150.00")
        other = Branch.objects.create(name="Norte", code="norte")
        other_category = FinanceCategory.objects.create(name="Otro ingreso", kind=FinanceCategory.Kind.INCOME)
        other_account = FinanceAccount.objects.create(name="Banco Norte", kind=FinanceAccount.Kind.BANK, branch=other)
        unrelated = FinanceOperation.objects.create(
            kind=FinanceOperation.Kind.INCOME, category=other_category, branch=other,
            account=other_account, concept="Privado", amount=Decimal("20.00"),
            operation_date=date(2026, 10, 16), status=FinanceOperation.Status.PENDING,
            created_by=self.approver,
        )
        self.assertEqual(self.client.get(reverse("finance:detail", args=[unrelated.pk])).status_code, 404)
        response = self.client.get(reverse("finance:detail", args=[operation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, operation.number)

    def test_finance_list_requires_permissions(self):
        limited = User.objects.create_user(username="no-finance", password="Secure-finance-123")
        self.client.force_login(limited)
        self.assertEqual(self.client.get(reverse("finance:list")).status_code, 403)

    def test_operation_creator_can_view_own_detail_after_create(self):
        operation = self.create(amount="25.00", session=self.session)
        response = self.client.get(reverse("finance:detail", args=[operation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, operation.number)

    def test_original_operation_detail_links_its_compensating_reversal(self):
        operation = self.create(amount="25.00", session=self.session)
        reversal = reverse_operation(
            operation_id=operation.pk,
            category_id=self.expense_category.pk,
            reason="Corrección",
            user=self.approver,
            idempotency_key=uuid.uuid4(),
            cash_session_id=self.session.pk,
        )
        response = self.client.get(reverse("finance:detail", args=[operation.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reversal.number)
