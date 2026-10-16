from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include(("django.contrib.auth.urls", "accounts"), namespace="accounts")),
    path("sucursales/", include("apps.branches.urls")),
    path("productos/", include("apps.products.urls")),
    path("inventario/", include("apps.inventory.urls")),
    path("clientes/", include("apps.customers.urls")),
    path("ventas/", include("apps.sales.urls")),
    path("caja/", include("apps.cash.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
