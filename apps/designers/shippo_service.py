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


from shippo.models.components import ShipmentCreateRequest, AddressCreateRequest, ParcelCreateRequest

def get_shipping_rates(from_address: dict, to_address: dict, weight_kg: float, dimensions: dict = None) -> dict:
    """
    Creates a shipment object on Shippo to fetch rates.
    If Shippo API fails (e.g. 401 unauthorized, invalid key, or network issue),
    it falls back to a zone-based shipping cost calculation.
    """
    # 1. Clean addresses
    from_country = (from_address.get('country') or 'GH').upper().strip()
    to_country = (to_address.get('country') or 'US').upper().strip()
    
    # Defaults for dimensions if not provided
    dims = dimensions or {'length': '30.00', 'width': '20.00', 'height': '10.00'}
    
    # 2. Try Shippo API
    try:
        addr_from = AddressCreateRequest(
            name=from_address.get('name') or 'Urbana Designer',
            street1=from_address.get('street1') or '1 Fort Road',
            city=from_address.get('city') or 'Accra',
            state=from_address.get('state') or '',
            zip=from_address.get('postal_code') or '',
            country=from_country
        )
        
        addr_to = AddressCreateRequest(
            name=to_address.get('name') or 'Urbana Customer',
            street1=to_address.get('street1') or 'Main Street',
            city=to_address.get('city') or 'New York',
            state=to_address.get('state') or '',
            zip=to_address.get('postal_code') or '',
            country=to_country
        )
        
        parcel = ParcelCreateRequest(
            length=str(dims.get('length', '30.00')),
            width=str(dims.get('width', '20.00')),
            height=str(dims.get('height', '10.00')),
            distance_unit='cm',
            weight=f"{float(weight_kg):.2f}",
            mass_unit='kg'
        )
        
        req = ShipmentCreateRequest(
            address_from=addr_from,
            address_to=addr_to,
            parcels=[parcel]
        )
        
        shipment = shippo_client.shipments.create(req)
        
        # Convert Shippo models to list of dict rates
        if shipment.rates:
            rates_list = []
            for rate in shipment.rates:
                rates_list.append({
                    "amount": float(rate.amount),
                    "currency": rate.currency,
                    "provider": rate.provider,
                    "service_level": rate.servicelevel.name if rate.servicelevel else 'Standard',
                    "estimated_days": rate.estimated_days or 5,
                    "source": "shippo"
                })
            return {
                "status": "success",
                "rates": rates_list
            }
    except Exception as e:
        # Log error in console/logs
        print(f"[Shippo API Warning] Dynamic rates lookup failed, using fallback logic. Details: {e}")
        
    # 3. Fallback Pricing Matrix (Zone-Based)
    # Determine the shipping zone
    if from_country == to_country:
        # Domestic shipping
        base_fee = 5.00
        per_kg_fee = 1.00
        service_name = "Domestic Standard"
        provider = "Local Carrier"
        estimated_days = 2
    elif from_country in ('NG', 'GH', 'KE', 'ZA') and to_country in ('NG', 'GH', 'KE', 'ZA'):
        # Intra-African cross-border
        base_fee = 15.00
        per_kg_fee = 3.00
        service_name = "Intra-Africa Express"
        provider = "DHL Africa"
        estimated_days = 4
    else:
        # International (US, EU, Rest of world)
        base_fee = 30.00
        per_kg_fee = 8.00
        service_name = "International Priority"
        provider = "DHL Express"
        estimated_days = 5
        
    total_fee = base_fee + (per_kg_fee * float(weight_kg))
    
    # Return a mocked standard rate structure matching Shippo format
    fallback_rates = [{
        "amount": round(total_fee, 2),
        "currency": "USD",
        "provider": provider,
        "service_level": service_name,
        "estimated_days": estimated_days,
        "source": "fallback"
    }]
    
    return {
        "status": "success",
        "rates": fallback_rates
    }


