from datetime import timedelta

from django import forms
from django.utils import timezone

from apps.branches.models import Branch


def authorized_branches(user):
    branches = Branch.objects.filter(is_active=True)
    if not user.has_global_branch_access:
        branches = branches.filter(authorized_users=user)
    return branches.order_by("name")


class ReportFilterForm(forms.Form):
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(), required=False, label="Sucursal"
    )
    date_from = forms.DateField(
        required=False, label="Desde", widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        required=False, label="Hasta", widget=forms.DateInput(attrs={"type": "date"})
    )
    group_by = forms.ChoiceField(
        required=False,
        label="Agrupar por",
        choices=(
            ("day", "Día"), ("branch", "Sucursal"), ("seller", "Vendedor"),
            ("product", "Producto"), ("category", "Categoría"),
            ("brand", "Marca"), ("variant", "Talla / color"),
            ("payment", "Medio de pago"), ("status", "Estado"),
        ),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = authorized_branches(user)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, (forms.Select, forms.SelectDateWidget)) else "form-control"

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("date_from")
        end = cleaned.get("date_to")
        if start and end and start > end:
            raise forms.ValidationError("La fecha inicial debe ser anterior o igual a la fecha final.")
        if start and end and (end - start).days > 366:
            raise forms.ValidationError("El período máximo de consulta es de 366 días.")
        return cleaned

    def date_range(self):
        start = self.cleaned_data.get("date_from")
        end = self.cleaned_data.get("date_to")
        today = timezone.localdate()
        if not start and not end:
            return today - timedelta(days=29), today
        return start or end, end or start
