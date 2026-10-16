import json
import uuid

from django import forms
from django.core.exceptions import ValidationError

from apps.branches.models import Branch
from apps.customers.models import Customer
from apps.products.models import ProductVariant

from .models import PaymentMethod, SaleLine


def authorized_branches(user):
    branches = Branch.objects.filter(is_active=True)
    if not user.has_global_branch_access:
        branches = branches.filter(authorized_users=user)
    return branches.order_by("name")


class SaleCreateForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), label="Sucursal")
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(), required=False, label="Cliente",
        empty_label="Venta a cliente ocasional",
    )
    discount_amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False,
        initial=0, label="Descuento autorizado (Bs)",
    )
    cart_json = forms.CharField(widget=forms.HiddenInput)
    payments_json = forms.CharField(widget=forms.HiddenInput)
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, initial_branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = authorized_branches(user)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True).order_by("full_name")
        if initial_branch:
            self.fields["branch"].initial = initial_branch
        self.initial.setdefault("cart_json", "[]")
        self.initial.setdefault("payments_json", "[]")
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.fields["branch"].widget.attrs["class"] = "form-select"
        self.fields["customer"].widget.attrs["class"] = "form-select"
        self.fields["discount_amount"].widget.attrs.update({"class": "form-control", "step": "0.01"})

    def clean_cart_json(self):
        raw = self.cleaned_data["cart_json"]
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("El carrito tiene un formato no válido.") from exc
        if not isinstance(data, list) or len(data) > 100:
            raise ValidationError("El carrito no es válido.")
        for row in data:
            if not isinstance(row, dict) or not str(row.get("variant_id", "")).isdigit():
                raise ValidationError("Una línea del carrito no es válida.")
            try:
                quantity = int(row.get("quantity", 0))
            except (TypeError, ValueError) as exc:
                raise ValidationError("La cantidad debe ser un número entero.") from exc
            if quantity <= 0:
                raise ValidationError("Las cantidades deben ser mayores que cero.")
        return data

    def clean_payments_json(self):
        raw = self.cleaned_data["payments_json"]
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("Los pagos tienen un formato no válido.") from exc
        if not isinstance(data, list) or len(data) > 10:
            raise ValidationError("La lista de pagos no es válida.")
        for row in data:
            if not isinstance(row, dict) or not str(row.get("method_id", "")).isdigit():
                raise ValidationError("Selecciona un medio de pago válido.")
        return data


class SaleReturnForm(forms.Form):
    reason = forms.CharField(max_length=255, label="Motivo", widget=forms.TextInput(attrs={"placeholder": "Talla incorrecta, producto defectuoso..."}))
    refund_method = forms.ModelChoiceField(
        queryset=PaymentMethod.objects.none(), label="Medio de reembolso"
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["refund_method"].queryset = PaymentMethod.objects.filter(is_active=True)
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs["class"] = "form-control"


class SaleReturnLineForm(forms.Form):
    sale_line_id = forms.IntegerField(widget=forms.HiddenInput)
    quantity = forms.IntegerField(min_value=0, label="Cantidad a devolver", initial=0)

    def __init__(self, *args, sale_line, **kwargs):
        self.sale_line = sale_line
        initial = kwargs.setdefault("initial", {})
        initial.setdefault("sale_line_id", sale_line.pk)
        initial.setdefault("quantity", 0)
        super().__init__(*args, **kwargs)
        self.fields["quantity"].widget.attrs.update({"class": "form-control", "max": sale_line.remaining_quantity})
