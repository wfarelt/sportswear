from django.db import models


class Branch(models.Model):
    name = models.CharField("nombre", max_length=120)
    code = models.SlugField("código", max_length=30, unique=True)
    address = models.CharField("dirección", max_length=255, blank=True)
    phone = models.CharField("teléfono", max_length=30, blank=True)
    email = models.EmailField("correo electrónico", blank=True)
    responsible = models.CharField("responsable", max_length=120, blank=True)
    is_active = models.BooleanField("activa", default=True)
    created_at = models.DateTimeField("creada el", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "sucursal"
        verbose_name_plural = "sucursales"

    def __str__(self):
        return f"{self.name} ({self.code})"
