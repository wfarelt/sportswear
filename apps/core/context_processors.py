from apps.branches.models import Branch


def branch_context(request):
    if not request.user.is_authenticated:
        return {"available_branches": [], "selected_branch": None}

    if request.user.has_global_branch_access:
        branches = Branch.objects.filter(is_active=True)
    else:
        branches = request.user.branches.filter(is_active=True)

    branches = branches.order_by("name")
    branch_list = list(branches)
    selected_id = request.session.get("selected_branch_id")
    selected_branch = next(
        (branch for branch in branch_list if str(branch.pk) == str(selected_id)), None
    )
    if selected_branch is None:
        default_access = request.user.branch_accesses.filter(
            branch__in=branch_list, is_default=True
        ).select_related("branch").first()
        selected_branch = default_access.branch if default_access else None

    return {"available_branches": branch_list, "selected_branch": selected_branch}
