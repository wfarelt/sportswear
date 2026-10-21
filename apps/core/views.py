from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.branches.models import Branch


@login_required
def dashboard(request):
    has_reports_access = request.user.is_superuser or request.user.has_perm("reports.view_reports")
    has_sales_access = request.user.is_superuser or request.user.has_perm("sales.view_sale")
    if has_reports_access or has_sales_access:
        from apps.reports.views import _dashboard_context, _filters

        form, branch, start, end = _filters(request)
        context = _dashboard_context(
            request,
            form,
            branch,
            start,
            end,
            allow_personal_sales=has_sales_access and not has_reports_access,
        )
        context["has_dashboard_data"] = True
    else:
        context = {"has_dashboard_data": False}
    return render(request, "core/dashboard.html", context)


@login_required
@require_POST
def select_branch(request):
    branch_id = request.POST.get("branch_id")
    if request.user.has_global_branch_access:
        branches = Branch.objects.filter(is_active=True)
    else:
        branches = request.user.branches.filter(is_active=True)

    branch = branches.filter(pk=branch_id).first()
    if branch is None:
        messages.error(request, "No tienes permiso para acceder a esa sucursal.")
    else:
        request.session["selected_branch_id"] = branch.pk
        messages.success(request, f"Sucursal activa: {branch.name}.")
    return redirect("core:dashboard")
