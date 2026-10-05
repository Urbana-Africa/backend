from rest_framework import viewsets, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from .permissions import IsCLevel
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import logging
import threading
from decimal import Decimal
from django.template.loader import render_to_string
from apps.core.models import *
from apps.customers.models import *
from apps.designers.models import *
from apps.pay.models import Withdrawal
from .models import (
    AuditEvent, DataQualityCheck, ReconciliationRun, ReconciliationException,
    WorkItem, ApprovalRequest, Case, PolicyVersion, PrivacyRequest,
    Incident, CapabilityGrant,
)
from apps.utils.pagination import StandardPagination
from .serializers import *
from .audit import record_audit
from .capabilities import HasCapability, has_capability
from apps.core.serializers import ProductSerializer
from apps.utils.email_sender import resend_sendmail, wrap_email_html

logger = logging.getLogger(__name__)



# =====================================================
# BASE ADMIN VIEWSET
# =====================================================

class AdminBaseViewSet(viewsets.ModelViewSet):
    """Staff-facing CRUD. ``IsAdminUser`` is the staff gate; ``HasCapability``
    enforces the named capability per domain — ``view_capability`` for safe
    methods, ``manage_capability`` for mutations, ``action_capabilities`` for
    per-action overrides. Leave the caps unset to keep staff-only behavior."""
    permission_classes = [IsAdminUser, HasCapability]
    pagination_class = StandardPagination

    view_capability = None
    manage_capability = None
    action_capabilities = {}

    filter_backends = [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]

# =====================================================
# CUSTOMER MANAGEMENT
# =====================================================

class AdminCustomerViewSet(AdminBaseViewSet):
    view_capability = 'customers.view'
    manage_capability = 'customers.manage'
    queryset = Customer.objects.select_related("user")
    serializer_class = AdminCustomerSerializer
    search_fields = ["user__email", "user__username"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]


class AdminAddressViewSet(AdminBaseViewSet):
    view_capability = 'customers.view'
    manage_capability = 'customers.manage'
    queryset = Address.objects.select_related("customer")
    serializer_class = AdminAddressSerializer


class AdminWishlistViewSet(AdminBaseViewSet):
    view_capability = 'customers.view'
    manage_capability = 'customers.manage'
    queryset = Wishlist.objects.all()
    serializer_class = AdminWishlistSerializer


class AdminCartItemViewSet(AdminBaseViewSet):
    view_capability = 'customers.view'
    manage_capability = 'customers.manage'
    queryset = CartItem.objects.all()
    serializer_class = AdminCartItemSerializer

# =====================================================
# PRODUCT & CATALOG
# =====================================================

class AdminProductViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    action_capabilities = {
        'publish': 'catalog.publish', 'unpublish': 'catalog.publish',
    }
    queryset = Product.objects.select_related(
        "user", "category", "brand", "currency"
    ).prefetch_related("sizes", "media")

    serializer_class = AdminProductSerializer
    parser_classes = (JSONParser, FormParser, MultiPartParser)

    filterset_fields = {
        "is_published": ["exact"],
        "is_admin_published": ["exact"],
        "is_active": ["exact"],
        "featured": ["exact"],
        "designer_product__designer": ["exact"],
    }

    search_fields = ["name", "sku"]
    ordering_fields = ["created_at", "name"]
    ordering = ["-created_at"]

    @action(detail=False, methods=["post"], url_path="create-for-designer")
    def create_for_designer(self, request):
        designer_id = request.data.get("designer_id")
        if not designer_id:
            return Response(
                {"status": "error", "message": "designer_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            designer = Designer.objects.get(id=designer_id)
        except Designer.DoesNotExist:
            return Response(
                {"status": "error", "message": "Designer not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Build product data (strip out files and designer_id)
        data = {k: v for k, v in request.data.items() if k not in ["designer_id", "media", "media[]"]}
        serializer = ProductSerializer(data=data)
        if serializer.is_valid():
            product = serializer.save(user=designer.user)
            # Link to designer
            DesignerProduct.objects.create(
                designer=designer,
                product=product,
                stock=data.get("stock", 0),
            )
            # Handle uploaded images
            images = request.FILES.getlist("media[]")
            if images:
                for img in images[:6]:
                    asset = MediaAsset.objects.create(
                        file=img,
                        media_type=MediaAsset.MediaType.IMAGE,
                    )
                    product.media.add(asset)
            return Response({
                "status": "success",
                "message": "Product created for designer",
                "data": AdminProductSerializer(product).data
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["patch"], url_path="unpublish")
    def unpublish(self, request, pk=None):
        product = self.get_object()

        reasons = request.data.get("unpublish_reasons", [])
        comment = request.data.get("comment", "")

        if not isinstance(reasons, list) or not reasons:
            return Response(
                {"message": "At least one unpublish reason is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        product.unpublish_reasons = reasons
        product.unpublish_comment = comment
        product.is_admin_published = False
        product.save()

        record_audit(
            request=request, action='catalog.unpublish', entity=product,
            before={'is_admin_published': True},
            after={'is_admin_published': False},
            reason=comment or ', '.join(reasons),
        )

        # In-app notification
        Notification.objects.create(
            user=product.user,
            title="Product unpublished",
            message=f"Your product '{product.name}' has been unpublished by admin. Reason: {', '.join(reasons)}",
            notification_type=Notification.Type.PRODUCT,
            link=f"/products/edit/{product.id}",
        )

        # Email notification
        try:
            context = {
                "designer_name": product.user.first_name or product.user.email,
                "product_name": product.name,
                "product_sku": product.sku,
                "is_published": False,
                "reasons": reasons,
                "comment": comment,
                "products_url": f"{settings.DESIGNER_URL}/products",
            }
            message = render_to_string("emails/designer_product_status_update.html", context)
            threading.Thread(
                target=resend_sendmail,
                args=(
                    f"Urbana — Product Unpublished: {product.name}",
                    [product.user.email],
                    message,
                ),
                kwargs={"from_email": "hello@accounts.urbanaafrica.com", "from_name": "Urbana Studio"},
            ).start()
        except Exception as e:
            logger.error("Error sending product unpublish email: %s", e)

        serializer = self.get_serializer(product)
        return Response({
            "status": "success",
            "data": serializer.data
        })

    @action(detail=True, methods=["patch"], url_path="publish")
    def publish(self, request, pk=None):
        """CAT-01 — publishing runs moderation checks (media, price,
        description, categorization). A failing product needs an
        ``exception_reason`` which lands in the audit record."""
        product = self.get_object()
        from .gates import evaluate_product_moderation
        gate = evaluate_product_moderation(product)
        exception_reason = (request.data.get('exception_reason')
                            or '').strip()
        if not gate['passed'] and not exception_reason:
            return Response(
                {"status": "error",
                 "message": "Product fails moderation checks — provide "
                            "exception_reason to publish anyway.",
                 "checks": gate['checks']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        product.is_admin_published = True
        product.save()

        record_audit(
            request=request, action='catalog.publish', entity=product,
            before={'is_admin_published': False},
            after={'is_admin_published': True, 'checks': gate['checks'],
                   'exception': exception_reason},
        )

        # In-app notification
        Notification.objects.create(
            user=product.user,
            title="Product published",
            message=f"Your product '{product.name}' is now live on Urbana.",
            notification_type=Notification.Type.PRODUCT,
            link=f"/products/edit/{product.id}",
        )

        # Send storefront live email on first published product
        try:
            published_count = Product.objects.filter(
                user=product.user, is_published=True, is_admin_published=True
            ).count()
            if published_count == 1:
                from apps.utils.notifications import send_designer_storefront_live
                send_designer_storefront_live(product.user)
        except Exception as e:
            logger.error("[EMAIL] Storefront live failed: %s", e)

        # Email notification
        try:
            context = {
                "designer_name": product.user.first_name or product.user.email,
                "product_name": product.name,
                "product_sku": product.sku,
                "is_published": True,
                "reasons": None,
                "comment": "",
                "products_url": f"{settings.DESIGNER_URL}/products",
            }
            message = render_to_string("emails/designer_product_status_update.html", context)
            threading.Thread(
                target=resend_sendmail,
                args=(
                    f"Urbana — Product Live: {product.name}",
                    [product.user.email],
                    message,
                ),
                kwargs={"from_email": "hello@accounts.urbanaafrica.com", "from_name": "Urbana Studio"},
            ).start()
        except Exception as e:
            logger.error("Error sending product publish email: %s", e)

        serializer = self.get_serializer(product)
        return Response({
            "status": "success",
            "data": serializer.data
        })


class AdminCategoryViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = Category.objects.all()
    serializer_class = AdminCategorySerializer


class AdminBrandViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = Brand.objects.all()
    serializer_class = AdminBrandSerializer


class AdminCurrencyViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = Currency.objects.all()
    serializer_class = AdminCurrencySerializer


class AdminSizesViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = Sizes.objects.all()
    serializer_class = AdminSizesSerializer


class AdminMediaAssetViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = MediaAsset.objects.all()
    serializer_class = AdminMediaAssetSerializer


class AdminReviewViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = Review.objects.select_related("product", "customer")
    serializer_class = AdminReviewSerializer


class AdminShippingMethodViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = ShippingMethod.objects.all()
    serializer_class = AdminShippingMethodSerializer


class AdminCountryViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = Country.objects.all()
    serializer_class = AdminCountrySerializer


# =====================================================
# ORDER MANAGEMENT
# =====================================================
class AdminOrderViewSet(AdminBaseViewSet):
    view_capability = 'orders.view'
    manage_capability = 'orders.edit_fulfillment'

    def partial_update(self, request, *args, **kwargs):
        """OPS-03 — fulfillment state moves only through legal transitions,
        each carrying a reason; same-status writes are no-ops."""
        from .cases import ORDER_TRANSITIONS, validate_transition
        order = self.get_object()
        new_status = request.data.get('status')
        if new_status is not None and new_status != order.status:
            reason = (request.data.get('reason') or '').strip()
            err = validate_transition(
                order, 'status', new_status, ORDER_TRANSITIONS,
                reason=reason, reason_label='order status change',
            )
            if err:
                record_audit(
                    request=request, action='orders.status_denied',
                    entity=order, before={'status': order.status},
                    after={'attempted': new_status}, reason=err['message'],
                )
                return Response(err, status=status.HTTP_400_BAD_REQUEST)
            prior = order.status
            response = super().partial_update(request, *args, **kwargs)
            if response.status_code < 300:
                record_audit(
                    request=request, action='orders.status_change',
                    entity=order, before={'status': prior},
                    after={'status': new_status,
                           'idempotency_key': request.data.get(
                               'idempotency_key', '')},
                    reason=reason,
                )
            return response
        return super().partial_update(request, *args, **kwargs)
    queryset = Order.objects.select_related("customer", "invoice")
    serializer_class = AdminOrderSerializer
    filterset_fields = ["status"]
    search_fields = ["order_id"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        """OPS-01 — one chronological view of everything that happened to an
        order: creation, payment, items, escrow, tracking, returns, disputes
        and staff audit actions. A support agent answers 'what happened'
        without switching systems."""
        order = self.get_object()
        events = []

        def ev(ts, type_, summary, **meta):
            if ts:
                events.append({'at': ts.isoformat(), 'type': type_,
                               'summary': summary, 'meta': meta})

        ev(order.created_at, 'order.created',
           f"Order {order.order_id} created — {order.status}",
           status=order.status, total=str(order.total_amount))

        payment = getattr(order.invoice, 'payment', None) if order.invoice else None
        if payment:
            ev(payment.date_time_added, 'payment.attempt',
               f"Payment {payment.reference} — {payment.status}",
               status=payment.status, processor=payment.processor,
               amount=str(payment.amount), currency=payment.currency)
            if payment.is_paid and payment.date_time_paid:
                ev(payment.date_time_paid, 'payment.confirmed',
                   f"Payment {payment.reference} confirmed via "
                   f"{payment.processor or 'provider'}",
                   amount=str(payment.amount), currency=payment.currency)

        item_ids = []
        items = order.items.select_related('designer', 'escrow').all()
        for item in items:
            item_ids.append(str(item.item_id))
            ev(item.created_at, 'item.created',
               f"Item {item.item_id} added — {item.status}",
               status=item.status, designer_status=item.designer_status,
               designer=getattr(item.designer, 'email', ''),
               sub_total=str(item.sub_total))
            if item.delivered_at:
                ev(item.delivered_at, 'item.delivered',
                   f"Item {item.item_id} delivered",
                   customer_status=item.customer_status)
            esc = getattr(item, 'escrow', None)
            if esc:
                ev(esc.created_at, 'escrow.held',
                   f"Escrow of {esc.amount} held for item {item.item_id}",
                   escrow_status=esc.status,
                   commission=str(esc.platform_commission))
                if esc.released_at:
                    ev(esc.released_at, 'escrow.released',
                       f"Escrow released to designer for item {item.item_id}",
                       amount=str(esc.amount))
            for rr in item.return_requests.all():
                ev(rr.created_at, 'return.requested',
                   f"Return {rr.return_id} requested — {rr.reason}",
                   status=rr.status)
                if rr.resolved_at:
                    ev(rr.resolved_at, 'return.resolved',
                       f"Return {rr.return_id} resolved — {rr.status}",
                       status=rr.status)
                dispute = getattr(rr, 'dispute', None)
                if dispute:
                    ev(dispute.created_at, 'dispute.opened',
                       f"Dispute {dispute.dispute_id} opened by "
                       f"{getattr(dispute.opened_by, 'email', '')}",
                       status=dispute.status)
                    if dispute.resolved_at:
                        ev(dispute.resolved_at, 'dispute.resolved',
                           f"Dispute {dispute.dispute_id} resolved — "
                           f"{dispute.resolution or dispute.status}",
                           refund=str(dispute.refund_amount or ''))

        tracking = getattr(order, 'tracking', None)
        if tracking:
            ev(tracking.last_updated, 'tracking.update',
               f"Tracking {tracking.tracking_number}: {tracking.current_status}",
               carrier=tracking.carrier or '',
               eta=str(tracking.estimated_delivery or ''))

        for a in AuditEvent.objects.filter(
            Q(entity_type='Order', entity_id__in=(order.order_id, str(order.pk)))
            | Q(entity_type='OrderItem', entity_id__in=item_ids)
        ).order_by('created_at'):
            ev(a.created_at, 'audit',
               f"{a.action} by {a.actor_email or 'system'}",
               actor=a.actor_email, before=a.before, after=a.after)

        events.sort(key=lambda e: e['at'])
        return Response({
            'order': order.order_id,
            'status': order.status,
            'customer': getattr(getattr(order, 'customer', None), 'email', '') or
                        getattr(getattr(getattr(order, 'customer', None), 'user', None), 'email', ''),
            'events': events,
            'meta': {
                'note': ('Item-level status transitions are derived from '
                         'available timestamps; full transition history lands '
                         'with the Phase-1 state machine.'),
            },
        })


class AdminOrderItemViewSet(AdminBaseViewSet):
    view_capability = 'orders.view'
    manage_capability = 'orders.edit_fulfillment'
    queryset = OrderItem.objects.select_related("order", "product")
    serializer_class = AdminOrderItemSerializer

    _STATUS_FIELDS = (
        ('status', 'ORDER_TRANSITIONS'),
        ('designer_status', 'ORDER_TRANSITIONS'),
        ('customer_status', 'CUSTOMER_STATUS_TRANSITIONS'),
        ('collection_origin_status', 'ORDER_TRANSITIONS'),
        ('collection_destination_status', 'ORDER_TRANSITIONS'),
    )

    def partial_update(self, request, *args, **kwargs):
        """OPS-03 — every status leg on an item moves through its own
        transition map; each change needs a reason and is audited."""
        from . import cases
        item = self.get_object()
        reason = (request.data.get('reason') or '').strip()
        for field, map_name in self._STATUS_FIELDS:
            new_value = request.data.get(field)
            if new_value is None or new_value == getattr(item, field):
                continue
            err = cases.validate_transition(
                item, field, new_value, getattr(cases, map_name),
                reason=reason, reason_label=f'{field} change',
            )
            if err:
                record_audit(
                    request=request, action='orders.status_denied',
                    entity=item, before={field: getattr(item, field)},
                    after={'attempted': new_value}, reason=err['message'],
                )
                return Response(err, status=status.HTTP_400_BAD_REQUEST)
        response = super().partial_update(request, *args, **kwargs)
        if response.status_code < 300:
            changed = {f: request.data[f] for f, _ in self._STATUS_FIELDS
                       if f in request.data}
            if changed:
                record_audit(
                    request=request, action='orders.item_status_change',
                    entity=item, after=changed,
                    reason=reason,
                )
        return response

    @action(detail=True, methods=["post"])
    def review_packaging_media(self, request, pk=None):
        order_item = self.get_object()
        status_val = request.data.get("status")
        reason = request.data.get("reason", "")
        
        if status_val not in ['approved', 'rejected']:
            return Response({"error": "Invalid status. Must be 'approved' or 'rejected'."}, status=400)
            
        order_item.packaging_approval_status = status_val
        if status_val == 'rejected':
            order_item.packaging_rejection_reason = reason
        else:
            order_item.packaging_rejection_reason = ""
            
        order_item.save()

        record_audit(
            request=request, action='orders.review_packaging',
            entity=order_item, after={'status': status_val}, reason=reason,
        )
        
        # Trigger email to designer
        from django.utils.html import escape

        try:
            if status_val == 'approved':
                message = (
                    f"<p>Your packaging media for Order Item <strong>{order_item.item_id}</strong> "
                    f"has been approved. You may now generate a shipping label.</p>"
                )
            else:
                message = (
                    f"<p>Your packaging media for Order Item <strong>{order_item.item_id}</strong> "
                    f"has been rejected.</p>"
                    f"<p><strong>Reason:</strong> {escape(reason)}</p>"
                    f"<p>Please update your packaging and re-upload.</p>"
                )

            subject = f"Urbana - Packaging Media {status_val.capitalize()}"
            message = wrap_email_html(message, subject)
            threading.Thread(
                target=resend_sendmail,
                args=(
                    subject,
                    [order_item.designer.email],
                    message,
                ),
                kwargs={"from_email": "hello@accounts.urbanaafrica.com", "from_name": "Urbana Studio"},
            ).start()
        except Exception as e:
            logger.error("Failed to send designer packaging email: %s", e)
            
        return Response({
            "status": "success",
            "message": f"Packaging media marked as {status_val}.",
            "data": self.get_serializer(order_item).data
        })


class AdminOrderTrackingViewSet(AdminBaseViewSet):
    view_capability = 'orders.view'
    manage_capability = 'orders.edit_fulfillment'
    queryset = OrderTracking.objects.select_related("order")
    serializer_class = AdminOrderTrackingSerializer

    def perform_create(self, serializer):
        instance = serializer.save()
        order = instance.order
        if order and order.customer and order.customer.user and order.customer.user.email:
            try:
                context = {
                    "customer_name": order.customer.user.first_name or order.customer.user.email,
                    "order_id": order.order_id,
                    "status": instance.status,
                    "tracking_number": instance.tracking_number or "",
                    "carrier": instance.carrier or "",
                    "estimated_delivery": str(instance.estimated_delivery) if instance.estimated_delivery else "",
                }
                message = render_to_string("emails/customer_shipping_update.html", context)
                threading.Thread(
                    target=resend_sendmail,
                    args=(
                        f"Urbana — Shipping Update for Order {order.order_id}",
                        [order.customer.user.email],
                        message,
                    ),
                    kwargs={"from_email": "support@accounts.urbanaafrica.com", "from_name": "Urbana Africa Support"},
                ).start()
            except Exception as e:
                logger.error("Error sending shipping update email: %s", e)

    def perform_update(self, serializer):
        instance = serializer.save()
        order = instance.order
        if order and order.customer and order.customer.user and order.customer.user.email:
            try:
                context = {
                    "customer_name": order.customer.user.first_name or order.customer.user.email,
                    "order_id": order.order_id,
                    "status": instance.status,
                    "tracking_number": instance.tracking_number or "",
                    "carrier": instance.carrier or "",
                    "estimated_delivery": str(instance.estimated_delivery) if instance.estimated_delivery else "",
                }
                message = render_to_string("emails/customer_shipping_update.html", context)
                threading.Thread(
                    target=resend_sendmail,
                    args=(
                        f"Urbana — Shipping Update for Order {order.order_id}",
                        [order.customer.user.email],
                        message,
                    ),
                    kwargs={"from_email": "support@accounts.urbanaafrica.com", "from_name": "Urbana Africa Support"},
                ).start()
            except Exception as e:
                logger.error("Error sending shipping update email: %s", e)

# =====================================================
# RETURN MANAGEMENT
# =====================================================

class AdminReturnRequestViewSet(AdminBaseViewSet):
    view_capability = 'support.view'
    manage_capability = 'support.manage'
    queryset = ReturnRequest.objects.select_related(
        "order_item",
        "order_item__order"
    )

    serializer_class = AdminReturnRequestSerializer

    filterset_fields = ["status", "admin_status"]
    search_fields = ["return_id"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    lookup_field = "return_id"
    lookup_url_kwarg = "return_id"

    @action(detail=True, methods=["post"], url_path="action")
    def perform_action(self, request, return_id=None):
        """
        POST /admin/returns/{return_id}/action

        Body:
        {
            "action": "approve" | "reject",
            "reason": "optional rejection reason"
        }
        """

        instance = self.get_object()

        action_type = request.data.get("action")
        reason = request.data.get("reason", "")

        if action_type not in ["approve", "reject"]:
            return Response(
                {"detail": "Invalid action. Must be 'approve' or 'reject'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = instance.order_item.order
        customer = order.customer

        context = {
            "customer": customer,
            "return_request": instance,
            "order": order,
            "order_item": instance.order_item,
            "reject_reason": reason,
        }

        if action_type == "approve":

            instance.admin_status = "approved"
            instance.status = ReturnRequest.Status.APPROVED
            instance.save()

            record_audit(
                request=request, action='support.return_approve',
                entity=instance,
                after={'status': 'approved', 'order': order.order_id},
            )

            subject = f"Your Return Request #{instance.return_id} Has Been Approved"

            message = render_to_string(
                "emails/return_approved.html",
                context,
            )

        elif action_type == "reject":

            if not reason:
                return Response(
                    {"detail": "Rejection reason is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            instance.admin_status = "rejected"
            instance.status = ReturnRequest.Status.REJECTED
            instance.reject_reason = reason
            instance.save()

            record_audit(
                request=request, action='support.return_reject',
                entity=instance, after={'status': 'rejected'}, reason=reason,
            )

            subject = f"Your Return Request #{instance.return_id} Was Rejected"

            message = render_to_string(
                "emails/return_rejected.html",
                context,
            )

        # Send email asynchronously
        threading.Thread(
            target=resend_sendmail,
            args=(subject, [customer.user.email], message),
            kwargs={"from_email": "support@accounts.urbanaafrica.com", "from_name": "Urbana Africa Support"},
        ).start()

        # Notify designer
        designer_user = instance.order_item.product.user
        if action_type == "approve":
            Notification.objects.create(
                user=designer_user,
                title="Return request approved",
                message=f"Return request #{instance.return_id} for {instance.order_item.product.name} has been approved by admin.",
                notification_type=Notification.Type.ORDER,
                link=f"/returns/{instance.order_item.id}",
            )
        elif action_type == "reject":
            Notification.objects.create(
                user=designer_user,
                title="Return request rejected",
                message=f"Return request #{instance.return_id} for {instance.order_item.product.name} was rejected by admin. Reason: {reason}",
                notification_type=Notification.Type.ORDER,
                link=f"/returns/{instance.order_item.id}",
            )

        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminDisputeViewSet(AdminBaseViewSet):
    view_capability = 'support.view'
    manage_capability = 'support.manage'
    queryset = Dispute.objects.select_related(
        "return_request",
        "return_request__order_item",
        "opened_by"
    )
    serializer_class = AdminDisputeSerializer

    filterset_fields = ["status", "resolution"]
    search_fields = ["dispute_id"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    lookup_field = "dispute_id"
    lookup_url_kwarg = "dispute_id"

    @action(detail=True, methods=["get"], url_path="refund-context")
    def refund_context(self, request, dispute_id=None):
        """SUP-03 — the pre-confirmation preview: eligibility, collected
        funds, prior refunds, escrow state and the fee consequences."""
        from .gates import refund_context
        return Response({"status": "success",
                         "data": refund_context(self.get_object())})

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, dispute_id=None):
        """
        POST /admin/disputes/{dispute_id}/resolve
        Resolves a dispute and updates the return request status.
        A resolution carrying ``refund_amount`` moves money — per the PRD
        rights table support staff can only *request* refunds, so that path
        additionally requires the ``finance.refund`` capability.
        """
        instance = self.get_object()
        resolution = request.data.get("resolution")
        admin_notes = request.data.get("admin_notes", "")
        refund_amount = request.data.get("refund_amount")

        if instance.status in (Dispute.Status.RESOLVED, Dispute.Status.CLOSED):
            return Response(
                {"detail": "Dispute already resolved — a second resolution "
                           "cannot issue a duplicate refund (SUP-03)."},
                status=400,
            )

        if resolution not in Dispute.Resolution.values:
            return Response(
                {"detail": f"Invalid resolution. Must be one of: {Dispute.Resolution.values}"},
                status=400
            )

        try:
            has_refund = refund_amount is not None \
                and Decimal(str(refund_amount)) > 0
        except Exception:
            return Response({"detail": "Invalid refund_amount."}, status=400)

        if has_refund and not has_capability(request.user, 'finance.refund'):
            record_audit(
                request=request, action='permission.denied',
                entity_type='capability', entity_id='finance.refund',
                after={'path': request.path, 'dispute': instance.dispute_id,
                       'refund_amount': str(refund_amount)},
                reason='dispute refund requires finance.refund',
            )
            return Response(
                {"detail": "Resolving with a refund requires the "
                           "finance.refund capability. Support staff can "
                           "request the refund instead."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # SUP-03 — a refund can never exceed the collected funds for the
        # disputed item minus refunds already issued against it.
        if has_refund:
            from .gates import refund_context
            ctx = refund_context(instance)
            if not ctx['payment_confirmed']:
                return Response(
                    {"detail": "The order for this item has no confirmed "
                               "payment — there are no collected funds to "
                               "refund.", "refund_context": ctx},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if Decimal(str(refund_amount)) > Decimal(ctx['remaining_refundable']):
                return Response(
                    {"detail": "Refund exceeds remaining refundable amount "
                               "for this item.",
                     "refund_context": ctx},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        prior_status = instance.status
        instance.resolution = resolution
        instance.admin_notes = admin_notes
        if refund_amount:
            instance.refund_amount = refund_amount
        
        instance.status = Dispute.Status.RESOLVED
        instance.resolved_at = timezone.now()
        instance.save()

        record_audit(
            request=request, action='support.dispute_resolve',
            entity=instance,
            before={'status': prior_status},
            after={'resolution': resolution, 'status': 'resolved',
                   'refund_amount': str(refund_amount or '')},
            reason=admin_notes,
        )

        # Update associated return request — an issued refund stays
        # pending until provider/settlement evidence lands.
        return_req = instance.return_request
        return_req.status = (
            ReturnRequest.Status.REFUND_PENDING if has_refund
            else ReturnRequest.Status.DISPUTE_RESOLVED
        )
        return_req.resolved_at = timezone.now()
        return_req.save()

        # Notification logic could go here (email to customer and designer)

        serializer = self.get_serializer(instance)
        return Response({
            "status": "success",
            "message": "Dispute resolved successfully.",
            "data": serializer.data
        })




# =====================================================
# DESIGNER MANAGEMENT
# =====================================================



class AdminDesignerViewSet(AdminBaseViewSet):
    view_capability = 'designers.view'
    manage_capability = 'designers.manage'
    queryset = Designer.objects.select_related("user")
    serializer_class = AdminDesignerSerializer

    filterset_fields = {
        "status": ["exact"],
        "is_verified": ["exact"],
        "country": ["exact"],
        "created_at": ["gte", "lte"],
    }

    search_fields = [
        "brand_name",
        "country",
        "user__first_name",
        "user__last_name",
        "user__email",
    ]

    ordering_fields = ["created_at", "brand_name"]
    ordering = ["-created_at"]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        stats = {
            "total": queryset.count(),
            "verified": queryset.filter(is_verified=True).count(),
            "unverified": queryset.filter(is_verified=False).count(),
            "approved": queryset.filter(status=Designer.Status.APPROVED).count(),
            "blocked": queryset.filter(status=Designer.Status.BLOCKED).count(),
            "rejected": queryset.filter(status=Designer.Status.REJECTED).count(),
            "pending": queryset.filter(status=Designer.Status.PENDING).count(),
        }

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response.data["stats"] = stats
            return response

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            "results": serializer.data,
            "stats": stats
        })

    def _send_status_notifications(self, designer, new_status):
        """In-app notification + status email for a designer status transition.

        Shared by the update-status action and perform_update so both code
        paths behave identically. On approval, the email carries an upload
        CTA when the designer still has no products — approval no longer
        requires products, so we nudge instead of gate.
        """
        status_messages = {
            Designer.Status.APPROVED: ("Profile approved", "Your designer profile has been approved. Upload your products to start selling.", "/products/add"),
            Designer.Status.REJECTED: ("Profile update required", "Your profile needs some refinements before approval.", "/profile-status"),
            Designer.Status.BLOCKED: ("Account restricted", "Your account has been restricted. Contact support for assistance.", "/help"),
            Designer.Status.PENDING: ("Profile under review", "Your designer profile is now under review by our curation team.", "/profile-status"),
        }
        if new_status in status_messages:
            title, msg, link = status_messages[new_status]
            Notification.objects.create(
                user=designer.user,
                title=title,
                message=msg,
                notification_type=Notification.Type.PROFILE,
                link=link,
            )

        # Send email notification (async, failures are logged not raised)
        def _send_status_email():
            try:
                from apps.core.models import Product as ProductModel
                products_count = ProductModel.objects.filter(user=designer.user).count()
                subject = f"Urbana Studio: Account Status Updated ({new_status.title()})"
                status_message_map = {
                    Designer.Status.APPROVED: "Your designer profile has been approved. Upload your products to start selling on Urbana Africa.",
                    Designer.Status.REJECTED: "Your profile needs a few refinements before it can be approved. Please review the details below and update your profile.",
                    Designer.Status.BLOCKED: "Your account has been restricted. Please contact support for assistance.",
                    Designer.Status.PENDING: "Your designer profile is now under review by our curation team.",
                }
                context = {
                    "designer": designer,
                    "status_label": new_status.title(),
                    "status_message": status_message_map.get(new_status, ""),
                    "designer_dashboard_url": f"{settings.DESIGNER_URL}/dashboard",
                    "products_url": f"{settings.DESIGNER_URL}/products/add",
                    "products_count": products_count,
                    "is_approved": new_status == Designer.Status.APPROVED,
                }
                message = render_to_string("emails/designer_account_status_update.html", context)
                resend_sendmail(
                    subject=subject,
                    recipient_list=[designer.user.email],
                    message=message,
                    from_email="hello@accounts.urbanaafrica.com",
                    from_name="Urbana Studio",
                )
            except Exception as e:
                logger.error("Designer status update email failed for %s: %s", designer.user.email, e)

        threading.Thread(target=_send_status_email, daemon=True).start()

    def perform_update(self, serializer):
        """Fire status notifications when PATCH /manage/designers/{id}
        changes status — the admin UI updates via partial_update, not the
        update-status action, so hooks live here to keep emails consistent."""
        previous_status = serializer.instance.status

        # DES-02 — approval through PATCH gets the same readiness gate.
        gate = None
        new_status = serializer.validated_data.get('status')
        if new_status == Designer.Status.APPROVED:
            from .gates import evaluate_designer_readiness
            gate = evaluate_designer_readiness(serializer.instance)
            reasons = serializer.validated_data.get('status_reasons') or []
            if not gate['passed'] and not reasons:
                raise ValidationError({
                    'detail': 'Approval requires all mandatory checks or a '
                              'documented exception via status_reasons.',
                    'checks': gate['checks'],
                })

        designer = serializer.save()
        if designer.status != previous_status:
            if designer.status == Designer.Status.APPROVED and not designer.is_verified:
                designer.is_verified = True
                designer.save(update_fields=["is_verified"])
            record_audit(
                request=self.request, action='designer.status_change',
                entity=designer,
                before={'status': previous_status},
                after={'status': designer.status,
                       'reasons': designer.status_reasons,
                       **({'checks': gate['checks']} if gate else {})},
            )
            self._send_status_notifications(designer, designer.status)

    @action(detail=True, methods=["patch"], url_path="update-status")
    def update_status(self, request, pk=None):
        """
        PATCH /admin/designers/{pk}/update-status
        Triggers an email notification on every status update.
        """
        designer = self.get_object()
        new_status = request.data.get("status")
        status_reasons = request.data.get("status_reasons", [])

        if not new_status:
            return Response(
                {"detail": "Status is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if new_status not in Designer.Status.values:
            return Response(
                {"detail": f"Invalid status. Choose from {Designer.Status.values}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # DES-02 — approval requires readiness evidence or an exception.
        gate = None
        if new_status == Designer.Status.APPROVED:
            from .gates import evaluate_designer_readiness
            gate = evaluate_designer_readiness(designer)
            if not gate['passed'] and not status_reasons:
                return Response(
                    {"detail": "Designer approval requires all mandatory "
                               "checks or a documented exception via "
                               "status_reasons.",
                     "checks": gate['checks']},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        prior_status = designer.status
        designer.status = new_status
        designer.status_reasons = status_reasons

        # If approved, we also mark as verified if not already
        if new_status == Designer.Status.APPROVED:
            designer.is_verified = True

        designer.save()

        record_audit(
            request=request, action='designer.status_change', entity=designer,
            before={'status': prior_status},
            after={'status': new_status, 'reasons': status_reasons,
                   **({'checks': gate['checks']} if gate else {})},
        )

        self._send_status_notifications(designer, new_status)

        serializer = self.get_serializer(designer)
        return Response({
            "status": "success",
            "message": f"Designer status updated to {new_status}",
            "data": serializer.data
        })

    @action(detail=True, methods=["get"], url_path="activation")
    def activation(self, request, pk=None):
        """DES-03 — approved → first listing → first paid order → first
        fulfilled order, each derived from source records."""
        from .designer_health import activation_funnel
        return Response({"status": "success",
                         "data": activation_funnel(self.get_object())})

    @action(detail=True, methods=["get"], url_path="health")
    def health(self, request, pk=None):
        """DES-04 — component health score with staff override support."""
        from .designer_health import health_score
        return Response({"status": "success",
                         "data": health_score(self.get_object())})

    @action(detail=True, methods=["post"], url_path="health-override")
    def health_override(self, request, pk=None):
        """DES-04 — override the computed score; requires reason + expiry."""
        designer = self.get_object()
        score = request.data.get("score")
        reason = (request.data.get("reason") or "").strip()
        expires_at = request.data.get("expires_at")
        if score is None or not reason or not expires_at:
            return Response(
                {"error": "score, reason and expires_at are required — "
                          "health overrides are time-bound (DES-04)."},
                status=status.HTTP_400_BAD_REQUEST)
        prior = designer.health_override
        designer.health_override = {
            "score": score, "reason": reason, "expires_at": expires_at,
            "set_by": request.user.email,
        }
        designer.save(update_fields=["health_override"])
        record_audit(
            request=request, action="designer.health_override",
            entity=designer, before={"override": prior},
            after=designer.health_override, reason=reason)
        return Response({"status": "success",
                         "data": {"health_override":
                                  designer.health_override}})

    @action(detail=True, methods=["post"], url_path="suspend")
    def suspend(self, request, pk=None):
        """DES-05 — suspend with risk category, rationale, notice and
        review date."""
        designer = self.get_object()
        reason = (request.data.get("reason") or "").strip()
        category = (request.data.get("risk_category") or "").strip()
        review_date = request.data.get("review_date")
        notice = (request.data.get("notice") or "").strip()
        if not reason or not category or not review_date:
            return Response(
                {"error": "reason, risk_category and review_date are "
                          "required for suspension (DES-05)."},
                status=status.HTTP_400_BAD_REQUEST)
        prior = {"status": designer.status,
                 "suspension": designer.suspension}
        designer.status = Designer.Status.BLOCKED
        designer.suspension = {
            "reason": reason, "risk_category": category,
            "notice": notice, "review_date": review_date,
            "set_by": request.user.email,
            "set_at": timezone.now().isoformat(),
        }
        designer.save(update_fields=["status", "suspension",
                                     "status_updated_at"])
        record_audit(
            request=request, action="designer.suspend", entity=designer,
            before=prior, after={"status": designer.status,
                                 "suspension": designer.suspension},
            reason=reason)
        return Response({"status": "success",
                         "data": AdminDesignerSerializer(designer).data})

    @action(detail=True, methods=["post"], url_path="reinstate")
    def reinstate(self, request, pk=None):
        """DES-05 — lift a suspension; keeps the suspension record in
        history via the audit trail."""
        designer = self.get_object()
        if designer.status != Designer.Status.BLOCKED:
            return Response(
                {"error": f"Designer is {designer.status}, not suspended."},
                status=status.HTTP_400_BAD_REQUEST)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response({"error": "reason is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        prior = {"status": designer.status,
                 "suspension": designer.suspension}
        designer.status = Designer.Status.APPROVED
        designer.suspension = {}
        designer.save(update_fields=["status", "suspension",
                                     "status_updated_at"])
        record_audit(
            request=request, action="designer.reinstate", entity=designer,
            before=prior, after={"status": designer.status}, reason=reason)
        return Response({"status": "success",
                         "data": AdminDesignerSerializer(designer).data})

class AdminDesignerProductViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = DesignerProduct.objects.select_related("designer", "product")
    serializer_class = AdminDesignerProductSerializer


class AdminCollectionViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = Collection.objects.select_related("designer")
    serializer_class = AdminCollectionSerializer


class AdminSmartCollectionViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = SmartCollection.objects.prefetch_related("products")
    serializer_class = AdminSmartCollectionSerializer
    filterset_fields = ["collection_type", "is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["display_order", "created_at"]
    ordering = ["display_order", "-created_at"]


class AdminDesignerAnalyticsViewSet(AdminBaseViewSet):
    view_capability = 'designers.view'
    manage_capability = 'designers.manage'
    queryset = DesignerAnalytics.objects.select_related("designer")
    serializer_class = AdminDesignerAnalyticsSerializer


class AdminShippingOptionViewSet(AdminBaseViewSet):
    view_capability = 'designers.view'
    manage_capability = 'designers.manage'
    queryset = ShippingOption.objects.select_related("designer")
    serializer_class = AdminShippingOptionSerializer


class AdminDesignerOrderViewSet(AdminBaseViewSet):
    view_capability = 'designers.view'
    manage_capability = 'designers.manage'
    queryset = DesignerOrder.objects.select_related("user", "order_item")
    serializer_class = AdminDesignerOrderSerializer


class AdminShipmentTrackingViewSet(AdminBaseViewSet):
    view_capability = 'designers.view'
    manage_capability = 'designers.manage'
    queryset = ShipmentTracking.objects.select_related("order")
    serializer_class = AdminShipmentTrackingSerializer


class AdminInventoryAlertViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = InventoryAlert.objects.select_related("designer_product")
    serializer_class = AdminInventoryAlertSerializer


class AdminPromotionViewSet(AdminBaseViewSet):
    view_capability = 'catalog.view'
    manage_capability = 'catalog.manage'
    queryset = Promotion.objects.select_related("designer")
    serializer_class = AdminPromotionSerializer


class AdminWithdrawalViewSet(AdminBaseViewSet):
    """Payout processing — finance-only (FIN-03). ``mark_completed`` must
    carry settlement evidence (provider transfer id or manual-proof
    reference); a bare status flip is not a settlement."""
    view_capability = 'finance.view'
    manage_capability = 'finance.approve_payout'
    queryset = Withdrawal.objects.select_related("user")
    serializer_class = AdminWithdrawalSerializer

    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Manual settlement (FIN-03). Requires settlement evidence; above
        ``PAYOUT_DUAL_APPROVAL_THRESHOLD`` a second finance-capable staff
        member must approve the recorded request — the maker can never
        be the checker."""
        from .approvals import payout_dual_threshold, request_approval

        withdrawal = self.get_object()
        reference = (request.data.get('settlement_reference')
                     or request.data.get('reference') or '').strip()
        note = (request.data.get('note') or '').strip()
        if not reference:
            return Response(
                {"status": "error",
                 "message": "settlement_reference is required — manual "
                            "settlement needs provider/manual proof (FIN-03)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if withdrawal.status == 'completed':
            return Response(
                {"status": "error", "message": "Withdrawal already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if withdrawal.amount > payout_dual_threshold():
            approval = request_approval(
                request=request, action='finance.payout_settled',
                entity=withdrawal,
                payload={'settlement_reference': reference, 'note': note,
                         'amount': str(withdrawal.amount)},
                reason=note or 'manual settlement request',
            )
            return Response(
                {"status": "approval_required",
                 "message": "Amount exceeds the dual-approval threshold — "
                            "a second approver must confirm via "
                            "/manage/approvals.",
                 "approval_id": approval.id},
                status=status.HTTP_202_ACCEPTED,
            )

        before = {'status': withdrawal.status,
                  'flutterwave_transfer_id': withdrawal.flutterwave_transfer_id}
        withdrawal.status = "completed"
        withdrawal.processed_at = timezone.now()
        withdrawal.flutterwave_transfer_id = reference
        withdrawal.save()
        record_audit(
            request=request, action='finance.payout_settled',
            entity=withdrawal, before=before,
            after={'status': 'completed', 'settlement_reference': reference,
                   'amount': str(withdrawal.amount)},
            reason=(request.data.get('note') or 'manual settlement'),
        )
        return Response({"status": "success", "message": "Withdrawal marked as completed."})

    @action(detail=True, methods=['post'])
    def process_automated_payout(self, request, pk=None):
        # Placeholder for automated stripe/flutterwave processing
        withdrawal = self.get_object()
        withdrawal.status = "completed"
        withdrawal.processed_at = timezone.now()
        withdrawal.flutterwave_transfer_id = f"auto_{withdrawal.id}"
        withdrawal.save()
        record_audit(
            request=request, action='finance.payout_auto',
            entity=withdrawal,
            after={'status': 'completed',
                   'transfer_id': withdrawal.flutterwave_transfer_id,
                   'amount': str(withdrawal.amount)},
        )
        return Response({"status": "success", "message": "Automated payout triggered and completed successfully."})



class AdminUploadProductMediaView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            product = Product.objects.get(
                id=request.data["product_id"],
                user=request.user
            )

            images = request.FILES.getlist("media[]")
            new_assets = []

            for img in images:
                asset = MediaAsset.objects.create(
                    file=img,
                    media_type=MediaAsset.MediaType.IMAGE,
                )
                product.media.add(asset)
                new_assets.append(asset)

            return Response(
                {
                    "status": "success",
                    "message": "Product uploaded.",
                    "data": MediaAssetSerializer(new_assets, many=True).data
                },
                status=status.HTTP_201_CREATED
            )

        except Exception:
            return Response(
                {"status": "error", "message": "Invalid data."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# =====================================================
# SUPPORT TICKETS
# =====================================================
from apps.core.serializers import SupportTicketSerializer, TicketMessageSerializer


class AdminTicketViewSet(AdminBaseViewSet):
    view_capability = 'support.view'
    manage_capability = 'support.manage'
    queryset = SupportTicket.objects.select_related("user").prefetch_related("messages")
    serializer_class = SupportTicketSerializer
    filterset_fields = ["status", "category", "priority"]
    search_fields = ["subject", "description", "reference"]
    ordering_fields = ["created_at", "updated_at", "priority"]
    ordering = ["-created_at"]

    @action(detail=True, methods=["post"], url_path="reply")
    def reply(self, request, pk=None):
        ticket = self.get_object()
        body = request.data.get("body", "").strip()
        if not body:
            return Response(
                {"status": "error", "message": "Reply body is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        msg = TicketMessage.objects.create(
            ticket=ticket,
            sender=request.user,
            body=body,
            is_internal=request.data.get("is_internal", False),
        )

        record_audit(
            request=request, action='support.ticket_reply', entity=ticket,
            after={'message_id': str(msg.pk),
                   'internal': bool(msg.is_internal)},
        )

        # Update ticket status if it was open
        if ticket.status == SupportTicket.Status.OPEN:
            ticket.status = SupportTicket.Status.IN_PROGRESS
            ticket.save(update_fields=["status"])

        # Email ticket owner about new reply
        if ticket.user and ticket.user.email:
            try:
                context = {
                    "user_name": ticket.user.first_name or ticket.user.email,
                    "ticket_reference": ticket.reference,
                    "sender_name": request.user.get_full_name() or request.user.email,
                    "reply_body": msg.body,
                    "ticket_url": f"{settings.DESIGNER_URL}/help",
                }
                message = render_to_string("emails/support_ticket_reply.html", context)
                threading.Thread(
                    target=resend_sendmail,
                    args=(
                        f"Urbana — New Reply on Ticket {ticket.reference}",
                        [ticket.user.email],
                        message,
                    ),
                    kwargs={"from_email": "support@accounts.urbanaafrica.com", "from_name": "Urbana Africa Support"},
                ).start()
            except Exception as e:
                logger.error("Error sending ticket reply email: %s", e)

        return Response(
            {
                "status": "success",
                "message": "Reply sent.",
                "data": TicketMessageSerializer(msg).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="status")
    def update_status(self, request, pk=None):
        ticket = self.get_object()
        new_status = request.data.get("status")
        valid_statuses = [c[0] for c in SupportTicket.Status.choices]
        if new_status not in valid_statuses:
            return Response(
                {"status": "error", "message": f"Invalid status. Choose from {valid_statuses}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from .cases import apply_ticket_transition
        reason = (request.data.get('reason') or '').strip()
        evidence = (request.data.get('evidence') or '').strip()
        prior_status = ticket.status
        err = apply_ticket_transition(
            ticket, new_status, reason=reason, evidence=evidence,
        )
        if err:
            record_audit(
                request=request, action='support.ticket_status.denied',
                entity=ticket, before={'status': prior_status},
                after={'attempted': new_status}, reason=err['message'],
            )
            return Response(err, status=status.HTTP_400_BAD_REQUEST)
        record_audit(
            request=request, action='support.ticket_status', entity=ticket,
            before={'status': prior_status},
            after={'status': new_status, 'evidence': evidence},
            reason=reason,
        )
        return Response(
            {"status": "success", "message": "Status updated.", "data": SupportTicketSerializer(ticket).data},
            status=status.HTTP_200_OK,
        )


class AdminGlobalSearchView(APIView):
    """Global search across all admin-managed entities."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        q = request.GET.get("q", "").strip()
        if not q:
            return Response({"status": "success", "query": q, "results": []})

        results = []
        caps = lambda c: has_capability(request.user, c)

        # Designers
        designers = (
            Designer.objects.filter(
                Q(brand_name__icontains=q) | Q(country__icontains=q) | Q(user__email__icontains=q) | Q(user__username__icontains=q)
            )[:5]
            if caps('designers.view') else []
        )
        for d in designers:
            results.append({
                "type": "designer",
                "id": d.id,
                "title": d.brand_name or "Designer",
                "subtitle": d.country or "",
                "status": "Verified" if d.is_verified else "Pending",
                "detail_url": f"/designers/{d.id}",
            })

        # Products
        products = (
            Product.objects.filter(
                Q(name__icontains=q) | Q(sku__icontains=q)
            )[:5]
            if caps('catalog.view') else []
        )
        for p in products:
            results.append({
                "type": "product",
                "id": p.id,
                "title": p.name,
                "subtitle": f"ID: {p.sku or '-'}",
                "status": "Published" if p.is_published else "Draft",
                "detail_url": f"/products/edit/{p.id}",
            })

        # Orders (OrderItem)
        order_items = (
            OrderItem.objects.filter(
                Q(order__order_id__icontains=q) | Q(order__status__icontains=q) | Q(product__name__icontains=q)
            ).select_related("order", "product")[:5]
            if caps('orders.view') else []
        )
        for item in order_items:
            results.append({
                "type": "order",
                "id": item.id,
                "title": f"Order #{item.order.order_id}",
                "subtitle": item.product.name,
                "status": item.status,
                "detail_url": f"/orders/{item.id}",
            })

        # Customers
        customers = (
            Customer.objects.select_related("user").filter(
                Q(user__email__icontains=q) | Q(user__username__icontains=q) | Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q) | Q(phone__icontains=q)
            )[:5]
            if caps('customers.view') else []
        )
        for c in customers:
            results.append({
                "type": "customer",
                "id": c.id,
                "title": c.user.get_full_name() or c.user.username,
                "subtitle": c.user.email,
                "status": "Active",
                "detail_url": "/customers",
            })

        # Returns
        returns = (
            ReturnRequest.objects.filter(
                Q(return_id__icontains=q) | Q(status__icontains=q)
            ).select_related("order_item__order")[:5]
            if caps('support.view') else []
        )
        for r in returns:
            results.append({
                "type": "return",
                "id": r.return_id,
                "title": f"Return #{r.return_id}",
                "subtitle": f"Order #{r.order_item.order.order_id}",
                "status": r.status,
                "detail_url": f"/returns/{r.order_item.id}",
            })

        # Disputes
        disputes = (
            Dispute.objects.filter(
                Q(id__icontains=q) | Q(return_request__return_id__icontains=q)
            ).select_related("return_request")[:5]
            if caps('support.view') else []
        )
        for d in disputes:
            results.append({
                "type": "dispute",
                "id": d.id,
                "title": f"Dispute #{d.id}",
                "subtitle": f"Return #{d.return_request.return_id}",
                "status": d.return_request.status,
                "detail_url": "/disputes",
            })

        # Tickets
        tickets = (
            SupportTicket.objects.filter(
                Q(subject__icontains=q) | Q(reference__icontains=q) | Q(description__icontains=q)
            )[:5]
            if caps('support.view') else []
        )
        for t in tickets:
            results.append({
                "type": "ticket",
                "id": t.id,
                "title": t.subject,
                "subtitle": f"Ref: {t.reference}",
                "status": t.status,
                "detail_url": f"/tickets/{t.id}",
            })

        # Smart Collections
        collections = (
            SmartCollection.objects.filter(
                Q(title__icontains=q) | Q(description__icontains=q)
            )[:5]
            if caps('catalog.view') else []
        )
        for c in collections:
            results.append({
                "type": "collection",
                "id": c.id,
                "title": c.title,
                "subtitle": c.collection_type,
                "status": "Active" if c.is_active else "Inactive",
                "detail_url": "/smart-collections",
            })

        return Response({"status": "success", "query": q, "results": results})


class CLevelDashboardAnalyticsView(APIView):
    """
    High-level overview for C-Suite executives.
    Provides aggregated GMV, Growth Metrics, User Growth, and Designer Analytics.
    """
    permission_classes = [IsCLevel]

    def get(self, request):
        from django.db.models import Sum, Count, F, Q
        from django.db.models.functions import TruncDate, TruncMonth, TruncWeek
        from django.utils import timezone
        import datetime

        now = timezone.now()
        thirty_days_ago = now - datetime.timedelta(days=30)
        six_months_ago = now - datetime.timedelta(days=180)

        # Canonical money truth (PRD §8): only provider-confirmed payments —
        # invoice.payment.is_paid, not soft-deleted. Order/item status labels
        # are operational, never financial truth.
        PAID = {
            'order__invoice__payment__is_paid': True,
            'order__invoice__payment__is_deleted': False,
        }

        # ---------------------------------------------------------
        # 1. Financial Analytics (Revenue & Expenses)
        # ---------------------------------------------------------

        # GMV = merchandise value of PAID items at checkout (excludes
        # shipping). OrderItem.sub_total = amount * quantity.
        total_gmv = OrderItem.objects.filter(
            **PAID,
        ).aggregate(total=Sum('sub_total'))['total'] or 0

        # Payouts = designer share of escrow actually RELEASED to wallets.
        # Held escrow is a liability, not an expense — reported separately.
        total_payouts = Escrow.objects.filter(status='released').aggregate(
            payouts=Sum(F('amount') - F('platform_commission'))
        )['payouts'] or 0
        held_escrow = Escrow.objects.filter(status='held').aggregate(
            total=Sum('amount'))['total'] or 0
        commission_recognized = Escrow.objects.filter(status='released').aggregate(
            total=Sum('platform_commission'))['total'] or 0

        # Time-Series Revenue vs Expenses (Daily for the last 30 days)
        daily_financials_qs = Escrow.objects.filter(created_at__gte=thirty_days_ago).annotate(
            date=TruncDate('created_at')
        ).values('date').annotate(
            revenue=Sum('amount'),
            expenses=Sum(F('amount') - F('platform_commission'))
        ).order_by('date')

        daily_financials = [
            {
                "date": entry['date'].strftime('%Y-%m-%d'),
                "revenue": float(entry['revenue'] or 0),
                "expenses": float(entry['expenses'] or 0)
            }
            for entry in daily_financials_qs
        ]

        # Monthly Financials (Last 6 months)
        monthly_financials_qs = Escrow.objects.filter(created_at__gte=six_months_ago).annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            revenue=Sum('amount'),
            expenses=Sum(F('amount') - F('platform_commission'))
        ).order_by('month')

        monthly_financials = [
            {
                "date": entry['month'].strftime('%b %Y'),
                "revenue": float(entry['revenue'] or 0),
                "expenses": float(entry['expenses'] or 0)
            }
            for entry in monthly_financials_qs
        ]

        # ---------------------------------------------------------
        # 2. Growth & Logistics Analytics
        # ---------------------------------------------------------
        
        total_users = Customer.objects.count()
        new_users_last_month = Customer.objects.filter(user__date_joined__gte=thirty_days_ago).count()

        total_designers = Designer.objects.count()
        active_designers = Designer.objects.filter(status='approved').count()

        # Monthly User Growth
        monthly_users_qs = Customer.objects.filter(user__date_joined__gte=six_months_ago).annotate(
            month=TruncMonth('user__date_joined')
        ).values('month').annotate(count=Count('id')).order_by('month')

        monthly_user_growth = [
            {
                "date": entry['month'].strftime('%b %Y'),
                "customers": entry['count']
            }
            for entry in monthly_users_qs
        ]

        # Monthly Designer Growth
        monthly_designers_qs = Designer.objects.filter(user__date_joined__gte=six_months_ago).annotate(
            month=TruncMonth('user__date_joined')
        ).values('month').annotate(count=Count('id')).order_by('month')

        # Merge them
        growth_dict = {item['date']: {"date": item['date'], "customers": item['customers'], "designers": 0} for item in monthly_user_growth}
        for entry in monthly_designers_qs:
            month_str = entry['month'].strftime('%b %Y')
            if month_str not in growth_dict:
                growth_dict[month_str] = {"date": month_str, "customers": 0, "designers": 0}
            growth_dict[month_str]["designers"] = entry['count']
        
        monthly_growth = sorted(list(growth_dict.values()), key=lambda x: datetime.datetime.strptime(x['date'], '%b %Y'))

        # Shipping Costs (Revenue from shipping — paid orders only)
        total_shipping_revenue = Order.objects.filter(
            invoice__payment__is_paid=True,
            invoice__payment__is_deleted=False,
        ).aggregate(total=Sum('shipping_amount'))['total'] or 0

        # Top Products — paid order items only
        top_products_qs = OrderItem.objects.filter(
            **PAID,
        ).values('product__name').annotate(
            units_sold=Sum('quantity'),
            revenue=Sum('sub_total')
        ).order_by('-units_sold')[:5]

        top_products = [
            {
                "name": entry['product__name'] or "Unknown",
                "units": entry['units_sold'] or 0,
                "revenue": float(entry['revenue'] or 0)
            }
            for entry in top_products_qs
        ]

        # Latest reconciliation status — freshness/trust metadata for execs.
        last_recon = ReconciliationRun.objects.order_by('-started_at').first()

        data = {
            "financials": {
                "total_gmv": float(total_gmv),
                "total_payouts": float(total_payouts),
                "held_escrow": float(held_escrow),
                "commission_recognized": float(commission_recognized),
                "total_shipping": float(total_shipping_revenue),
                "daily_series": daily_financials,
                "monthly_series": monthly_financials,
            },
            "growth": {
                "total_users": total_users,
                "new_users_last_month": new_users_last_month,
                "monthly_series": monthly_growth,
            },
            "designers": {
                "total_designers": total_designers,
                "active_designers": active_designers,
            },
            "products": {
                "top_products": top_products
            },
            "meta": {
                "generated_at": now.isoformat(),
                "currency": "mixed",
                "currency_note": (
                    "Amounts are aggregated across transaction currencies; "
                    "per-currency separation lands with FIN-05."
                ),
                "reconciliation": {
                    "last_run_status": getattr(last_recon, 'status', None),
                    "last_run_at": (
                        last_recon.started_at.isoformat() if last_recon else None
                    ),
                    "open_exceptions": (
                        ReconciliationException.objects
                        .filter(status='open').count()
                    ),
                },
                "definitions": {
                    "total_gmv": "Merchandise value (sub_total) of paid order items — provider-confirmed payments only.",
                    "total_payouts": "Designer share (amount - commission) of released escrow.",
                    "held_escrow": "Escrow still held — liability, not expense.",
                    "commission_recognized": "Platform commission on released escrow.",
                    "total_shipping": "Shipping amounts on paid orders.",
                    "total_users": "Customer accounts (all time).",
                    "active_designers": "Designers with status 'approved'.",
                },
                "sources": {
                    "payments": "pay.Payment (is_paid, not deleted)",
                    "orders": "customers.Order via invoice.payment",
                    "escrow": "pay.Escrow",
                    "growth": "customers.Customer / designers.Designer signups",
                },
            },
        }

        return Response(data, status=status.HTTP_200_OK)


# =====================================================
# NEWSLETTER MANAGEMENT
# =====================================================
from .permissions import IsMarketer
from apps.newsletter.models import Newsletter, NewsletterSubscriber

class AdminNewsletterSubscriberViewSet(viewsets.ModelViewSet):
    queryset = NewsletterSubscriber.objects.all()
    serializer_class = AdminNewsletterSubscriberSerializer
    permission_classes = [IsMarketer, HasCapability]
    view_capability = 'marketing.view'
    manage_capability = 'marketing.configure'
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["is_active"]
    search_fields = ["email", "full_name"]
    ordering_fields = ["subscribed_at"]
    ordering = ["-subscribed_at"]


class AdminNewsletterViewSet(viewsets.ModelViewSet):
    queryset = Newsletter.objects.all()
    serializer_class = AdminNewsletterSerializer
    permission_classes = [IsMarketer, HasCapability]
    view_capability = 'marketing.view'
    manage_capability = 'marketing.configure'
    action_capabilities = {'send_newsletter': 'marketing.send'}
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["is_draft"]
    search_fields = ["title", "subject", "content"]
    ordering_fields = ["created_at", "sent_at"]
    ordering = ["-created_at"]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="send")
    def send_newsletter(self, request, pk=None):
        newsletter = self.get_object()

        if not newsletter.is_draft:
            return Response(
                {"detail": "This newsletter has already been sent."},
                status=status.HTTP_400_BAD_REQUEST
            )

        active_subscribers = NewsletterSubscriber.objects.filter(is_active=True)
        if not active_subscribers.exists():
            return Response(
                {"detail": "No active subscribers found."},
                status=status.HTTP_400_BAD_REQUEST
            )

        def _send_emails():
            try:
                for subscriber in active_subscribers:
                    context = {
                        "subject": newsletter.subject,
                        "content": newsletter.content,
                        "subscriber_name": subscriber.full_name,
                        "site_url": settings.STORE_URL,
                    }
                    message = render_to_string("emails/newsletter.html", context)
                    
                    resend_sendmail(
                        subject=newsletter.subject,
                        recipient_list=[subscriber.email],
                        message=message,
                        from_email="marketing@accounts.urbanaafrica.com",
                        from_name="Urbana Africa",
                    )
            except Exception as e:
                logger.error("[Newsletter Error] Failed to send newsletter %s: %s", newsletter.title, e)

        # Send asynchronously via threading
        threading.Thread(target=_send_emails, daemon=True).start()

        # Update newsletter status
        newsletter.mark_as_sent()

        return Response({
            "status": "success",
            "message": f"Newsletter is being dispatched to {active_subscribers.count()} subscribers."
        }, status=status.HTTP_200_OK)

# =====================================================
# GOVERNANCE — audit events & data health (Phase 0)
# =====================================================

class AuditEventViewSet(viewsets.ReadOnlyModelViewSet):
    """Append-only audit trail — read/search only, no mutation endpoints."""
    queryset = AuditEvent.objects.all()
    serializer_class = AuditEventSerializer
    permission_classes = [HasCapability]
    required_capability = 'audit.view'
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['action', 'entity_type', 'entity_id', 'actor_email', 'actor_role']
    search_fields = ['actor_email', 'entity_id', 'action', 'reason']
    ordering_fields = ['created_at']
    ordering = ['-created_at']


class DataQualityCheckViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = DataQualityCheck.objects.all()
    serializer_class = DataQualityCheckSerializer
    permission_classes = [HasCapability]
    required_capability = 'health.view'
    action_capabilities = {'run': 'health.manage'}
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['check_name', 'status']
    ordering_fields = ['checked_at']
    ordering = ['-checked_at']

    @action(detail=False, methods=['post'])
    def run(self, request):
        """Manually trigger the health sweep (ops/superadmin only)."""
        from .checks import run_all_checks
        days = int(request.data.get('days') or 30)
        summary = run_all_checks(days=min(max(days, 1), 90), actor=request.user)
        record_audit(
            request=request, action='health.run',
            entity_type='DataQualitySweep', entity_id='',
            after=summary,
        )
        return Response(summary, status=status.HTTP_202_ACCEPTED)


class ReconciliationRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ReconciliationRun.objects.all()
    serializer_class = ReconciliationRunSerializer
    permission_classes = [HasCapability]
    required_capability = 'finance.view'
    action_capabilities = {'run': 'finance.reconcile'}
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['name', 'status']
    ordering_fields = ['started_at']
    ordering = ['-started_at']

    @action(detail=False, methods=['post'])
    def run(self, request):
        from .checks import reconcile_payments_vs_orders
        days = int(request.data.get('days') or 30)
        run = reconcile_payments_vs_orders(days=min(max(days, 1), 90),
                                           actor=request.user)
        record_audit(
            request=request, action='reconciliation.run',
            entity=run, after={'status': run.status,
                               'exceptions': run.exception_count},
        )
        return Response(ReconciliationRunSerializer(run).data,
                        status=status.HTTP_202_ACCEPTED)


class ReconciliationExceptionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ReconciliationException.objects.select_related(
        'run', 'resolved_by',
    )
    serializer_class = ReconciliationExceptionSerializer
    permission_classes = [HasCapability]
    required_capability = 'finance.view'
    action_capabilities = {'resolve': 'finance.reconcile'}
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['status', 'issue', 'entity_type', 'run']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        exc = self.get_object()
        note = (request.data.get('note') or '').strip()
        if exc.status == 'resolved':
            return Response({'error': 'Exception already resolved'},
                            status=status.HTTP_400_BAD_REQUEST)
        exc.status = 'resolved'
        exc.resolved_by = request.user
        exc.resolved_at = timezone.now()
        exc.resolution_note = note
        exc.save(update_fields=['status', 'resolved_by', 'resolved_at',
                                'resolution_note'])
        record_audit(
            request=request, action='reconciliation.resolve',
            entity=exc, reason=note,
            after={'issue': exc.issue, 'entity': f'{exc.entity_type}:{exc.entity_id}'},
        )
        return Response(ReconciliationExceptionSerializer(exc).data)


# =====================================================
# WORK QUEUES (Phase 1 — operate safely)
# =====================================================

class WorkItemViewSet(viewsets.ReadOnlyModelViewSet):
    """Unified staff work queue. Items are derived from source records by
    the periodic ``sync_work_queues`` sweep (or the manual ``sync`` action);
    humans assign/start/resolve/close — never edit source links."""
    queryset = WorkItem.objects.select_related('assigned_to', 'resolved_by')
    serializer_class = WorkItemSerializer
    permission_classes = [HasCapability]
    required_capability = 'work.view'
    action_capabilities = {
        'assign': 'work.manage', 'start': 'work.manage',
        'resolve': 'work.manage', 'close': 'work.manage',
        'reopen': 'work.manage', 'escalate': 'work.manage',
        'sync': 'work.manage', 'priority': 'work.manage',
    }
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['queue', 'status', 'priority', 'assigned_to', 'escalated']
    search_fields = ['title', 'entity_id']
    ordering_fields = ['due_at', 'created_at', 'priority']
    ordering = ['status', 'due_at', '-created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('overdue', '').lower() in ('true', '1', 'yes'):
            qs = qs.filter(due_at__lt=timezone.now(),
                           status__in=('open', 'in_progress'))
        if p.get('mine', '').lower() in ('true', '1', 'yes'):
            qs = qs.filter(assigned_to=self.request.user)
        if p.get('open', '').lower() in ('true', '1', 'yes'):
            qs = qs.filter(status__in=('open', 'in_progress'))
        return qs

    @action(detail=False, methods=['get'])
    def summary(self, request):
        rows = WorkItem.objects.filter(status__in=('open', 'in_progress'))
        by_queue = {
            r['queue']: r['count']
            for r in rows.values('queue').annotate(count=Count('id'))
        }
        return Response({
            'by_queue': by_queue,
            'overdue': rows.filter(due_at__lt=timezone.now()).count(),
            'unassigned': rows.filter(assigned_to__isnull=True).count(),
            'mine': rows.filter(assigned_to=request.user).count(),
            'total_open': rows.count(),
        })

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        item = self.get_object()
        user_id = request.data.get('user_id')
        if user_id in (None, ''):
            assignee = request.user  # claim for self
        else:
            assignee = get_user_model().objects.filter(id=user_id).first()
            if not assignee or getattr(assignee, 'user_type', '') != 'admin':
                return Response({'error': 'Assignee must be a staff user'},
                                status=status.HTTP_400_BAD_REQUEST)
        before = {'assigned_to': getattr(item.assigned_to, 'email', None)}
        item.assigned_to = assignee
        if item.status == 'open':
            item.status = 'in_progress'
        item.save(update_fields=['assigned_to', 'status', 'updated_at'])
        record_audit(
            request=request, action='work.assign', entity=item, before=before,
            after={'assigned_to': assignee.email, 'status': item.status},
        )
        return Response(WorkItemSerializer(item).data)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        item = self.get_object()
        if item.status != 'open':
            return Response({'error': f'Only open items can start ({item.status})'},
                            status=status.HTTP_400_BAD_REQUEST)
        item.status = 'in_progress'
        if not item.assigned_to:
            item.assigned_to = request.user
        item.save(update_fields=['status', 'assigned_to', 'updated_at'])
        record_audit(request=request, action='work.start', entity=item)
        return Response(WorkItemSerializer(item).data)

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        item = self.get_object()
        note = (request.data.get('note') or '').strip()
        if item.status in ('resolved', 'closed'):
            return Response({'error': f'Item already {item.status}'},
                            status=status.HTTP_400_BAD_REQUEST)
        item.status = 'resolved'
        item.resolved_by = request.user
        item.resolved_at = timezone.now()
        item.resolution_note = note
        item.save(update_fields=['status', 'resolved_by', 'resolved_at',
                                 'resolution_note', 'updated_at'])
        record_audit(request=request, action='work.resolve', entity=item,
                     reason=note)
        return Response(WorkItemSerializer(item).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        item = self.get_object()
        note = (request.data.get('note') or '').strip()
        if item.status == 'closed':
            return Response({'error': 'Item already closed'},
                            status=status.HTTP_400_BAD_REQUEST)
        item.status = 'closed'
        item.resolved_by = request.user
        item.resolved_at = timezone.now()
        item.resolution_note = note
        item.save(update_fields=['status', 'resolved_by', 'resolved_at',
                                 'resolution_note', 'updated_at'])
        record_audit(request=request, action='work.close', entity=item,
                     reason=note)
        return Response(WorkItemSerializer(item).data)

    @action(detail=True, methods=['post'])
    def reopen(self, request, pk=None):
        item = self.get_object()
        if item.status not in ('resolved', 'closed'):
            return Response({'error': f'Item is not resolved ({item.status})'},
                            status=status.HTTP_400_BAD_REQUEST)
        item.status = 'open'
        item.resolved_by = None
        item.resolved_at = None
        item.resolution_note = ''
        item.save(update_fields=['status', 'resolved_by', 'resolved_at',
                                 'resolution_note', 'updated_at'])
        record_audit(request=request, action='work.reopen', entity=item,
                     reason=(request.data.get('note') or ''))
        return Response(WorkItemSerializer(item).data)

    @action(detail=True, methods=['post'])
    def escalate(self, request, pk=None):
        item = self.get_object()
        item.escalated = True
        if item.priority != 'urgent':
            item.priority = 'urgent'
        item.save(update_fields=['escalated', 'priority', 'updated_at'])
        record_audit(request=request, action='work.escalate', entity=item,
                     reason=(request.data.get('reason') or ''))
        return Response(WorkItemSerializer(item).data)

    @action(detail=True, methods=['post'])
    def priority(self, request, pk=None):
        item = self.get_object()
        new = request.data.get('priority')
        if new not in dict(WorkItem.PRIORITY_CHOICES):
            return Response({'error': 'Invalid priority'},
                            status=status.HTTP_400_BAD_REQUEST)
        before = {'priority': item.priority}
        item.priority = new
        item.save(update_fields=['priority', 'updated_at'])
        record_audit(request=request, action='work.priority', entity=item,
                     before=before, after={'priority': new})
        return Response(WorkItemSerializer(item).data)

    @action(detail=False, methods=['post'])
    def sync(self, request):
        """Manually re-derive work items from sources."""
        from .queues import sync_work_queues
        stats = sync_work_queues()
        record_audit(request=request, action='work.sync',
                     entity_type='WorkItem', after=stats)
        return Response(stats, status=status.HTTP_202_ACCEPTED)


class ApprovalRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """Maker-checker approvals (PRD §5). The initiator can never approve
    their own request; approval executes the recorded action and every
    step writes an immutable audit event."""
    queryset = ApprovalRequest.objects.select_related(
        'requested_by', 'decided_by'
    )
    serializer_class = ApprovalRequestSerializer
    permission_classes = [HasCapability]
    required_capability = 'finance.view'
    action_capabilities = {
        'approve': 'finance.approve_payout', 'reject': 'finance.approve_payout',
    }
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'action', 'entity_type']
    search_fields = ['entity_id', 'reason']
    ordering = ['-created_at']

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        from .approvals import execute_approval

        approval = self.get_object()
        if approval.status == 'executed':
            return Response({'error': 'Request already executed'},
                            status=status.HTTP_400_BAD_REQUEST)
        if approval.status not in ('pending', 'approved'):
            return Response(
                {'error': f'Cannot approve a {approval.status} request'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (approval.requested_by_id
                and approval.requested_by_id == request.user.id):
            record_audit(
                request=request, action='permission.denied',
                entity=approval,
                reason='initiator cannot approve own request',
            )
            return Response(
                {'error': 'The requester cannot approve their own request '
                          '(maker-checker).'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # The approver must hold the capability for the request's domain —
        # finance.* needs finance.approve_payout, access.* needs
        # users.grant_role, settings.* needs settings.publish.
        _domain_cap = {
            'finance.': 'finance.approve_payout',
            'access.': 'users.grant_role',
            'settings.': 'settings.publish',
        }
        domain_cap = next(
            (cap for prefix, cap in _domain_cap.items()
             if approval.action.startswith(prefix)), None)
        if domain_cap and not has_capability(request.user, domain_cap):
            record_audit(
                request=request, action='permission.denied',
                entity=approval,
                reason=f'approve requires {domain_cap}')
            return Response(
                {'error': f'Approving this request requires '
                          f'{domain_cap}.'},
                status=status.HTTP_403_FORBIDDEN)

        if approval.status == 'pending':
            approval.status = 'approved'
            approval.decided_by = request.user
            approval.decided_at = timezone.now()
            approval.save(update_fields=['status', 'decided_by', 'decided_at'])
            record_audit(
                request=request, action='approval.approved', entity=approval,
                after={'for': approval.action,
                       'entity_id': approval.entity_id},
                reason=(request.data.get('note') or ''),
            )

        try:
            result = execute_approval(approval, request)
        except Exception as exc:
            logger.exception('Approval execution failed for %s', approval.id)
            return Response(
                {'error': f'Approved but execution failed: {exc}',
                 'approval_status': approval.status},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({
            'status': 'executed', 'approval_id': approval.id,
            'result': result,
        })

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        approval = self.get_object()
        if approval.status not in ('pending', 'approved'):
            return Response(
                {'error': f'Cannot reject a {approval.status} request'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (approval.requested_by_id
                and approval.requested_by_id == request.user.id):
            return Response(
                {'error': 'The requester cannot reject their own request.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        approval.status = 'rejected'
        approval.decided_by = request.user
        approval.decided_at = timezone.now()
        approval.save(update_fields=['status', 'decided_by', 'decided_at'])
        record_audit(
            request=request, action='approval.rejected', entity=approval,
            reason=(request.data.get('reason') or ''),
        )
        return Response(ApprovalRequestSerializer(approval).data)


# =====================================================
# SUP-01 — CANONICAL CASE INBOX
# =====================================================

class AdminCaseViewSet(AdminBaseViewSet):
    """Single case inbox (SUP-01): one canonical case ID + one owner,
    linked to the ticket/return/dispute/order/designer source records.
    Status follows the SUP-02 lifecycle via ``cases.TICKET_TRANSITIONS``.
    """
    view_capability = 'support.view'
    manage_capability = 'support.manage'
    queryset = Case.objects.select_related(
        'owner', 'order', 'designer', 'ticket', 'return_request', 'dispute')
    serializer_class = CaseSerializer
    filterset_fields = ['status', 'category', 'priority', 'owner']
    search_fields = ['case_ref', 'subject']
    ordering = ['-created_at']

    def partial_update(self, request, *args, **kwargs):
        # Status and ownership move only through the governed actions —
        # a plain PATCH would bypass the SUP-02 transition record.
        if 'status' in request.data or 'owner' in request.data:
            return Response(
                {'error': 'Use the /status or /assign actions — direct '
                          'status/owner writes bypass the audit record.'},
                status=status.HTTP_400_BAD_REQUEST)
        return super().partial_update(request, *args, **kwargs)

    _SOURCE_FIELDS = {
        'ticket': 'ticket', 'SupportTicket': 'ticket',
        'return': 'return_request', 'ReturnRequest': 'return_request',
        'dispute': 'dispute', 'Dispute': 'dispute',
        'order': 'order', 'Order': 'order',
        'designer': 'designer',
    }

    @action(detail=True, methods=['post'], url_path='status')
    def update_status(self, request, pk=None):
        from .cases import apply_ticket_transition
        case = self.get_object()
        new_status = request.data.get('status')
        reason = (request.data.get('reason') or '').strip()
        evidence = (request.data.get('evidence') or '').strip()
        prior = case.status
        err = apply_ticket_transition(
            case, new_status, reason=reason, evidence=evidence)
        if err:
            record_audit(
                request=request, action='support.case_status.denied',
                entity=case, before={'status': prior},
                after={'attempted': new_status}, reason=err['message'])
            return Response(err, status=status.HTTP_400_BAD_REQUEST)
        if new_status == 'resolved':
            case.resolved_at = timezone.now()
            case.resolution_note = reason
            case.save(update_fields=['resolved_at', 'resolution_note'])
        record_audit(
            request=request, action='support.case_status', entity=case,
            before={'status': prior},
            after={'status': new_status, 'evidence': evidence},
            reason=reason)
        return Response({'status': 'success',
                         'data': CaseSerializer(case).data})

    @action(detail=True, methods=['post'], url_path='assign')
    def assign(self, request, pk=None):
        case = self.get_object()
        owner_id = request.data.get('owner')
        owner = get_user_model().objects.filter(pk=owner_id).first()
        if owner_id and not owner:
            return Response({'error': 'Owner not found.'},
                            status=status.HTTP_404_NOT_FOUND)
        prior = case.owner_id
        case.owner = owner
        case.save(update_fields=['owner', 'updated_at'])
        record_audit(
            request=request, action='support.case_assign', entity=case,
            before={'owner': str(prior or '')},
            after={'owner': str(owner_id or '')},
            reason=request.data.get('reason', ''))
        return Response({'status': 'success',
                         'data': CaseSerializer(case).data})

    @action(detail=True, methods=['post'], url_path='link')
    def link(self, request, pk=None):
        """Attach an additional source record — tickets, returns,
        disputes, orders, designers — to the canonical case."""
        case = self.get_object()
        etype = request.data.get('entity_type')
        eid = request.data.get('entity_id')
        field = self._SOURCE_FIELDS.get(etype)
        if not field or not eid:
            return Response(
                {'error': f"entity_type must be one of "
                          f"{sorted(self._SOURCE_FIELDS)} with entity_id."},
                status=status.HTTP_400_BAD_REQUEST)
        prior = list(case.related_links)
        if field in ('ticket', 'return_request', 'dispute', 'order',
                     'designer') and not getattr(case, f'{field}_id'):
            setattr(case, field + '_id', eid)
            case.save(update_fields=[field, 'updated_at'])
        else:
            case.related_links = prior + [{'type': etype, 'id': str(eid)}]
            case.save(update_fields=['related_links', 'updated_at'])
        record_audit(
            request=request, action='support.case_link', entity=case,
            after={'linked': {'type': etype, 'id': str(eid)}})
        return Response({'status': 'success',
                         'data': CaseSerializer(case).data})


# =====================================================
# GOV-02 — POLICY REGISTRY
# =====================================================

class AdminPolicyVersionViewSet(AdminBaseViewSet):
    """Policy/config registry (GOV-02). Draft → publish → retire, with
    version diffs and rollback. Publishing a high-impact policy requires
    a different approver than the author (maker-checker)."""
    view_capability = 'audit.view'
    manage_capability = 'settings.publish'
    queryset = PolicyVersion.objects.select_related(
        'created_by', 'approved_by')
    serializer_class = PolicyVersionSerializer
    filterset_fields = ['key', 'status']
    search_fields = ['key']
    ordering = ['key', '-version']

    def perform_create(self, serializer):
        key = serializer.validated_data['key']
        latest = (PolicyVersion.objects.filter(key=key)
                  .order_by('-version').first())
        serializer.save(
            version=(latest.version + 1) if latest else 1,
            created_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='publish')
    def publish(self, request, pk=None):
        policy = self.get_object()
        if policy.status != 'draft':
            return Response(
                {'error': f'Only draft policies can be published '
                          f'({policy.status}).'},
                status=status.HTTP_400_BAD_REQUEST)
        # Maker-checker: high-impact changes need a different approver.
        if (policy.impact == 'high'
                and policy.created_by_id == request.user.id):
            record_audit(
                request=request, action='permission.denied', entity=policy,
                reason='author cannot publish own high-impact policy')
            return Response(
                {'error': 'High-impact policies must be published by a '
                          'different approver (maker-checker).'},
                status=status.HTTP_403_FORBIDDEN)
        prior = (PolicyVersion.objects
                 .filter(key=policy.key, status='published')
                 .exclude(pk=policy.pk).first())
        policy.status = 'published'
        policy.approved_by = request.user
        policy.effective_at = timezone.now()
        policy.save(update_fields=['status', 'approved_by', 'effective_at'])
        if prior:
            prior.status = 'retired'
            prior.save(update_fields=['status'])
        record_audit(
            request=request, action='settings.policy_publish',
            entity=policy,
            before={'status': 'draft',
                    'previous_value': prior.value if prior else None},
            after={'status': 'published', 'value': policy.value},
            reason=policy.reason)
        return Response({'status': 'success',
                         'data': PolicyVersionSerializer(policy).data})

    @action(detail=True, methods=['post'], url_path='rollback')
    def rollback(self, request, pk=None):
        """Roll a key back to this version's value — creates a *new*
        published version, never rewrites history."""
        policy = self.get_object()
        if policy.impact == 'high':
            record_audit(
                request=request, action='permission.denied', entity=policy,
                reason='high-impact rollback requires approval flow')
            return Response(
                {'error': 'High-impact rollback requires a draft + '
                          'publish approval cycle.'},
                status=status.HTTP_403_FORBIDDEN)
        latest = (PolicyVersion.objects.filter(key=policy.key)
                  .order_by('-version').first())
        new_version = PolicyVersion.objects.create(
            key=policy.key, version=(latest.version + 1 if latest else 1),
            value=policy.value, status='published', impact=policy.impact,
            reason=(request.data.get('reason')
                    or f'rollback to v{policy.version}'),
            created_by=request.user, approved_by=request.user,
            effective_at=timezone.now())
        current = (PolicyVersion.objects
                   .filter(key=policy.key, status='published')
                   .exclude(pk=new_version.pk).first())
        if current:
            current.status = 'rolled_back'
            current.save(update_fields=['status'])
        record_audit(
            request=request, action='settings.policy_rollback',
            entity=new_version,
            before={'rolled_back_to': policy.version,
                    'previous': current.value if current else None},
            after={'value': new_version.value},
            reason=new_version.reason)
        return Response({'status': 'success',
                         'data': PolicyVersionSerializer(new_version).data})

    @action(detail=True, methods=['get'], url_path='diff')
    def diff(self, request, pk=None):
        """Version diff + current published value for impact preview."""
        policy = self.get_object()
        current = (PolicyVersion.objects
                   .filter(key=policy.key, status='published')
                   .exclude(pk=policy.pk).first())
        return Response({'status': 'success', 'data': {
            'key': policy.key, 'version': policy.version,
            'proposed': policy.value,
            'current_published': {
                'version': current.version if current else None,
                'value': current.value if current else None},
            'changed_keys': sorted(
                set(policy.value) ^ set(current.value)
                | {k for k in policy.value
                   if current and policy.value.get(k) != current.value.get(k)}
            ) if current else sorted(policy.value),
        }})


# =====================================================
# GOV-03 — PRIVACY CENTER
# =====================================================

PRIVACY_REQUEST_DEADLINE_DAYS = 30


class AdminPrivacyRequestViewSet(AdminBaseViewSet):
    """Privacy requests (GOV-03): access, correction, deletion and
    marketing objection with identity verification + deadline tracking."""
    view_capability = 'privacy.view'
    manage_capability = 'privacy.manage'
    queryset = PrivacyRequest.objects.select_related(
        'subject_user', 'handler')
    serializer_class = PrivacyRequestSerializer
    filterset_fields = ['request_type', 'status', 'handler']
    search_fields = ['subject_email']
    ordering = ['due_at', '-created_at']

    def perform_create(self, serializer):
        days = int(getattr(settings, 'PRIVACY_REQUEST_DEADLINE_DAYS',
                           PRIVACY_REQUEST_DEADLINE_DAYS))
        instance = serializer.save(
            due_at=timezone.now() + timedelta(days=days))
        record_audit(
            request=self.request, action='privacy.request_received',
            entity=instance,
            after={'type': instance.request_type,
                   'subject': instance.subject_email,
                   'due_at': str(instance.due_at)})

    @action(detail=True, methods=['post'], url_path='verify')
    def verify(self, request, pk=None):
        pr = self.get_object()
        if pr.verified_at:
            return Response({'error': 'Already verified.'},
                            status=status.HTTP_400_BAD_REQUEST)
        pr.status = 'in_progress'
        pr.verified_at = timezone.now()
        pr.handler = request.user
        pr.save(update_fields=['status', 'verified_at', 'handler',
                               'updated_at'])
        record_audit(
            request=request, action='privacy.verified', entity=pr,
            reason=(request.data.get('method')
                    or 'identity verified'))
        return Response({'status': 'success',
                         'data': PrivacyRequestSerializer(pr).data})

    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        pr = self.get_object()
        if pr.status in ('completed', 'rejected'):
            return Response({'error': f'Request already {pr.status}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        retention = (request.data.get('retention_exception') or '').strip()
        if pr.request_type == 'deletion' and not (
                request.data.get('propagated') or retention):
            return Response(
                {'error': 'Deletion completion requires either propagated='
                          'true or a lawful retention_exception (GOV-03).'},
                status=status.HTTP_400_BAD_REQUEST)
        pr.status = 'completed'
        pr.completed_at = timezone.now()
        pr.handler = pr.handler or request.user
        pr.retention_exception = retention
        pr.save(update_fields=['status', 'completed_at', 'handler',
                               'retention_exception', 'updated_at'])
        record_audit(
            request=request, action='privacy.completed', entity=pr,
            after={'type': pr.request_type,
                   'retention_exception': bool(retention)},
            reason=request.data.get('notes', ''))
        return Response({'status': 'success',
                         'data': PrivacyRequestSerializer(pr).data})

    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        pr = self.get_object()
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            return Response({'error': 'A rejection reason is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        pr.status = 'rejected'
        pr.notes = f"{pr.notes}\nRejected: {reason}".strip()
        pr.save(update_fields=['status', 'notes', 'updated_at'])
        record_audit(
            request=request, action='privacy.rejected', entity=pr,
            reason=reason)
        return Response({'status': 'success',
                         'data': PrivacyRequestSerializer(pr).data})


# =====================================================
# GOV-04 — INCIDENT CENTER
# =====================================================

INCIDENT_TRANSITIONS = {
    'open': {'mitigating', 'resolved', 'closed'},
    'mitigating': {'resolved', 'closed'},
    'resolved': {'postmortem', 'closed', 'open'},
    'postmortem': {'closed'},
    'closed': {'open'},
}


class AdminIncidentViewSet(AdminBaseViewSet):
    """Incident center (GOV-04). Sev1/2 open incidents feed the work
    queue; every status change + timeline entry is audited."""
    view_capability = 'health.view'
    manage_capability = 'risk.manage'
    queryset = Incident.objects.select_related('owner')
    serializer_class = IncidentSerializer
    filterset_fields = ['severity', 'status', 'owner']
    search_fields = ['title', 'summary']
    ordering = ['-created_at']

    @action(detail=True, methods=['post'], url_path='status')
    def update_status(self, request, pk=None):
        incident = self.get_object()
        new_status = request.data.get('status')
        reason = (request.data.get('reason') or '').strip()
        prior = incident.status
        if new_status == prior:
            return Response({'status': 'success',
                             'data': IncidentSerializer(incident).data})
        if not reason:
            return Response({'error': 'A reason is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        allowed = INCIDENT_TRANSITIONS.get(prior, set())
        if new_status not in allowed:
            return Response(
                {'error': f'Cannot move {prior} → {new_status}.',
                 'allowed': sorted(allowed)},
                status=status.HTTP_400_BAD_REQUEST)
        incident.status = new_status
        if new_status == 'resolved':
            incident.resolved_at = timezone.now()
        incident.save(update_fields=['status', 'resolved_at', 'updated_at'])
        record_audit(
            request=request, action='gov.incident_status', entity=incident,
            before={'status': prior}, after={'status': new_status},
            reason=reason)
        return Response({'status': 'success',
                         'data': IncidentSerializer(incident).data})

    @action(detail=True, methods=['post'], url_path='timeline')
    def add_timeline_entry(self, request, pk=None):
        incident = self.get_object()
        note = (request.data.get('note') or '').strip()
        if not note:
            return Response({'error': 'note is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        entry = {'at': timezone.now().isoformat(),
                 'by': request.user.email, 'note': note}
        incident.timeline = list(incident.timeline or []) + [entry]
        incident.save(update_fields=['timeline', 'updated_at'])
        record_audit(
            request=request, action='gov.incident_timeline',
            entity=incident, after={'entry': note[:200]})
        return Response({'status': 'success',
                         'data': IncidentSerializer(incident).data})


# =====================================================
# GOV-01 — ACCESS CENTER (capability grants)
# =====================================================

SENSITIVE_CAPABILITIES = {
    'finance.approve_payout', 'finance.mark_settled', 'finance.refund',
    'finance.reconcile', 'users.grant_role', 'settings.publish',
    'privacy.manage', 'privacy.export',
}


class AdminCapabilityGrantViewSet(AdminBaseViewSet):
    """Access center (GOV-01): grant or revoke a named capability for one
    user, optionally time-bound (break-glass). Effective immediately —
    capabilities resolve per request. Granting a sensitive capability
    creates a maker-checker ApprovalRequest instead of applying directly.
    """
    view_capability = 'users.grant_role'
    manage_capability = 'users.grant_role'
    queryset = CapabilityGrant.objects.select_related(
        'user', 'granted_by')
    serializer_class = CapabilityGrantSerializer
    filterset_fields = ['user', 'capability', 'granted']
    ordering = ['-created_at']

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        capability = data['capability']
        if data['granted'] and capability in SENSITIVE_CAPABILITIES:
            from .approvals import request_approval
            ap = request_approval(
                request=request, action='access.capability_grant',
                entity=data['user'],
                payload={
                    'capability': capability,
                    'expires_at': str(data.get('expires_at') or ''),
                },
                reason=data.get('reason', ''),
            )
            return Response(
                {'status': 'approval_required',
                 'message': 'Sensitive capability grant requires a second '
                            'approver via /manage/approvals.',
                 'approval_id': ap.id},
                status=status.HTTP_202_ACCEPTED)
        grant = serializer.save(granted_by=request.user)
        record_audit(
            request=request, action='access.capability_grant', entity=grant,
            after={'user': str(grant.user_id), 'capability': capability,
                   'granted': grant.granted,
                   'expires_at': str(grant.expires_at or '')},
            reason=grant.reason)
        return Response({'status': 'success',
                         'data': CapabilityGrantSerializer(grant).data},
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='revoke')
    def revoke(self, request, pk=None):
        """Revoke a grant — takes effect on the next API request."""
        grant = self.get_object()
        if grant.revoked_at:
            return Response({'error': 'Already revoked.'},
                            status=status.HTTP_400_BAD_REQUEST)
        grant.revoked_at = timezone.now()
        grant.save(update_fields=['revoked_at'])
        record_audit(
            request=request, action='access.capability_revoke',
            entity=grant,
            before={'capability': grant.capability,
                    'user': str(grant.user_id)},
            reason=request.data.get('reason', ''))
        return Response({'status': 'success',
                         'data': CapabilityGrantSerializer(grant).data})


# =====================================================
# FIN-05 / FIN-06 — FINANCE WORKBENCH METRICS
# =====================================================

class AdminFinanceSummaryView(APIView):
    """FIN-05 — separated money metrics: GMV ≠ collections ≠ revenue.

    Every figure carries its source and definition; missing data is null,
    never fabricated zero.
    """
    permission_classes = [HasCapability]
    required_capability = 'finance.view'

    def get(self, request):
        from django.db.models import Sum
        from apps.customers.models import OrderItem as OI
        from apps.pay.models import Payment, Escrow, Withdrawal

        def total(qs, field='amount'):
            return qs.aggregate(t=Sum(field))['t']

        paid_payment = Q(is_paid=True, is_deleted=False)
        item_paid = Q(order__invoice__payment__is_paid=True,
                      order__invoice__payment__is_deleted=False)

        # GMV — paid merchandise value (pre-refund, excl. shipping/tax).
        gmv = total(OI.objects.filter(item_paid), 'sub_total')
        # Customer collections — what the provider actually captured.
        collections = total(Payment.objects.filter(paid_payment))
        # Recognized platform revenue — commission on *released* escrow.
        commission = total(
            Escrow.objects.filter(status='released'),
            'platform_commission')
        # Designer payable — released escrow awaiting withdrawal.
        payable = total(
            Escrow.objects.filter(status='released'), 'amount')
        held = total(Escrow.objects.filter(status='held'), 'amount')
        pending_payouts = total(
            Withdrawal.objects.filter(status='pending'), 'amount')
        refund_pending = total(
            OI.objects.filter(
                return_requests__status='refund_pending',
            ).distinct(), 'sub_total')

        currencies = list(
            Payment.objects.filter(paid_payment)
            .values_list('currency', flat=True).distinct())

        return Response({'status': 'success', 'data': {
            'gmv': str(gmv) if gmv is not None else None,
            'customer_collections': (
                str(collections) if collections is not None else None),
            'commission_recognized': (
                str(commission) if commission is not None else None),
            'designer_payable_released': (
                str(payable) if payable is not None else None),
            'escrow_held': str(held) if held is not None else None,
            'payouts_pending': (
                str(pending_payouts) if pending_payouts is not None
                else None),
            'refunds_pending': (
                str(refund_pending) if refund_pending is not None
                else None),
            'meta': {
                'generated_at': timezone.now().isoformat(),
                'currencies': currencies,
                'definitions': {
                    'gmv': 'sub_total of items on paid orders',
                    'customer_collections':
                        'sum of paid, non-deleted payments',
                    'commission_recognized':
                        'platform_commission on released escrow only',
                    'designer_payable_released':
                        'released escrow amount owed to designers',
                    'escrow_held': 'escrow amount still held',
                    'payouts_pending': 'withdrawals awaiting settlement',
                    'refunds_pending':
                        'sub_total of items with refund_pending returns',
                },
                'sources': ['Payment', 'Invoice', 'OrderItem', 'Escrow',
                            'Withdrawal', 'ReturnRequest'],
                'warning': 'multi-currency deployments must not sum '
                           'across currencies — see currencies list.',
            },
        }})


class AdminLiabilityForecastView(APIView):
    """FIN-06 — cash/liability view: held escrow, pending refunds,
    pending payouts, chargeback exposure. Inputs are visible; no hidden
    assumptions."""
    permission_classes = [HasCapability]
    required_capability = 'finance.view'

    def get(self, request):
        from django.db.models import Count, Sum
        from apps.customers.models import Dispute, OrderItem as OI
        from apps.pay.models import Escrow, Withdrawal

        held = Escrow.objects.filter(status='held').aggregate(
            t=Sum('amount'))['t']
        payouts = Withdrawal.objects.filter(status='pending').aggregate(
            t=Sum('amount'))['t']
        refunds = OI.objects.filter(
            return_requests__status='refund_pending').distinct().aggregate(
            t=Sum('sub_total'))['t']
        open_disputes = Dispute.objects.filter(
            status__in=['opened', 'under_review', 'escalated'])
        dispute_exposure = open_disputes.aggregate(
            t=Sum('refund_amount'))['t']

        return Response({'status': 'success', 'data': {
            'liabilities': {
                'escrow_held': str(held or 0),
                'payouts_pending': str(payouts or 0),
                'refunds_pending': str(refunds or 0),
                'dispute_exposure': {
                    'open_disputes': open_disputes.count(),
                    'declared_refund_total': str(dispute_exposure or 0),
                },
            },
            'meta': {
                'generated_at': timezone.now().isoformat(),
                'assumptions': [
                    'held escrow is a designer liability until released',
                    'pending withdrawals are committed cash outflow',
                    'refund_pending uses item sub_total as the exposure',
                    'dispute exposure counts declared refund_amounts only',
                ],
                'version': 1,
            },
        }})


# =====================================================
# CAT-02 — INVENTORY HEALTH CONSOLE
# =====================================================

class AdminInventoryHealthView(APIView):
    """CAT-02 — out-of-stock, stale stock, oversell risk and fast sellers.
    Each row traces to the product + the signal that flagged it."""
    permission_classes = [HasCapability]
    required_capability = 'catalog.view'

    def get(self, request):
        from django.db.models import Count, Sum
        from apps.customers.models import OrderItem as OI

        base = Product.objects.filter(is_active=True)
        window = timezone.now() - timedelta(days=30)
        stale_window = timezone.now() - timedelta(days=90)

        sales = dict(
            OI.objects.filter(
                order__invoice__payment__is_paid=True,
                created_at__gte=window,
            ).values_list('product_id').annotate(
                sold=Sum('quantity')))

        def rows(products, flag, extra=None):
            out = []
            for p in products[:200]:
                row = {'id': str(p.id), 'name': p.name,
                       'stock': p.stock, 'price': str(p.price),
                       'flag': flag}
                if extra:
                    row.update(extra(p))
                out.append(row)
            return out

        out_of_stock = rows(
            base.filter(stock=0, is_published=True), 'out_of_stock')
        stale = rows(
            base.filter(stock__gt=0, updated_at__lt=stale_window)
            .exclude(id__in=sales), 'stale_stock',
            lambda p: {'last_update': str(p.updated_at)})
        fast = rows(
            base.filter(id__in=[k for k, v in sales.items() if v >= 10]),
            'fast_selling',
            lambda p: {'sold_30d': sales.get(p.id, 0)})
        oversell = rows(
            [p for p in base.filter(id__in=sales)
             if sales.get(p.id, 0) > 0 and p.stock == 0],
            'oversell_risk',
            lambda p: {'sold_30d': sales.get(p.id, 0)})

        return Response({'status': 'success', 'data': {
            'out_of_stock': out_of_stock,
            'stale_stock': stale,
            'fast_selling': fast,
            'oversell_risk': oversell,
            'meta': {
                'generated_at': timezone.now().isoformat(),
                'sales_window_days': 30, 'stale_after_days': 90,
                'definitions': {
                    'oversell_risk': 'paid sales in last 30d while '
                                     'stock is 0',
                },
            },
        }})
