"""
Urbana Core Algorithm V2.0 — API Views
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status

from django.utils import timezone
from django.core.paginator import Paginator

from apps.algorithm.models import (
    AlgorithmConfig,
    AnomalyLog,
    CategoryBalance,
    ProductScore,
    SessionIntent,
    UserActivity,
    UserPreferenceProfile,
)
from apps.algorithm.services import (
    DesignerIntelligenceEngine,
    PersonalisationEngine,
    ProductScoringEngine,
    RankingEngine,
    SessionIntentEngine,
    TrendDetectionEngine,
)
from apps.core.models import Product
from apps.core.serializers import ProductSerializer


# =====================================================
# 1. Behavior Tracking
# =====================================================

class TrackEventView(APIView):
    """POST /core/track — Log a single or batch user interaction event."""
    permission_classes = [AllowAny]

    def post(self, request):
        events = request.data
        if isinstance(events, dict):
            events = [events]

        created = 0
        for event in events:
            user = request.user if request.user.is_authenticated else None
            session_id = event.get("session_id", "")
            if not session_id:
                continue

            activity = UserActivity.objects.create(
                user=user,
                session_id=session_id,
                event_type=event.get("event_type", ""),
                product_id=event.get("product_id") or None,
                designer_id=event.get("designer_id") or None,
                metadata=event.get("metadata", {}),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                ip_address=self._get_client_ip(request),
            )
            created += 1

            # Real-time session intent update
            SessionIntentEngine.record_action(
                session_id=session_id,
                action_type=event.get("event_type", ""),
                user=user,
            )

        return Response({"status": "ok", "events_logged": created})

    def _get_client_ip(self, request):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")


# =====================================================
# 2. Personalised Feed
# =====================================================

class FeedView(APIView):
    """GET /core/feed — Personalised product feed."""
    permission_classes = [AllowAny]

    def get(self, request):
        session_id = request.headers.get("X-Session-ID", "") or request.GET.get("session_id", "")
        category_id = request.GET.get("category")
        limit = min(int(request.GET.get("limit", 20)), 100)
        offset = int(request.GET.get("offset", 0))
        exclude_product = request.GET.get("exclude_product")

        user = request.user if request.user.is_authenticated else None

        feed = RankingEngine.get_feed(
            user=user,
            session_id=session_id,
            category_id=category_id,
            limit=limit,
            offset=offset,
            exclude_product_id=exclude_product,
        )

        # Serialize products
        products = [item["product"] for item in feed]
        serializer = ProductSerializer(products, many=True, context={"request": request})

        # Attach ranking metadata
        results = []
        for item, serialized in zip(feed, serializer.data):
            serialized["_ranking"] = {
                "final_score": round(item["final_score"], 4),
                "urbana_score": round(item["urbana_score"], 4),
                "is_trending": item["is_trending"],
                "intent_level": item["intent_level"],
            }
            results.append(serialized)

        # Return session intent for frontend adaptation
        intent = 0.5
        if session_id:
            si = SessionIntent.objects.filter(session_id=session_id).first()
            if si:
                intent = si.intent_score

        return Response({
            "results": results,
            "meta": {
                "intent_score": round(intent, 2),
                "intent_level": SessionIntentEngine.get_intent_level(intent),
                "count": len(results),
            },
        })


# =====================================================
# 3. Trending Products
# =====================================================

class TrendingView(APIView):
    """GET /core/trending — Top trending products."""
    permission_classes = [AllowAny]

    def get(self, request):
        limit = min(int(request.GET.get("limit", 20)), 50)
        trending = RankingEngine.get_trending(limit=limit)

        products = [item["product"] for item in trending]
        serializer = ProductSerializer(products, many=True, context={"request": request})

        results = []
        for item, serialized in zip(trending, serializer.data):
            serialized["_ranking"] = {"trend_score": round(item["trend_score"], 4)}
            results.append(serialized)

        return Response({"results": results, "count": len(results)})


# =====================================================
# 4. Recommendations ("For You")
# =====================================================

class RecommendationsView(APIView):
    """GET /core/recommendations — Personalised suggestions for authenticated users."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.GET.get("limit", 10)), 50)
        session_id = request.headers.get("X-Session-ID", "") or request.GET.get("session_id", "")

        feed = RankingEngine.get_feed(
            user=request.user,
            session_id=session_id,
            limit=limit,
        )

        products = [item["product"] for item in feed]
        serializer = ProductSerializer(products, many=True, context={"request": request})

        results = []
        for item, serialized in zip(feed, serializer.data):
            serialized["_ranking"] = {
                "final_score": round(item["final_score"], 4),
                "personalisation": round(
                    item["final_score"] / max(item["urbana_score"], 0.001), 2
                ),
            }
            results.append(serialized)

        return Response({"results": results, "count": len(results)})


# =====================================================
# 5. User Profile
# =====================================================

class UserProfileView(APIView):
    """GET /core/user-profile — Return computed preference profile."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            profile = UserPreferenceProfile.objects.get(user=request.user)
        except UserPreferenceProfile.DoesNotExist:
            return Response({"detail": "Profile not computed yet."}, status=404)

        return Response({
            "category_affinity": profile.category_affinity,
            "preferred_price_range": {
                "min": str(profile.preferred_price_min) if profile.preferred_price_min else None,
                "max": str(profile.preferred_price_max) if profile.preferred_price_max else None,
            },
            "discount_responsiveness": profile.discount_responsiveness,
            "size_profile": profile.size_profile,
            "cultural_affinity": profile.cultural_affinity,
            "engagement": {
                "sessions_per_week": profile.sessions_per_week,
                "avg_session_duration_seconds": profile.avg_session_duration_seconds,
            },
            "last_computed": profile.last_computed_at,
        })


# =====================================================
# 6. Admin / Algorithm Config
# =====================================================

class AlgorithmConfigView(APIView):
    """GET /manage/algorithm-config | PATCH — Admin algorithm settings."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        config = AlgorithmConfig.get_active()
        return Response({
            "market_stage": config.market_stage,
            "weights": config.get_weights(),
            "exploration_ratio": config.exploration_ratio,
            "new_product_boost_days": config.new_product_boost_days,
            "trending_sigma_threshold": config.trending_sigma_threshold,
            "trend_window_hours": config.trend_window_hours,
        })

    def patch(self, request):
        config = AlgorithmConfig.get_active()
        data = request.data

        if "market_stage" in data:
            config.market_stage = data["market_stage"]
            # Auto-adjust weights per stage
            stage_weights = {
                "early": {
                    "weight_engagement": 0.35,
                    "weight_conversion": 0.15,
                    "weight_retention": 0.10,
                    "weight_freshness": 0.25,
                    "weight_designer": 0.10,
                    "weight_fit": 0.05,
                },
                "growth": {
                    "weight_engagement": 0.20,
                    "weight_conversion": 0.40,
                    "weight_retention": 0.10,
                    "weight_freshness": 0.15,
                    "weight_designer": 0.10,
                    "weight_fit": 0.05,
                },
                "scale": {
                    "weight_engagement": 0.15,
                    "weight_conversion": 0.25,
                    "weight_retention": 0.35,
                    "weight_freshness": 0.10,
                    "weight_designer": 0.10,
                    "weight_fit": 0.05,
                },
            }
            for field, value in stage_weights.get(config.market_stage, {}).items():
                setattr(config, field, value)

        for field in [
            "exploration_ratio", "new_product_boost_days",
            "trending_sigma_threshold", "trend_window_hours",
        ]:
            if field in data:
                setattr(config, field, data[field])

        # Manual weight override
        for field in [
            "weight_engagement", "weight_conversion", "weight_retention",
            "weight_freshness", "weight_designer", "weight_fit",
        ]:
            if field in data:
                setattr(config, field, data[field])

        config.save()
        return Response({"status": "updated", "config": {
            "market_stage": config.market_stage,
            "weights": config.get_weights(),
        }})


# =====================================================
# 7. Category Balance (Admin)
# =====================================================

class CategoryBalanceView(APIView):
    """GET /manage/category-balance — Admin view of category saturation."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        balances = CategoryBalance.objects.select_related("category").all()
        return Response({
            "results": [
                {
                    "category_id": b.category.id,
                    "category_name": b.category.name,
                    "supply": b.supply_score,
                    "demand": b.demand_score,
                    "saturation_index": round(b.saturation_index, 2),
                    "exposure_multiplier": round(b.exposure_multiplier, 2),
                }
                for b in balances
            ]
        })


# =====================================================
# 8. Anomaly / Anti-Gaming (Admin)
# =====================================================

class AnomalyLogView(APIView):
    """GET /manage/anomalies — Admin view of flagged suspicious activity."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        limit = min(int(request.GET.get("limit", 50)), 200)
        reviewed = request.GET.get("reviewed")
        qs = AnomalyLog.objects.all().order_by("-created_at")
        if reviewed is not None:
            qs = qs.filter(reviewed=(reviewed.lower() == "true"))
        qs = qs[:limit]

        return Response({
            "results": [
                {
                    "id": a.id,
                    "anomaly_type": a.anomaly_type,
                    "user_id": a.user_id,
                    "session_id": a.session_id,
                    "description": a.description,
                    "score_suppressed": a.score_suppressed,
                    "reviewed": a.reviewed,
                    "created_at": a.created_at,
                }
                for a in qs
            ]
        })
