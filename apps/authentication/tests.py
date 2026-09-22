from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

User = get_user_model()


class AdminUserActionTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            email="super@example.com", password="password123"
        )

        self.admin = User.objects.create_user(
            email="admin@example.com", password="password123"
        )
        self.admin.user_type = "admin"
        self.admin.admin_role = "support_agent"
        self.admin.is_staff = True
        self.admin.is_active = True
        self.admin.save()

    def test_superadmin_can_change_admin_role(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(
            "/auth/user/action",
            {
                "user_id": self.admin.id,
                "action": "change_admin_role",
                "admin_role": "product_manager",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.admin.refresh_from_db()
        self.assertEqual(self.admin.admin_role, "product_manager")
        self.assertTrue(self.admin.is_staff)

    def test_non_superadmin_cannot_change_admin_role(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/auth/user/action",
            {
                "user_id": self.superadmin.id,
                "action": "change_admin_role",
                "admin_role": "support_agent",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.superadmin.refresh_from_db()
        self.assertEqual(self.superadmin.admin_role, "superadmin")

    def test_change_admin_role_rejects_non_admin_target(self):
        customer = User.objects.create_user(
            email="customer@example.com", password="password123"
        )
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(
            "/auth/user/action",
            {
                "user_id": customer.id,
                "action": "change_admin_role",
                "admin_role": "marketer",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_superadmin_cannot_change_own_role(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(
            "/auth/user/action",
            {
                "user_id": self.superadmin.id,
                "action": "change_admin_role",
                "admin_role": "support_agent",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.superadmin.refresh_from_db()
        self.assertEqual(self.superadmin.admin_role, "superadmin")

    def test_change_admin_role_rejects_invalid_role(self):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(
            "/auth/user/action",
            {
                "user_id": self.admin.id,
                "action": "change_admin_role",
                "admin_role": "not_a_role",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.admin.refresh_from_db()
        self.assertEqual(self.admin.admin_role, "support_agent")
