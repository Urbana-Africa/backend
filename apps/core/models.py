# core/models.py
from random import random
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from django.core.validators import MinValueValidator
import uuid
import secrets
import string
from apps.authentication.models import User



ALPHABET = string.ascii_letters + string.digits  # 62 chars

def generate_custom_uuid(length=20):
    """
    Generates a secure random ID.

    Example:
    '4fK2x8PqL9sWm3QzT7aB'
    """
    return ''.join(secrets.choice(ALPHABET) for _ in range(length))

class BaseModel(models.Model):
    """
    All models inherit this base model.
    By default, it uses a custom ID instead of UUID.
    """
    id = models.CharField(
        primary_key=True,
        max_length=50,
        editable=False,
        default=generate_custom_uuid
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']



class UserSettings(BaseModel):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="settings"
    )

    # App
    language = models.CharField(max_length=10, default="en-US")
    currency = models.CharField(max_length=5, default="USD")

    # Notifications
    email_notifications = models.BooleanField(default=True)
    order_updates = models.BooleanField(default=True)

    # Privacy
    public_profile = models.BooleanField(default=False)
    personalized_recommendations = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} settings"
# ---------------------------
# Country & Currency
# ---------------------------
class Country(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=5, unique=True)
    continent = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return self.name


class Currency(models.Model):
    name = models.CharField(max_length=50)
    code = models.CharField(max_length=10, unique=True)
    symbol = models.CharField(max_length=10, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} ({self.symbol})"


# ---------------------------
# Media
# ---------------------------
class MediaAsset(BaseModel):
    class MediaType(models.TextChoices):
        IMAGE = "image"
        VIDEO = "video"
        DOCUMENT = "document"
    user = models.ForeignKey(User, on_delete=models.CASCADE,null = True, default=None, related_name="media_assets")
    file = models.FileField(upload_to="uploads/%Y/%m/%d/")
    media_type = models.CharField(max_length=20, choices=MediaType.choices, default=MediaType.IMAGE)
    alt_text = models.CharField(max_length=255, blank=True, null=True)
    caption = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.alt_text or str(self.file)


# ---------------------------
# Categories
# ---------------------------
class Category(BaseModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank = True)
    parent = models.ForeignKey("self", null=True, blank=True, related_name="subcategories", on_delete=models.SET_NULL)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


# ---------------------------
# Products
# ---------------------------
class Brand(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name



class Sizes(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField(default=None)

    def __str__(self):
        return self.name
    
class Product(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE,null = True, default=None, related_name="products")
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField()
    price = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    discount = models.DecimalField(max_digits=5, decimal_places=2, blank=True, default=0, help_text="Discount in %")
    currency = models.ForeignKey(Currency, on_delete=models.SET_NULL, null=True)
    category = models.ForeignKey(Category, related_name='products', on_delete=models.SET_NULL, null=True)
    categories = models.ManyToManyField(Category, blank=True, related_name='products_m2m')
    subcategory = models.ForeignKey(Category, related_name='sub_products', on_delete=models.SET_NULL, null=True, blank=True)
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True)
    material = models.CharField(max_length=100, blank=True, null=True)
    origin = models.CharField(max_length=100, blank=True, null=True)
    sizes = models.ManyToManyField(Sizes, default=None, blank=True,)
    stock = models.PositiveIntegerField(default=0)
    sku = models.CharField(max_length=50, unique=True, blank=True, null=True)
    is_published = models.BooleanField(default=False)
    is_admin_published = models.BooleanField(default=False)
    unpublish_reasons = models.JSONField(default=list, blank=True)
    unpublish_comment = models.TextField(blank=True, null=True)
    media = models.ManyToManyField(MediaAsset, blank=True, related_name='products')
    featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_sustainable = models.BooleanField(default=False)  # for sustainable tab
    sustainability_notes = models.TextField(blank=True, null=True)

    # Popularity metric for trending tab
    popularity_score = models.PositiveIntegerField(default=0)

    # ---------------------------
    # PRD V1 Fields
    # ---------------------------
    class AvailabilityType(models.TextChoices):
        READY_TO_SHIP = "ready_to_ship", "Ready to Ship"
        MADE_TO_ORDER = "made_to_order", "Made to Order"
        PRE_ORDER = "pre_order", "Pre-Order"
        RENTABLE = "rentable", "Rentable"

    class PrintType(models.TextChoices):
        ANKARA = "ankara", "Ankara"
        ADIRE = "adire", "Adire"
        KENTE = "kente", "Kente"
        BOGOLAN = "bogolan", "Bogolan"
        OTHER = "other", "Other"

    class Occasion(models.TextChoices):
        WEDDING = "wedding", "Wedding"
        WORK = "work", "Work"
        CASUAL = "casual", "Casual"
        PARTY = "party", "Party"
        TRADITIONAL = "traditional", "Traditional"
        OTHER = "other", "Other"

    availability_type = models.CharField(
        max_length=20,
        choices=AvailabilityType.choices,
        default=AvailabilityType.READY_TO_SHIP,
    )
    print_type = models.CharField(
        max_length=20,
        choices=PrintType.choices,
        default=PrintType.OTHER,
        blank=True,
    )
    occasion = models.CharField(
        max_length=20,
        choices=Occasion.choices,
        default=Occasion.OTHER,
        blank=True,
    )
    country_of_origin = models.ForeignKey(
        Country,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    lead_time_days = models.PositiveIntegerField(
        default=0,
        help_text="Lead time in days for Made-to-Order or Pre-Order items",
    )
    rental_price_per_day = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Daily rental price for rentable items",
    )
    fit_stats = models.JSONField(
        default=dict,
        blank=True,
        help_text='Aggregated fit feedback e.g. {"true_to_size": 87, "too_tight": 5, "too_loose": 8}',
    )
    size_chart_image = models.ImageField(
        upload_to="product_size_charts/",
        blank=True,
        null=True,
        help_text="Upload a size chart image for this product",
    )
    fit_me_image = models.ImageField(
        upload_to="product_fitme_images/",
        blank=True,
        null=True,
        help_text=(
            "PNG with alpha transparency (transparent background) for AI FitMe. "
            "Best results: front-facing garment, unworn, clean edges, "
            "portrait orientation for dresses/tops, landscape for accessories. "
            "Minimum 512px on short edge."
        ),
    )
    weight_kg = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0.50,
        help_text="Product weight in kilograms (kg)"
    )
    length_cm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=30.00,
        help_text="Package length in centimeters (cm)"
    )
    width_cm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=20.00,
        help_text="Package width in centimeters (cm)"
    )
    height_cm = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=10.00,
        help_text="Package height in centimeters (cm)"
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name + str(round(random()*9999999)))
        if not self.sku:
            self.sku = f"U-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Color(models.Model):
    product = models.ForeignKey(Product, null=True, default=None, related_name='colors', on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    hex_code = models.CharField(max_length=7, blank=True, null=True)  # optional

    def __str__(self):
        return self.name
# ---------------------------
# Reviews
# ---------------------------
class Review(BaseModel):
    product = models.ForeignKey(Product, related_name='reviews', on_delete=models.CASCADE)
    customer = models.ForeignKey('customers.Customer', related_name='reviews', on_delete=models.CASCADE)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)], default=5)
    comment = models.TextField(blank=True, null=True)
    fit_feedback = models.CharField(
        max_length=50, blank=True,
        choices=[
            ("perfect_fit", "Perfect fit"),
            ("true_to_size", "True to size"),
            ("too_tight", "Too tight"),
            ("too_loose", "Too loose"),
            ("runs_large", "Runs large"),
        ]
    )
    is_approved = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.rating} stars by {self.customer}"


class ShippingMethod(models.Model):
    """Available shipping methods for orders."""
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    estimated_days = models.PositiveIntegerField(help_text="Estimated delivery in days")
    is_active = models.BooleanField(default=True)
    applicable_countries = models.JSONField(
        default=list,
        blank=True,
        help_text="List of ISO country codes this method applies to. Empty = global."
    )

    def __str__(self):
        return f"{self.name} (${self.price})"

    def is_available_for_country(self, country_code):
        if not self.applicable_countries:
            return True
        return country_code in self.applicable_countries






class ContactMessage(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField()
    message = models.TextField()

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_messages",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    is_resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} - {self.email}"


# ---------------------------
# Support Tickets
# ---------------------------
class SupportTicket(BaseModel):
    class Category(models.TextChoices):
        PAYOUT = "payout", "Payout & Wallet"
        PRODUCT = "product", "Products & Listings"
        ORDER = "order", "Orders & Returns"
        SHIPPING = "shipping", "Shipping & Delivery"
        ACCOUNT = "account", "Account & Profile"
        TECHNICAL = "technical", "Technical Issue"
        OTHER = "other", "Other"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        WAITING = "waiting", "Waiting on Designer"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    # Allow unauthenticated guests to submit tickets too
    guest_name = models.CharField(max_length=150, blank=True, null=True)
    guest_email = models.EmailField(blank=True, null=True)

    subject = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.OPEN
    )

    # Optional admin reply
    admin_reply = models.TextField(blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    # Ticket reference number (short, human-readable)
    reference = models.CharField(max_length=10, unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.reference:
            import random
            import string
            self.reference = "TKT-" + "".join(
                random.choices(string.digits, k=6)
            )
        if self.status == self.Status.RESOLVED and not self.resolved_at:
            from django.utils import timezone
            self.resolved_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.reference}] {self.subject}"

    class Meta:
        ordering = ["-created_at"]


class TicketMessage(models.Model):
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="ticket_messages"
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_internal = models.BooleanField(
        default=False,
        help_text="Internal notes only visible to staff",
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.ticket.reference}] {self.sender} at {self.created_at}"


# ---------------------------
# Smart Collections (PRD Phase 2)
# ---------------------------
class SmartCollection(BaseModel):
    class CollectionType(models.TextChoices):
        CURATED = "curated", "Curated"
        TRENDING = "trending", "Trending"
        SEASONAL = "seasonal", "Seasonal"
        OCCASION = "occasion", "Occasion"
        PRINT = "print", "Print Style"

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, blank=True)
    subtitle = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    collection_type = models.CharField(
        max_length=20, choices=CollectionType.choices, default=CollectionType.CURATED
    )
    products = models.ManyToManyField(Product, blank=True, related_name="smart_collections")
    cover_image = models.ImageField(upload_to="smart_collections/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    auto_generated = models.BooleanField(default=False, help_text="True if created by AI mode")
    query = models.CharField(max_length=300, blank=True, help_text="Query string used to generate this collection")

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ["display_order", "-created_at"]


# ---------------------------
# Product View Tracking (PRD Phase 2)
# ---------------------------
class ProductView(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="view_events")
    designer = models.ForeignKey(
        "designers.Designer",
        on_delete=models.CASCADE,
        related_name="product_view_events",
        null=True,
        blank=True,
    )
    session_id = models.CharField(max_length=100, blank=True, db_index=True)
    event_type = models.CharField(max_length=50, default="product_view")
    source = models.CharField(max_length=50, blank=True, default="organic")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["designer", "created_at"]),
            models.Index(fields=["product", "created_at"]),
        ]


# ---------------------------
# Designer Daily Analytics (PRD Phase 2)
# ---------------------------
class DesignerDailyAnalytics(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    designer = models.ForeignKey(
        "designers.Designer",
        on_delete=models.CASCADE,
        related_name="daily_analytics",
    )
    date = models.DateField(db_index=True)
    page_views = models.PositiveIntegerField(default=0)
    unique_visitors = models.PositiveIntegerField(default=0)
    add_to_cart_events = models.PositiveIntegerField(default=0)
    purchase_events = models.PositiveIntegerField(default=0)
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("designer", "date")
        ordering = ["-date"]


# ---------------------------
# Loyalty & Rewards (PRD Phase 2)
# ---------------------------
class LoyaltyPoints(models.Model):
    class TransactionType(models.TextChoices):
        EARN = "earn", "Earn"
        REDEEM = "redeem", "Redeem"
        EXPIRE = "expire", "Expire"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="loyalty_transactions")
    points = models.IntegerField()
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)
    description = models.CharField(max_length=255, blank=True)
    order = models.ForeignKey(
        "customers.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class LoyaltyBalance(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="loyalty_balance")
    total_points = models.IntegerField(default=0)
    lifetime_earned = models.IntegerField(default=0)
    lifetime_redeemed = models.IntegerField(default=0)
    tier = models.CharField(
        max_length=20,
        choices=[
            ("bronze", "Bronze"),
            ("silver", "Silver"),
            ("gold", "Gold"),
        ],
        default="bronze",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} — {self.total_points} pts ({self.tier})"


# ---------------------------
# Size Recommendation (PRD Phase 2)
# ---------------------------
class SizeRecommendation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="size_recommendations")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="size_recommendations")
    recommended_size = models.CharField(max_length=50, help_text="Recommended size name")
    confidence_score = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0)], help_text="0-100 confidence %"
    )
    body_measurements = models.JSONField(
        default=dict, blank=True, help_text='{"chest": 96, "waist": 82, "hips": 100}'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user", "product"]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} → {self.product.name} : {self.recommended_size}"


# ---------------------------
# User Lookbook (PRD Phase 2)
# ---------------------------
class UserLookbook(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="lookbooks")
    name = models.CharField(max_length=200, help_text="Lookbook title")
    description = models.TextField(blank=True)
    products = models.ManyToManyField(Product, blank=True, related_name="lookbooks")
    cover_image = models.ImageField(upload_to="lookbooks/", blank=True, null=True)
    is_public = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} — {self.name}"


# ---------------------------
# AI Semantic Search (Vector Embeddings)
# ---------------------------
class ProductEmbedding(models.Model):
    """
    Stores pre-computed text embeddings for products to enable
    semantic / vector similarity search in the AI mode.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.OneToOneField(
        "core.Product",
        on_delete=models.CASCADE,
        related_name="embedding",
    )
    embedding = models.JSONField(
        default=list,
        blank=True,
        help_text="Vector embedding (list of floats) for semantic search",
    )
    embedding_text = models.TextField(
        blank=True,
        help_text="Concatenated text used to generate the embedding",
    )
    dimensions = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Embedding({self.product.name}, {self.dimensions}d)"


class SubscriptionPlan(models.Model):
    class Tier(models.TextChoices):
        FREE = "free", "Free"
        STYLE_SEEKER = "style_seeker", "Style Seeker"
        STYLE_ICON = "style_icon", "Style Icon"

    name = models.CharField(max_length=50)
    slug = models.SlugField(unique=True)
    tier = models.CharField(max_length=20, choices=Tier.choices, default=Tier.FREE)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.ForeignKey(Currency, on_delete=models.SET_NULL, null=True, blank=True)
    ai_calls_daily = models.PositiveIntegerField(default=10, help_text="Daily AI search/chat limit")
    has_ai_outfit_builder = models.BooleanField(default=False)
    has_ai_personalized_search = models.BooleanField(default=False)
    has_ai_fitme = models.BooleanField(default=False)
    has_gift_concierge = models.BooleanField(default=False)
    has_event_styling = models.BooleanField(default=False)
    has_priority_support = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    features = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["price_monthly"]

    def __str__(self):
        return self.name


class UserSubscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    billing_cycle = models.CharField(max_length=10, choices=[("monthly", "Monthly"), ("yearly", "Yearly")], default="monthly")
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    ai_calls_used_today = models.PositiveIntegerField(default=0)
    ai_calls_reset_at = models.DateField(auto_now_add=True)
    auto_renew = models.BooleanField(default=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.user.email} — {self.plan.name if self.plan else 'None'}"
