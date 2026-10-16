from django.urls import path

from . import views

app_name = "cash"

urlpatterns = [
    path("", views.session_list, name="session_list"),
    path("abrir/", views.session_open, name="session_open"),
    path("movimientos/", views.movement_list, name="movement_list"),
    path("movimientos/nuevo/", views.movement_create, name="movement_create"),
    path("turnos/<int:pk>/", views.session_detail, name="session_detail"),
    path("turnos/<int:pk>/cerrar/", views.session_close, name="session_close"),
]
