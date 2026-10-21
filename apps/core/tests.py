from django.test import TestCase
from django.urls import reverse

from apps.branches.models import Branch
from apps.users.models import User, UserBranchAccess


class DashboardAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="seller", password="A-secure-test-password-123", role=User.Role.SELLER
        )

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(
            response, f"{reverse('accounts:login')}?next={reverse('core:dashboard')}"
        )

    def test_authenticated_user_without_operational_permissions_sees_access_notice_not_demo(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Acceso no habilitado")
        self.assertNotContains(response, "Vista de demostración")
        self.assertNotContains(response, "EJEMPLO · NO REAL")

    def test_user_cannot_select_unassigned_branch(self):
        branch = Branch.objects.create(name="Central", code="central")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:select_branch"), {"branch_id": branch.pk}
        )
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertNotIn("selected_branch_id", self.client.session)

    def test_user_can_select_assigned_branch(self):
        branch = Branch.objects.create(name="Central", code="central")
        UserBranchAccess.objects.create(user=self.user, branch=branch)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("core:select_branch"), {"branch_id": branch.pk}
        )
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertEqual(self.client.session["selected_branch_id"], branch.pk)

    def test_admin_can_select_any_active_branch(self):
        admin = User.objects.create_user(
            username="admin", password="A-secure-test-password-123", role=User.Role.ADMIN
        )
        branch = Branch.objects.create(name="Central", code="central")
        self.client.force_login(admin)
        response = self.client.post(
            reverse("core:select_branch"), {"branch_id": branch.pk}
        )
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertEqual(self.client.session["selected_branch_id"], branch.pk)
