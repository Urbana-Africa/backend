# designers/shippo_service.py

import shippo
from django.conf import settings

# Safety buffer applied to shipping rates (15%) to prevent losses
# on actual shipping costs being higher than quoted at checkout.
SHIPPING_BUFFER_PERCENT = 0.15

# ── Country name → ISO-3166-1 alpha-2 code mapping ────────────
# Shippo requires 2-letter codes (e.g. "GH", "US", "NG").
# If your DB stores full names, we normalise them here.
_COUNTRY_NAME_TO_CODE = {
    "GHANA": "GH",
    "NIGERIA": "NG",
    "KENYA": "KE",
    "SOUTH AFRICA": "ZA",
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "USA": "US",
    "UNITED KINGDOM": "GB",
    "UK": "GB",
    "CANADA": "CA",
    "GERMANY": "DE",
    "FRANCE": "FR",
    "ITALY": "IT",
    "SPAIN": "ES",
    "NETHERLANDS": "NL",
    "BELGIUM": "BE",
    "SWITZERLAND": "CH",
    "AUSTRALIA": "AU",
    "CHINA": "CN",
    "JAPAN": "JP",
    "INDIA": "IN",
    "BRAZIL": "BR",
    "MEXICO": "MX",
    "UNITED ARAB EMIRATES": "AE",
    "SAUDI ARABIA": "SA",
    "EGYPT": "EG",
    "MOROCCO": "MA",
    "ETHIOPIA": "ET",
    "UGANDA": "UG",
    "TANZANIA": "TZ",
    "RWANDA": "RW",
    "SENEGAL": "SN",
    "IVORY COAST": "CI",
    "COTE D'IVOIRE": "CI",
    "CAMEROON": "CM",
    "ZAMBIA": "ZM",
    "ZIMBABWE": "ZW",
    "NAMIBIA": "NA",
    "BOTSWANA": "BW",
    "MALAWI": "MW",
    "MOZAMBIQUE": "MZ",
    "ANGOLA": "AO",
    "DEMOCRATIC REPUBLIC OF THE CONGO": "CD",
    "CONGO": "CG",
    "GABON": "GA",
    "TOGO": "TG",
    "BENIN": "BJ",
    "BURKINA FASO": "BF",
    "NIGER": "NE",
    "MALI": "ML",
    "GUINEA": "GN",
    "SIERRA LEONE": "SL",
    "LIBERIA": "LR",
    "GAMBIA": "GM",
    "GUINEA-BISSAU": "GW",
    "CAPE VERDE": "CV",
    "SEYCHELLES": "SC",
    "MAURITIUS": "MU",
    "MADAGASCAR": "MG",
}


def _normalise_country(value: str) -> str:
    """
    Convert a country name or code to a 2-letter ISO code.
    If already a 2-letter code, return as-is.
    """
    if not value:
        return "US"
    v = value.strip().upper()
    # Already a 2-letter code?
    if len(v) == 2 and v.isalpha():
        return v
    # Known full name?
    return _COUNTRY_NAME_TO_CODE.get(v, v[:2] if len(v) >= 2 else "US")

# ── Validate SHIPPO_API_KEY at startup ──────────────────────
if not settings.SHIPPO_API_KEY:
    import warnings
    warnings.warn(
        "SHIPPO_API_KEY is not set. Shipping rates will use fallback estimates. "
        "Add SHIPPO_API_KEY=shippo_test_xxx to your backend/.env file. "
        "Get your key at: https://portal.goshippo.com/api-config/api",
        RuntimeWarning,
        stacklevel=2,
    )
    shippo_client = None
else:
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

    if shippo_client is None:
        return {
            "status": "error",
            "error": "SHIPPO_API_KEY not configured. Tracking unavailable.",
            "raw": None,
        }

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


from shippo.models.components import ShipmentCreateRequest, AddressCreateRequest, ParcelCreateRequest, TransactionCreateRequest

def get_shipping_rates(from_address: dict, to_address: dict, weight_kg: float, dimensions: dict = None, local_shipping_fee: float = None) -> dict:
    """
    Creates a shipment object on Shippo to fetch rates.
    If Shippo API fails (e.g. 401 unauthorized, invalid key, or network issue),
    it falls back to a zone-based shipping cost calculation.
    """
    # 1. Clean addresses — normalise country names to 2-letter ISO codes
    from_country = _normalise_country(from_address.get('country') or 'GH')
    to_country = _normalise_country(to_address.get('country') or 'US')
    
    # If domestic shipping and a custom local shipping fee is specified, use it immediately
    if from_country == to_country and local_shipping_fee is not None and float(local_shipping_fee) > 0:
        return {
            "status": "success",
            "rates": [{
                "amount": float(local_shipping_fee),
                "currency": "USD",
                "provider": "Local Carrier",
                "service_level": "Domestic Custom",
                "estimated_days": 2,
                "source": "designer_custom"
            }]
        }
    
    # Defaults for dimensions if not provided
    dims = dimensions or {'length': '30.00', 'width': '20.00', 'height': '10.00'}

    # ── Helper: build fallback rates ──────────────────────
    def _build_fallback(w, fc, tc):
        if fc == tc:
            tiers = [
                {"provider": "Local Carrier", "service_level": "Domestic Economy", "base": 3.50, "per_kg": 0.80, "days": 5},
                {"provider": "Local Carrier", "service_level": "Domestic Standard", "base": 5.00, "per_kg": 1.00, "days": 2},
                {"provider": "Local Carrier", "service_level": "Domestic Express", "base": 8.00, "per_kg": 1.50, "days": 1},
            ]
        elif fc in ('NG', 'GH', 'KE', 'ZA') and tc in ('NG', 'GH', 'KE', 'ZA'):
            tiers = [
                {"provider": "DHL Africa", "service_level": "Intra-Africa Economy", "base": 10.00, "per_kg": 2.20, "days": 7},
                {"provider": "DHL Africa", "service_level": "Intra-Africa Standard", "base": 15.00, "per_kg": 3.00, "days": 4},
                {"provider": "DHL Africa", "service_level": "Intra-Africa Express", "base": 22.00, "per_kg": 4.50, "days": 2},
            ]
        else:
            tiers = [
                {"provider": "DHL Express", "service_level": "International Economy", "base": 18.00, "per_kg": 5.50, "days": 10},
                {"provider": "DHL Express", "service_level": "International Standard", "base": 30.00, "per_kg": 8.00, "days": 5},
                {"provider": "DHL Express", "service_level": "International Priority", "base": 45.00, "per_kg": 12.00, "days": 2},
            ]
        rates = []
        for t in tiers:
            fee = round((t["base"] + (t["per_kg"] * w)) * (1 + SHIPPING_BUFFER_PERCENT), 2)
            rates.append({"amount": fee, "currency": "USD", "provider": t["provider"],
                          "service_level": t["service_level"], "estimated_days": t["days"],
                          "source": "fallback_buffered"})
        return rates

    # ── Try Shippo API ────────────────────────────────────
    shippo_error = None

    if not settings.SHIPPO_API_KEY or shippo_client is None:
        shippo_error = {
            "type": "ConfigError",
            "message": "SHIPPO_API_KEY is not configured. Create backend/.env and add: SHIPPO_API_KEY=shippo_test_xxx",
            "status_code": None,
            "raw_response": None,
            "traceback": None,
        }
    else:
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

            # Capture Shippo messages (validation errors, carrier rejections, etc.)
            shippo_messages = []
            if hasattr(shipment, 'messages') and shipment.messages:
                for msg in shipment.messages:
                    shippo_messages.append(str(msg))

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

                for rate in rates_list:
                    rate["amount"] = round(rate["amount"] * (1 + SHIPPING_BUFFER_PERCENT), 2)
                    rate["source"] = "shippo_buffered"

                rates_list.sort(key=lambda r: r["amount"])

                return {
                    "status": "success",
                    "rates": rates_list,
                    "error": None,
                    "debug": {
                        "shippo_key_present": True,
                        "from_country": from_country,
                        "to_country": to_country,
                        "weight_kg": float(weight_kg),
                        "shippo_messages": shippo_messages,
                    }
                }
            else:
                shippo_error = {
                    "type": "NoRates",
                    "message": "Shippo returned no rates. Check that carriers are enabled in your Shippo dashboard.",
                    "status_code": None,
                    "raw_response": shippo_messages if shippo_messages else None,
                    "traceback": None,
                }
        except Exception as e:
            import traceback
            error_type = type(e).__name__
            error_msg = str(e)
            error_trace = traceback.format_exc()

            raw_response = None
            status_code = None
            if hasattr(e, 'body'):
                raw_response = e.body
            if hasattr(e, 'status'):
                status_code = e.status
            if hasattr(e, 'status_code'):
                status_code = e.status_code
            if hasattr(e, 'response') and e.response is not None:
                try:
                    raw_response = e.response.text if hasattr(e.response, 'text') else str(e.response)
                    status_code = e.response.status_code if hasattr(e.response, 'status_code') else status_code
                except Exception:
                    pass

            print(f"[Shippo API Error] Type: {error_type} | Status: {status_code} | Error: {error_msg}")
            print(error_trace)

            shippo_error = {
                "type": error_type,
                "message": error_msg,
                "status_code": status_code,
                "raw_response": raw_response,
                "traceback": error_trace,
            }

    # ── Fallback ───────────────────────────────────────────
    w = float(weight_kg)
    fallback_rates = _build_fallback(w, from_country, to_country)

    return {
        "status": "success",
        "rates": fallback_rates,
        "error": shippo_error,
        "debug": {
            "shippo_key_present": bool(settings.SHIPPO_API_KEY),
            "from_country": from_country,
            "from_country_original": from_address.get('country'),
            "to_country": to_country,
            "to_country_original": to_address.get('country'),
            "weight_kg": w,
        }
    }


def generate_label_for_order_item(order_item) -> dict:
    """
    Creates a shipment on Shippo and purchases the cheapest label for the given OrderItem.
    Uses the designer's origin address and the per-item shipping destination (supports
    multi-address orders). Uses privacy-masked details for the buyer.
    """
    # 1. Dev/Staging mock fallback to avoid credentials issue in non-prod
    if getattr(settings, 'ENV', 'dev') != 'prod' or not settings.SHIPPO_API_KEY:
        return {
            "status": "success",
            "label_url": "https://shippo-delivery-labels.s3.amazonaws.com/mock-label.pdf",
            "tracking_number": f"USPS-MOCK-{order_item.id}",
            "carrier": "USPS",
            "shippo_transaction_id": "tx_mock_12345"
        }

    try:
        # 2. Get designer (sender) address details from the item's designer profile
        designer = order_item.designer
        profile = getattr(designer, 'designer_profile', None)

        from_name = 'Urbana Designer'
        from_street = '1 Fort Road'
        from_city = 'Accra'
        from_state = ''
        from_zip = ''
        from_country = 'GH'
        from_phone = ''

        if profile:
            from_name = profile.brand_name or from_name
            from_city = profile.city or from_city
            from_country = (profile.country or from_country).upper().strip()
            from_phone = profile.phone or from_phone

        addr_from = AddressCreateRequest(
            name=from_name,
            street1=from_street,
            city=from_city,
            state=from_state,
            zip=from_zip,
            country=from_country,
            phone=from_phone
        )

        # 3. Get customer (recipient) address — use per-item address for multi-destination orders
        props = order_item.properties or {}
        per_item_addr_id = props.get('shipping_address_id')

        if per_item_addr_id:
            from apps.customers.models import Address
            try:
                shipping_address = Address.objects.get(id=per_item_addr_id)
            except Address.DoesNotExist:
                shipping_address = order_item.order.shipping_address
        else:
            shipping_address = order_item.order.shipping_address

        if not shipping_address:
            return {
                "status": "error",
                "error": "No shipping address associated with the order item."
            }

        addr_to = AddressCreateRequest(
            name=shipping_address.recipient_name or 'Urbana Customer',
            street1=shipping_address.line1,
            street2=shipping_address.line2 or '',
            city=shipping_address.city,
            state=shipping_address.state or '',
            zip=shipping_address.postal_code or '',
            country=(shipping_address.country or 'US').upper().strip(),
            phone=order_item.masked_phone or '',
            email=order_item.masked_email or ''
        )

        # 4. Get product parcel dimensions/weight
        product = order_item.product
        weight = float(product.weight_kg) if product and product.weight_kg else 0.5
        length = str(product.length_cm) if product and product.length_cm else '30.00'
        width = str(product.width_cm) if product and product.width_cm else '20.00'
        height = str(product.height_cm) if product and product.height_cm else '10.00'

        parcel = ParcelCreateRequest(
            length=length,
            width=width,
            height=height,
            distance_unit='cm',
            weight=f"{weight:.2f}",
            mass_unit='kg'
        )

        # 5. Create Shipment on Shippo
        req = ShipmentCreateRequest(
            address_from=addr_from,
            address_to=addr_to,
            parcels=[parcel]
        )
        shipment = shippo_client.shipments.create(req)

        # Capture any validation/carrier messages
        shippo_messages = []
        if hasattr(shipment, 'messages') and shipment.messages:
            for msg in shipment.messages:
                shippo_messages.append(str(msg))

        # 6. Purchase the best-matching rate
        if shipment.rates:
            # Sort by rate amount; default to cheapest
            rates_sorted = sorted(shipment.rates, key=lambda r: float(r.amount))
            selected_rate = rates_sorted[0]

            # TODO: If the customer selected a specific service level during checkout,
            # match it here instead of always picking cheapest. The selected rate info
            # is stored on the Order as shipping_method = "Provider - ServiceLevel".
            # For now we use cheapest to guarantee a label is always purchasable.

            tx_req = TransactionCreateRequest(rate=selected_rate.object_id, async_=False)
            tx = shippo_client.transactions.create(tx_req)

            if tx.status == 'SUCCESS':
                return {
                    "status": "success",
                    "label_url": tx.label_url,
                    "tracking_number": tx.tracking_number,
                    "carrier": selected_rate.provider,
                    "shippo_transaction_id": tx.object_id
                }
            else:
                return {
                    "status": "error",
                    "error": f"Shippo label transaction failed: {tx.messages}",
                    "shippo_messages": shippo_messages,
                }
        else:
            return {
                "status": "error",
                "error": "No rates available for this shipping route.",
                "shippo_messages": shippo_messages if shippo_messages else None,
            }

    except Exception as e:
        import traceback
        return {
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }



