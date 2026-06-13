import threading
from random import random
from django.template.loader import render_to_string
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from apps.utils.email_sender import resend_sendmail

from apps.core.serializers import ShippingMethodSerializer
from apps.designers.models import DesignerOrder, Notification
from apps.pay.models import Invoice
from .models import Customer, Address, Wishlist, CartItem, Order, OrderItem, ReturnRequest, Dispute
from .serializers import (
    CustomerSerializer, AddressSerializer, InvoiceSerializer, WishlistSerializer,
    CartItemSerializer, OrderSerializer, ReturnRequestSerializer, DisputeSerializer
)
from apps.core.models import MediaAsset, Product, ShippingMethod
from django.utils import timezone
from .models import OrderTracking
from .serializers import OrderTrackingSerializer
from .models import OrderTracking, CartItem, OrderItem
from django.core.paginator import Paginator
from rest_framework import status
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
# ---------------- Checkout Preview ----------------
class CheckoutPreviewView(APIView):
    """Calculates checkout preview totals (subtotal, shipping cost, duties/taxes, total) based on country/address."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            customer = request.user.customer_profile
            shipping_address_id = request.data.get('shipping_address_id')
            country = request.data.get('country')

            buyer_country = 'US'
            if shipping_address_id:
                try:
                    shipping_address = customer.addresses.get(id=shipping_address_id)
                    buyer_country = shipping_address.country or 'US'
                except Address.DoesNotExist:
                    pass
            elif country:
                buyer_country = country

            # Support inline items from AI chat checkout
            inline_items = request.data.get('items')
            if inline_items and isinstance(inline_items, list):
                product_ids = [i.get('product_id') for i in inline_items if i.get('product_id')]
                products = Product.objects.filter(id__in=product_ids)
                product_map = {p.id: p for p in products}
                cart_items = []
                for i in inline_items:
                    pid = i.get('product_id')
                    if pid and pid in product_map:
                        # Fake a cart-like object for reuse
                        class FakeItem:
                            def __init__(self, prod, qty):
                                self.product = prod
                                self.quantity = qty
                        cart_items.append(FakeItem(product_map[pid], i.get('quantity', 1)))
            else:
                cart_items = list(CartItem.objects.filter(customer=customer).select_related('product'))

            if not cart_items:
                return Response({
                    "status": "success",
                    "data": {
                        "sub_total": 0.0,
                        "shipping_amount": 0.0,
                        "duties_amount": 0.0,
                        "total_amount": 0.0,
                    }
                })

            from decimal import Decimal
            from apps.pay.services.pricing import calculate_product_price_breakdown
            from apps.core.models import ShippingMethod

            sub_total = Decimal("0.00")
            duties_amount = Decimal("0.00")

            for item in cart_items:
                breakdown = calculate_product_price_breakdown(item.product, buyer_country)
                qty = Decimal(str(item.quantity))

                item_base = breakdown['base_price'] * qty
                item_duties = breakdown['duties_buffer'] * qty

                sub_total += item_base
                duties_amount += item_duties

            # Resolve shipping amount from selected rate or fallback
            shipping_rate = request.data.get('shipping_rate')
            if shipping_rate and isinstance(shipping_rate, dict) and 'amount' in shipping_rate:
                shipping_amount = Decimal(str(shipping_rate['amount']))
            else:
                # Fallback to dynamic shipping calculation
                shipping_amount = Decimal("0.00")
                for item in cart_items:
                    breakdown = calculate_product_price_breakdown(item.product, buyer_country)
                    qty = Decimal(str(item.quantity))
                    shipping_amount += breakdown['shipping_cost'] * qty

            total_amount = sub_total + shipping_amount + duties_amount

            shipping_method_name = (
                f"{shipping_rate['provider']} - {shipping_rate['service_level']}"
                if shipping_rate and isinstance(shipping_rate, dict)
                else "Dynamic Shipping"
            )

            return Response({
                "status": "success",
                "data": {
                    "sub_total": float(sub_total),
                    "shipping_amount": float(shipping_amount),
                    "duties_amount": float(duties_amount),
                    "total_amount": float(total_amount),
                    "buyer_country": buyer_country,
                    "shipping_method_name": shipping_method_name,
                }
            })
        except Exception as e:
            print(e)
            return Response({'status': 'error', 'message': 'An error occurred calculating preview.'}, status=400)


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

            # 3️⃣ Calculate totals with dynamic pricing & trade bloc surcharges
            from decimal import Decimal
            from apps.pay.services.pricing import calculate_product_price_breakdown

            buyer_country = shipping_address.country or 'US'

            # Support inline items from AI chat checkout
            inline_items = request.data.get('items')
            if inline_items and isinstance(inline_items, list):
                product_ids = [i.get('product_id') for i in inline_items if i.get('product_id')]
                products = Product.objects.filter(id__in=product_ids)
                product_map = {p.id: p for p in products}
                cart_items = []
                for i in inline_items:
                    pid = i.get('product_id')
                    if pid and pid in product_map:
                        class InlineItem:
                            def __init__(self, prod, qty, color='', size=''):
                                self.product = prod
                                self.quantity = qty
                                self.color = color
                                self.size = size
                                self.properties = {}
                        cart_items.append(InlineItem(
                            product_map[pid],
                            i.get('quantity', 1),
                            i.get('color', ''),
                            i.get('size', '')
                        ))
            else:
                cart_items = list(CartItem.objects.filter(customer=customer).select_related('product'))

            if not cart_items:
                return Response({"status":"error", "message":"Cart is empty."}, status=400)
            for item in cart_items:
                if item.product.stock < item.quantity:
                    return Response({"status":"error", "message":f'{item.product.name} is out of stock'}, status=400)

            sub_total = Decimal("0.00")
            duties_amount = Decimal("0.00")
            item_breakdowns = []
            for item in cart_items:
                # Calculate the dynamic pricing breakdown in USD
                breakdown = calculate_product_price_breakdown(item.product, buyer_country)
                qty = Decimal(str(item.quantity))

                item_base = breakdown['base_price'] * qty
                item_shipping = breakdown['shipping_cost'] * qty
                item_duties = breakdown['duties_buffer'] * qty
                item_margin = breakdown['platform_margin'] * qty
                item_total = breakdown['total_price'] * qty

                sub_total += item_base
                duties_amount += item_duties

                item_breakdowns.append({
                    'item': item,
                    'breakdown': breakdown,
                    'item_base': item_base,
                    'item_shipping': item_shipping,
                    'item_duties': item_duties,
                    'item_margin': item_margin,
                    'item_total': item_total
                })

            # Resolve shipping method from selected rate or fallback
            shipping_rate = request.data.get('shipping_rate')
            if shipping_rate and isinstance(shipping_rate, dict) and 'amount' in shipping_rate:
                shipping_method = f"{shipping_rate['provider']} - {shipping_rate['service_level']}"
                shipping_amount = Decimal(str(shipping_rate['amount']))
            else:
                shipping_method = request.data.get('shipping_method', 'Designer Fulfilled')
                shipping_amount = Decimal("0.00")
                for item in cart_items:
                    breakdown = calculate_product_price_breakdown(item.product, buyer_country)
                    qty = Decimal(str(item.quantity))
                    shipping_amount += breakdown['shipping_cost'] * qty

            total_amount = sub_total + shipping_amount + duties_amount

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
                sub_total=sub_total,
                shipping_amount=shipping_amount,
                status='pending'
            )

            # 6️⃣ Build per-group shipping addresses when ship_all_to_same is false
            ship_all_to_same = request.data.get('ship_all_to_same', True)
            group_addresses_payload = request.data.get('group_addresses', [])

            from collections import defaultdict
            designer_groups_checkout = defaultdict(list)
            for item in cart_items:
                product = item.product
                designer = getattr(product, 'user', None)
                profile = getattr(designer, 'designer_profile', None) if designer else None
                designer_id = getattr(profile, 'id', 'default') if profile else 'default'
                designer_groups_checkout[designer_id].append(item)

            group_address_map = {}
            if not ship_all_to_same and group_addresses_payload:
                for g_idx, (designer_id, group_items) in enumerate(designer_groups_checkout.items()):
                    if g_idx < len(group_addresses_payload):
                        ga = group_addresses_payload[g_idx]
                        saved_addr_id = ga.get('saved_address_id')
                        if saved_addr_id:
                            try:
                                group_addr = customer.addresses.get(id=saved_addr_id)
                            except Address.DoesNotExist:
                                group_addr = shipping_address
                        else:
                            group_addr, _ = Address.objects.get_or_create(
                                customer=customer,
                                line1=ga.get('line1', shipping_address.line1),
                                line2=ga.get('line2', shipping_address.line2),
                                city=ga.get('city', shipping_address.city),
                                state=ga.get('state', shipping_address.state),
                                postal_code=ga.get('postal_code', shipping_address.postal_code),
                                country=ga.get('country', shipping_address.country),
                                defaults={
                                    'recipient_name': ga.get('recipient_name', shipping_address.recipient_name),
                                    'phone': ga.get('phone', shipping_address.phone or ''),
                                }
                            )
                        group_address_map[designer_id] = group_addr
                    else:
                        group_address_map[designer_id] = shipping_address
            else:
                for designer_id in designer_groups_checkout:
                    group_address_map[designer_id] = shipping_address

            # 7️⃣ Order Items
            for entry in item_breakdowns:
                item = entry['item']
                breakdown = entry['breakdown']

                # Determine which group this item belongs to
                product = item.product
                designer = getattr(product, 'user', None)
                profile = getattr(designer, 'designer_profile', None) if designer else None
                item_designer_id = getattr(profile, 'id', 'default') if profile else 'default'
                item_shipping_address = group_address_map.get(item_designer_id, shipping_address)

                # Persist details in item properties
                item_properties = {
                    **(item.properties or {}),
                    'base_price': float(breakdown['base_price']),
                    'shipping_cost': float(breakdown['shipping_cost']),
                    'duties_buffer': float(breakdown['duties_buffer']),
                    'platform_margin': float(breakdown['platform_margin']),
                    'total_price': float(breakdown['total_price']),
                    'shipping_address_id': item_shipping_address.id,
                    'shipping_address_str': f"{item_shipping_address.line1}, {item_shipping_address.city}, {item_shipping_address.country}",
                }

                # Resolve color/size strings to model instances (for AI inline items)
                color_obj = item.color
                size_obj = item.size
                if isinstance(item.color, str) and item.color:
                    color_obj = item.product.colors.filter(name__iexact=item.color).first()
                if isinstance(item.size, str) and item.size:
                    size_obj = item.product.sizes.filter(name__iexact=item.size).first()

                order_item = OrderItem.objects.create(
                    order=order,
                    tracking_number = f"URBITR-{order.pk}-{timezone.now().strftime('%Y%m%d%H%M')}-{(random() * 99999999990).__round__()}",
                    properties = item_properties,
                    product=item.product,
                    color=color_obj,
                    size=size_obj,
                    quantity=item.quantity,
                    sub_total=entry['item_total'],
                    amount=breakdown['total_price']
                )
                item.product.stock = item.product.stock - item.quantity if item.product.stock > 0 else 0
                item.product.save()
                DesignerOrder.objects.create(order_item = order_item,user=order_item.product.user)

                # Notify designer of new order
                Notification.objects.create(
                    user=order_item.product.user,
                    title="New order received",
                    message=f"You have a new order for {item.product.name} (Qty: {item.quantity}).",
                    notification_type=Notification.Type.ORDER,
                    link=f"/orders/{order_item.id}",
                )

                # Low stock alert
                if item.product.stock <= 5 and item.product.stock >= 0:
                    Notification.objects.create(
                        user=order_item.product.user,
                        title="Low stock alert",
                        message=f"Your product '{item.product.name}' is running low. Only {item.product.stock} units left in stock.",
                        notification_type=Notification.Type.PRODUCT,
                        link=f"/products/edit/{item.product.id}",
                    )

            # 7️⃣ Clear cart (only if items came from cart, not inline AI items)
            if not (inline_items and isinstance(inline_items, list)):
                CartItem.objects.filter(customer=customer).delete()

            # 8️⃣ Create Tracking
            estimated_days = 3
            if shipping_rate and isinstance(shipping_rate, dict):
                estimated_days = shipping_rate.get('estimated_days', 3)
            else:
                shipping_method_obj = ShippingMethod.objects.filter(name=shipping_method).first()
                if shipping_method_obj:
                    estimated_days = shipping_method_obj.estimated_days
            estimated_delivery = timezone.now().date() + timezone.timedelta(days=estimated_days)
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


# ---------------- Shipping Rates (Live Shippo) ----------------
class ShippingRatesView(APIView):
    """
    Fetch live shipping rates from Shippo based on cart + selected address.
    Items are grouped by designer — each designer ships from their own location.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        customer = request.user.customer_profile

        # Resolve destination address
        shipping_address_id = request.data.get('shipping_address_id')
        to_address = {
            'name': request.data.get('recipient_name', 'Urbana Customer'),
            'street1': request.data.get('line1', ''),
            'street2': request.data.get('line2', ''),
            'city': request.data.get('city', ''),
            'state': request.data.get('state', ''),
            'postal_code': request.data.get('postal_code', ''),
            'country': request.data.get('country', 'US'),
        }

        if shipping_address_id:
            try:
                addr = customer.addresses.get(id=shipping_address_id)
                to_address = {
                    'name': addr.recipient_name or 'Urbana Customer',
                    'street1': addr.line1,
                    'street2': addr.line2 or '',
                    'city': addr.city,
                    'state': addr.state or '',
                    'postal_code': addr.postal_code or '',
                    'country': addr.country or 'US',
                }
            except Address.DoesNotExist:
                pass

        # Support inline items from AI chat checkout
        inline_items = request.data.get('items')
        if inline_items and isinstance(inline_items, list):
            product_ids = [i.get('product_id') for i in inline_items if i.get('product_id')]
            products = Product.objects.filter(id__in=product_ids).select_related(
                'user__designer_profile'
            )
            product_map = {p.id: p for p in products}
            cart_items = []
            for i in inline_items:
                pid = i.get('product_id')
                if pid and pid in product_map:
                    class FakeItem:
                        def __init__(self, prod, qty):
                            self.product = prod
                            self.quantity = qty
                    cart_items.append(FakeItem(product_map[pid], i.get('quantity', 1)))
        else:
            cart_items = list(CartItem.objects.filter(customer=customer).select_related(
                'product', 'product__user__designer_profile'
            ))

        if not cart_items:
            return Response({"status": "success", "data": {"rates": [], "shipment_groups": []}})

        # Group items by designer
        from collections import defaultdict
        designer_groups = defaultdict(list)
        for item in cart_items:
            product = item.product
            designer = getattr(product, 'user', None)
            profile = getattr(designer, 'designer_profile', None) if designer else None
            designer_id = getattr(profile, 'id', 'default') if profile else 'default'
            designer_groups[designer_id].append({'item': item, 'profile': profile, 'product': product})

        from apps.designers.shippo_service import get_shipping_rates

        shipment_groups = []
        all_errors = []
        test_origin = request.data.get('origin_address')
        ship_all_to_same = request.data.get('ship_all_to_same', True)
        group_addresses = request.data.get('group_addresses', [])

        for group_idx, (designer_id, group_items) in enumerate(designer_groups.items()):
            first = group_items[0]
            profile = first['profile']

            # Build origin address
            if test_origin:
                from_address = {
                    'name': test_origin.get('name', 'Urbana Designer'),
                    'street1': test_origin.get('street1', '1 Fort Road'),
                    'city': test_origin.get('city', 'New York'),
                    'state': test_origin.get('state', ''),
                    'postal_code': test_origin.get('postal_code', ''),
                    'country': test_origin.get('country', 'US'),
                }
            else:
                from_address = {
                    'name': profile.brand_name if profile else 'Urbana Designer',
                    'street1': '1 Fort Road',
                    'city': profile.city if profile else 'Accra',
                    'state': '',
                    'postal_code': '',
                    'country': (profile.country or 'GH').upper().strip() if profile else 'GH',
                }

            # Build destination address — use per-group address when ship_all_to_same is false
            if not ship_all_to_same and group_addresses and group_idx < len(group_addresses):
                ga = group_addresses[group_idx]
                group_to_address = {
                    'name': ga.get('recipient_name', to_address.get('name', 'Urbana Customer')),
                    'street1': ga.get('line1', to_address.get('street1', '')),
                    'street2': ga.get('line2', to_address.get('street2', '')),
                    'city': ga.get('city', to_address.get('city', '')),
                    'state': ga.get('state', to_address.get('state', '')),
                    'postal_code': ga.get('postal_code', to_address.get('postal_code', '')),
                    'country': ga.get('country', to_address.get('country', 'US')),
                }
            else:
                group_to_address = to_address

            group_weight = sum(
                float(entry['product'].weight_kg or 0.5) * entry['item'].quantity
                for entry in group_items
            )

            rates_result = get_shipping_rates(
                from_address=from_address,
                to_address=group_to_address,
                weight_kg=group_weight,
            )

            items_summary = []
            for entry in group_items:
                prod = entry['product']
                media_first = prod.media.first()
                image_url = None
                if media_first and hasattr(media_first, 'file') and media_first.file:
                    try:
                        image_url = media_first.file.url
                    except (AttributeError, ValueError):
                        image_url = str(media_first.file)
                items_summary.append({
                    'product_id': prod.id,
                    'name': prod.name,
                    'quantity': entry['item'].quantity,
                    'image': image_url,
                })

            shipment_groups.append({
                'designer_id': designer_id,
                'designer_name': profile.brand_name if profile else 'Urbana Designer',
                'origin_country': from_address['country'],
                'origin_city': from_address['city'],
                'destination_country': group_to_address['country'],
                'items': items_summary,
                'total_weight_kg': round(group_weight, 2),
                'rates': rates_result.get('rates', []),
                'error': rates_result.get('error'),
            })

            if rates_result.get('error'):
                all_errors.append(rates_result['error'])

        # Build combined rates by summing cheapest per group for each service level
        # This only works cleanly when all groups share the same provider/service_level names
        combined_rates = []
        if all(g['rates'] for g in shipment_groups):
            service_levels = {}
            for group in shipment_groups:
                for rate in group['rates']:
                    key = f"{rate['provider']}::{rate['service_level']}"
                    if key not in service_levels:
                        service_levels[key] = {
                            'provider': rate['provider'],
                            'service_level': rate['service_level'],
                            'amount': 0,
                            'currency': rate['currency'],
                            'estimated_days': rate['estimated_days'],
                            'source': rate['source'],
                        }
                    service_levels[key]['amount'] += rate['amount']
                    service_levels[key]['estimated_days'] = max(
                        service_levels[key]['estimated_days'],
                        rate['estimated_days']
                    )
            combined_rates = list(service_levels.values())
            combined_rates.sort(key=lambda r: r['amount'])

        return Response({
            "status": "success",
            "data": {
                "rates": combined_rates,
                "shipment_groups": shipment_groups,
                "multi_shipment": len(shipment_groups) > 1,
                "errors": all_errors if all_errors else None,
            }
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
            
            # Serialize package tracking info per item
            item_shipments = []
            for item in order.items.all():
                shipment_info = {
                    "item_id": item.item_id,
                    "product_name": item.product.name if item.product else "Unknown Product",
                    "quantity": item.quantity,
                    "tracking_number": item.tracking_number,
                    "status": item.status,
                    "carrier": item.properties.get("carrier") or "",
                    "estimated_delivery": None,
                    "tracking_events": []
                }
                
                # Fetch Shipment model data if exists
                try:
                    from apps.designers.models import Shipment
                    shipment = Shipment.objects.filter(order_item=item).first()
                    if shipment:
                        shipment_info["tracking_status"] = shipment.tracking_status
                        if shipment.tracking_data:
                            shipment_info["tracking_events"] = shipment.tracking_data.get("tracking_history", [])
                            eta = shipment.tracking_data.get("estimated_delivery_date")
                            if eta:
                                shipment_info["estimated_delivery"] = eta
                except Exception as e:
                    print(f"Error fetching shipment data: {e}")
                
                item_shipments.append(shipment_info)

            return Response({
                "status": "success",
                "message": f"Tracking info for order {order_id} retrieved successfully.",
                "data": {
                    **serializer.data,
                    "item_shipments": item_shipments
                }
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

        cart_items = CartItem.objects.filter(customer=customer).select_related('product')
        removed_items = []
        valid_items = []

        for item in cart_items:
            product = item.product
            # Product no longer exists, inactive, or unpublished
            if not product or not product.is_active or not product.is_published:
                removed_items.append({
                    "name": product.name if product else "Unknown",
                    "reason": "no_longer_available"
                })
                item.delete()
                continue

            # Out of stock
            if product.stock <= 0:
                removed_items.append({
                    "name": product.name,
                    "reason": "out_of_stock"
                })
                item.delete()
                continue

            # Quantity exceeds available stock — clamp to stock instead of removing
            if product.stock < item.quantity:
                removed_items.append({
                    "name": product.name,
                    "reason": "quantity_adjusted",
                    "old_quantity": item.quantity,
                    "new_quantity": product.stock
                })
                item.quantity = product.stock
                item.save()

            valid_items.append(item)

        serializer = CartItemSerializer(valid_items, many=True)
        return Response({
            "status": "success",
            "message": "Cart retrieved successfully.",
            "data": serializer.data,
            "removed_items": removed_items
        })

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

        # validate return eligibility
        if order_item.status not in ('delivered', 'returned'):
            return Response({
                "status": "error",
                "message": "Returns can only be requested for delivered items."
            }, status=400)

        if order_item.delivered_at:
            from datetime import timedelta
            window = timezone.now() - order_item.delivered_at
            if window.days > 7:
                return Response({
                    "status": "error",
                    "message": "The 7-day return window for this item has closed."
                }, status=400)

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


# ---------------- Return Detail ----------------
class ReturnDetailView(APIView):
    """GET /customers/returns/<return_id> — fetch full detail for a single return."""
    permission_classes = [IsAuthenticated]

    def get(self, request, return_id):
        try:
            return_request = ReturnRequest.objects.get(
                return_id=return_id,
                order_item__order__customer=request.user.customer_profile
            )
        except ReturnRequest.DoesNotExist:
            return Response({"status": "error", "message": "Return not found."}, status=404)

        serializer = ReturnRequestSerializer(return_request)
        return Response({"status": "success", "data": serializer.data})


# ---------------- Dispute ----------------
class DisputeView(APIView):
    """POST /customers/disputes — open a dispute after a return is rejected."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return_id = request.data.get("return_id")
        notes = request.data.get("notes", "")

        try:
            return_request = ReturnRequest.objects.get(
                return_id=return_id,
                order_item__order__customer=request.user.customer_profile
            )
        except ReturnRequest.DoesNotExist:
            return Response({"status": "error", "message": "Return request not found."}, status=404)

        if return_request.status != ReturnRequest.Status.REJECTED:
            return Response({
                "status": "error",
                "message": "Disputes can only be opened on rejected return requests."
            }, status=400)

        if hasattr(return_request, "dispute"):
            return Response({"status": "error", "message": "A dispute already exists for this return."}, status=400)

        dispute = Dispute.objects.create(
            return_request=return_request,
            opened_by=request.user,
            customer_notes=notes,
        )

        # Upload customer evidence photos
        for photo in request.FILES.getlist("evidence_photos"):
            media = MediaAsset.objects.create(
                file=photo,
                media_type=MediaAsset.MediaType.IMAGE
            )
            dispute.customer_evidence.add(media)

        # Update return status
        return_request.status = ReturnRequest.Status.DISPUTE_OPENED
        return_request.save()

        serializer = DisputeSerializer(dispute)
        return Response({
            "status": "success",
            "message": "Dispute opened successfully.",
            "data": serializer.data
        }, status=201)

    def get(self, request):
        """GET /customers/disputes — list customer's disputes."""
        disputes = Dispute.objects.filter(
            return_request__order_item__order__customer=request.user.customer_profile
        ).order_by("-created_at")
        serializer = DisputeSerializer(disputes, many=True)
        return Response({"status": "success", "data": serializer.data})


class CustomerSearchView(APIView):
    """Global search for customer app across orders, returns, wishlist, addresses and profile."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = request.GET.get("q", "").strip()
        if not q:
            return Response({"status": "success", "query": q, "results": []})

        customer = request.user.customer_profile
        results = []

        # Orders
        orders = Order.objects.filter(customer=customer).filter(
            Q(order_id__icontains=q) | Q(status__icontains=q)
        )[:5]
        for o in orders:
            results.append({
                "type": "order",
                "id": o.id,
                "title": f"Order #{o.order_id}",
                "subtitle": f"{o.total_amount} — {o.status.title()}",
                "status": o.status,
                "detail_url": f"/orders/{o.order_id}",
            })

        # Returns
        returns = ReturnRequest.objects.filter(
            order_item__order__customer=customer
        ).filter(
            Q(return_id__icontains=q) | Q(status__icontains=q)
        ).select_related("order_item__order")[:5]
        for r in returns:
            results.append({
                "type": "return",
                "id": r.return_id,
                "title": f"Return #{r.return_id}",
                "subtitle": f"Order #{r.order_item.order.order_id}",
                "status": r.status,
                "detail_url": f"/returns/{r.return_id}",
            })

        # Wishlist
        wishlist = Wishlist.objects.filter(customer=customer).filter(
            Q(product__name__icontains=q) | Q(product__sku__icontains=q)
        ).select_related("product")[:5]
        for w in wishlist:
            results.append({
                "type": "wishlist",
                "id": w.id,
                "title": w.product.name,
                "subtitle": f"Added {w.added_at.strftime('%b %d, %Y')}",
                "status": "Saved",
                "detail_url": f"/wishlist",
            })

        # Addresses
        addresses = Address.objects.filter(customer=customer).filter(
            Q(line1__icontains=q) | Q(city__icontains=q) | Q(country__icontains=q) | Q(postal_code__icontains=q)
        )[:3]
        for a in addresses:
            results.append({
                "type": "address",
                "id": a.id,
                "title": a.line1,
                "subtitle": f"{a.city}, {a.country}",
                "status": "Default" if a.is_default else "",
                "detail_url": "/profile",
            })

        return Response({"status": "success", "query": q, "results": results})
