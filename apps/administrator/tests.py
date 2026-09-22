from unittest.mock import patch
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from apps.designers.models import Designer, Notification
from apps.core.models import Product, Currency

User = get_user_model()


class AdminDesignerStatusTests(APITestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com", password="password123"
        )
        self.client.force_authenticate(user=self.admin_user)

        self.designer_user = User.objects.create_user(
            email="designer@example.com", username="designer1", password="password123"
        )
        self.designer = Designer.objects.create(
            user=self.designer_user,
            brand_name="Test Brand",
            status=Designer.Status.PENDING,
        )
        self.currency = Currency.objects.create(name="USD", symbol="$")

    @patch("apps.administrator.views.resend_sendmail")
    def test_approve_designer_with_zero_products_succeeds(self, mock_mail):
        """Approval is no longer gated on product count — zero-product
        designers can be approved and are nudged to upload instead."""
        url = f"/manage/designers/{self.designer.id}/update-status"
        response = self.client.patch(url, {"status": "approved"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.APPROVED)
        self.assertTrue(self.designer.is_verified)

    @patch("apps.administrator.views.resend_sendmail")
    def test_approve_designer_with_one_product_succeeds(self, mock_mail):
        Product.objects.create(
            user=self.designer_user,
            name="Test Dress",
            price=100.0,
            currency=self.currency,
        )
        url = f"/manage/designers/{self.designer.id}/update-status"
        response = self.client.patch(url, {"status": "approved"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.APPROVED)
        self.assertTrue(self.designer.is_verified)

    @patch("apps.administrator.views.resend_sendmail")
    def test_reject_designer_with_zero_products_succeeds(self, mock_mail):
        url = f"/manage/designers/{self.designer.id}/update-status"
        response = self.client.patch(url, {"status": "rejected"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.REJECTED)

    @patch("apps.administrator.views.resend_sendmail")
    def test_invalid_status_rejected(self, mock_mail):
        url = f"/manage/designers/{self.designer.id}/update-status"
        response = self.client.patch(url, {"status": "bogus"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.PENDING)

    @patch("apps.administrator.views.resend_sendmail")
    def test_partial_update_status_triggers_notifications(self, mock_mail):
        """The admin UI PATCHes the resource directly (partial_update) —
        status transitions there must fire the same notification + email
        as the update-status action."""
        url = f"/manage/designers/{self.designer.id}"
        response = self.client.patch(
            url,
            {"status": "approved", "status_reasons": ["Brand authenticity verified"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.designer.refresh_from_db()
        self.assertEqual(self.designer.status, Designer.Status.APPROVED)
        self.assertTrue(self.designer.is_verified)

        self.assertTrue(
            Notification.objects.filter(
                user=self.designer_user, title="Profile approved"
            ).exists()
        )

    @patch("apps.administrator.views.resend_sendmail")
    def test_partial_update_without_status_change_sends_no_notification(self, mock_mail):
        url = f"/manage/designers/{self.designer.id}"
        response = self.client.patch(
            url, {"city": "Lagos"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            Notification.objects.filter(user=self.designer_user).exists()
        )
