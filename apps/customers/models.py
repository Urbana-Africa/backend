import uuid
from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.core.validators import MinValueValidator
from apps.authentication.models import User
from apps.core.models import BaseModel, Color, MediaAsset, Product, Sizes
from apps.pay.models import Escrow, Invoice


class Customer(models.Model):
    """Customer profile linked to the user account."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='customer_profile')
    phone = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to='customers/avatars/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.user.get_full_name() or self.user.username


class Address(models.Model):
    """Shipping/Billing address."""
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='addresses')
    label = models.CharField(max_length=50, default="Home")
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.label} - {self.customer}"


class Wishlist(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='wishlist')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='wishlisted_by')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('customer', 'product')


class CartItem(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='cart_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    color = models.ForeignKey(Color, on_delete=models.SET_NULL, null=True, default = None)
    size = models.ForeignKey(Sizes, on_delete=models.SET_NULL, null=True,default=None,)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    added_at = models.DateTimeField(auto_now_add=True)
    properties = models.JSONField(default=dict)

    class Meta:
        pass

    def subtotal(self):
        return self.quantity * self.product.price


class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='orders')
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='invoices')
    order_id = models.SlugField(unique=True, editable=False)
    shipping_address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    sub_total = models.DecimalField(max_digits=10, decimal_places=2, default = 0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    shipping_method = models.CharField(max_length=50, default='')
    shipping_amount = models.DecimalField(max_digits=10,decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.order_id:
            self.order_id = slugify(f"{self.customer.user.username}-{self.pk or ''}")[:50]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Order {self.order_id} ({self.customer})"

def generate_item_id():
    return f'UOIT_{uuid.uuid4().hex[:15].upper()}'

class OrderItem(models.Model):
    """Individual items in an order."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]
    COLLECTION_ORIGIN_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('received', 'Received'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]
    CUSTOMER_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('received', 'Received'),
        ('returned', 'Returned'),
    ]
    COLLECTION_DESTINATION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('received', 'Received'),
        ('approved', 'Approved'),
        ('cancelled', 'Cancelled'),
        ('returned', 'Returned'),
    ]
    designer = models.ForeignKey(User, on_delete=models.CASCADE, null=True,default=None, related_name='order_items')
    escrow = models.OneToOneField(Escrow, on_delete=models.CASCADE, null=True,default=None, related_name='order_item')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    item_id = models.CharField(max_length=100, null=True, default=generate_item_id)
    color = models.ForeignKey(Color, on_delete=models.SET_NULL, null=True, default = None, related_name='color')
    size = models.ForeignKey(Sizes, on_delete=models.SET_NULL, null=True,default=None, related_name='sizes')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    sub_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    properties = models.JSONField(default=dict, editable=False)
    tracking_number = models.CharField(max_length=100, unique=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    designer_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    collection_destination_status = models.CharField(max_length=20, choices=COLLECTION_DESTINATION_STATUS_CHOICES, default='pending')
    collection_origin_status = models.CharField(max_length=20, choices=COLLECTION_ORIGIN_STATUS_CHOICES, default='pending')
    customer_status = models.CharField(max_length=20, choices=CUSTOMER_STATUS_CHOICES, default='pending')
    masked_email = models.EmailField(blank=True, null=True, help_text="Forwarding alias that hides the real customer email.")
    masked_phone = models.CharField(max_length=50, blank=True, null=True, help_text="Masked customer phone number for designer view.")
    created_at = models.DateTimeField(auto_now_add=True, null= True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    
    def subtotal(self):
        return self.quantity * self.amount

    def save(self, *args, **kwargs):
        if not self.item_id:
            self.item_id = f"UOIT-{uuid.uuid4().hex[:15].upper()}"
        if not self.designer:
            if self.product:
                self.designer = self.product.user
                
        # Generate masked data for designer protection
        if not self.masked_email and self.order and self.order.customer:
            from apps.utils.masking import generate_masked_email
            self.masked_email = generate_masked_email(self.order.customer.user.email)
            
        if not self.masked_phone and self.order and self.order.customer:
            from apps.utils.masking import generate_masked_phone
            self.masked_phone = generate_masked_phone(self.order.customer.phone)

        super().save(*args, **kwargs)
        
class ReturnRequest(BaseModel):

    class Status(models.TextChoices):
        PENDING             = "pending",             "Pending"
        REVIEWING           = "reviewing",           "Reviewing"
        APPROVED            = "approved",            "Approved"
        REJECTED            = "rejected",            "Rejected"
        RETURN_IN_TRANSIT   = "return_in_transit",   "Return In Transit"
        RETURN_RECEIVED     = "return_received",     "Return Received"
        REFUND_PENDING      = "refund_pending",      "Refund Pending"
        REFUNDED            = "refunded",            "Refunded"
        DISPUTE_OPENED      = "dispute_opened",      "Dispute Opened"
        DISPUTE_UNDER_REVIEW= "dispute_under_review","Dispute Under Review"
        DISPUTE_RESOLVED    = "dispute_resolved",    "Dispute Resolved"
        DISPUTE_ESCALATED   = "dispute_escalated",   "Dispute Escalated"
        RETURNED            = "returned",            "Returned"
        CANCELED            = "canceled",            "Canceled"

    class Reason(models.TextChoices):
        DAMAGED         = "damaged"
        WRONG_ITEM      = "wrong_item"
        WRONG_SIZE      = "wrong_size"
        NOT_AS_DESCRIBED= "not_as_described"
        POOR_QUALITY    = "poor_quality"
        MISSING_PARTS   = "missing_parts"
        LATE_DELIVERY   = "late_delivery"
        CHANGED_MIND    = "changed_mind"
        OTHER           = "other"

    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        related_name="return_requests"
    )

    return_id = models.CharField(max_length=20, unique=True, editable=False)

    reason = models.CharField(max_length=50, choices=Reason.choices)

    description = models.TextField(blank=True)
    reject_reason = models.TextField(blank=True)

    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.PENDING
    )
    designer_status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    admin_status    = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    customer_status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)

    # Evidence
    product_photos = models.ManyToManyField(
        MediaAsset,
        blank=True,
        related_name="return_product_photos"
    )

    packaging_photo = models.ForeignKey(
        MediaAsset,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="return_packaging_photo"
    )

    unboxing_video = models.ForeignKey(
        MediaAsset,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="return_unboxing_video"
    )

    # Logistics
    return_tracking_number = models.CharField(max_length=100, blank=True)

    # workflow timestamps
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_return_eligible(self):
        """Returns True if still within the 7-day return window from delivery."""
        from django.utils import timezone
        delivered_at = self.order_item.delivered_at
        if not delivered_at:
            return False
        return (timezone.now() - delivered_at).days <= 7

    def save(self, *args, **kwargs):
        if not self.return_id:
            self.return_id = f"RET-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.return_id


class Dispute(BaseModel):
    """A dispute raised by the customer after a return request is rejected."""

    class Status(models.TextChoices):
        OPENED        = "opened",         "Opened"
        UNDER_REVIEW  = "under_review",   "Under Review"
        RESOLVED      = "resolved",       "Resolved"
        ESCALATED     = "escalated",      "Escalated"
        CLOSED        = "closed",         "Closed"

    class Resolution(models.TextChoices):
        REFUND_APPROVED  = "refund_approved",  "Refund Approved"
        REFUND_DENIED    = "refund_denied",    "Refund Denied"
        PARTIAL_REFUND   = "partial_refund",   "Partial Refund"
        PLATFORM_CREDIT  = "platform_credit",  "Platform Credit"

    dispute_id = models.CharField(max_length=20, unique=True, editable=False)

    return_request = models.OneToOneField(
        ReturnRequest,
        on_delete=models.CASCADE,
        related_name="dispute"
    )

    opened_by = models.ForeignKey(
        "authentication.User",
        on_delete=models.CASCADE,
        related_name="disputes_opened"
    )

    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPENED)
    resolution = models.CharField(max_length=30, choices=Resolution.choices, null=True, blank=True)

    # Customer side
    customer_notes = models.TextField(blank=True)
    customer_evidence = models.ManyToManyField(
        MediaAsset,
        blank=True,
        related_name="dispute_customer_evidence"
    )

    # Designer side
    designer_notes = models.TextField(blank=True)
    designer_evidence = models.ManyToManyField(
        MediaAsset,
        blank=True,
        related_name="dispute_designer_evidence"
    )

    # Admin resolution
    admin_notes = models.TextField(blank=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    resolved_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.dispute_id:
            self.dispute_id = f"DSP-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.dispute_id


class OrderTracking(models.Model):
    """Tracks the status of an order in detail."""
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='tracking')
    tracking_number = models.CharField(max_length=100, unique=True)
    carrier = models.CharField(max_length=100, blank=True, null=True)
    current_status = models.CharField(max_length=50, default='Pending')
    last_updated = models.DateTimeField(auto_now=True)
    estimated_delivery = models.DateField(blank=True, null=True)

    def __str__(self):
        return f"Tracking {self.tracking_number} ({self.order.order_id})"
