from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.decorators import permission_required_or_superuser

from .forms import BranchForm
from .models import Branch


@permission_required_or_superuser("branches.view_branch")
def branch_list(request):
    branches = Branch.objects.all()
    query = request.GET.get("q", "").strip()
    if query:
        branches = branches.filter(name__icontains=query) | branches.filter(code__icontains=query)
    return render(request, "branches/branch_list.html", {"branches": branches.distinct(), "query": query})


@permission_required_or_superuser("branches.add_branch")
def branch_create(request):
    form = BranchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        branch = form.save()
        messages.success(request, f"La sucursal {branch.name} se creó correctamente.")
        return redirect("branches:list")
    return render(request, "branches/branch_form.html", {"form": form, "page_title": "Nueva sucursal"})


@permission_required_or_superuser("branches.change_branch")
def branch_update(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    form = BranchForm(request.POST or None, instance=branch)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"La sucursal {branch.name} se actualizó correctamente.")
        return redirect("branches:list")
    return render(
        request,
        "branches/branch_form.html",
        {"form": form, "page_title": f"Editar sucursal: {branch.name}", "branch": branch},
    )
