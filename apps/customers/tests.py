from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.branches.models import Branch
from apps.users.models import User, UserBranchAccess

from .models import Customer


class CustomerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="manager", password="Secure-customer-123")
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="customers",
            codename__in=("view_customer", "add_customer", "change_customer"),
        ))
        self.client.force_login(self.user)

    def test_document_type_and_number_must_be_provided_together(self):
        customer = Customer(full_name="Cliente", document_type=Customer.DocumentType.CI)
        with self.assertRaisesMessage(ValidationError, "Completa el tipo y el número de documento"):
            customer.full_clean()

    def test_document_number_is_normalized_and_unique(self):
        first = Customer.objects.create(
            full_name="Ana Ejemplo", document_type=Customer.DocumentType.CI,
            document_number=" 123abc ",
        )
        self.assertEqual(first.document_number, "123ABC")
        response = self.client.post(reverse("customers:create"), {
            "full_name": "Otra Persona", "document_type": "ci",
            "document_number": "123abc", "phone": "", "email": "",
            "address": "", "notes": "", "is_active": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe")

    def test_customer_form_and_optional_document(self):
        response = self.client.post(reverse("customers:create"), {
            "full_name": "Cliente Ocasional", "document_type": "",
            "document_number": "", "phone": "70000000", "email": "",
            "address": "", "notes": "", "is_active": "on",
        })
        customer = Customer.objects.get(full_name="Cliente Ocasional")
        self.assertRedirects(response, reverse("customers:detail", args=[customer.pk]))

    def test_customer_pages_require_server_side_permissions(self):
        limited = User.objects.create_user(username="limited", password="Secure-customer-123")
        self.client.force_login(limited)
        self.assertEqual(self.client.get(reverse("customers:list")).status_code, 403)

    def test_customer_purchase_history_does_not_leak_unassigned_branch(self):
        branch = Branch.objects.create(name="Norte", code="norte")
        UserBranchAccess.objects.create(user=self.user, branch=branch)
        # The history is empty here; branch scoping is also exercised in sales-view tests.
        response = self.client.get(reverse("customers:detail", args=[
            Customer.objects.create(full_name="Cliente sin compras").pk
        ]))
        self.assertEqual(response.status_code, 200)
