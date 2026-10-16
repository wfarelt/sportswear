import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.forms import formset_factory

from apps.branches.models import Branch
from apps.products.models import ProductVariant
from .models import StockLevel, StockTransferLine


def authorized_branches(user):
    branches = Branch.objects.filter(is_active=True)
    if user.has_global_branch_access:
        return branches.order_by("name")
    return branches.filter(authorized_users=user).order_by("name")


def active_variants():
    return ProductVariant.objects.filter(
        is_active=True, product__is_active=True
    ).select_related("product", "size", "color").order_by("product__name", "sku")


class BootstrapFormMixin:
    def style_fields(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            elif isinstance(field.widget, forms.HiddenInput):
                continue
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs["class"] = "form-control"
                field.widget.attrs.setdefault("rows", 3)
            else:
                field.widget.attrs["class"] = "form-control"


class StockMovementForm(BootstrapFormMixin, forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), label="Sucursal")
    variant = forms.ModelChoiceField(queryset=ProductVariant.objects.none(), label="Producto / variante")
    quantity = forms.IntegerField(min_value=1, label="Cantidad")
    unit_cost = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False,
        label="Costo unitario", help_text="Opcional para entradas; si se omite se usa el costo del catálogo.",
    )
    reason = forms.CharField(max_length=255, label="Motivo", widget=forms.TextInput(attrs={"placeholder": "Compra, devolución, merma autorizada..."}))
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, initial_branch=None, include_cost=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = authorized_branches(user)
        self.fields["variant"].queryset = active_variants()
        if initial_branch:
            self.fields["branch"].initial = initial_branch
        if not include_cost:
            self.fields.pop("unit_cost")
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.style_fields()


class StockAdjustmentForm(BootstrapFormMixin, forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), label="Sucursal")
    variant = forms.ModelChoiceField(queryset=ProductVariant.objects.none(), label="Producto / variante")
    counted_quantity = forms.IntegerField(min_value=0, label="Cantidad contada")
    reason = forms.CharField(max_length=255, label="Motivo del ajuste", widget=forms.TextInput(attrs={"placeholder": "Conteo físico, diferencia identificada..."}))
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, initial_branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = authorized_branches(user)
        self.fields["variant"].queryset = active_variants()
        if initial_branch:
            self.fields["branch"].initial = initial_branch
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.style_fields()


class StockMinimumForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = StockLevel
        fields = ("minimum_quantity",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class TransferCreateForm(BootstrapFormMixin, forms.Form):
    origin = forms.ModelChoiceField(queryset=Branch.objects.none(), label="Sucursal de origen")
    destination = forms.ModelChoiceField(queryset=Branch.objects.none(), label="Sucursal de destino")
    notes = forms.CharField(required=False, label="Observaciones", widget=forms.Textarea(attrs={"rows": 2}))
    creation_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        branches = authorized_branches(user)
        self.fields["origin"].queryset = branches
        self.fields["destination"].queryset = branches
        self.initial.setdefault("creation_key", uuid.uuid4())
        self.style_fields()

    def clean(self):
        cleaned_data = super().clean()
        origin = cleaned_data.get("origin")
        destination = cleaned_data.get("destination")
        if origin and destination and origin.pk == destination.pk:
            self.add_error("destination", "El destino debe ser distinto del origen.")
        return cleaned_data


class TransferLineForm(BootstrapFormMixin, forms.Form):
    variant = forms.ModelChoiceField(queryset=ProductVariant.objects.none(), label="Variante")
    quantity = forms.IntegerField(min_value=1, label="Cantidad")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["variant"].queryset = active_variants()
        self.style_fields()


class BaseTransferLineFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        selected = [form.cleaned_data["variant"].pk for form in self.forms if form.cleaned_data]
        if len(selected) != len(set(selected)):
            raise ValidationError("No repitas una variante en la transferencia.")


TransferLineFormSet = formset_factory(
    TransferLineForm, formset=BaseTransferLineFormSet,
    extra=3, min_num=1, validate_min=True,
)


class TransferReceiptForm(BootstrapFormMixin, forms.Form):
    notes = forms.CharField(required=False, max_length=255, label="Observaciones / diferencias")
    finalize = forms.BooleanField(
        required=False, label="Cerrar recepción y registrar como faltante lo no recibido",
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.style_fields()


class ActionKeyForm(BootstrapFormMixin, forms.Form):
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault("idempotency_key", uuid.uuid4())


class TransferReceiptLineForm(BootstrapFormMixin, forms.Form):
    line = forms.ModelChoiceField(queryset=StockTransferLine.objects.none(), widget=forms.HiddenInput)
    quantity = forms.IntegerField(min_value=0, label="Recibidas ahora")

    def __init__(self, *args, transfer, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["line"].queryset = transfer.lines.select_related(
            "variant__product", "variant__size", "variant__color"
        )
        self.style_fields()


class BaseTransferReceiptLineFormSet(forms.BaseFormSet):
    def __init__(self, *args, transfer, **kwargs):
        self.transfer = transfer
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["transfer"] = self.transfer
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        line_ids = [form.cleaned_data["line"].pk for form in self.forms if form.cleaned_data]
        if len(line_ids) != len(set(line_ids)):
            raise ValidationError("Cada línea de la transferencia debe aparecer una sola vez.")


TransferReceiptLineFormSet = formset_factory(
    TransferReceiptLineForm, formset=BaseTransferReceiptLineFormSet,
    extra=0, min_num=1, validate_min=True,
)
