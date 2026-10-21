from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.audit.models import AuditEvent
from apps.branches.models import Branch
from apps.users.models import User, UserBranchAccess
from django.contrib.auth.models import Permission
from django.urls import reverse


class BranchAccessModelTests(TestCase):
    def test_access_assignment_is_unique_per_user_and_branch(self):
        user = User.objects.create_user(username="seller", password="pass")
        branch = Branch.objects.create(name="Central", code="central")
        UserBranchAccess.objects.create(user=user, branch=branch)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserBranchAccess.objects.create(user=user, branch=branch)

    def test_branch_string_uses_name_and_code(self):
        branch = Branch.objects.create(name="Central", code="central")
        self.assertEqual(str(branch), "Central (central)")


class BranchManagementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="manager", password="secure-pass-123")
        permissions = Permission.objects.filter(
            content_type__app_label="branches",
            codename__in=("view_branch", "add_branch", "change_branch"),
        )
        self.user.user_permissions.add(*permissions)
        self.client.force_login(self.user)

    def test_branch_list_requires_permission(self):
        user = User.objects.create_user(username="noaccess", password="secure-pass-123")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("branches:list")).status_code, 403)

    def test_create_and_update_branch(self):
        response = self.client.post(
            reverse("branches:create"),
            {
                "name": "Sucursal Norte",
                "code": "norte",
                "address": "Av. Central",
                "responsible": "Ana",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("branches:list"))
        branch = Branch.objects.get(code="norte")
        self.assertEqual(branch.responsible, "Ana")
        response = self.client.post(
            reverse("branches:update", args=[branch.pk]),
            {
                "name": "Sucursal Norte",
                "code": "norte",
                "address": "Av. Nueva",
                "responsible": "Ana",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("branches:list"))
        branch.refresh_from_db()
        self.assertEqual(branch.address, "Av. Nueva")
        event = AuditEvent.objects.filter(object_id=str(branch.pk)).latest("pk")
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.before_data["address"], "Av. Central")
        self.assertEqual(event.after_data["address"], "Av. Nueva")

    def test_branch_search_includes_address_and_responsible(self):
        Branch.objects.create(
            name="Sucursal Norte", code="norte", address="Avenida Libertad",
            responsible="Ana Pérez",
        )
        for query, expected in (("Libertad", "Sucursal Norte"), ("Ana Pérez", "Sucursal Norte")):
            with self.subTest(query=query):
                response = self.client.get(reverse("branches:list"), {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected)
