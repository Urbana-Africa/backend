"""
Urbana Core Algorithm V2.0 — Scoring & Ranking Services
"""

import math
import statistics
from datetime import timedelta
from typing import Dict, List, Optional

from django.utils import timezone
from django.db.models import Avg, Count, F, Q, Sum
from django.core.cache import cache

from apps.algorithm.models import (
    AlgorithmConfig,
    CategoryBalance,
    DesignerScore,
    ProductScore,
    ProductTrendSnapshot,
    SessionIntent,
    UserActivity,
    UserPreferenceProfile,
)
from apps.core.models import Category, Product, Review
from apps.customers.models import OrderItem, ReturnRequest
from apps.designers.models import Designer, DesignerOrder, ShipmentTracking


# =====================================================
# 1. Product Scoring Engine
# =====================================================

class ProductScoringEngine:
    """Computes Urbana Score for products."""

    FRESHNESS_BOOST_DAYS = 7
    CACHE_KEY_PREFIX = "product_score"
    CACHE_TTL = 3600  # 1 hour

    @classmethod
    def compute_all(cls, config: Optional[AlgorithmConfig] = None):
        """Batch recompute all product scores. Called nightly."""
        if config is None:
            config = AlgorithmConfig.get_active()

        weights = config.get_weights()
        now = timezone.now()
        boost_cutoff = now - timedelta(days=config.new_product_boost_days)

        # Preload aggregates
        products = Product.objects.filter(is_published=True, is_active=True)
        scores_to_update = []

        for product in products.prefetch_related(
            "reviews", "order_items", "activities"
        ):
            ps, _ = ProductScore.objects.get_or_create(product=product)

            # 1. Engagement Score
            ps.engagement_score = cls._compute_engagement(product)

            # 2. Conversion Score
            ps.conversion_score = cls._compute_conversion(product)

            # 3. Retention Score
            ps.retention_score = cls._compute_retention(product)

            # 4. Freshness Score
            ps.freshness_score = cls._compute_freshness(product, boost_cutoff)

            # 5. Designer Score (from Designer Intelligence Engine)
            try:
                ds = DesignerScore.objects.get(designer=product.user.designer_profile)
                ps.designer_score = ds.operational_score
            except Exception:
                ps.designer_score = 0.5

            # 6. Fit Confidence
            ps.fit_confidence = cls._compute_fit_confidence(product)

            # Composite Urbana Score
            ps.urbana_score = (
                weights["engagement"] * ps.engagement_score
                + weights["conversion"] * ps.conversion_score
                + weights["retention"] * ps.retention_score
                + weights["freshness"] * ps.freshness_score
                + weights["designer"] * ps.designer_score
                + weights["fit"] * ps.fit_confidence
            )

            # Trend detection
            ps.trend_score = TrendDetectionEngine.compute_trend_score(product)
            ps.is_trending = ps.trend_score > config.trending_sigma_threshold

            ps.computed_at = now
            scores_to_update.append(ps)

        ProductScore.objects.bulk_update(
            scores_to_update,
            [
                "engagement_score",
                "conversion_score",
                "retention_score",
                "freshness_score",
                "designer_score",
                "fit_confidence",
                "urbana_score",
                "trend_score",
                "is_trending",
                "computed_at",
            ],
            batch_size=500,
        )

        return len(scores_to_update)

    @classmethod
    def _compute_engagement(cls, product: Product) -> float:
        """Engagement based on CTR, time-on-product, saves."""
        last_30d = timezone.now() - timedelta(days=30)
        activities = UserActivity.objects.filter(
            product=product, created_at__gte=last_30d
        )

        views = activities.filter(event_type=UserActivity.EventType.PRODUCT_VIEW).count()
        clicks = activities.filter(event_type=UserActivity.EventType.PRODUCT_CLICK).count()
        saves = activities.filter(event_type=UserActivity.EventType.WISHLIST_ADD).count()
        shares = activities.filter(event_type=UserActivity.EventType.SHARE).count()

        # Time on product (stored in metadata as seconds)
        time_entries = [
            a.metadata.get("seconds", 0)
            for a in activities.filter(event_type=UserActivity.EventType.TIME_ON_PRODUCT)
        ]
        avg_time = sum(time_entries) / len(time_entries) if time_entries else 0

        # Normalized engagement score
        score = min(1.0, (
            min(clicks / max(views, 1), 0.5) * 0.4
            + min(saves / max(views, 1), 0.3) * 0.3
            + min(avg_time / 60, 1.0) * 0.2
            + min(shares / max(views, 1), 0.2) * 0.1
        ))
        return max(0.0, score)

    @classmethod
    def _compute_conversion(cls, product: Product) -> float:
        """Purchases / Clicks over last 30 days."""
        last_30d = timezone.now() - timedelta(days=30)
        clicks = UserActivity.objects.filter(
            product=product,
            event_type=UserActivity.EventType.PRODUCT_CLICK,
            created_at__gte=last_30d,
        ).count()
        purchases = OrderItem.objects.filter(
            product=product, created_at__gte=last_30d
        ).count()

        if clicks == 0:
            return 0.0
        return min(1.0, purchases / clicks)

    @classmethod
    def _compute_retention(cls, product: Product) -> float:
        """Repeat purchases, low return rate, good ratings."""
        # Rating
        avg_rating = Review.objects.filter(product=product, is_approved=True).aggregate(
            avg=Avg("rating")
        )["avg"] or 3.0
        rating_score = (avg_rating - 1) / 4  # normalize 1-5 to 0-1

        # Return rate
        total_items = OrderItem.objects.filter(product=product).count()
        returns = ReturnRequest.objects.filter(
            order_item__product=product
        ).count()
        return_rate = returns / max(total_items, 1)
        return_score = max(0, 1 - return_rate * 5)  # >20% returns = 0

        # Repeat purchase rate
        customers = (
            OrderItem.objects.filter(product=product)
            .values("order__customer")
            .annotate(buys=Count("id"))
        )
        repeat_count = sum(1 for c in customers if c["buys"] > 1)
        repeat_rate = repeat_count / max(len(customers), 1)

        return min(1.0, rating_score * 0.4 + return_score * 0.4 + repeat_rate * 0.2)

    @classmethod
    def _compute_freshness(cls, product: Product, boost_cutoff) -> float:
        """New products get a boost that decays over time."""
        age_days = (timezone.now() - product.created_at).days
        if age_days <= 7:
            return 1.0 - (age_days / 7) * 0.3  # 1.0 -> 0.7
        return max(0.3, 1.0 - (age_days / 90))  # linear decay over 90 days

    @classmethod
    def _compute_fit_confidence(cls, product: Product) -> float:
        """Predict size accuracy based on return reasons."""
        wrong_size_returns = ReturnRequest.objects.filter(
            order_item__product=product, reason=ReturnRequest.Reason.WRONG_SIZE
        ).count()
        total_returns = ReturnRequest.objects.filter(
            order_item__product=product
        ).count()

        if total_returns == 0:
            return 0.7  # neutral if no data

        fit_error_rate = wrong_size_returns / total_returns
        return max(0.1, 1.0 - fit_error_rate * 2)

    @classmethod
    def get_score(cls, product_id: str) -> float:
        cache_key = f"{cls.CACHE_KEY_PREFIX}:{product_id}"
        score = cache.get(cache_key)
        if score is not None:
            return score
        try:
            ps = ProductScore.objects.get(product_id=product_id)
            cache.set(cache_key, ps.urbana_score, cls.CACHE_TTL)
            return ps.urbana_score
        except ProductScore.DoesNotExist:
            return 0.0


# =====================================================
# 2. Designer Intelligence Engine
# =====================================================

class DesignerIntelligenceEngine:
    """Computes Designer Operational Score and BHS."""

    @classmethod
    def compute_all(cls):
        """Recompute scores for all designers."""
        for designer in Designer.objects.all():
            cls.compute_designer(designer)

    @classmethod
    def compute_designer(cls, designer: Designer):
        ds, _ = DesignerScore.objects.get_or_create(designer=designer)
        now = timezone.now()
        last_30d = now - timedelta(days=30)
        last_60d = now - timedelta(days=60)

        # Delivery speed: % orders delivered within SLA (e.g., 7 days)
        orders = DesignerOrder.objects.filter(
            order_item__product__user=designer.user, created_at__gte=last_30d
        )
        total_orders = orders.count()
        on_time = orders.filter(
            status="delivered",
            order_item__delivered_at__lte=F("created_at") + timedelta(days=7),
        ).count()
        ds.delivery_speed_score = on_time / max(total_orders, 1)

        # Fulfillment rate: % orders fulfilled (not cancelled)
        fulfilled = total_orders - orders.filter(status="cancelled").count()
        ds.fulfillment_rate_score = fulfilled / max(total_orders, 1)

        # Customer rating: average of product reviews
        avg_rating = Review.objects.filter(
            product__user=designer.user, is_approved=True
        ).aggregate(avg=Avg("rating"))["avg"] or 3.0
        ds.customer_rating_score = (avg_rating - 1) / 4

        # Responsiveness: designer reply time (mock if no messaging system yet)
        ds.responsiveness_score = 0.7  # default until messaging is built

        # Operational score
        ds.operational_score = (
            ds.delivery_speed_score * 0.3
            + ds.fulfillment_rate_score * 0.3
            + ds.customer_rating_score * 0.25
            + ds.responsiveness_score * 0.15
        )

        # BHS - Sales Growth (MoM)
        sales_this_month = OrderItem.objects.filter(
            product__user=designer.user,
            created_at__gte=last_30d,
        ).count()
        sales_last_month = OrderItem.objects.filter(
            product__user=designer.user,
            created_at__gte=last_60d,
            created_at__lt=last_30d,
        ).count()
        if sales_last_month > 0:
            ds.sales_growth_mom = (sales_this_month - sales_last_month) / sales_last_month
        else:
            ds.sales_growth_mom = 0

        # Inventory stability (stockout rate inverse)
        low_stock = sum(
            1 for p in designer.products.all() if p.stock <= 3
        )
        total_products = designer.products.count()
        ds.inventory_stability = 1 - (low_stock / max(total_products, 1))

        # Content quality (media completeness)
        products_with_media = sum(
            1 for p in designer.products.all() if p.product.media.exists()
        )
        ds.content_quality = products_with_media / max(total_products, 1)

        # BHS composite
        ds.business_health_score = min(1.0, max(0.0, (
            ds.sales_growth_mom * 0.3
            + ds.inventory_stability * 0.25
            + ds.content_quality * 0.25
            + ds.operational_score * 0.2
        )))

        # Lifecycle stage
        total_sales = OrderItem.objects.filter(
            product__user=designer.user
        ).count()
        if total_sales < 10:
            ds.lifecycle_stage = "new"
        elif total_sales < 100:
            ds.lifecycle_stage = "growing"
        else:
            ds.lifecycle_stage = "top"

        # Apply penalty if expired
        if ds.penalty_expires_at and now >= ds.penalty_expires_at:
            ds.penalty_multiplier = 1.0
            ds.penalty_reason = []
            ds.penalty_expires_at = None

        ds.computed_at = now
        ds.save()
        return ds

    @classmethod
    def apply_penalty(cls, designer: Designer, reason: str, severity: str = "medium"):
        ds, _ = DesignerScore.objects.get_or_create(designer=designer)
        multipliers = {"low": 0.8, "medium": 0.6, "high": 0.4}
        ds.penalty_multiplier = multipliers.get(severity, 0.6)
        ds.penalty_reason.append(reason)
        ds.penalty_expires_at = timezone.now() + timedelta(days=30)
        ds.save()
        return ds


# =====================================================
# 3. Trend Detection Engine
# =====================================================

class TrendDetectionEngine:
    """Detects trending products using velocity metrics."""

    @classmethod
    def compute_trend_score(cls, product: Product) -> float:
        """Compare last 48h engagement to baseline (30-day average)."""
        now = timezone.now()
        window_48h = now - timedelta(hours=48)
        baseline_start = now - timedelta(days=30)

        # Current window
        current_clicks = UserActivity.objects.filter(
            product=product,
            event_type=UserActivity.EventType.PRODUCT_CLICK,
            created_at__gte=window_48h,
        ).count()
        current_purchases = OrderItem.objects.filter(
            product=product, created_at__gte=window_48h
        ).count()

        # Baseline: average per 48h over last 30 days
        total_days = 30
        total_clicks = UserActivity.objects.filter(
            product=product,
            event_type=UserActivity.EventType.PRODUCT_CLICK,
            created_at__gte=baseline_start,
        ).count()
        total_purchases = OrderItem.objects.filter(
            product=product, created_at__gte=baseline_start
        ).count()

        baseline_clicks = total_clicks / max(total_days / 2, 1)
        baseline_purchases = total_purchases / max(total_days / 2, 1)

        # Velocity ratios
        click_velocity = current_clicks / max(baseline_clicks, 1)
        purchase_velocity = current_purchases / max(baseline_purchases, 1)

        return click_velocity * 0.6 + purchase_velocity * 0.4

    @classmethod
    def take_hourly_snapshot(cls):
        """Called every hour to capture engagement counts."""
        now = timezone.now()
        hour = now.replace(minute=0, second=0, microsecond=0)
        last_hour = hour - timedelta(hours=1)

        for product in Product.objects.filter(is_published=True):
            clicks = UserActivity.objects.filter(
                product=product,
                event_type=UserActivity.EventType.PRODUCT_CLICK,
                created_at__gte=last_hour,
                created_at__lt=hour,
            ).count()
            views = UserActivity.objects.filter(
                product=product,
                event_type=UserActivity.EventType.PRODUCT_VIEW,
                created_at__gte=last_hour,
                created_at__lt=hour,
            ).count()
            saves = UserActivity.objects.filter(
                product=product,
                event_type=UserActivity.EventType.WISHLIST_ADD,
                created_at__gte=last_hour,
                created_at__lt=hour,
            ).count()
            purchases = OrderItem.objects.filter(
                product=product, created_at__gte=last_hour, created_at__lt=hour
            ).count()
            shares = UserActivity.objects.filter(
                product=product,
                event_type=UserActivity.EventType.SHARE,
                created_at__gte=last_hour,
                created_at__lt=hour,
            ).count()

            ProductTrendSnapshot.objects.update_or_create(
                product=product, hour=hour,
                defaults={
                    "clicks": clicks, "views": views, "saves": saves,
                    "purchases": purchases, "shares": shares,
                },
            )


# =====================================================
# 4. Session Intent Engine
# =====================================================

class SessionIntentEngine:
    """Real-time session intent scoring."""

    @classmethod
    def record_action(cls, session_id: str, action_type: str, user=None):
        si, _ = SessionIntent.objects.get_or_create(
            session_id=session_id, defaults={"user": user}
        )
        si.total_actions += 1
        si.click_count += 1 if action_type in ("click", "product_click") else 0
        if action_type == "add_to_cart":
            si.add_to_cart_count += 1
        elif action_type == "search":
            si.search_count += 1
        elif action_type == "depth_view":
            si.product_depth_views += 1
        si.save()
        cls.compute_intent(si)
        return si

    @classmethod
    def compute_intent(cls, si: SessionIntent) -> float:
        """Compute SIS from session signals."""
        duration_min = max(
            (timezone.now() - si.first_action_at).total_seconds() / 60, 1
        )
        actions_per_min = si.total_actions / duration_min

        # Normalized components
        cart_freq = min(si.add_to_cart_count / max(si.total_actions, 1), 1.0)
        speed = min(actions_per_min / 10, 1.0)  # cap at 10 actions/min
        search_weight = 0.15 if si.search_count > 0 else 0.0
        depth_weight = min(si.product_depth_views / max(si.total_actions, 1), 0.3)

        score = cart_freq * 0.4 + speed * 0.2 + search_weight + depth_weight
        si.intent_score = min(1.0, max(0.0, score))
        si.save(update_fields=["intent_score"])
        return si.intent_score

    @classmethod
    def get_intent_level(cls, score: float) -> str:
        if score > 0.7:
            return "high"
        elif score >= 0.4:
            return "medium"
        return "low"


# =====================================================
# 5. Personalisation Engine
# =====================================================

class PersonalisationEngine:
    """Matches products to user profiles for ranking."""

    @classmethod
    def compute_match(cls, product: Product, user=None, profile: Optional[UserPreferenceProfile] = None) -> float:
        """Return personalisation match score (0-1) for a product."""
        if user is None or not user.is_authenticated:
            return 0.5  # neutral for guests

        if profile is None:
            try:
                profile = UserPreferenceProfile.objects.get(user=user)
            except UserPreferenceProfile.DoesNotExist:
                return 0.5

        scores = []

        # Style match (category affinity)
        if product.category_id and profile.category_affinity:
            cat_weight = profile.category_affinity.get(str(product.category_id), 0)
            scores.append(min(1.0, cat_weight))

        # Price match
        if product.price and profile.preferred_price_max and profile.preferred_price_min:
            preferred_mid = (profile.preferred_price_min + profile.preferred_price_max) / 2
            if preferred_mid > 0:
                price_match = 1 - abs(float(product.price) - float(preferred_mid)) / float(preferred_mid)
                scores.append(max(0, price_match))

        # Culture match
        if product.origin and profile.cultural_affinity:
            culture_match = profile.cultural_affinity.get(product.origin, 0)
            scores.append(min(1.0, culture_match))

        if not scores:
            return 0.5
        return sum(scores) / len(scores)

    @classmethod
    def recompute_profile(cls, user):
        """Nightly batch: rebuild UserPreferenceProfile from activity history."""
        profile, _ = UserPreferenceProfile.objects.get_or_create(user=user)
        last_90d = timezone.now() - timedelta(days=90)

        activities = UserActivity.objects.filter(
            user=user, created_at__gte=last_90d
        )

        # Category affinity
        category_counts = {}
        for a in activities.filter(product__isnull=False):
            cat_id = str(a.product.category_id)
            category_counts[cat_id] = category_counts.get(cat_id, 0) + 1
        total = sum(category_counts.values()) or 1
        profile.category_affinity = {
            k: round(v / total, 3) for k, v in category_counts.items()
        }

        # Price preference
        purchases = activities.filter(
            event_type=UserActivity.EventType.PURCHASE, product__isnull=False
        )
        prices = [float(a.product.price) for a in purchases if a.product.price]
        if prices:
            profile.preferred_price_min = min(prices)
            profile.preferred_price_max = max(prices)

        # Cultural affinity
        origin_counts = {}
        for a in activities.filter(product__origin__isnull=False):
            origin_counts[a.product.origin] = origin_counts.get(a.product.origin, 0) + 1
        total_origins = sum(origin_counts.values()) or 1
        profile.cultural_affinity = {
            k: round(v / total_origins, 3) for k, v in origin_counts.items()
        }

        # Size profile
        size_counts = {}
        for a in activities.filter(
            event_type=UserActivity.EventType.PURCHASE, metadata__size__isnull=False
        ):
            size = a.metadata.get("size")
            if size:
                cat = str(a.product.category_id) if a.product else "all"
                size_counts[cat] = size
        profile.size_profile = size_counts

        # Engagement
        from django.db.models import Count
        sessions = (
            activities.values("session_id")
            .annotate(count=Count("id"))
            .distinct()
        )
        profile.sessions_per_week = len(sessions) / 12  # 90 days ≈ 12 weeks
        profile.save()
        return profile


# =====================================================
# 6. Ranking Engine
# =====================================================

class RankingEngine:
    """Generates the final personalised feed."""

    @classmethod
    def get_feed(
        cls,
        user=None,
        session_id: str = "",
        category_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        exclude_product_id=None,
    ) -> List[Dict]:
        """
        Return a ranked list of products for the feed.
        70-80% exploitation (high final score), 20-30% exploration (new/unseen).
        """
        config = AlgorithmConfig.get_active()
        si = SessionIntent.objects.filter(session_id=session_id).first()
        intent_score = si.intent_score if si else 0.5
        intent_level = SessionIntentEngine.get_intent_level(intent_score)

        # Base queryset
        qs = Product.objects.filter(
            is_published=True, is_active=True
        ).select_related("category", "user")
        if category_id:
            qs = qs.filter(category_id=category_id)
        if exclude_product_id:
            qs = qs.exclude(id=exclude_product_id)

        # Fetch scores
        product_scores = {
            ps.product_id: ps for ps in ProductScore.objects.filter(product__in=qs)
        }
        profile = None
        if user and user.is_authenticated:
            try:
                profile = UserPreferenceProfile.objects.get(user=user)
            except UserPreferenceProfile.DoesNotExist:
                pass

        # Geo factor (simple version: prefer same region)
        geo_factor_map = {}
        if user and hasattr(user, "customer_profile"):
            user_country = user.customer_profile.addresses.filter(
                is_default=True
            ).first()
            user_country = user_country.country if user_country else None
            for p in qs:
                designer_country = p.user.designer_profile.country if hasattr(p.user, "designer_profile") else None
                if user_country and designer_country and user_country == designer_country:
                    geo_factor_map[p.id] = 1.2
                else:
                    geo_factor_map[p.id] = 1.0

        ranked = []
        for product in qs:
            ps = product_scores.get(product.id)
            if not ps:
                continue

            # Base score
            base_score = ps.urbana_score

            # Personalisation
            personalisation = PersonalisationEngine.compute_match(
                product, user, profile
            )

            # Geo factor
            geo = geo_factor_map.get(product.id, 1.0)

            # Trend boost
            trend = 1.0 + (ps.trend_score * 0.2) if ps.is_trending else 1.0

            # Final score
            final_score = base_score * personalisation * intent_score * geo * trend

            # Designer lifecycle boost
            try:
                ds = DesignerScore.objects.get(designer=product.user.designer_profile)
                if ds.lifecycle_stage == "new":
                    final_score *= 1.2
                elif ds.is_penalized():
                    final_score *= ds.penalty_multiplier
            except Exception:
                pass

            ranked.append({
                "product": product,
                "final_score": final_score,
                "urbana_score": ps.urbana_score,
                "is_trending": ps.is_trending,
                "intent_level": intent_level,
            })

        # Sort by final score
        ranked.sort(key=lambda x: x["final_score"], reverse=True)

        # Exploration: inject underexposed/new products
        exploitation_count = int(limit * (1 - config.exploration_ratio))
        exploration_count = limit - exploitation_count

        exploited = ranked[:exploitation_count]
        # Exploration: pick products with lower scores but high freshness
        explorers = [r for r in ranked[exploitation_count:] if r["urbana_score"] < 0.4]
        explorers = sorted(explorers, key=lambda x: x["urbana_score"], reverse=True)[:exploration_count]

        # Interleave (exploitation first, then exploration)
        final_feed = exploited + explorers

        return final_feed[offset:offset + limit]

    @classmethod
    def get_trending(cls, limit: int = 20) -> List[Dict]:
        """Return top trending products."""
        trending = ProductScore.objects.filter(
            is_trending=True
        ).select_related("product").order_by("-trend_score")[:limit]
        return [{"product": t.product, "trend_score": t.trend_score} for t in trending]
