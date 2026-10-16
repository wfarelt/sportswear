from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrador"
        MANAGER = "manager", "Gerente"
        SELLER = "seller", "Vendedor"
        STOCKKEEPER = "stockkeeper", "Almacenero"
        CASHIER = "cashier", "Cajero"

    role = models.CharField(
        "rol principal", max_length=20, choices=Role.choices, default=Role.SELLER
    )
    branches = models.ManyToManyField(
        "branches.Branch",
        through="UserBranchAccess",
        related_name="authorized_users",
        blank=True,
        verbose_name="sucursales autorizadas",
    )

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"

    @property
    def has_global_branch_access(self):
        return self.is_superuser or self.role == self.Role.ADMIN


class UserBranchAccess(models.Model):
    user = models.ForeignKey(
        "users.User", on_delete=models.CASCADE, related_name="branch_accesses"
    )
    branch = models.ForeignKey(
        "branches.Branch", on_delete=models.CASCADE, related_name="user_accesses"
    )
    is_default = models.BooleanField("sucursal predeterminada", default=False)
    assigned_at = models.DateTimeField("asignada el", auto_now_add=True)

    class Meta:
        ordering = ["branch__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "branch"], name="unique_user_branch_access"
            )
        ]
        verbose_name = "acceso de usuario a sucursal"
        verbose_name_plural = "accesos de usuarios a sucursales"

    def __str__(self):
        return f"{self.user} — {self.branch}"
