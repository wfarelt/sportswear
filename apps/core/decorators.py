from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def permission_required_or_superuser(permission):
    """Require a Django model permission; superusers retain implicit access."""
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not (request.user.is_superuser or request.user.has_perm(permission)):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
