from random import random
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.serializers import ShippingMethodSerializer
from apps.designers.models import DesignerOrder
from apps.pay.models import Invoice
from .models import Customer, Address, Wishlist, CartItem, Order, OrderItem, ReturnRequest
from .serializers import (
    CustomerSerializer, AddressSerializer, InvoiceSerializer, WishlistSerializer,
    CartItemSerializer, OrderSerializer, ReturnRequestSerializer
)
from apps.core.models import Product, ShippingMethod
from django.utils import timezone
from .models import OrderTracking
from .serializers import OrderTrackingSerializer
from .models import OrderTracking, CartItem, OrderItem
from django.core.paginator import Paginator
from rest_framework import status
from django.core.exceptions import ObjectDoesNotExist
# ---------------- Checkout ----------------
class CheckoutView(APIView):
    """Handles checkout: cart → order → payment → tracking"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            # serializer = CheckoutSerializer(data=request.data)
            # serializer.is_valid(raise_exception=True)
            customer = request.user.customer_profile

            shipping_address_id = request.data.get('shipping_address_id')

            # 1️⃣ Create or fetch shipping address
            if shipping_address_id:
                # Use existing address
                try:
                    shipping_address = customer.addresses.get(id=shipping_address_id)
                except Address.DoesNotExist:
                    return Response({"status": "error", "message": "Shipping address not found."}, status=404)

            else:
                # Create new address
                serialized_address = AddressSerializer(data = request.data, partial= True)
                if serialized_address.is_valid():
                    shipping_address = serialized_address.save(customer = customer)
                else:
                    return Response({"status": "error", "message": "Invalid Address data"}, status=400)
                # shipping_address = Address.objects.create(
                #     customer=customer,
                #     line1=request.data["line1"],
                #     line2=request.data["line2"],
                #     postal_code=request.data["post_code"],
                #     city=request.data["city"],
                #     state = request.data["state"],
                #     country=request.data["country"],
                #     is_default=True,  # optional
                # )

            # 2️⃣ Get shipping method
            shipping_method_key = request.data['shipping_method']
            shipping_amount = request.data['shipping_amount']

            # 3️⃣ Calculate totals
            cart_items = CartItem.objects.filter(customer=customer).select_related('product')
            if not cart_items.exists():
                return Response({"status":"error", "message":"Cart is empty."}, status=400)
            for item in cart_items:
                if item.product.stock < item.quantity:
                    return Response({"status":"error", "message":f'{item.product.name} is out of stock'}, status=400)

            print(shipping_amount)
            subtotal = sum([item.subtotal() for item in cart_items])
            total_amount = subtotal + int(shipping_amount)

            # 4️⃣ Create Payment record
            invoice = Invoice.objects.create(
                amount=total_amount,
                user = request.user
                # payment_method=request.data["payment_method"],
                # is_paid=False
            )

            # 5️⃣ Create Order
            order = Order.objects.create(
                invoice=invoice,
                order_id= f"URBON-{round(random())*9}-{timezone.now().strftime('%Y%m%d%H%M')}-{(random() * 99999999990).__round__()}",
                customer=customer,
                shipping_address=shipping_address,
                shipping_method = request.data['shipping_method'],
                total_amount=total_amount,
                status='pending'
            )

            # 6️⃣ Order Items
            for item in cart_items:
                order_item = OrderItem.objects.create(
                    order=order,
                    tracking_number = f"URBITR-{order.pk}-{timezone.now().strftime('%Y%m%d%H%M')}-{(random() * 99999999990).__round__()}",
                    properties = item.properties,
                    product=item.product,
                    color=item.color,
                    size=item.size,
                    quantity=item.quantity,
                    sub_total=item.product.price*item.quantity,
                    amount=item.product.price
                )
                item.product.stock = item.product.stock - item.quantity if item.product.stock > 0 else 0
                item.product.save()
                DesignerOrder.objects.create(order_item = order_item,user=order_item.product.user)

            # 7️⃣ Clear cart
            cart_items.delete()

            # 8️⃣ Create Tracking
            estimated_delivery = timezone.now().date() + timezone.timedelta(days=3)  # modify if needed
            tracking_number = f"URBOTR-{order.pk}-{timezone.now().strftime('%Y%m%d%H%M')}-{(random() * 99999999990).__round__()}"
            OrderTracking.objects.create(
                order=order,
                tracking_number=tracking_number,
                current_status='Pending',
                estimated_delivery=estimated_delivery
            )

            return Response({
                "status": "success",
                "message": "Order placed successfully. Proceed to payment.",
                "data": {
                    "order_id": order.order_id,
                    'invoice': InvoiceSerializer(invoice).data,
                    "total_amount": total_amount,
                    "tracking_number": tracking_number,
                    "estimated_delivery": estimated_delivery
                }
            }, status=201)
        except Exception as e:
            print(request.data)
            print(e)
            return Response({'status':'error','message':'An error occured'}, status=400)


# ---------------- Shipping Methods ----------------
class ShippingMethodListView(APIView):
    """List available shipping methods."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        methods = ShippingMethod.objects.filter(is_active=True)
        serializer = ShippingMethodSerializer(methods, many=True)
        return Response({
            "status":"success",
            "message": "Available shipping methods retrieved successfully.",
            "data": serializer.data
        })


# ---------------- Order Tracking ----------------
class OrderTrackingView(APIView):
    """Retrieve tracking info for a specific order."""
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        try:
            order = Order.objects.get(order_id=order_id, customer=request.user.customer_profile)
            tracking = order.tracking
            serializer = OrderTrackingSerializer(tracking)
            return Response({
                "status":"success",
                "message": f"Tracking info for order {order_id} retrieved successfully.",
                "data": serializer.data
            })
        except Order.DoesNotExist:
            return Response({"status":"error", "message": "Order not found."}, status=404)
        except OrderTracking.DoesNotExist:
            return Response({"status":"error", "message": "Tracking info not available yet."}, status=404)

# ---------------- Customer Profile ----------------
class CustomerProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer, _ = Customer.objects.get_or_create(user=request.user)
        serializer = CustomerSerializer(customer)
        return Response({
            "status":"success",
            "message": "Customer profile retrieved successfully.",
            "data": serializer.data
        })

    def put(self, request):
        customer, _ = Customer.objects.get_or_create(user=request.user)
        serializer = CustomerSerializer(customer, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({
            "status":"success",
            "message": "Customer profile updated successfully.",
            "data": serializer.data
        })


# ---------------- Addresses ----------------
class AddressView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        addresses = Address.objects.filter(customer=request.user.customer_profile)
        serializer = AddressSerializer(addresses, many=True)
        return Response({
            "status":"success",
            "message": "Addresses retrieved successfully.",
            "data": serializer.data
        })

    def post(self, request):
        serializer = AddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(customer=request.user.customer_profile)
        return Response({
            "status":"success",
            "message": "Address added successfully.",
            "data": serializer.data
        })


# ---------------- Wishlist ----------------
class WishlistView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wishlist = Wishlist.objects.filter(customer=request.user.customer_profile)
        serializer = WishlistSerializer(wishlist, many=True)
        return Response({
            "status":"success",
            "message": "Wishlist retrieved successfully.",
            "data": serializer.data
        })

    def post(self, request):
        product_id = request.data.get('product_id')
        customer = request.user.customer_profile
        print(request.data)
        try:
            product = Product.objects.get(id=product_id, is_published=True)
        except Product.DoesNotExist:
            return Response({"status":"error", "message": "Product not found."}, status=404)

        wishlist, created = Wishlist.objects.get_or_create(customer=customer, product=product)
        if not created:
            wishlist.delete()
            return Response({"status":"success", "message": "Product removed from wishlist."})
        return Response({"status":"success", "message": "Product added to wishlist."})

    def delete(self, request):
        product_id = request.data.get('product_id')
        customer = request.user.customer_profile
        print(request.data)
        try:
            product = Product.objects.get(id=product_id, is_published=True)
            wishlist = Wishlist.objects.get(customer=customer, product=product)
            wishlist.delete()
        except ObjectDoesNotExist:
            return Response({"status":"error", "message": "Product or wishlist not found."}, status=404)

        return Response({"status":"success", "message": "Product removed from wishlist."})



# ---------------- Cart ----------------
class CartView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        Customer.objects.get_or_create(user= request.user) 

        cart_items = CartItem.objects.filter(customer=request.user.customer_profile)
        serializer = CartItemSerializer(cart_items, many=True)
        return Response({"status":"success", "message": "Cart retrieved successfully.", "data": serializer.data})

    def post(self, request):
        product_id = request.data.get('product_id')
        try:
            customer = request.user.customer_profile
        except Customer.DoesNotExist:
            customer, _ = Customer.objects.get_or_create(user = request.user)
        try:
            product = Product.objects.get(id=product_id, is_published=True)
        except Product.DoesNotExist:
            return Response({"status":"error", "message": "Product not found."}, status=404)
        serializer = CartItemSerializer(data = request.data, partial = True)
        if serializer.is_valid():
            serializer.save(customer = customer, product = product)
        else:
            print(serializer.error_messages)
        return Response({"status":"success", "message": "Cart updated successfully."})
    

    def put(self, request):
        product_id = request.data.get('product_id')
        cart_item_id = request.data.get('cart_item_id')
        try:
            customer = request.user.customer_profile
        except Customer.DoesNotExist:
            customer, _ = Customer.objects.get_or_create(user = request.user)
        try:
            product = Product.objects.get(id=product_id, is_published=True)
        except Product.DoesNotExist:
            return Response({"status":"error", "message": "Product not found."}, status=404)
        cart_item = CartItem.objects.get(id=cart_item_id)
        serializer = CartItemSerializer(cart_item, data = request.data, partial = True)
        if serializer.is_valid():
            serializer.save(customer = customer, product = product)
        else:
            print(serializer.error_messages)
        return Response({"status":"success", "message": "Cart updated successfully."})

    def delete(self, request):
        item_id = request.data.get('item_id')
        deleted, _ = CartItem.objects.filter(id=item_id, customer=request.user.customer_profile).delete()
        if deleted:
            return Response({"status":"success", "message": "Item removed from cart."})
        return Response({"status":"error", "message": "Item not found in cart."}, status=404)


# ---------------- Orders ----------------
class OrderListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        page = int(request.GET.get("page", 1))
        limit = int(request.GET.get("limit", 10))

        queryset = (
            Order.objects
            .filter(customer=request.user.customer_profile)
            .order_by("-created_at")
        )

        paginator = Paginator(queryset, limit)
        page_obj = paginator.get_page(page)

        serializer = OrderSerializer(page_obj, many=True)

        return Response(
            {
                "status": "success",
                "message": "Orders retrieved successfully.",
                "data": serializer.data,
                "pagination": {
                    "current_page": page_obj.number,
                    "total_pages": paginator.num_pages,
                    "total_items": paginator.count,
                    "has_next": page_obj.has_next(),
                    "has_previous": page_obj.has_previous(),
                },
            },
            status=status.HTTP_200_OK,
        )


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        order_id = request.GET.get('order_id')

        order = Order.objects.get(order_id=order_id, customer=request.user.customer_profile)
        serializer = OrderSerializer(order)

        return Response(
            {
                "status": "success",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )



# ---------------- Return Requests ----------------
class ReturnRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        returns = ReturnRequest.objects.filter(order_item__order__customer=request.user.customer_profile)
        serializer = ReturnRequestSerializer(returns, many=True)
        return Response({"status":"success", "message": "Return requests retrieved successfully.", "data": serializer.data})

    def post(self, request):
        order_item_id = request.data.get('order_item_id')
        reason = request.data.get('reason')
        try:
            order_item = OrderItem.objects.get(id=order_item_id, order__customer=request.user.customer_profile)
        except OrderItem.DoesNotExist:
            return Response({"status":"error", "message": "Order item not found."}, status=404)

        return_request,_ = ReturnRequest.objects.get_or_create(order_item=order_item)
        return_request.reason=reason
        return_request.save()
        serializer = ReturnRequestSerializer(return_request)
        return Response({"status":"success", "message": "Return request submitted successfully.", "data": serializer.data})
