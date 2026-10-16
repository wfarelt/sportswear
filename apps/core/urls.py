from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sucursal/seleccionar/", views.select_branch, name="select_branch"),
]
