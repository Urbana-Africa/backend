import decimal
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework.test import APITestCase
from rest_framework import status
from apps.customers.models import Customer, Address, Order, OrderItem
from apps.designers.models import Designer, DesignerProduct
from apps.core.models import Product
from apps.pay.models import Payment, Invoice, Escrow, Wallet
from unittest.mock import patch

User = get_user_model()


class SeedSalesViewTest(TransactionTestCase):
    """Test the seed sales endpoint for generating test data"""
    
    def setUp(self):
        """Set up test data"""
        # Create test designer
        self.designer_user = User.objects.create_user(
            username="testdesigner",
            email="designer@test.com",
            password="testpass123",
            first_name="Test",
            last_name="Designer"
        )
        
        self.designer = Designer.objects.create(
            user=self.designer_user,
            brand_name="Test Brand",
            is_verified=True
        )
        
        # Create test product for designer
        self.core_product = Product.objects.create(
            name="Test Product",
            price=decimal.Decimal("5000.00"),
            user=self.designer_user
        )
        
        self.designer_product = DesignerProduct.objects.create(
            designer=self.designer,
            product=self.core_product,
            stock=100
        )
    
    def test_seed_sales_endpoint_only_in_debug(self):
        """Test that seed sales endpoint only works in DEBUG mode"""
        with patch.object(settings, 'DEBUG', False):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
            self.assertIn("Only available in DEBUG mode", response.json()['error'])
    
    def test_seed_sales_creates_unique_order_ids(self):
        """Test that seeded orders have unique order IDs"""
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # Check that all orders have unique order_ids
            order_ids = Order.objects.values_list('order_id', flat=True)
            self.assertEqual(len(order_ids), len(set(order_ids)))
    
    def test_seed_sales_creates_unique_tracking_numbers(self):
        """Test that seeded order items have unique tracking numbers"""
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # Check that all order items have unique tracking_numbers
            tracking_numbers = OrderItem.objects.values_list('tracking_number', flat=True)
            self.assertEqual(len(tracking_numbers), len(set(tracking_numbers)))
    
    def test_seed_sales_creates_correct_number_of_records(self):
        """Test that seeding creates the expected number of records"""
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # Should create 5 successful + 2 returned = 7 orders per designer
            orders = Order.objects.filter(customer__user__username="seed_customer")
            self.assertEqual(orders.count(), 7)
            
            # Should create 7 order items (1 per order)
            order_items = OrderItem.objects.filter(order__customer__user__username="seed_customer")
            self.assertEqual(order_items.count(), 7)
            
            # Should create 7 payments
            payments = Payment.objects.filter(user__username="seed_customer")
            self.assertEqual(payments.count(), 7)
            
            # Should create 7 escrows
            escrows = Escrow.objects.filter(customer__username="seed_customer")
            self.assertEqual(escrows.count(), 7)
    
    def test_seed_sales_creates_seed_customer(self):
        """Test that seed customer is created correctly"""
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            seed_user = User.objects.get(username="seed_customer")
            self.assertEqual(seed_user.email, "seed@example.com")
            self.assertEqual(seed_user.first_name, "Seed")
            self.assertEqual(seed_user.last_name, "Customer")
            
            customer = Customer.objects.get(user=seed_user)
            address = Address.objects.get(customer=customer, label="Seeding Address")
            self.assertEqual(address.line1, "123 Seed St")
            self.assertEqual(address.city, "Lagos")
    
    def test_seed_sales_creates_successful_and_returned_orders(self):
        """Test that both successful and returned orders are created"""
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # Check successful orders
            successful_orders = Order.objects.filter(
                customer__user__username="seed_customer",
                status="delivered"
            )
            self.assertEqual(successful_orders.count(), 5)
            
            # Check returned orders
            returned_orders = Order.objects.filter(
                customer__user__username="seed_customer",
                status="returned"
            )
            self.assertEqual(returned_orders.count(), 2)
            
            # Check corresponding order items
            successful_items = OrderItem.objects.filter(
                order__customer__user__username="seed_customer",
                status="delivered"
            )
            self.assertEqual(successful_items.count(), 5)
            
            returned_items = OrderItem.objects.filter(
                order__customer__user__username="seed_customer",
                status="returned"
            )
            self.assertEqual(returned_items.count(), 2)
    
    def test_seed_sales_creates_escrows_with_correct_status(self):
        """Test that escrows are created with correct status"""
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # All escrows should start as 'held' and then be processed
            escrows = Escrow.objects.filter(customer__username="seed_customer")
            self.assertEqual(escrows.count(), 7)
            
            # Check that escrows have correct amounts and commission
            for escrow in escrows:
                self.assertEqual(escrow.amount, self.core_product.price)
                expected_commission = escrow.amount * decimal.Decimal("0.10")
                self.assertEqual(escrow.platform_commission, expected_commission)
    
    @patch('apps.pay.services.escrow.release_escrow')
    @patch('apps.pay.services.escrow.refund_escrow_to_customer')
    def test_seed_sales_calls_escrow_services(self, mock_refund, mock_release):
        """Test that appropriate escrow services are called"""
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # Should call release_escrow 5 times (for successful orders)
            self.assertEqual(mock_release.call_count, 5)
            
            # Should call refund_escrow_to_customer 2 times (for returned orders)
            self.assertEqual(mock_refund.call_count, 2)
    
    def test_seed_sales_creates_designer_product_if_missing(self):
        """Test that designer product is created if designer doesn't have one"""
        # Create designer without product
        new_designer_user = User.objects.create_user(
            username="newdesigner",
            email="new@designer.com",
            password="testpass123"
        )
        new_designer = Designer.objects.create(
            user=new_designer_user,
            brand_name="New Brand"
        )
        
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # Should create a product for the new designer
            new_designer.refresh_from_db()
            self.assertTrue(
                DesignerProduct.objects.filter(designer=new_designer).exists()
            )
            
            # Should create orders for the new designer too
            orders = Order.objects.filter(
                orderitem__product__user=new_designer_user
            )
            self.assertEqual(orders.count(), 7)
    
    def test_seed_sales_id_generation_formats(self):
        """Test that generated IDs follow expected formats"""
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # Check order ID format
            orders = Order.objects.filter(customer__user__username="seed_customer")
            for order in orders:
                self.assertTrue(order.order_id.startswith("URBON-"))
                self.assertIn("-", order.order_id)
            
            # Check tracking number format
            order_items = OrderItem.objects.filter(
                order__customer__user__username="seed_customer"
            )
            for item in order_items:
                self.assertTrue(item.tracking_number.startswith("URBITR-"))
                self.assertIn("-", item.tracking_number)


class SeedSalesIntegrationTest(TransactionTestCase):
    """Integration tests for seed sales functionality"""
    
    def test_multiple_seed_calls_dont_duplicate_ids(self):
        """Test that multiple calls to seed sales don't create duplicate IDs"""
        with patch.object(settings, 'DEBUG', True):
            # First call
            response1 = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response1.status_code, status.HTTP_200_OK)
            
            initial_order_count = Order.objects.count()
            initial_item_count = OrderItem.objects.count()
            
            # Second call
            response2 = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response2.status_code, status.HTTP_200_OK)
            
            # Should have created more records without conflicts
            final_order_count = Order.objects.count()
            final_item_count = OrderItem.objects.count()
            
            self.assertGreater(final_order_count, initial_order_count)
            self.assertGreater(final_item_count, initial_item_count)
            
            # All order IDs should still be unique
            order_ids = Order.objects.values_list('order_id', flat=True)
            self.assertEqual(len(order_ids), len(set(order_ids)))
            
            # All tracking numbers should still be unique
            tracking_numbers = OrderItem.objects.values_list('tracking_number', flat=True)
            self.assertEqual(len(tracking_numbers), len(set(tracking_numbers)))
    
    def test_seed_sales_with_multiple_designers(self):
        """Test seed sales with multiple designers"""
        # Create additional designers
        for i in range(3):
            user = User.objects.create_user(
                username=f"designer{i}",
                email=f"designer{i}@test.com",
                password="testpass123"
            )
            Designer.objects.create(
                user=user,
                brand_name=f"Brand {i}"
            )
        
        with patch.object(settings, 'DEBUG', True):
            response = self.client.get('/api/pay/seed-sales')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            
            # Should create orders for all designers
            total_orders = Order.objects.filter(customer__user__username="seed_customer").count()
            # 7 orders per designer * 4 designers = 28 orders
            self.assertEqual(total_orders, 28)
