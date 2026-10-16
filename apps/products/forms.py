from django import forms
from django.db.models import Q

from .models import Brand, Category, Color, Product, ProductVariant, Size


class BootstrapModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs["class"] = "form-control"
                field.widget.attrs.setdefault("rows", 3)
            else:
                field.widget.attrs["class"] = "form-control"


class CategoryForm(BootstrapModelForm):
    class Meta:
        model = Category
        fields = ("name", "parent", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["parent"].queryset = Category.objects.exclude(pk=self.instance.pk)


class BrandForm(BootstrapModelForm):
    class Meta:
        model = Brand
        fields = ("name", "is_active")


class SizeForm(BootstrapModelForm):
    class Meta:
        model = Size
        fields = ("name", "sort_order", "is_active")


class ColorForm(BootstrapModelForm):
    class Meta:
        model = Color
        fields = ("name", "hex_code", "is_active")
        widgets = {"hex_code": forms.TextInput(attrs={"type": "color", "class": "form-control form-control-color"})}


class ProductForm(BootstrapModelForm):
    class Meta:
        model = Product
        fields = (
            "internal_code", "name", "model_name", "supplier_name", "description",
            "category", "brand", "cost",
            "sale_price", "promotional_price", "tax_rate", "image", "is_active",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "cost": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "sale_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "promotional_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "tax_rate": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = Category.objects.filter(is_active=True)
        brands = Brand.objects.filter(is_active=True)
        if self.instance.pk:
            categories = Category.objects.filter(Q(is_active=True) | Q(pk=self.instance.category_id))
            brands = Brand.objects.filter(Q(is_active=True) | Q(pk=self.instance.brand_id))
        self.fields["category"].queryset = categories
        self.fields["brand"].queryset = brands


class ProductVariantForm(BootstrapModelForm):
    class Meta:
        model = ProductVariant
        fields = (
            "sku", "barcode", "size", "color", "cost", "sale_price",
            "promotional_price", "is_active",
        )
        widgets = {
            "cost": forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "Usar costo base"}),
            "sale_price": forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "Usar precio base"}),
            "promotional_price": forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "Sin promoción"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sizes = Size.objects.filter(is_active=True)
        colors = Color.objects.filter(is_active=True)
        if self.instance.pk:
            sizes = Size.objects.filter(Q(is_active=True) | Q(pk=self.instance.size_id))
            colors = Color.objects.filter(Q(is_active=True) | Q(pk=self.instance.color_id))
        self.fields["size"].queryset = sizes
        self.fields["color"].queryset = colors
        self.fields["cost"].required = False
        self.fields["sale_price"].required = False
        self.fields["promotional_price"].required = False
