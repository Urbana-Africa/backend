"""Transactional & lifecycle email notifications for Urbana.

All emails are sent on a daemon thread via :func:`resend_sendmail`.  Failures
are logged (never silently swallowed) but never raised into the request path
— a failed email must not roll back a committed business transaction.

Idempotency
-----------
Order-confirmation / shipped / delivered emails are guarded by a short-lived
cache key keyed on the ``OrderItem`` id and the email event.  This prevents
duplicate sends when more than one code path can fire the same event (e.g. the
client-side confirm view and the server-side webhook for the same payment).
"""

import logging
import threading

from django.conf import settings
from django.core.cache import cache
from django.template.loader import render_to_string
from django.utils import timezone

from apps.utils.email_sender import resend_sendmail, wrap_email_html
from apps.authentication.models import User
from apps.designers.models import Designer
from apps.customers.models import Order, OrderItem
from apps.core.models import Product

logger = logging.getLogger(__name__)

# Idempotency window — long enough to absorb webhook/confirm retries and
# duplicate status updates, short enough that a legitimate re-send days later
# (e.g. admin re-trigger) is still possible.
_IDEMPOTENCY_TTL = 60 * 60 * 24 * 7  # 7 days


def _already_sent(event, item_id):
    """Return True if this (event, item) pair was sent recently."""
    key = f"email_sent:{event}:{item_id}"
    if cache.get(key):
        return True
    cache.set(key, True, _IDEMPOTENCY_TTL)
    return False


def _delivery_timeline(product):
    """Return a human-readable delivery timeline based on product availability and lead time."""
    if not product:
        return "As agreed"
    lead = getattr(product, "lead_time_days", 0) or 0
    avail = getattr(product, "availability_type", "ready_to_ship") or ""
    if lead <= 0:
        return "As agreed"
    if "made_to_order" in avail:
        return f"Made to order · {lead} days"
    if "pre_order" in avail:
        return f"Pre-order · {lead} days"
    if "custom_order" in avail:
        return f"Custom order · {lead} days"
    return f"Ships in {lead} days"


def _format_amount(amount, currency=None):
    """Format a monetary amount with a currency symbol, falling back gracefully."""
    try:
        symbol_map = {
            "NGN": "₦", "USD": "$", "EUR": "€", "GBP": "£", "GHS": "₵", "KES": "KSh",
        }
        symbol = symbol_map.get((currency or "").upper(), "")
        return f"{symbol}{amount:,.0f}"
    except Exception:
        return str(amount)


def _send_async(subject, recipient, message, from_email, from_name):
    """Fire-and-forget send on a daemon thread, logging failures."""
    def _run():
        try:
            resend_sendmail(
                subject=subject,
                recipient_list=[recipient] if isinstance(recipient, str) else recipient,
                message=message,
                from_email=from_email,
                from_name=from_name,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Async email send failed (subject=%r): %s", subject, exc)

    threading.Thread(target=_run, daemon=True).start()


# ============================================================
# Designer / Vendor Emails
# ============================================================

def send_designer_welcome_email(user: User):
    """Email 1: Founder welcome email — immediately after vendor signs up."""
    cache_key = f"welcome_email_sent_{user.id}"
    if cache.get(cache_key):
        return
    cache.set(cache_key, True, 60 * 60 * 24 * 365)

    try:
        designer = user.designer_profile
        context = {
            "designer_name": user.first_name or user.email,
            "designer_url": f"{settings.DESIGNER_URL}/dashboard",
            "storefront_url": f"{settings.STORE_URL}/d/{designer.id}" if designer else settings.STORE_URL,
        }
        message = render_to_string("emails/designer_welcome.html", context)
        _send_async(
            "Welcome to Urbana Africa", user.email, message,
            "hello@accounts.urbanaafrica.com", "Urbana Africa Team",
        )
    except Exception as e:
        logger.exception("Designer welcome failed: %s", e)


def send_designer_product_upload_reminder(user: User):
    """Email 2: Product upload reminder — 24 hours after signup."""
    try:
        context = {
            "designer_name": user.first_name or user.email,
            "products_url": f"{settings.DESIGNER_URL}/products/add",
        }
        message = render_to_string("emails/designer_product_upload_reminder.html", context)
        _send_async(
            "Upload your best products to Urbana", user.email, message,
            "designers@accounts.urbanaafrica.com", "Zuri from Urbana Africa",
        )
    except Exception as e:
        logger.exception("Designer upload reminder failed: %s", e)


def send_designer_storefront_reminder(user: User):
    """Email 3: Storefront completion reminder — 48-72 hours after signup if no products."""
    try:
        designer = user.designer_profile
        context = {
            "designer_name": user.first_name or user.email,
            "products_url": f"{settings.DESIGNER_URL}/products/add",
            "storefront_url": f"{settings.STORE_URL}/d/{designer.id}" if designer else settings.STORE_URL,
        }
        message = render_to_string("emails/designer_storefront_reminder.html", context)
        _send_async(
            "Your Urbana storefront is waiting", user.email, message,
            "designers@accounts.urbanaafrica.com", "Zuri from Urbana Africa",
        )
    except Exception as e:
        logger.exception("Designer storefront reminder failed: %s", e)


def send_designer_storefront_live(user: User):
    """Email 4: Storefront is live — when first product is published."""
    try:
        designer = user.designer_profile
        context = {
            "designer_name": user.first_name or user.email,
            "storefront_url": f"{settings.STORE_URL}/d/{designer.id}" if designer else settings.STORE_URL,
            "designer_url": f"{settings.DESIGNER_URL}/dashboard",
        }
        message = render_to_string("emails/designer_storefront_live.html", context)
        _send_async(
            "Your Urbana storefront is live", user.email, message,
            "designers@accounts.urbanaafrica.com", "Zuri from Urbana Africa",
        )
    except Exception as e:
        logger.exception("Designer storefront live failed: %s", e)


def send_designer_new_order(order_item: OrderItem):
    """Email 5: New order notification — when a customer places an order (idempotent)."""
    if _already_sent("designer_new_order", order_item.id):
        return
    try:
        designer = order_item.designer
        if not designer or not designer.email:
            return
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
        _send_async(
            "You have a new Urbana order", designer.email, message,
            "designers@accounts.urbanaafrica.com", "Zuri from Urbana Africa",
        )
    except Exception as e:
        logger.exception("Designer new order failed: %s", e)


def send_designer_order_shipped(order_item: OrderItem):
    """Email 6: Order shipped confirmation — when vendor marks order as shipped (idempotent)."""
    if _already_sent("designer_order_shipped", order_item.id):
        return
    try:
        designer = order_item.designer
        if not designer or not designer.email:
            return
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
        _send_async(
            "Your Urbana order has been marked as shipped", designer.email, message,
            "designers@accounts.urbanaafrica.com", "Zuri from Urbana Africa",
        )
    except Exception as e:
        logger.exception("Designer order shipped failed: %s", e)


# ============================================================
# Customer Emails
# ============================================================

def send_customer_welcome_email(user: User):
    """Email 1: Customer welcome — immediately after signup."""
    cache_key = f"welcome_email_sent_{user.id}"
    if cache.get(cache_key):
        return
    cache.set(cache_key, True, 60 * 60 * 24 * 365)

    try:
        context = {
            "first_name": user.first_name or "there",
            "shop_url": f"{settings.STORE_URL}/shop",
        }
        message = render_to_string("emails/customer_welcome.html", context)
        _send_async(
            "Welcome to Urbana Africa", user.email, message,
            "hello@accounts.urbanaafrica.com", "Urbana Africa",
        )
    except Exception as e:
        logger.exception("Customer welcome failed: %s", e)


def send_customer_browse_reminder(user: User):
    """Email 2: Browse / discovery — 24-48 hours after signup if no purchase."""
    try:
        context = {
            "first_name": user.first_name or "there",
            "shop_url": f"{settings.STORE_URL}/shop",
        }
        message = render_to_string("emails/customer_browse.html", context)
        _send_async(
            "Discover African fashion differently", user.email, message,
            "hello@accounts.urbanaafrica.com", "Urbana Africa",
        )
    except Exception as e:
        logger.exception("Customer browse reminder failed: %s", e)


def send_customer_order_confirmed(order_item: OrderItem):
    """Email 3: Order confirmation — when customer places an order (idempotent)."""
    if _already_sent("customer_order_confirmed", order_item.id):
        return
    try:
        customer = order_item.order.customer
        recipient = customer.user.email
        if not recipient:
            return
        product = order_item.product
        designer = product.user if product else None
        currency = getattr(order_item.order, "currency", None)
        context = {
            "first_name": customer.user.first_name or "there",
            "order_number": order_item.item_id,
            "designer_name": designer.first_name or designer.email if designer else "Designer",
            "product_name": product.name if product else "Product",
            "size": order_item.size.name if order_item.size else "N/A",
            "colour": order_item.color.name if order_item.color else "N/A",
            "quantity": order_item.quantity,
            "order_total": _format_amount(order_item.sub_total, currency),
            "delivery_timeline": _delivery_timeline(product),
            "order_url": f"{settings.CUSTOMER_URL}/orders/{order_item.item_id}",
            "store_url": settings.STORE_URL,
        }
        message = render_to_string("emails/customer_order_confirmed.html", context)
        _send_async(
            "Your Urbana order is confirmed", recipient, message,
            "support@accounts.urbanaafrica.com", "Urbana Africa Support",
        )
    except Exception as e:
        logger.exception("Customer order confirmed failed: %s", e)


def send_customer_order_shipped(order_item: OrderItem):
    """Email 4: Order shipped — when vendor marks order as shipped (idempotent)."""
    if _already_sent("customer_order_shipped", order_item.id):
        return
    try:
        customer = order_item.order.customer
        recipient = customer.user.email
        if not recipient:
            return
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
        _send_async(
            "Your Urbana order is on its way", recipient, message,
            "support@accounts.urbanaafrica.com", "Urbana Africa Support",
        )
    except Exception as e:
        logger.exception("Customer order shipped failed: %s", e)


def send_customer_order_delivered(order_item: OrderItem):
    """Email 5: Order delivered — when order is marked as delivered (idempotent)."""
    if _already_sent("customer_order_delivered", order_item.id):
        return
    try:
        customer = order_item.order.customer
        recipient = customer.user.email
        if not recipient:
            return
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
        _send_async(
            "Your Urbana order has arrived", recipient, message,
            "support@accounts.urbanaafrica.com", "Urbana Africa Support",
        )
    except Exception as e:
        logger.exception("Customer order delivered failed: %s", e)


def send_customer_review_request(order_item: OrderItem):
    """Email 6: Review / fit feedback — 2-3 days after delivery (idempotent)."""
    if _already_sent("customer_review_request", order_item.id):
        return
    try:
        customer = order_item.order.customer
        recipient = customer.user.email
        if not recipient:
            return
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
        _send_async(
            "How did your Urbana piece fit?", recipient, message,
            "hello@accounts.urbanaafrica.com", "Urbana Africa",
        )
    except Exception as e:
        logger.exception("Customer review request failed: %s", e)


def send_admin_designer_notification(designer_user, action_word):
    """Notify all C-suite, superadmin, and support agent users of designer signup/profile updates."""
    from django.db.models import Q

    try:
        admins = User.objects.filter(
            Q(user_type="admin", admin_role__in=["c_level", "superadmin", "support_agent"]) | Q(is_superuser=True),
            is_active=True,
            is_deleted=False
        )
        recipient_list = list({email for email in admins.values_list("email", flat=True) if email})

        if not recipient_list:
            # No admin users configured — log rather than spam a guessed address.
            logger.warning(
                "Admin designer notification skipped (no admin recipients) for %s (%s)",
                designer_user.email, action_word,
            )
            return

        subject = f"Urbana Admin: Designer profile {action_word}"
        message = (
            f"<p>Designer <strong>{designer_user.email}</strong> has {action_word} their profile.</p>"
            f"<p>Please review the details in the admin dashboard.</p>"
        )
        message = wrap_email_html(message, subject)
        _send_async(
            subject, recipient_list, message,
            "hello@accounts.urbanaafrica.com", "Urbana Africa Notification",
        )
    except Exception as e:
        logger.exception("Admin designer notification failed: %s", e)
