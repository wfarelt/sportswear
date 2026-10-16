from decimal import Decimal

from django.core.exceptions import ValidationError
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.products.forms import ProductVariantForm
from apps.products.models import Brand, Category, Color, Product, ProductVariant, Size
from apps.users.models import User


class ProductTestDataMixin:
    def make_product(self, **overrides):
        category, _ = Category.objects.get_or_create(name="Poleras")
        brand, _ = Brand.objects.get_or_create(name="Marca Uno")
        values = {
            "internal_code": "POL-001",
            "name": "Polera deportiva",
            "category": category,
            "brand": brand,
            "cost": Decimal("40.00"),
            "sale_price": Decimal("80.00"),
        }
        values.update(overrides)
        return Product.objects.create(**values)

    def make_user(self, **kwargs):
        user = User.objects.create_user(
            username="manager", password="Secure-test-password-123", **kwargs
        )
        user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="products",
                codename__in=[
                    "view_product", "add_product", "change_product",
                    "add_productvariant", "change_productvariant",
                    "view_category", "add_category", "change_category",
                    "view_brand", "add_brand", "change_brand",
                    "view_size", "add_size", "change_size",
                    "view_color", "add_color", "change_color",
                ],
            )
        )
        return user


class ProductModelTests(ProductTestDataMixin, TestCase):
    def test_category_brand_and_case_insensitive_name_constraints(self):
        Category.objects.create(name="Poleras")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Category.objects.create(name="POLERAS")

    def test_product_rejects_promotion_above_base_price(self):
        product = Product(
            internal_code="POL-001",
            name="Polera deportiva",
            category=Category.objects.create(name="Poleras"),
            brand=Brand.objects.create(name="Marca Uno"),
            cost=Decimal("40.00"),
            sale_price=Decimal("80.00"),
            promotional_price=Decimal("90.00"),
        )
        with self.assertRaises(ValidationError):
            product.full_clean()

    def test_variant_overrides_inherit_product_prices_when_blank(self):
        product = self.make_product()
        size = Size.objects.create(name="M")
        variant = ProductVariant.objects.create(product=product, sku="POL-001-M", size=size)
        self.assertEqual(variant.effective_cost, Decimal("40.00"))
        self.assertEqual(variant.effective_sale_price, Decimal("80.00"))

    def test_variant_needs_size_or_color(self):
        product = self.make_product()
        variant = ProductVariant(product=product, sku="POL-EMPTY")
        with self.assertRaises(ValidationError):
            variant.full_clean()

    def test_variant_sale_price_cannot_fall_below_inherited_promotion(self):
        product = self.make_product(promotional_price=Decimal("70.00"))
        size = Size.objects.create(name="M")
        variant = ProductVariant(
            product=product,
            sku="POL-M-LOW",
            size=size,
            sale_price=Decimal("60.00"),
        )
        with self.assertRaises(ValidationError):
            variant.full_clean()

    def test_variant_option_combination_is_unique(self):
        product = self.make_product()
        size = Size.objects.create(name="M")
        color = Color.objects.create(name="Negro", hex_code="#000000")
        ProductVariant.objects.create(product=product, sku="POL-M-NEG", size=size, color=color)
        form = ProductVariantForm(
            data={"sku": "POL-M-NEG-2", "size": size.pk, "color": color.pk, "is_active": "on"},
            instance=ProductVariant(product=product),
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(form.non_field_errors())


class ProductViewTests(ProductTestDataMixin, TestCase):
    def setUp(self):
        self.user = self.make_user()
        self.product = self.make_product()
        self.client.force_login(self.user)

    def test_product_list_searches_variant_sku(self):
        size = Size.objects.create(name="M")
        ProductVariant.objects.create(product=self.product, sku="POL-001-M", size=size)
        response = self.client.get(reverse("products:list"), {"q": "POL-001-M"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Polera deportiva")

    def test_product_detail_renders_variant_catalog(self):
        size = Size.objects.create(name="M")
        ProductVariant.objects.create(product=self.product, sku="POL-001-M", size=size)
        response = self.client.get(reverse("products:detail", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Variantes vendibles")
        self.assertContains(response, "POL-001-M")

    def test_product_create_requires_permission(self):
        user = User.objects.create_user(username="limited", password="test-password-123")
        self.client.force_login(user)
        response = self.client.get(reverse("products:create"))
        self.assertEqual(response.status_code, 403)

    def test_reference_page_requires_specific_permission(self):
        response = self.client.get(reverse("products:reference_list", args=["categorias"]))
        self.assertEqual(response.status_code, 200)

    def test_variant_cannot_be_edited_through_another_product_url(self):
        size = Size.objects.create(name="M")
        variant = ProductVariant.objects.create(product=self.product, sku="POL-001-M", size=size)
        other_product = self.make_product(internal_code="SHORT-001", name="Short deportivo")
        response = self.client.get(
            reverse("products:variant_update", args=[other_product.pk, variant.pk])
        )
        self.assertEqual(response.status_code, 404)
