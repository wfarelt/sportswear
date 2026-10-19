from django.db import models


class ReportPermission(models.Model):
	"""Permission carrier; all report values are queried from operational records."""

	class Meta:
		managed = False
		default_permissions = ()
		permissions = [
			("view_reports", "Puede consultar el dashboard de reportes"),
			("view_sales_reports", "Puede consultar reportes de ventas"),
			("view_inventory_reports", "Puede consultar reportes de inventario"),
			("view_finance_reports", "Puede consultar reportes financieros"),
			("view_payment_reports", "Puede consultar reportes por medio de pago"),
		]
		verbose_name = "permisos de reportes"
		verbose_name_plural = "permisos de reportes"
