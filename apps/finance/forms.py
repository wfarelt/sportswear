import uuid
from decimal import Decimal
from pathlib import Path

from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.branches.models import Branch
from apps.cash.models import CashSession
from apps.cash.services import get_open_sessions
from apps.sales.models import PaymentMethod

from .models import FinanceAccount, FinanceCategory, FinanceOperation


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


def authorized_branches(user):
    branches = Branch.objects.filter(is_active=True)
    if not user.has_global_branch_access:
        branches = branches.filter(authorized_users=user)
    return branches.order_by("name")


class FinanceOperationForm(BootstrapFormMixin, forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), label="Sucursal")
    category = forms.ModelChoiceField(queryset=FinanceCategory.objects.none(), label="Categoría")
    account = forms.ModelChoiceField(queryset=FinanceAccount.objects.none(), label="Cuenta")
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"), label="Importe (Bs)")
    concept = forms.CharField(max_length=180, label="Concepto")
    operation_date = forms.DateField(
        label="Fecha", widget=forms.DateInput(attrs={"type": "date"})
    )
    payment_method = forms.ModelChoiceField(
        queryset=PaymentMethod.objects.none(), required=False, label="Medio de pago"
    )
    cash_session = forms.ModelChoiceField(
        queryset=CashSession.objects.none(), required=False,
        label="Turno abierto (solo efectivo)",
    )
    notes = forms.CharField(required=False, label="Observaciones", widget=forms.Textarea())
    attachment = forms.FileField(required=False, label="Comprobante adjunto")
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, kind, **kwargs):
        super().__init__(*args, **kwargs)
        branches = authorized_branches(user)
        self.fields["branch"].queryset = branches
        self.fields["category"].queryset = FinanceCategory.objects.filter(
            kind=kind, is_active=True
        ).order_by("name")
        account_scope = Q(branch__in=branches)
        if user.has_global_branch_access or user.has_perm("finance.use_general_finance_accounts"):
            account_scope |= Q(branch__isnull=True, kind=FinanceAccount.Kind.BANK)
        self.fields["account"].queryset = FinanceAccount.objects.filter(
            is_active=True
        ).filter(account_scope).select_related("branch", "cash_resource")
        self.fields["payment_method"].queryset = PaymentMethod.objects.filter(is_active=True)
        sessions = get_open_sessions(user)
        branch_id = self.data.get("branch") if self.is_bound else None
        if branch_id and str(branch_id).isdigit():
            sessions = sessions.filter(resource__branch_id=branch_id)
            self.fields["account"].queryset = self.fields["account"].queryset.filter(
                Q(branch_id=branch_id)
                | Q(branch__isnull=True, kind=FinanceAccount.Kind.BANK)
            )
        self.fields["cash_session"].queryset = sessions
        self.initial.setdefault("operation_date", timezone.localdate())
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.fields["amount"].widget.attrs["step"] = "0.01"
        self.style_fields()
        self.kind = kind

    def clean(self):
        cleaned = super().clean()
        branch = cleaned.get("branch")
        account = cleaned.get("account")
        session = cleaned.get("cash_session")
        if branch and account and account.branch_id and account.branch_id != branch.pk:
            self.add_error("account", "La cuenta no pertenece a la sucursal seleccionada.")
        if account and account.kind == FinanceAccount.Kind.CASH:
            if not account.cash_resource_id:
                self.add_error("account", "La cuenta de efectivo no tiene una caja física asociada.")
            if session and session.resource_id != account.cash_resource_id:
                self.add_error("cash_session", "El turno debe pertenecer a la caja de la cuenta.")
            method = cleaned.get("payment_method")
            if method and not method.affects_cash:
                self.add_error("payment_method", "Este medio de pago no representa efectivo físico.")
        elif session:
            self.add_error("cash_session", "Las operaciones bancarias no usan turnos de caja.")
        return cleaned

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if attachment is None:
            return attachment
        if attachment.size > 10 * 1024 * 1024:
            raise forms.ValidationError("El comprobante no puede superar 10 MB.")
        extension = Path(attachment.name).suffix.lower()
        if extension not in {".pdf", ".jpg", ".jpeg", ".png"}:
            raise forms.ValidationError("Solo se admiten comprobantes PDF, JPG o PNG.")
        signatures = {
            ".pdf": b"%PDF-",
            ".jpg": b"\xff\xd8\xff",
            ".jpeg": b"\xff\xd8\xff",
            ".png": b"\x89PNG\r\n\x1a\n",
        }
        if not attachment.read(16).startswith(signatures[extension]):
            raise forms.ValidationError("El contenido no coincide con el tipo de comprobante indicado.")
        attachment.seek(0)
        return attachment


class ApproveFinanceOperationForm(BootstrapFormMixin, forms.Form):
    cash_session = forms.ModelChoiceField(
        queryset=CashSession.objects.none(), required=False,
        label="Turno abierto para contabilizar (efectivo)",
    )

    def __init__(self, *args, user, operation, **kwargs):
        super().__init__(*args, **kwargs)
        sessions = get_open_sessions(user, operation.branch_id)
        account = operation.account
        if account.kind == FinanceAccount.Kind.CASH:
            sessions = sessions.filter(resource_id=account.cash_resource_id)
            self.fields["cash_session"].required = True
        else:
            self.fields["cash_session"].widget = forms.HiddenInput()
        self.fields["cash_session"].queryset = sessions
        self.style_fields()


class RejectFinanceOperationForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=255, label="Motivo del rechazo")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()


class ReverseFinanceOperationForm(BootstrapFormMixin, forms.Form):
    category = forms.ModelChoiceField(queryset=FinanceCategory.objects.none(), label="Categoría para la reversión")
    reason = forms.CharField(max_length=255, label="Motivo de la reversión")
    cash_session = forms.ModelChoiceField(
        queryset=CashSession.objects.none(), required=False,
        label="Turno abierto para contabilizar (efectivo)",
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, user, operation, **kwargs):
        super().__init__(*args, **kwargs)
        opposite = (
            FinanceOperation.Kind.EXPENSE
            if operation.kind == FinanceOperation.Kind.INCOME
            else FinanceOperation.Kind.INCOME
        )
        self.fields["category"].queryset = FinanceCategory.objects.filter(
            kind=opposite, is_active=True
        ).order_by("name")
        sessions = get_open_sessions(user, operation.branch_id)
        if operation.account.kind == FinanceAccount.Kind.CASH:
            sessions = sessions.filter(resource_id=operation.account.cash_resource_id)
            self.fields["cash_session"].required = True
        else:
            self.fields["cash_session"].widget = forms.HiddenInput()
        self.fields["cash_session"].queryset = sessions
        self.initial.setdefault("idempotency_key", uuid.uuid4())
        self.style_fields()
