from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.audit.models import AuditEvent
from apps.branches.models import Branch
from apps.inventory.models import StockLevel
from apps.products.models import Brand, Category, Product, ProductVariant, Size
from apps.sales.models import Sale, SaleLine
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
        event = AuditEvent.objects.get(
            object_id=str(customer.pk), content_type__app_label="customers",
            content_type__model="customer", action=AuditEvent.Action.CREATE,
        )
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.after_data["full_name"], "Cliente Ocasional")

    def test_customer_update_records_before_and_after_values(self):
        customer = self._make_visible_customer("Nombre anterior")
        response = self.client.post(
            reverse("customers:update", args=[customer.pk]),
            {
                "full_name": "Nombre actualizado", "document_type": "", "document_number": "",
                "phone": "", "email": "", "address": "", "notes": "", "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("customers:detail", args=[customer.pk]))
        event = AuditEvent.objects.get(
            object_id=str(customer.pk), content_type__app_label="customers",
            content_type__model="customer", action=AuditEvent.Action.UPDATE,
        )
        self.assertEqual(event.before_data["full_name"], "Nombre anterior")
        self.assertEqual(event.after_data["full_name"], "Nombre actualizado")

    def test_customer_pages_require_server_side_permissions(self):
        limited = User.objects.create_user(username="limited", password="Secure-customer-123")
        self.client.force_login(limited)
        self.assertEqual(self.client.get(reverse("customers:list")).status_code, 403)

    def test_customer_purchase_history_does_not_leak_unassigned_branch(self):
        customer = self._make_visible_customer("Cliente local")
        other_branch = Branch.objects.create(name="Norte", code="norte")
        other_user = User.objects.create_user(username="other-seller", password="Secure-customer-123")
        UserBranchAccess.objects.create(user=other_user, branch=other_branch)
        other_user.user_permissions.add(Permission.objects.get(
            content_type__app_label="customers", codename="view_customer"
        ))
        remote_customer = self._make_customer_with_sale(other_user, other_branch, "Cliente remoto")
        self.assertEqual(
            self.client.get(reverse("customers:detail", args=[remote_customer.pk])).status_code,
            404,
        )
        response = self.client.get(reverse("customers:detail", args=[customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Cliente remoto")

        self.client.force_login(other_user)
        self.assertEqual(
            self.client.get(reverse("customers:detail", args=[customer.pk])).status_code,
            404,
        )

    def _make_customer_with_sale(self, seller, branch, customer_name):
        category = Category.objects.create(name=f"Categoría {branch.code}")
        brand = Brand.objects.create(name=f"Marca {branch.code}")
        product = Product.objects.create(
            internal_code=f"CODE-{branch.code}", name=f"Producto {branch.code}",
            category=category, brand=brand, cost="10.00", sale_price="20.00",
        )
        size = Size.objects.create(name=f"M-{branch.code}")
        variant = ProductVariant.objects.create(
            product=product, sku=f"SKU-{branch.code}", size=size
        )
        StockLevel.objects.create(branch=branch, variant=variant, quantity=1)
        customer = Customer.objects.create(full_name=customer_name)
        sale = Sale.objects.create(
            number=f"T-{branch.code}", branch=branch, seller=seller, customer=customer,
            subtotal="20.00", discount_amount="0.00", total="20.00",
            status=Sale.Status.CONFIRMED,
        )
        SaleLine.objects.create(
            sale=sale, variant=variant, product_name=product.name,
            size_name=size.name, sku=variant.sku, quantity=1,
            unit_price="20.00", unit_cost="10.00", discount_amount="0.00",
            tax_rate="0.00", tax_amount="0.00", line_total="20.00",
        )
        return customer

    def _make_visible_customer(self, name):
        branch = Branch.objects.create(name="Central", code="central")
        UserBranchAccess.objects.create(user=self.user, branch=branch)
        return self._make_customer_with_sale(self.user, branch, name)
