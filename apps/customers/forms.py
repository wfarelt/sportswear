from django import forms

from .models import Customer


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = (
            "full_name", "document_type", "document_number", "phone", "email",
            "address", "notes", "is_active",
        )
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-check-input" if isinstance(field.widget, forms.CheckboxInput)
                else "form-control"
            )
