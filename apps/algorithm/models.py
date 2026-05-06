"""
Urbana Core Algorithm V2.0 Models
Adaptive Marketplace Intelligence System
"""

import json
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from django.conf import settings

from apps.core.models import BaseModel, Product
from apps.designers.models import Designer


# =====================================================
# 1. User Intelligence System
# =====================================================

class UserActivity(BaseModel):
    """Raw behavioral event stream for every user interaction."""

    class EventType(models.TextChoices):
        PRODUCT_CLICK = "product_click", "Product Click"
        PRODUCT_VIEW = "product_view", "Product View"
        ADD_TO_CART = "add_to_cart", "Add to Cart"
        REMOVE_FROM_CART = "remove_from_cart", "Remove from Cart"
        WISHLIST_ADD = "wishlist_add", "Wishlist Add"
        WISHLIST_REMOVE = "wishlist_remove", "Wishlist Remove"
        PURCHASE = "purchase", "Purchase"
        SEARCH = "search", "Search Query"
        SCROLL_DEPTH = "scroll_depth", "Scroll Depth"
        PRICE_FILTER = "price_filter", "Price Filter"
        CATEGORY_BROWSE = "category_browse", "Category Browse"
        TIME_ON_PRODUCT = "time_on_product", "Time on Product"
        REVIEW_VIEW = "review_view", "Review View"
        STORY_VIEW = "story_view", "Story View"
        SHARE = "share", "Share"
        RETURN_INITIATED = "return_initiated", "Return Initiated"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activities",
    )
    session_id = models.CharField(max_length=100, db_index=True)
    event_type = models.CharField(max_length=30, choices=EventType.choices, db_index=True)

    # The primary entity this event relates to
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activities",
    )
    designer = models.ForeignKey(
        Designer,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="activities",
    )

    # Flexible JSON metadata for event-specific data
    metadata = models.JSONField(default=dict, blank=True)

    # Context
    user_agent = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    country_code = models.CharField(max_length=5, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "event_type", "created_at"]),
            models.Index(fields=["session_id", "created_at"]),
            models.Index(fields=["product", "event_type", "created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} by {self.user or self.session_id[:8]}"


class UserPreferenceProfile(BaseModel):
    """
    Computed long-term user profile for personalization.
    Updated nightly from UserActivity aggregation.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preference_profile",
    )

    # Style preferences: { "category_id": weight, ... }
    category_affinity = models.JSONField(default=dict)

    # Price sensitivity profile
    preferred_price_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    preferred_price_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount_responsiveness = models.FloatField(default=0.5)  # 0-1

    # Size profile: { "category_id": "most_bought_size", ... }
    size_profile = models.JSONField(default=dict)

    # Cultural affinity: { "country_code": weight, ... }
    cultural_affinity = models.JSONField(default=dict)

    # Engagement metrics
    sessions_per_week = models.FloatField(default=0)
    avg_session_duration_seconds = models.FloatField(default=0)

    # Computed timestamp
    last_computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_computed_at"]

    def __str__(self):
        return f"Profile for {self.user.email}"


class SessionIntent(BaseModel):
    """Real-time session intent tracking (ephemeral, in-cache primarily)."""
    session_id = models.CharField(max_length=100, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    # Intent signals (updated in real-time)
    add_to_cart_count = models.PositiveIntegerField(default=0)
    click_count = models.PositiveIntegerField(default=0)
    search_count = models.PositiveIntegerField(default=0)
    product_depth_views = models.PositiveIntegerField(default=0)
    total_actions = models.PositiveIntegerField(default=0)

    # Timestamps
    first_action_at = models.DateTimeField(auto_now_add=True)
    last_action_at = models.DateTimeField(auto_now=True)

    # Computed score (0-1)
    intent_score = models.FloatField(default=0.0, validators=[MinValueValidator(0), MaxValueValidator(1)])

    class Meta:
        ordering = ["-last_action_at"]

    def __str__(self):
        return f"Intent {self.intent_score:.2f} for {self.session_id[:8]}"


# =====================================================
# 2. Product Scoring Engine
# =====================================================

class ProductScore(BaseModel):
    """
    Computed Urbana Score for every product.
    Nightly batch + incremental updates on key events.
    """
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="urbana_score",
    )

    # Component scores (0-1)
    engagement_score = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    conversion_score = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    retention_score = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    freshness_score = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    designer_score = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])
    fit_confidence = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])

    # Final composite score
    urbana_score = models.FloatField(default=0, validators=[MinValueValidator(0), MaxValueValidator(1)])

    # Trend detection
    trend_score = models.FloatField(default=0)
    is_trending = models.BooleanField(default=False)

    # Exploration allocation
    exploration_weight = models.FloatField(default=0.1)

    # Computed timestamp
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-urbana_score"]
        indexes = [
            models.Index(fields=["urbana_score", "computed_at"]),
            models.Index(fields=["is_trending", "trend_score"]),
        ]

    def __str__(self):
        return f"{self.product.name}: {self.urbana_score:.3f}"


class AlgorithmConfig(BaseModel):
    """Singleton configuration for algorithm weights and stage."""

    class MarketStage(models.TextChoices):
        EARLY = "early", "Early"
        GROWTH = "growth", "Growth"
        SCALE = "scale", "Scale"

    # Singleton enforcement
    is_active = models.BooleanField(default=True, unique=True)

    market_stage = models.CharField(
        max_length=10,
        choices=MarketStage.choices,
        default=MarketStage.GROWTH,
    )

    # Dynamic weights (must sum to ~1.0)
    weight_engagement = models.FloatField(default=0.20)
    weight_conversion = models.FloatField(default=0.40)
    weight_retention = models.FloatField(default=0.10)
    weight_freshness = models.FloatField(default=0.15)
    weight_designer = models.FloatField(default=0.10)
    weight_fit = models.FloatField(default=0.05)

    # Exploration settings
    exploration_ratio = models.FloatField(default=0.25)  # 25% slots for exploration
    new_product_boost_days = models.PositiveIntegerField(default=7)

    # Trend detection thresholds
    trending_sigma_threshold = models.FloatField(default=2.0)
    trend_window_hours = models.PositiveIntegerField(default=48)

    class Meta:
        verbose_name = "Algorithm Configuration"
        verbose_name_plural = "Algorithm Configurations"

    def __str__(self):
        return f"Config [{self.market_stage}]"

    def get_weights(self):
        return {
            "engagement": self.weight_engagement,
            "conversion": self.weight_conversion,
            "retention": self.weight_retention,
            "freshness": self.weight_freshness,
            "designer": self.weight_designer,
            "fit": self.weight_fit,
        }

    @classmethod
    def get_active(cls):
        """Return the active singleton config, creating one if needed."""
        obj, _ = cls.objects.get_or_create(is_active=True, defaults={})
        return obj


# =====================================================
# 3. Designer Intelligence Engine
# =====================================================

class DesignerScore(BaseModel):
    """
    Operational Designer Score + Business Health Score.
    Updated via order/return/review signals.
    """
    designer = models.OneToOneField(
        Designer,
        on_delete=models.CASCADE,
        related_name="score",
    )

    # Operational scores (0-1)
    delivery_speed_score = models.FloatField(default=0.5)
    fulfillment_rate_score = models.FloatField(default=0.5)
    customer_rating_score = models.FloatField(default=0.5)
    responsiveness_score = models.FloatField(default=0.5)

    # Composite operational score
    operational_score = models.FloatField(default=0.5)

    # Business Health Score components
    sales_growth_mom = models.FloatField(default=0)  # Month-over-month
    inventory_stability = models.FloatField(default=0.5)
    content_quality = models.FloatField(default=0.5)
    responsiveness_trend = models.FloatField(default=0)

    # Composite BHS
    business_health_score = models.FloatField(default=0.5)

    # Lifecycle stage
    lifecycle_stage = models.CharField(
        max_length=20,
        default="new",
        choices=[
            ("new", "New"),
            ("growing", "Growing"),
            ("top", "Top"),
        ],
    )

    # Penalty multipliers (1.0 = no penalty)
    penalty_multiplier = models.FloatField(default=1.0)
    penalty_reason = models.JSONField(default=list, blank=True)
    penalty_expires_at = models.DateTimeField(null=True, blank=True)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-business_health_score"]

    def __str__(self):
        return f"{self.designer.user.username}: BHS={self.business_health_score:.2f}"

    def is_penalized(self):
        if self.penalty_multiplier < 1.0 and self.penalty_expires_at:
            return timezone.now() < self.penalty_expires_at
        return False


# =====================================================
# 4. Trend Detection Engine
# =====================================================

class ProductTrendSnapshot(BaseModel):
    """Hourly snapshots of product engagement for trend velocity calculation."""
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="trend_snapshots",
    )
    hour = models.DateTimeField(db_index=True)

    clicks = models.PositiveIntegerField(default=0)
    views = models.PositiveIntegerField(default=0)
    saves = models.PositiveIntegerField(default=0)
    purchases = models.PositiveIntegerField(default=0)
    shares = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("product", "hour")
        ordering = ["-hour"]

    def __str__(self):
        return f"{self.product.name} @ {self.hour.strftime('%Y-%m-%d %H')}: clicks={self.clicks}"


# =====================================================
# 5. Category Balancing System
# =====================================================

class CategoryBalance(BaseModel):
    """Weekly computed supply/demand balance per category."""
    category = models.OneToOneField(
        "core.Category",
        on_delete=models.CASCADE,
        related_name="balance",
    )

    # Demand: browse + purchase counts
    demand_score = models.FloatField(default=0)
    # Supply: active product count
    supply_score = models.FloatField(default=0)

    # Saturation index
    saturation_index = models.FloatField(default=1.0)

    # Exposure multiplier for ranking (1.0 = normal, <1 = reduce, >1 = boost)
    exposure_multiplier = models.FloatField(default=1.0)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-saturation_index"]

    def __str__(self):
        return f"{self.category.name}: CSI={self.saturation_index:.2f}"


# =====================================================
# 6. Anti-Gaming / Anomaly Detection
# =====================================================

class AnomalyLog(BaseModel):
    """Flags suspicious patterns for anti-gaming."""

    class AnomalyType(models.TextChoices):
        RAPID_CLICKS = "rapid_clicks", "Rapid Sequential Clicks"
        REVIEW_BURST = "review_burst", "Review Burst"
        BOT_PATTERN = "bot_pattern", "Bot-Like Pattern"
        RATING_MANIPULATION = "rating_manipulation", "Rating Manipulation"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    session_id = models.CharField(max_length=100, blank=True)
    anomaly_type = models.CharField(max_length=30, choices=AnomalyType.choices)
    description = models.TextField()
    evidence = models.JSONField(default=dict)

    # Action taken
    score_suppressed = models.BooleanField(default=False)
    suppression_expires_at = models.DateTimeField(null=True, blank=True)

    reviewed = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_anomalies",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.anomaly_type} by {self.user or self.session_id[:8]}"
