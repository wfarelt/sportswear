import uuid

from django import forms

from apps.branches.models import Branch
from apps.users.models import User

from .models import CashMovement, CashResource, CashSession
from .services import get_open_sessions


def authorized_branches(user):
    branches = Branch.objects.filter(is_active=True)
    if not user.has_global_branch_access:
        branches = branches.filter(authorized_users=user)
    return branches.order_by("name")


class BootstrapFormMixin:
    def style_fields(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.HiddenInput):
                continue
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs.update({"class": "form-control", "rows": 3})
            else:
                field.widget.attrs["class"] = "form-control"


class CashResourceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CashResource
        fields = ("branch", "code", "name", "is_active")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = authorized_branches(user)
        self.style_fields()


class OpenSessionForm(BootstrapFormMixin, forms.Form):
    resource = forms.ModelChoiceField(queryset=CashResource.objects.none(), label="Caja")
    opening_balance = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, label="Saldo inicial (Bs)"
    )
    notes = forms.CharField(required=False, max_length=255, label="Observaciones", widget=forms.Textarea())

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["resource"].queryset = CashResource.objects.filter(
            branch__in=authorized_branches(user), branch__is_active=True, is_active=True
        ).order_by("branch__name", "name")
        self.style_fields()


class ManualMovementForm(BootstrapFormMixin, forms.Form):
    session = forms.ModelChoiceField(queryset=CashSession.objects.none(), label="Turno abierto")
    kind = forms.ChoiceField(
        choices=(
            (CashMovement.Kind.WITHDRAWAL, "Retiro de efectivo"),
        ),
        label="Tipo de movimiento",
    )
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0.01, label="Importe (Bs)")
    reason = forms.CharField(max_length=255, label="Motivo")
    reference = forms.CharField(required=False, max_length=100, label="Referencia / comprobante")
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, initial_session=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["session"].queryset = get_open_sessions(user)
        if initial_session:
            self.fields["session"].initial = initial_session
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.style_fields()


class CloseSessionForm(BootstrapFormMixin, forms.Form):
    counted_cash = forms.DecimalField(max_digits=12, decimal_places=2, min_value=0, label="Efectivo contado (Bs)")
    notes = forms.CharField(required=False, max_length=255, label="Observaciones de cierre")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class CashSessionFilterForm(BootstrapFormMixin, forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False, label="Sucursal")
    status = forms.ChoiceField(
        choices=(("", "Todos los turnos"), (CashSession.Status.OPEN, "Abiertos"), (CashSession.Status.CLOSED, "Cerrados")),
        required=False, label="Estado",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = authorized_branches(user)
        self.style_fields()
