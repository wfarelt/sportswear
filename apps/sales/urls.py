from django.urls import path

from . import views

app_name = "sales"

urlpatterns = [
    path("", views.sale_list, name="list"),
    path("nueva/", views.sale_create, name="create"),
    path("buscar-productos/", views.product_search, name="product_search"),
    path("<int:pk>/", views.sale_detail, name="detail"),
    path("<int:pk>/comprobante/", views.sale_receipt, name="receipt"),
    path("<int:pk>/anular/", views.sale_cancel, name="cancel"),
    path("<int:pk>/devolucion/", views.sale_return, name="return"),
]
