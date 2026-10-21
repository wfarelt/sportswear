from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.audit.services import record_event, snapshot
from apps.core.decorators import permission_required_or_superuser

from .forms import BranchForm
from .models import Branch


@permission_required_or_superuser("branches.view_branch")
def branch_list(request):
    branches = Branch.objects.all()
    query = request.GET.get("q", "").strip()
    if query:
        branches = branches.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(address__icontains=query)
            | Q(responsible__icontains=query)
        )
    return render(request, "branches/branch_list.html", {"branches": branches.distinct(), "query": query})


@permission_required_or_superuser("branches.add_branch")
@transaction.atomic
def branch_create(request):
    form = BranchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        branch = form.save()
        record_event(actor=request.user, action=AuditEvent.Action.CREATE, instance=branch)
        messages.success(request, f"La sucursal {branch.name} se creó correctamente.")
        return redirect("branches:list")
    return render(request, "branches/branch_form.html", {"form": form, "page_title": "Nueva sucursal"})


@permission_required_or_superuser("branches.change_branch")
@transaction.atomic
def branch_update(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    form = BranchForm(request.POST or None, instance=branch)
    if request.method == "POST" and form.is_valid():
        before = snapshot(Branch.objects.get(pk=branch.pk))
        form.save()
        record_event(
            actor=request.user, action=AuditEvent.Action.UPDATE, instance=branch,
            before_data=before,
        )
        messages.success(request, f"La sucursal {branch.name} se actualizó correctamente.")
        return redirect("branches:list")
    return render(
        request,
        "branches/branch_form.html",
        {"form": form, "page_title": f"Editar sucursal: {branch.name}", "branch": branch},
    )
