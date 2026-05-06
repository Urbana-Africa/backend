"""
Celery tasks for Urbana Core Algorithm V2.0 batch processing.
"""

from celery import shared_task
from django.utils import timezone

from apps.algorithm.services import (
    DesignerIntelligenceEngine,
    PersonalisationEngine,
    ProductScoringEngine,
    TrendDetectionEngine,
)
from apps.algorithm.models import AlgorithmConfig, CategoryBalance
from apps.core.models import Category, Product
from apps.customers.models import OrderItem


@shared_task
def compute_all_product_scores():
    """Nightly task: recompute Urbana Score for all products."""
    config = AlgorithmConfig.get_active()
    count = ProductScoringEngine.compute_all(config=config)
    return {"status": "ok", "products_scored": count}


@shared_task
def compute_product_score(product_id: str):
    """Incremental update for a single product."""
    from apps.core.models import Product
    try:
        product = Product.objects.get(id=product_id)
        ProductScoringEngine.compute_all()
        return {"status": "ok", "product_id": product_id}
    except Product.DoesNotExist:
        return {"status": "error", "detail": "Product not found"}


@shared_task
def compute_designer_score(designer_id: str):
    """Incremental update for a single designer."""
    from apps.designers.models import Designer
    try:
        designer = Designer.objects.get(id=designer_id)
        DesignerIntelligenceEngine.compute_designer(designer)
        return {"status": "ok", "designer_id": designer_id}
    except Designer.DoesNotExist:
        return {"status": "error", "detail": "Designer not found"}


@shared_task
def compute_all_designer_scores():
    """Nightly task: recompute all designer scores."""
    DesignerIntelligenceEngine.compute_all()
    return {"status": "ok"}


@shared_task
def take_trend_snapshots():
    """Hourly task: capture engagement snapshots for trend detection."""
    TrendDetectionEngine.take_hourly_snapshot()
    return {"status": "ok"}


@shared_task
def recompute_user_profiles():
    """Nightly task: rebuild all user preference profiles."""
    from apps.authentication.models import User
    count = 0
    for user in User.objects.filter(is_active=True):
        try:
            PersonalisationEngine.recompute_profile(user)
            count += 1
        except Exception:
            continue
    return {"status": "ok", "profiles_recomputed": count}


@shared_task
def compute_category_balance():
    """Weekly task: recompute category saturation indices."""
    from django.db.models import Count
    from apps.algorithm.models import UserActivity

    for category in Category.objects.all():
        # Supply: active product count
        supply = Product.objects.filter(
            category=category, is_published=True, is_active=True
        ).count()

        # Demand: browse + purchase events in last 30 days
        last_30d = timezone.now() - timezone.timedelta(days=30)
        demand = UserActivity.objects.filter(
            product__category=category,
            created_at__gte=last_30d,
            event_type__in=["product_click", "purchase"],
        ).count()

        # OrderItem-based purchases as fallback
        purchase_demand = OrderItem.objects.filter(
            product__category=category, created_at__gte=last_30d
        ).count()
        demand += purchase_demand

        csi = supply / max(demand, 1)

        # Exposure multiplier
        if csi > 1.5:
            exposure = max(0.5, 1 - (csi - 1.5) * 0.2)
        elif csi < 0.5:
            exposure = min(1.5, 1 + (0.5 - csi) * 0.5)
        else:
            exposure = 1.0

        CategoryBalance.objects.update_or_create(
            category=category,
            defaults={
                "supply_score": supply,
                "demand_score": demand,
                "saturation_index": csi,
                "exposure_multiplier": exposure,
            },
        )

    return {"status": "ok"}
