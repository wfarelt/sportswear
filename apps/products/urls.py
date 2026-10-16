from django.urls import path

from . import views

app_name = "products"

urlpatterns = [
    path("", views.product_list, name="list"),
    path("nuevo/", views.product_create, name="create"),
    path("catalogos/", views.reference_index, name="reference_index"),
    path("catalogos/<slug:kind>/", views.reference_list, name="reference_list"),
    path("catalogos/<slug:kind>/nuevo/", views.reference_create, name="reference_create"),
    path("catalogos/<slug:kind>/<int:pk>/editar/", views.reference_update, name="reference_update"),
    path("<int:pk>/", views.product_detail, name="detail"),
    path("<int:pk>/editar/", views.product_update, name="update"),
    path("<int:product_pk>/variantes/nueva/", views.variant_create, name="variant_create"),
    path("<int:product_pk>/variantes/<int:pk>/editar/", views.variant_update, name="variant_update"),
]
