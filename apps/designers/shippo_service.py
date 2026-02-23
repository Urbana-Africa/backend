# designers/shippo_service.py

import shippo
from django.conf import settings

# Initialize the client once (recommended)
shippo_client = shippo.Shippo(
    api_key_header=settings.SHIPPO_API_KEY,   # most common in recent versions
    # api_key=settings.SHIPPO_API_KEY,        # sometimes accepted too — check your version
)

# designers/shippo_service.py
import shippo
from django.conf import settings

# Initialize once (recommended; use api_key_header for current SDK)
shippo_client = shippo.Shippo(api_key_header=settings.SHIPPO_API_KEY)


def create_or_fetch_tracking(carrier: str, tracking_number: str) -> dict:
    """
    Registers a tracking request with Shippo (creates if new, fetches/updates existing).
    Uses test tracking number in non-prod environments.
    Returns a normalized dict based on the Track response.
    """
    carrier = carrier.lower().strip()  # Shippo expects lowercase like 'usps', 'ups'

    # Use test number in dev/staging for reliable testing
    effective_tracking_number = (
        tracking_number if settings.ENV == 'prod' else 'SHIPPO_TRANSIT'
    )

    try:
        track = shippo_client.tracking_status.create(
            carrier=carrier,
            tracking_number=effective_tracking_number,
            # Optional but recommended: helps correlate in your system
            # metadata=f"Order #{your_order_id}",
        )

        # Safest: convert model to dict (most recent SDK versions support .model_dump() or .dict())
        # Fallback to dict() if needed
        if hasattr(track, "model_dump"):
            track_dict = track.model_dump()
        elif hasattr(track, "dict"):
            track_dict = track.dict()
        else:
            track_dict = track  # older style or plain attrs

        # Extract key fields (handle possible None values safely)
        current_status = track.tracking_status.status if track.tracking_status else None
        return {
            "status": current_status,  # e.g. 'TRANSIT', 'DELIVERED', 'UNKNOWN'
            "eta": track.eta,          # datetime or None
            "tracking_history": [
                {
                    "status": status.status,
                    "status_details": status.status_details,
                    "status_date": status.status_date,
                    "location": (
                        {
                            "city": status.location.city,
                            "state": status.location.state,
                            "country": status.location.country,
                            "zip": status.location.zip,
                        }
                        if status.location
                        else None
                    ),
                }
                for status in track.tracking_history
            ] if track.tracking_history else [],
            # "raw": track.__dict__,  # full serialized response for debugging
            # Useful extras
            "carrier": track.carrier,
            "tracking_number": track.tracking_number,
            "object_created": track.tracking_status.object_created,
            "object_updated": track.tracking_status.object_updated,
            "messages": track.messages,  # any warnings/errors from carrier
        }

    # except ApiException as e:
    #     # Shippo API errors (e.g. invalid carrier, auth failure, rate limit)
    #     return {
    #         "status": "error",
    #         "error": f"Shippo API error: {e.status} - {e.message or str(e)}",
    #         "raw": None,
    #     }
    except Exception as e:
        # Network, unexpected, etc.
        print(f"Unexpected tracking error: {e}")  # log for dev
        return {
            "status": "error",
            "error": f"Unexpected error: {str(e)}",
            "raw": None,
        }

