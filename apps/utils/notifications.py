import threading
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from datetime import timedelta

from apps.utils.email_sender import resend_sendmail
from apps.authentication.models import User
from apps.designers.models import Designer
from apps.customers.models import Order, OrderItem
from apps.core.models import Product


def _delivery_timeline(product):
    """Return a human-readable delivery timeline based on product availability and lead time."""
    if not product:
        return "As agreed"
    lead = getattr(product, "lead_time_days", 0) or 0
    avail = getattr(product, "availability_type", "ready_to_ship")
    if lead <= 0:
        return "As agreed"
    if avail == "made_to_order":
        return f"Made to order · {lead} days"
    if avail == "pre_order":
        return f"Pre-order · {lead} days"
    return f"Ships in {lead} days"

# ============================================================
# Designer / Vendor Emails
# ============================================================

def send_designer_welcome_email(user: User):
    """Email 1: Founder welcome email — immediately after vendor signs up"""
    try:
        designer = user.designer_profile
        context = {
            "designer_name": user.first_name or user.email,
            "designer_url": f"{settings.DESIGNER_URL}/dashboard",
            "storefront_url": f"{settings.STORE_URL}/d/{designer.id}" if designer else settings.STORE_URL,
        }
        message = render_to_string("emails/designer_welcome.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Welcome to Urbana Africa", [user.email], message),
            kwargs={"from_email": "hello@accounts.urbanaafrica.com", "from_name": "Michael Lan, Founder of Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Designer welcome failed: {e}")


def send_designer_product_upload_reminder(user: User):
    """Email 2: Product upload reminder — 24 hours after signup"""
    try:
        designer = user.designer_profile
        context = {
            "designer_name": user.first_name or user.email,
            "products_url": f"{settings.DESIGNER_URL}/products/add",
        }
        message = render_to_string("emails/designer_product_upload_reminder.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Upload your best products to Urbana", [user.email], message),
            kwargs={"from_email": "designers@accounts.urbanaafrica.com", "from_name": "Zuri from Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Designer upload reminder failed: {e}")


def send_designer_storefront_reminder(user: User):
    """Email 3: Storefront completion reminder — 48-72 hours after signup if no products"""
    try:
        designer = user.designer_profile
        context = {
            "designer_name": user.first_name or user.email,
            "products_url": f"{settings.DESIGNER_URL}/products/add",
            "storefront_url": f"{settings.STORE_URL}/d/{designer.id}" if designer else settings.STORE_URL,
        }
        message = render_to_string("emails/designer_storefront_reminder.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Your Urbana storefront is waiting", [user.email], message),
            kwargs={"from_email": "designers@accounts.urbanaafrica.com", "from_name": "Zuri from Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Designer storefront reminder failed: {e}")


def send_designer_storefront_live(user: User):
    """Email 4: Storefront is live — when first product is published"""
    try:
        designer = user.designer_profile
        context = {
            "designer_name": user.first_name or user.email,
            "storefront_url": f"{settings.STORE_URL}/d/{designer.id}" if designer else settings.STORE_URL,
            "designer_url": f"{settings.DESIGNER_URL}/dashboard",
        }
        message = render_to_string("emails/designer_storefront_live.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Your Urbana storefront is live", [user.email], message),
            kwargs={"from_email": "designers@accounts.urbanaafrica.com", "from_name": "Zuri from Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Designer storefront live failed: {e}")


def send_designer_new_order(order_item: OrderItem):
    """Email 5: New order notification — when a customer places an order"""
    try:
        designer = order_item.designer
        product = order_item.product
        order = order_item.order
        context = {
            "designer_name": designer.first_name or designer.email,
            "order_number": order_item.item_id,
            "product_name": product.name if product else "Product",
            "quantity": order_item.quantity,
            "size": order_item.size.name if order_item.size else "N/A",
            "colour": order_item.color.name if order_item.color else "N/A",
            "delivery_timeline": _delivery_timeline(product),
            "order_url": f"{settings.DESIGNER_URL}/orders/{order_item.item_id}",
        }
        message = render_to_string("emails/designer_new_order.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("You have a new Urbana order", [designer.email], message),
            kwargs={"from_email": "designers@accounts.urbanaafrica.com", "from_name": "Zuri from Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Designer new order failed: {e}")


def send_designer_order_shipped(order_item: OrderItem):
    """Email 6: Order shipped confirmation — when vendor marks order as shipped"""
    try:
        designer = order_item.designer
        product = order_item.product
        shipment = getattr(order_item, "shipment", None)
        context = {
            "designer_name": designer.first_name or designer.email,
            "order_number": order_item.item_id,
            "product_name": product.name if product else "Product",
            "tracking_number": getattr(shipment, "tracking_number", "N/A"),
            "courier_name": getattr(shipment, "carrier", "N/A"),
            "order_url": f"{settings.DESIGNER_URL}/orders/{order_item.item_id}",
        }
        message = render_to_string("emails/designer_order_shipped.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Your Urbana order has been marked as shipped", [designer.email], message),
            kwargs={"from_email": "designers@accounts.urbanaafrica.com", "from_name": "Zuri from Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Designer order shipped failed: {e}")


# ============================================================
# Customer Emails
# ============================================================

def send_customer_welcome_email(user: User):
    """Email 1: Customer welcome — immediately after signup"""
    try:
        context = {
            "first_name": user.first_name or "there",
            "shop_url": f"{settings.STORE_URL}/shop",
        }
        message = render_to_string("emails/customer_welcome.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Welcome to Urbana Africa", [user.email], message),
            kwargs={"from_email": "hello@accounts.urbanaafrica.com", "from_name": "Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Customer welcome failed: {e}")


def send_customer_browse_reminder(user: User):
    """Email 2: Browse / discovery — 24-48 hours after signup if no purchase"""
    try:
        context = {
            "first_name": user.first_name or "there",
            "shop_url": f"{settings.STORE_URL}/shop",
        }
        message = render_to_string("emails/customer_browse.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Discover African fashion differently", [user.email], message),
            kwargs={"from_email": "hello@accounts.urbanaafrica.com", "from_name": "Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Customer browse reminder failed: {e}")


def send_customer_order_confirmed(order_item: OrderItem):
    """Email 3: Order confirmation — when customer places an order"""
    try:
        customer = order_item.order.customer
        product = order_item.product
        designer = product.user if product else None
        context = {
            "first_name": customer.user.first_name or "there",
            "order_number": order_item.item_id,
            "designer_name": designer.first_name or designer.email if designer else "Designer",
            "product_name": product.name if product else "Product",
            "size": order_item.size.name if order_item.size else "N/A",
            "colour": order_item.color.name if order_item.color else "N/A",
            "quantity": order_item.quantity,
            "order_total": f"₦{order_item.sub_total:,.0f}",
            "delivery_timeline": _delivery_timeline(product),
            "order_url": f"{settings.CUSTOMER_URL}/orders/{order_item.item_id}",
            "store_url": settings.STORE_URL,
        }
        message = render_to_string("emails/customer_order_confirmed.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Your Urbana order is confirmed", [customer.user.email], message),
            kwargs={"from_email": "support@accounts.urbanaafrica.com", "from_name": "Urbana Africa Support"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Customer order confirmed failed: {e}")


def send_customer_order_shipped(order_item: OrderItem):
    """Email 4: Order shipped — when vendor marks order as shipped"""
    try:
        customer = order_item.order.customer
        product = order_item.product
        designer = product.user if product else None
        shipment = getattr(order_item, "shipment", None)
        context = {
            "first_name": customer.user.first_name or "there",
            "order_number": order_item.item_id,
            "designer_name": designer.first_name or designer.email if designer else "Designer",
            "product_name": product.name if product else "Product",
            "courier_name": getattr(shipment, "carrier", "N/A"),
            "tracking_number": getattr(shipment, "tracking_number", "N/A"),
            "order_url": f"{settings.CUSTOMER_URL}/orders/{order_item.item_id}",
        }
        message = render_to_string("emails/customer_order_shipped.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Your Urbana order is on its way", [customer.user.email], message),
            kwargs={"from_email": "support@accounts.urbanaafrica.com", "from_name": "Urbana Africa Support"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Customer order shipped failed: {e}")


def send_customer_order_delivered(order_item: OrderItem):
    """Email 5: Order delivered — when order is marked as delivered"""
    try:
        customer = order_item.order.customer
        product = order_item.product
        designer = product.user if product else None
        context = {
            "first_name": customer.user.first_name or "there",
            "order_number": order_item.item_id,
            "designer_name": designer.first_name or designer.email if designer else "Designer",
            "product_name": product.name if product else "Product",
            "order_url": f"{settings.CUSTOMER_URL}/orders/{order_item.item_id}",
        }
        message = render_to_string("emails/customer_order_delivered.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("Your Urbana order has arrived", [customer.user.email], message),
            kwargs={"from_email": "support@accounts.urbanaafrica.com", "from_name": "Urbana Africa Support"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Customer order delivered failed: {e}")


def send_customer_review_request(order_item: OrderItem):
    """Email 6: Review / fit feedback — 2-3 days after delivery"""
    try:
        customer = order_item.order.customer
        product = order_item.product
        designer = product.user if product else None
        context = {
            "first_name": customer.user.first_name or "there",
            "order_number": order_item.item_id,
            "designer_name": designer.first_name or designer.email if designer else "Designer",
            "product_name": product.name if product else "Product",
            "order_url": f"{settings.CUSTOMER_URL}/orders/{order_item.item_id}",
            "storefront_url": f"{settings.STORE_URL}/d/{designer.designer_profile.id}" if designer and hasattr(designer, 'designer_profile') else settings.STORE_URL,
        }
        message = render_to_string("emails/customer_review_request.html", context)
        threading.Thread(
            target=resend_sendmail,
            args=("How did your Urbana piece fit?", [customer.user.email], message),
            kwargs={"from_email": "hello@accounts.urbanaafrica.com", "from_name": "Urbana Africa"},
        ).start()
    except Exception as e:
        print(f"[EMAIL] Customer review request failed: {e}")
