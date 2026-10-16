import json
import uuid

from django import forms
from django.core.exceptions import ValidationError

from apps.branches.models import Branch
from apps.cash.models import CashSession
from apps.cash.services import get_open_sessions
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
    cash_session = forms.ModelChoiceField(queryset=CashSession.objects.none(), label="Turno de caja")
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
        if initial_branch:
            self.fields["branch"].initial = initial_branch
        selected_branch_id = (
            self.data.get("branch") if self.is_bound else
            (initial_branch.pk if initial_branch else None)
        )
        self.fields["cash_session"].queryset = get_open_sessions(user, selected_branch_id)
        self.fields["customer"].queryset = Customer.objects.filter(is_active=True).order_by("full_name")
        self.initial.setdefault("cart_json", "[]")
        self.initial.setdefault("payments_json", "[]")
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.fields["branch"].widget.attrs["class"] = "form-select"
        self.fields["cash_session"].widget.attrs["class"] = "form-select"
        self.fields["customer"].widget.attrs["class"] = "form-select"
        self.fields["discount_amount"].widget.attrs.update({"class": "form-control", "step": "0.01"})

    def clean(self):
        cleaned_data = super().clean()
        branch = cleaned_data.get("branch")
        cash_session = cleaned_data.get("cash_session")
        if branch and cash_session and cash_session.resource.branch_id != branch.pk:
            self.add_error("cash_session", "El turno de caja debe pertenecer a la sucursal seleccionada.")
        return cleaned_data

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
    cash_session = forms.ModelChoiceField(queryset=CashSession.objects.none(), label="Turno que registra el reembolso")
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, branch_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["refund_method"].queryset = PaymentMethod.objects.filter(is_active=True)
        self.fields["cash_session"].queryset = get_open_sessions(user, branch_id)
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


class SaleCancelForm(forms.Form):
    reason = forms.CharField(max_length=255, label="Motivo obligatorio para anular")
    cash_session = forms.ModelChoiceField(
        queryset=CashSession.objects.none(), label="Turno que registra el reembolso"
    )

    def __init__(self, *args, user, branch_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cash_session"].queryset = get_open_sessions(user, branch_id)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
