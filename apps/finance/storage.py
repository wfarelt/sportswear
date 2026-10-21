from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateFinanceStorage(FileSystemStorage):
    """Store financial attachments outside the publicly served media directory."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", settings.PRIVATE_MEDIA_ROOT)
        kwargs.setdefault("base_url", "")
        super().__init__(*args, **kwargs)

    def url(self, name):
        raise ValueError("Los comprobantes financieros requieren descarga autorizada.")
