import json
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from django.utils import timezone
from apps.customers.models import Customer, Address, Order, OrderItem
from apps.designers.models import Designer, Shipment
from apps.core.models import Product, Currency
from apps.pay.models import Invoice, Payment

User = get_user_model()


class ShippoIntegrationTests(APITestCase):
    def setUp(self):
        # 1. Create a designer user & profile
        self.designer_user = User.objects.create_user(
            username="designer1", email="designer@example.com", password="password123"
        )
        self.designer_user.is_active = True
        self.designer_user.save()
        
        self.designer_profile = Designer.objects.create(
            user=self.designer_user,
            brand_name="Test Brand",
            country="NG",
            city="Lagos"
        )

        # 2. Create customer user & profile
        self.customer_user = User.objects.create_user(
            username="customer1", email="customer@example.com", password="password123"
        )
        self.customer_user.is_active = True
        self.customer_user.save()
        self.customer_profile = Customer.objects.create(
            user=self.customer_user,
            phone="+2348011111111"
        )

        # 3. Create address
        self.address = Address.objects.create(
            customer=self.customer_profile,
            recipient_name="Recipient Customer",
            phone="+15555555555",
            line1="123 Shopping Avenue",
            city="New York",
            state="NY",
            country="US",
            postal_code="10001",
            is_default=True
        )

        # 4. Create invoice & payment (required for sub-order visibility)
        self.payment = Payment.objects.create(
            user=self.customer_user,
            amount=100.0,
            status="success",
            is_paid=True
        )
        self.invoice = Invoice.objects.create(
            user=self.customer_user,
            payment=self.payment,
            amount=100
        )

        # 5. Create product and order
        self.currency = Currency.objects.create(name="USD", symbol="$")
        self.product = Product.objects.create(
            user=self.designer_user,
            name="Silk Dress",
            price=80.0,
            currency=self.currency,
            weight_kg=1.2,
            length_cm=30,
            width_cm=20,
            height_cm=10,
            is_published=True
        )
        self.order = Order.objects.create(
            customer=self.customer_profile,
            invoice=self.invoice,
            total_amount=100.00,
            status="processing"
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            product=self.product,
            quantity=1,
            amount=80.00,
            sub_total=80.00,
            tracking_number="URBITR-INIT-TEST-12345",
            status="pending"
        )

    def test_generate_shipping_label_success(self):
        """Designer generates a Shippo shipping label successfully (mocked in non-prod)."""
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(self.designer_user)
        self.client.cookies['access_token'] = str(refresh.access_token)
        url = f"/designers/orders/{self.order_item.item_id}/generate_shipping_label"
        
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "success")
        self.assertIn("label_url", response.data["data"])
        
        # Verify order item was updated
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "shipped")
        self.assertEqual(self.order_item.designer_status, "shipped")
        self.assertTrue(self.order_item.tracking_number.startswith("USPS-MOCK-"))
        self.assertEqual(self.order_item.properties["carrier"], "USPS")
        self.assertEqual(self.order_item.properties["shipping_label_url"], response.data["data"]["label_url"])

        # Verify Shipment object was created
        shipment = Shipment.objects.get(order_item=self.order_item)
        self.assertEqual(shipment.carrier, "USPS")
        self.assertEqual(shipment.tracking_status, "in_transit")

    def test_shippo_webhook_updates_to_delivered(self):
        """Shippo webhook delivers package and sets OrderItem to delivered."""
        # Setup pre-shipped order item with USPS-MOCK-999 tracking
        self.order_item.tracking_number = "USPS-MOCK-999"
        self.order_item.status = "shipped"
        self.order_item.designer_status = "shipped"
        self.order_item.save()

        # Call Webhook
        url = reverse("shippo-webhook")
        payload = {
            "event": "track_updated",
            "data": {
                "tracking_number": "USPS-MOCK-999",
                "carrier": "USPS",
                "tracking_status": {
                    "status": "DELIVERED",
                    "status_details": "Package delivered to porch",
                    "status_date": "2026-06-07T12:00:00Z"
                }
            }
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

        # Verify OrderItem is updated to delivered
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "delivered")
        self.assertEqual(self.order_item.designer_status, "delivered")
        self.assertIsNotNone(self.order_item.delivered_at)

        # Verify Shipment database record is updated
        shipment = Shipment.objects.get(order_item=self.order_item)
        self.assertEqual(shipment.tracking_status, "DELIVERED")

