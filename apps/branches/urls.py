from django.urls import path

from . import views

app_name = "branches"

urlpatterns = [
    path("", views.branch_list, name="list"),
    path("nueva/", views.branch_create, name="create"),
    path("<int:pk>/editar/", views.branch_update, name="update"),
]
