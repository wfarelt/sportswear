from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("ventas/", views.sales_report, name="sales"),
    path("inventario/", views.inventory_report, name="inventory"),
    path("finanzas/", views.finance_report, name="finance"),
    path("exportar/<slug:report_type>.<str:file_format>", views.export_report, name="export"),
]
