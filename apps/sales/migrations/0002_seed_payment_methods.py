from django.db import migrations


def create_default_methods(apps, schema_editor):
    PaymentMethod = apps.get_model("sales", "PaymentMethod")
    defaults = [
        {"code": "cash", "name": "Efectivo", "affects_cash": True, "sort_order": 1},
        {"code": "bank-transfer", "name": "Transferencia bancaria", "affects_cash": False, "sort_order": 2},
        {"code": "qr", "name": "QR", "affects_cash": False, "sort_order": 3},
        {"code": "card", "name": "Tarjeta", "affects_cash": False, "sort_order": 4},
    ]
    for method in defaults:
        PaymentMethod.objects.update_or_create(code=method["code"], defaults=method)


def remove_default_methods(apps, schema_editor):
    PaymentMethod = apps.get_model("sales", "PaymentMethod")
    PaymentMethod.objects.filter(code__in=["cash", "bank-transfer", "qr", "card"]).delete()


class Migration(migrations.Migration):
    dependencies = [("sales", "0001_initial")]

    operations = [migrations.RunPython(create_default_methods, remove_default_methods)]
