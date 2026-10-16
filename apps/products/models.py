from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class ActiveNamedModel(models.Model):
    name = models.CharField("nombre", max_length=100)
    is_active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField("creado el", auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(ActiveNamedModel):
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="subcategories",
        null=True,
        blank=True,
        verbose_name="categoría principal",
    )

    class Meta(ActiveNamedModel.Meta):
        verbose_name = "categoría"
        verbose_name_plural = "categorías"
        constraints = [
            models.UniqueConstraint(Lower("name"), name="unique_category_name_ci")
        ]

    def clean(self):
        super().clean()
        if self.pk and self.parent_id == self.pk:
            from django.core.exceptions import ValidationError

            raise ValidationError({"parent": "Una categoría no puede ser su propia categoría principal."})


class Brand(ActiveNamedModel):
    class Meta(ActiveNamedModel.Meta):
        verbose_name = "marca"
        verbose_name_plural = "marcas"
        constraints = [models.UniqueConstraint(Lower("name"), name="unique_brand_name_ci")]


class Size(ActiveNamedModel):
    sort_order = models.PositiveSmallIntegerField("orden", default=0)

    class Meta(ActiveNamedModel.Meta):
        ordering = ["sort_order", "name"]
        verbose_name = "talla"
        verbose_name_plural = "tallas"
        constraints = [models.UniqueConstraint(Lower("name"), name="unique_size_name_ci")]


class Color(ActiveNamedModel):
    hex_code = models.CharField("código hexadecimal", max_length=7, default="#808080")

    class Meta(ActiveNamedModel.Meta):
        verbose_name = "color"
        verbose_name_plural = "colores"
        constraints = [models.UniqueConstraint(Lower("name"), name="unique_color_name_ci")]

    def clean(self):
        super().clean()
        import re
        from django.core.exceptions import ValidationError

        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.hex_code or ""):
            raise ValidationError({"hex_code": "Ingresa un color hexadecimal válido, por ejemplo #1A2B3C."})


class Product(models.Model):
    internal_code = models.CharField("código interno", max_length=40, unique=True)
    name = models.CharField("nombre", max_length=180, db_index=True)
    description = models.TextField("descripción", blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products", verbose_name="categoría"
    )
    brand = models.ForeignKey(
        Brand, on_delete=models.PROTECT, related_name="products", verbose_name="marca"
    )
    cost = models.DecimalField(
        "costo base", max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    sale_price = models.DecimalField(
        "precio de venta base", max_digits=10, decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    promotional_price = models.DecimalField(
        "precio promocional base", max_digits=10, decimal_places=2,
        null=True, blank=True, validators=[MinValueValidator(0)],
    )
    tax_rate = models.DecimalField(
        "impuesto (%)", max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    image = models.ImageField("imagen principal", upload_to="products/", blank=True)
    is_active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField("creado el", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado el", auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["name", "is_active"], name="product_name_active_idx"),
            models.Index(fields=["category", "brand"], name="product_cat_brand_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(cost__gte=0), name="product_cost_nonnegative"),
            models.CheckConstraint(condition=Q(sale_price__gte=0), name="product_price_nonnegative"),
            models.CheckConstraint(
                condition=Q(promotional_price__isnull=True) | Q(promotional_price__lte=models.F("sale_price")),
                name="product_promo_not_above_price",
            ),
        ]
        verbose_name = "producto"
        verbose_name_plural = "productos"

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if self.promotional_price is not None and self.promotional_price > self.sale_price:
            errors["promotional_price"] = "El precio promocional no puede superar el precio de venta."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.name} ({self.internal_code})"


class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField("SKU", max_length=64, unique=True)
    barcode = models.CharField("código de barras", max_length=64, null=True, blank=True, unique=True)
    size = models.ForeignKey(
        Size, on_delete=models.PROTECT, related_name="variants", null=True, blank=True,
        verbose_name="talla",
    )
    color = models.ForeignKey(
        Color, on_delete=models.PROTECT, related_name="variants", null=True, blank=True,
        verbose_name="color",
    )
    cost = models.DecimalField(
        "costo", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    sale_price = models.DecimalField(
        "precio de venta", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    promotional_price = models.DecimalField(
        "precio promocional", max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField("activa", default=True)
    created_at = models.DateTimeField("creada el", auto_now_add=True)

    class Meta:
        ordering = ["product__name", "size__sort_order", "color__name", "sku"]
        indexes = [
            models.Index(fields=["product", "is_active"], name="variant_product_active_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(size__isnull=False) | Q(color__isnull=False),
                name="variant_has_an_option",
            ),
            models.UniqueConstraint(
                fields=["product", "size", "color"],
                condition=Q(size__isnull=False, color__isnull=False),
                name="unique_variant_size_color",
            ),
            models.UniqueConstraint(
                fields=["product", "size"],
                condition=Q(size__isnull=False, color__isnull=True),
                name="unique_variant_size_only",
            ),
            models.UniqueConstraint(
                fields=["product", "color"],
                condition=Q(size__isnull=True, color__isnull=False),
                name="unique_variant_color_only",
            ),
            models.CheckConstraint(
                condition=Q(cost__isnull=True) | Q(cost__gte=0), name="variant_cost_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(sale_price__isnull=True) | Q(sale_price__gte=0),
                name="variant_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(promotional_price__isnull=True)
                | Q(sale_price__isnull=True)
                | Q(promotional_price__lte=models.F("sale_price")),
                name="variant_promo_not_above_price",
            ),
        ]
        verbose_name = "variante de producto"
        verbose_name_plural = "variantes de productos"

    def clean(self):
        from django.core.exceptions import ValidationError

        if not self.size_id and not self.color_id:
            raise ValidationError("Cada variante debe tener al menos una característica: talla o color.")
        if self.product_id:
            effective_price = self.sale_price if self.sale_price is not None else self.product.sale_price
            effective_promotion = (
                self.promotional_price
                if self.promotional_price is not None
                else self.product.promotional_price
            )
            if effective_promotion is not None and effective_price is not None:
                if effective_promotion > effective_price:
                    raise ValidationError({
                        "promotional_price":
                            "El precio promocional efectivo no puede superar el precio de venta de la variante."
                    })
        if self.product_id:
            duplicates = ProductVariant.objects.filter(
                product_id=self.product_id, size_id=self.size_id, color_id=self.color_id
            )
            if self.pk:
                duplicates = duplicates.exclude(pk=self.pk)
            if duplicates.exists():
                raise ValidationError("Ya existe una variante con esta combinación de talla y color.")

    @property
    def effective_cost(self):
        return self.cost if self.cost is not None else self.product.cost

    @property
    def effective_sale_price(self):
        return self.sale_price if self.sale_price is not None else self.product.sale_price

    @property
    def effective_promotional_price(self):
        if self.promotional_price is not None:
            return self.promotional_price
        return self.product.promotional_price

    def __str__(self):
        options = " / ".join(value for value in (str(self.size) if self.size_id else "", str(self.color) if self.color_id else "") if value)
        return f"{self.product.name} — {options or self.sku}"
