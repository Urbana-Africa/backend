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
from apps.core.models import MediaAsset, Product, ShippingMethod
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

            # 2️⃣ Clear shipping requirement
            # shipping_method is passed down or defaulted to "Designer Fulfilled"
            shipping_method = request.data.get('shipping_method', 'Designer Fulfilled')

            # 3️⃣ Calculate totals
            cart_items = CartItem.objects.filter(customer=customer).select_related('product')
            if not cart_items.exists():
                return Response({"status":"error", "message":"Cart is empty."}, status=400)
            for item in cart_items:
                if item.product.stock < item.quantity:
                    return Response({"status":"error", "message":f'{item.product.name} is out of stock'}, status=400)

            subtotal = sum([item.subtotal() for item in cart_items])
            total_amount = subtotal # logistics handled by designer

            # 4️⃣ Create Payment record
            invoice = Invoice.objects.create(
                amount=total_amount,
                user = request.user
            )

            # 5️⃣ Create Order
            order = Order.objects.create(
                invoice=invoice,
                order_id= f"URBON-{round(random())*9}-{timezone.now().strftime('%Y%m%d%H%M')}-{(random() * 99999999990).__round__()}",
                customer=customer,
                shipping_address=shipping_address,
                shipping_method=shipping_method,
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
        customer, _ = Customer.objects.get_or_create(user=request.user)

        cart_items = CartItem.objects.filter(customer=customer)
        serializer = CartItemSerializer(cart_items, many=True)
        return Response({"status":"success", "message": "Cart retrieved successfully.", "data": serializer.data})

    def post(self, request):
        product_id = request.data.get('product_id')
        size_id = request.data.get('size')
        color_id = request.data.get('color')
        quantity = int(request.data.get('quantity', 1))

        customer, _ = Customer.objects.get_or_create(user = request.user)
        try:
            product = Product.objects.get(id=product_id, is_published=True)
        except Product.DoesNotExist:
            return Response({"status":"error", "message": "Product not found."}, status=404)
            
        cart_item, created = CartItem.objects.get_or_create(
            customer=customer,
            product=product,
            size_id=size_id,
            color_id=color_id,
            defaults={'quantity': quantity}
        )
        
        if not created:
            cart_item.quantity += quantity
            cart_item.save()
            
        return Response({"status":"success", "message": "Cart updated successfully."})
    

    def put(self, request):
        product_id = request.data.get('product_id')
        cart_item_id = request.data.get('cart_item_id')
        customer, _ = Customer.objects.get_or_create(user = request.user)
        try:
            product = Product.objects.get(id=product_id, is_published=True)
        except Product.DoesNotExist:
            return Response({"status":"error", "message": "Product not found."}, status=404)

        if cart_item_id:
            try:
                cart_item = CartItem.objects.get(id=cart_item_id)
            except CartItem.DoesNotExist:
                # 🧟‍♂️ React provided a zombie item ID (cart not cleared after Checkout) -> Auto-heal it!
                size_id = request.data.get('size')
                color_id = request.data.get('color')
                cart_item, _ = CartItem.objects.get_or_create(
                    product=product, size_id=size_id, color_id=color_id, customer=customer,
                    defaults={'quantity': int(request.data.get('quantity', 1))}
                )
                
            serializer = CartItemSerializer(cart_item, data = request.data, partial = True)
            if serializer.is_valid():
                serializer.save(customer = customer, product = product)
            else:
                return Response({"status":"error", "message": "Validation failed", "errors": serializer.errors}, status=400)
        else:
            # Fallback for syncs before id refreshes
            size_id = request.data.get('size')
            color_id = request.data.get('color')
            cart_item, created = CartItem.objects.get_or_create(
                product=product, size_id=size_id, color_id=color_id, customer=customer,
                defaults={'quantity': int(request.data.get('quantity', 1))}
            )
            
            serializer = CartItemSerializer(cart_item, data = request.data, partial=True)
            if serializer.is_valid():
                serializer.save(customer = customer, product = product)
            else:
                return Response({"status":"error", "message": "Validation failed", "errors": serializer.errors}, status=400)
                    
        return Response({"status":"success", "message": "Cart updated successfully."})

    def delete(self, request):
        item_id = request.data.get('item_id')
        if item_id:
            deleted, _ = CartItem.objects.filter(id=item_id, customer=request.user.customer_profile).delete()
            if deleted:
                return Response({"status":"success", "message": "Item removed from cart."})
                
        # Fallback to variant identifiers if explicitly omitted due to browser caching delays
        product_id = request.data.get('product_id')
        size_id = request.data.get('size_id')
        color_id = request.data.get('color_id')
        
        if product_id and size_id and color_id:
            deleted, _ = CartItem.objects.filter(
                product_id=product_id, size_id=size_id, color_id=color_id, customer=request.user.customer_profile
            ).delete()
            
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
        returns = ReturnRequest.objects.filter(
            order_item__order__customer=request.user.customer_profile
        ).order_by("-created_at")
        serializer = ReturnRequestSerializer(returns, many=True)

        return Response({
            "status": "success",
            "message": "Return requests retrieved successfully.",
            "data": serializer.data
        })


    def post(self, request):

        order_item_id = request.data.get("order_item_id")
        reason = request.data.get("reason")
        description = request.data.get("description")

        try:
            order_item = OrderItem.objects.get(
                id=order_item_id,
                order__customer=request.user.customer_profile
            )

        except OrderItem.DoesNotExist:
            return Response({
                "status": "error",
                "message": "Order item not found"
            }, status=404)

        # prevent duplicate return
        if ReturnRequest.objects.filter(order_item=order_item).exists():
            return Response({
                "status": "error",
                "message": "Return already requested for this item"
            }, status=400)

        return_request = ReturnRequest.objects.create(
            order_item=order_item,
            reason=reason,
            description=description
        )

        # product photos
        photos = request.FILES.getlist("product_photos")

        for photo in photos:
            media = MediaAsset.objects.create(
                file=photo,
                media_type=MediaAsset.MediaType.IMAGE
            )
            return_request.product_photos.add(media)

        # packaging photo
        packaging = request.FILES.get("packaging_photo")

        if packaging:
            media = MediaAsset.objects.create(
                file=packaging,
                media_type=MediaAsset.MediaType.IMAGE
            )
            return_request.packaging_photo = media

        # unboxing video
        video = request.FILES.get("unboxing_video")

        if video:
            media = MediaAsset.objects.create(
                file=video,
                media_type=MediaAsset.MediaType.VIDEO
            )
            return_request.unboxing_video = media

        return_request.save()

        # 🔒 Lock escrow funds — keep held until admin resolves
        try:
            escrow = order_item.escrow
            if escrow:
                from apps.pay.services.escrow import hold_escrow_for_return
                hold_escrow_for_return(escrow.id)
        except Exception:
            pass  # no escrow attached yet — safe to ignore

        serializer = ReturnRequestSerializer(return_request)

        return Response({
            "status": "success",
            "message": "Return request submitted successfully.",
            "data": serializer.data
        })


class ReturnResolveView(APIView):
    """
    PATCH /customers/returns/<id>/resolve
    Admin-only: approve or reject a return request.

    Body: { "action": "approve" | "reject", "reason": "..." }

    approve → refund customer wallet, escrow → refunded
    reject  → release escrow to designer wallet, escrow → released
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, return_id):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response({"status": "error", "message": "Forbidden"}, status=403)

        action = request.data.get("action")
        reason = request.data.get("reason", "")

        if action not in ("approve", "reject"):
            return Response(
                {"status": "error", "message": 'action must be "approve" or "reject"'},
                status=400,
            )

        try:
            return_request = ReturnRequest.objects.get(return_id=return_id)
        except ReturnRequest.DoesNotExist:
            return Response({"status": "error", "message": "Return request not found"}, status=404)

        if return_request.status not in ("pending", "reviewing"):
            return Response(
                {"status": "error", "message": f"Cannot resolve a return in '{return_request.status}' state"},
                status=400,
            )

        order_item = return_request.order_item

        try:
            escrow = order_item.escrow
        except Exception:
            escrow = None

        if action == "approve":
            return_request.status = ReturnRequest.Status.APPROVED
            return_request.admin_status = ReturnRequest.Status.APPROVED
            return_request.customer_status = ReturnRequest.Status.APPROVED
            return_request.reject_reason = ""

            if escrow:
                from apps.pay.services.escrow import refund_escrow_to_customer
                try:
                    refund_escrow_to_customer(escrow.id)
                except Exception as e:
                    return Response(
                        {"status": "error", "message": f"Escrow refund failed: {str(e)}"},
                        status=500,
                    )

        elif action == "reject":
            return_request.status = ReturnRequest.Status.REJECTED
            return_request.admin_status = ReturnRequest.Status.REJECTED
            return_request.reject_reason = reason

            if escrow:
                from apps.pay.services.escrow import release_escrow
                try:
                    release_escrow(escrow.id)
                except Exception as e:
                    return Response(
                        {"status": "error", "message": f"Escrow release failed: {str(e)}"},
                        status=500,
                    )

        from django.utils import timezone as tz
        return_request.reviewed_at = tz.now()
        return_request.resolved_at = tz.now()
        return_request.save()

        serializer = ReturnRequestSerializer(return_request)
        return Response({
            "status": "success",
            "message": f"Return {action}d successfully.",
            "data": serializer.data,
        })