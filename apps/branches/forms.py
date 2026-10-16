from django import forms

from .models import Branch


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ("name", "code", "address", "phone", "email", "responsible", "is_active")
        widgets = {"address": forms.TextInput(attrs={"placeholder": "Calle, número y zona"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"
