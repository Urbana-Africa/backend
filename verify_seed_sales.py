#!/usr/bin/env python
"""
Simple verification script for seed sales functionality
"""

import os
import sys
import django

# Add the backend directory to Python path
backend_path = os.path.join(os.path.dirname(__file__))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbana.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.customers.models import Customer, Address, Order, OrderItem
from apps.designers.models import Designer, DesignerProduct
from apps.core.models import Product
from apps.pay.models import Payment, Invoice, Escrow

def verify_seed_sales():
    """Verify that seed sales created data correctly"""
    print("🔍 Verifying Seed Sales Data...")
    print("=" * 50)
    
    try:
        # Check seed customer
        User = get_user_model()
        try:
            seed_user = User.objects.get(username="seed_customer")
            customer = Customer.objects.get(user=seed_user)
            print(f"✅ Seed customer found: {seed_user.email}")
            
            # Check address
            address = Address.objects.filter(customer=customer, label="Seeding Address").first()
            if address:
                print(f"✅ Seed address found: {address.line1}, {address.city}")
            else:
                print("❌ Seed address not found")
                
        except User.DoesNotExist:
            print("❌ Seed customer not found")
            return
        
        # Check orders
        orders = Order.objects.filter(customer=customer)
        print(f"✅ Found {orders.count()} orders for seed customer")
        
        # Check order IDs are unique
        order_ids = list(orders.values_list('order_id', flat=True))
        if len(order_ids) == len(set(order_ids)):
            print("✅ All order IDs are unique")
        else:
            print("❌ Duplicate order IDs found!")
            
        # Check order items
        order_items = OrderItem.objects.filter(order__customer=customer)
        print(f"✅ Found {order_items.count()} order items")
        
        # Check tracking numbers are unique
        tracking_numbers = list(order_items.values_list('tracking_number', flat=True))
        if len(tracking_numbers) == len(set(tracking_numbers)):
            print("✅ All tracking numbers are unique")
        else:
            print("❌ Duplicate tracking numbers found!")
            
        # Check payments
        payments = Payment.objects.filter(user=seed_user)
        print(f"✅ Found {payments.count()} payments")
        
        # Check escrows
        escrows = Escrow.objects.filter(customer=seed_user)
        print(f"✅ Found {escrows.count()} escrows")
        
        # Check status breakdown
        successful_orders = orders.filter(status="delivered").count()
        returned_orders = orders.filter(status="returned").count()
        print(f"✅ {successful_orders} successful orders, {returned_orders} returned orders")
        
        print("\n🎉 Seed sales verification completed successfully!")
        
    except Exception as e:
        print(f"❌ Error during verification: {e}")

def cleanup_seed_data():
    """Clean up all seed sales data"""
    print("🧹 Cleaning up seed sales data...")
    
    try:
        User = get_user_model()
        
        # Get seed user
        try:
            seed_user = User.objects.get(username="seed_customer")
            customer = Customer.objects.get(user=seed_user)
            
            # Delete in correct order to respect foreign keys
            OrderItem.objects.filter(order__customer=customer).delete()
            Order.objects.filter(customer=customer).delete()
            Payment.objects.filter(user=seed_user).delete()
            Escrow.objects.filter(customer=seed_user).delete()
            Invoice.objects.filter(user=seed_user).delete()
            Address.objects.filter(customer=customer).delete()
            customer.delete()
            seed_user.delete()
            
            print("✅ Seed customer and all related data deleted")
            
        except User.DoesNotExist:
            print("ℹ️ No seed customer found to clean up")
        
        print("✅ Cleanup completed")
        
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        cleanup_seed_data()
    else:
        verify_seed_sales()
