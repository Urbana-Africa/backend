"""
Django signals to trigger incremental algorithm updates.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.algorithm.services import (
    DesignerIntelligenceEngine,
    ProductScoringEngine,
    SessionIntentEngine,
)
from apps.algorithm.models import UserActivity
from apps.core.models import Review
from apps.customers.models import OrderItem, ReturnRequest
from apps.designers.models import DesignerOrder


@receiver(post_save, sender=UserActivity)
def on_activity_save(sender, instance, created, **kwargs):
    """Update session intent on every activity."""
    if created and instance.session_id:
        SessionIntentEngine.record_action(
            session_id=instance.session_id,
            action_type=instance.event_type,
            user=instance.user,
        )


@receiver(post_save, sender=OrderItem)
def on_order_item_save(sender, instance, created, **kwargs):
    """Recompute product conversion + designer scores on purchase."""
    if created and instance.product:
        # Fire async task for heavy computation
        try:
            from celery import current_app
            current_app.send_task("apps.algorithm.tasks.compute_product_score", args=[instance.product_id])
            if instance.designer:
                current_app.send_task("apps.algorithm.tasks.compute_designer_score", args=[instance.designer_id])
        except Exception:
            # Fallback to synchronous if Celery not available
            ProductScoringEngine._compute_conversion(instance.product)


@receiver(post_save, sender=ReturnRequest)
def on_return_save(sender, instance, created, **kwargs):
    """Apply penalty and recompute scores on return."""
    if created:
        product = instance.order_item.product
        if product and product.user:
            try:
                designer = product.user.designer_profile
                # Check if return rate is high enough to trigger penalty
                from django.db.models import Count
                total_returns = ReturnRequest.objects.filter(
                    order_item__product__user=product.user
                ).count()
                total_items = OrderItem.objects.filter(
                    product__user=product.user
                ).count()
                return_rate = total_returns / max(total_items, 1)
                if return_rate > 0.15:
                    DesignerIntelligenceEngine.apply_penalty(
                        designer, f"High return rate: {return_rate:.1%}", severity="medium"
                    )
            except Exception:
                pass


@receiver(post_save, sender=Review)
def on_review_save(sender, instance, created, **kwargs):
    """Recompute product retention score on review."""
    if created and instance.product:
        try:
            from celery import current_app
            current_app.send_task("apps.algorithm.tasks.compute_product_score", args=[instance.product_id])
        except Exception:
            pass


@receiver(post_save, sender=DesignerOrder)
def on_designer_order_save(sender, instance, created, **kwargs):
    """Recompute designer delivery speed score."""
    if instance.status in ("delivered", "shipped") and instance.order_item and instance.order_item.product:
        try:
            from celery import current_app
            designer = instance.order_item.product.user.designer_profile
            current_app.send_task("apps.algorithm.tasks.compute_designer_score", args=[designer.id])
        except Exception:
            pass
