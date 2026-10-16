from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.stock_list, name="stock_list"),
    path("existencias/<int:pk>/minimo/", views.update_stock_minimum, name="stock_minimum"),
    path("entradas/nueva/", views.receipt_create, name="receipt_create"),
    path("salidas/nueva/", views.issue_create, name="issue_create"),
    path("ajustes/nuevo/", views.adjustment_create, name="adjustment_create"),
    path("movimientos/", views.movement_list, name="movement_list"),
    path("movimientos/<int:pk>/", views.operation_detail, name="operation_detail"),
    path("transferencias/", views.transfer_list, name="transfer_list"),
    path("transferencias/nueva/", views.transfer_create, name="transfer_create"),
    path("transferencias/<int:pk>/", views.transfer_detail, name="transfer_detail"),
    path("transferencias/<int:pk>/enviar/", views.transfer_submit, name="transfer_submit"),
    path("transferencias/<int:pk>/despachar/", views.transfer_dispatch_view, name="transfer_dispatch"),
    path("transferencias/<int:pk>/recibir/", views.transfer_receive_view, name="transfer_receive"),
    path("transferencias/<int:pk>/cancelar/", views.transfer_cancel, name="transfer_cancel"),
]
