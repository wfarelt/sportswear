from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("", views.operation_list, name="list"),
    path("ingresos/nuevo/", views.income_create, name="income_create"),
    path("egresos/nuevo/", views.expense_create, name="expense_create"),
    path("<int:pk>/comprobante/", views.operation_attachment, name="attachment"),
    path("<int:pk>/", views.operation_detail, name="detail"),
    path("<int:pk>/autorizar/", views.operation_approve, name="approve"),
    path("<int:pk>/rechazar/", views.operation_reject, name="reject"),
    path("<int:pk>/revertir/", views.operation_reverse, name="reverse"),
]
