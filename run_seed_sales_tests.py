#!/usr/bin/env python
"""
Test runner for seed sales functionality
Run this script to test the seed sales endpoint and ensure everything works correctly
"""

import os
import sys
import django

# Add the backend directory to Python path
backend_path = os.path.join(os.path.dirname(__file__))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from django.test.utils import get_runner
from django.conf import settings
from django.core.management import execute_from_command_line

def run_seed_sales_tests():
    """Run the seed sales tests"""
    print("🧪 Running Seed Sales Tests...")
    print("=" * 50)
    
    # Run the tests
    execute_from_command_line([
        'manage.py', 
        'test', 
        'apps.pay.tests.test_seed_sales',
        '--verbosity=2'
    ])

def run_specific_test(test_class=None, test_method=None):
    """Run a specific test class or method"""
    test_path = 'apps.pay.tests.test_seed_sales'
    
    if test_class:
        test_path += f'.{test_class}'
        if test_method:
            test_path += f'.{test_method}'
    
    print(f"🧪 Running specific test: {test_path}")
    print("=" * 50)
    
    execute_from_command_line([
        'manage.py', 
        'test', 
        test_path,
        '--verbosity=2'
    ])

def test_seed_sales_endpoint():
    """Test the actual seed sales endpoint"""
    print("🌱 Testing Seed Sales Endpoint...")
    print("=" * 50)
    
    try:
        from django.test import Client
        from django.contrib.auth import get_user_model
        from apps.designers.models import Designer
        from apps.customers.models import Order, OrderItem
        
        # Create a test client
        client = Client()
        
        # Create a test designer first
        User = get_user_model()
        designer_user = User.objects.create_user(
            username="testdesigner_endpoint",
            email="designer@test.com",
            password="testpass123"
        )
        
        Designer.objects.create(
            user=designer_user,
            brand_name="Test Brand"
        )
        
        # Test the endpoint
        response = client.get('/api/pay/seed-sales')
        
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Response: {data}")
            
            # Check created records
            order_count = Order.objects.filter(customer__user__username="seed_customer").count()
            item_count = OrderItem.objects.filter(order__customer__user__username="seed_customer").count()
            
            print(f"✅ Created {order_count} orders")
            print(f"✅ Created {item_count} order items")
            
            # Check for duplicates
            order_ids = Order.objects.filter(customer__user__username="seed_customer").values_list('order_id', flat=True)
            tracking_numbers = OrderItem.objects.filter(order__customer__user__username="seed_customer").values_list('tracking_number', flat=True)
            
            duplicate_orders = len(order_ids) != len(set(order_ids))
            duplicate_tracking = len(tracking_numbers) != len(set(tracking_numbers))
            
            if duplicate_orders:
                print("❌ Duplicate order IDs found!")
            else:
                print("✅ All order IDs are unique")
            
            if duplicate_tracking:
                print("❌ Duplicate tracking numbers found!")
            else:
                print("✅ All tracking numbers are unique")
                
        else:
            print(f"❌ Error: {response.content.decode()}")
            
    except Exception as e:
        print(f"❌ Error testing endpoint: {e}")

def cleanup_test_data():
    """Clean up test data created during testing"""
    print("🧹 Cleaning up test data...")
    
    try:
        from django.contrib.auth import get_user_model
        from apps.customers.models import Customer, Address, Order, OrderItem
        from apps.designers.models import Designer, DesignerProduct
        from apps.core.models import Product
        from apps.pay.models import Payment, Invoice, Escrow
        
        User = get_user_model()
        
        # Clean up seed customer data
        try:
            seed_user = User.objects.get(username="seed_customer")
            Customer.objects.filter(user=seed_user).delete()
            seed_user.delete()
            print("✅ Cleaned up seed customer")
        except User.DoesNotExist:
            pass
        
        # Clean up test designers
        test_designers = User.objects.filter(username__in=[
            "testdesigner", "testdesigner_endpoint", "newdesigner",
            "designer0", "designer1", "designer2"
        ])
        for user in test_designers:
            try:
                designer = Designer.objects.get(user=user)
                DesignerProduct.objects.filter(designer=designer).delete()
                Product.objects.filter(user=user).delete()
                designer.delete()
                user.delete()
                print(f"✅ Cleaned up designer: {user.username}")
            except Designer.DoesNotExist:
                user.delete()
        
        print("✅ Cleanup completed")
        
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "test":
            run_seed_sales_tests()
        elif command == "endpoint":
            test_seed_sales_endpoint()
        elif command == "cleanup":
            cleanup_test_data()
        elif command == "specific":
            test_class = sys.argv[2] if len(sys.argv) > 2 else None
            test_method = sys.argv[3] if len(sys.argv) > 3 else None
            run_specific_test(test_class, test_method)
        else:
            print("Usage:")
            print("  python run_seed_sales_tests.py test        - Run all tests")
            print("  python run_seed_sales_tests.py endpoint    - Test the actual endpoint")
            print("  python run_seed_sales_tests.py cleanup     - Clean up test data")
            print("  python run_seed_sales_tests.py specific [class] [method] - Run specific test")
    else:
        print("🚀 Seed Sales Test Suite")
        print("=" * 30)
        print("Available commands:")
        print("  python run_seed_sales_tests.py test        - Run all tests")
        print("  python run_seed_sales_tests.py endpoint    - Test the actual endpoint")
        print("  python run_seed_sales_tests.py cleanup     - Clean up test data")
        print("  python run_seed_sales_tests.py specific [class] [method] - Run specific test")
        print()
        print("Running full test suite...")
        run_seed_sales_tests()
