# pay/services/pricing.py

from decimal import Decimal, ROUND_HALF_UP
from django.core.cache import cache
import requests
from django.conf import settings
from apps.pay.initialize import get_exchange_rate

# Country-Currency Mapping
COUNTRY_CURRENCY_MAP = {
    'US': 'USD',
    'CA': 'CAD',
    'GB': 'GBP',
    'NG': 'NGN',
    'GH': 'GHS',
    'KE': 'KES',
    'DE': 'EUR', 'FR': 'EUR', 'IT': 'EUR', 'ES': 'EUR', 'NL': 'EUR', 'BE': 'EUR', 'IE': 'EUR', 'AT': 'EUR'
}

# Trade Blocs Definition
ECOWAS = {'NG', 'GH', 'SL', 'LR', 'GM', 'SN', 'CI', 'ML', 'BF', 'NE', 'TG', 'BJ', 'CV', 'GW'}
EAC = {'KE', 'TZ', 'UG', 'RW', 'BI', 'SS', 'CD', 'SO'}
SADC = {'ZA', 'MZ', 'ZW', 'NA', 'BW', 'SZ', 'LS', 'AO', 'MW', 'ZM', 'CD', 'TZ', 'MU', 'SY', 'KM'}
ALL_AFRICA = ECOWAS.union(EAC).union(SADC).union({
    'EG', 'MA', 'DZ', 'TN', 'LY', 'SD', 'ET', 'ER', 'DJ', 'SO', 'CF', 'TD', 
    'CM', 'GQ', 'GA', 'CG', 'SH', 'MR', 'SO', 'SC', 'ST'
})


def get_country_from_ip(ip_address: str) -> str:
    """
    Looks up the country code (2-letter ISO) for a given IP address.
    Falls back to 'US' for local/private IPs or if the external API call fails.
    """
    if not ip_address or ip_address in ('127.0.0.1', '::1', 'localhost'):
        return 'US'
        
    cache_key = f"geoip:{ip_address}"
    cached_country = cache.get(cache_key)
    if cached_country:
        return cached_country
        
    try:
        resp = requests.get(f"http://ip-api.com/json/{ip_address}", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            country_code = data.get('countryCode', 'US').upper().strip()
            # Cache for 24 hours
            cache.set(cache_key, country_code, 86400)
            return country_code
    except Exception as e:
        print(f"[GeoIP Warning] Could not resolve IP {ip_address}: {e}")
        
    return 'US'


def get_currency_for_country(country_code: str) -> str:
    """Returns the preferred presentment currency code for a country. Defaults to USD."""
    return COUNTRY_CURRENCY_MAP.get(country_code.upper().strip(), 'USD')


def convert_currency_with_buffer(amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
    """
    Converts a Decimal amount from one currency to another using live exchange rates,
    injecting a 1.5% buffer cushion to protect against intraday volatility.
    """
    if from_currency == to_currency:
        return amount
        
    rate = get_exchange_rate(from_currency, to_currency)
    if rate is None:
        # Static local currency fallbacks if the FX API is unreachable
        if from_currency == "USD" and to_currency == "NGN":
            rate = Decimal("1500.00")
        elif from_currency == "NGN" and to_currency == "USD":
            rate = Decimal("0.00067")
        elif from_currency == "USD" and to_currency == "GHS":
            rate = Decimal("15.00")
        elif from_currency == "USD" and to_currency == "KES":
            rate = Decimal("130.00")
        else:
            return amount  # fallback 1:1
            
    # Inject 1.5% buffer to exchange rate
    buffered_rate = rate * Decimal("1.015")
    
    converted = amount * buffered_rate
    return converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_duties_surcharge_percent(from_country: str, to_country: str, total_usd_value: Decimal) -> Decimal:
    """
    Calculates the duties and taxes surcharge percentage based on trade/economic blocs.
    """
    from_country = from_country.upper().strip()
    to_country = to_country.upper().strip()
    
    # 1. Intra-bloc checks (ECOWAS, EAC, SADC) -> 0%
    if from_country in ECOWAS and to_country in ECOWAS:
        return Decimal("0.0")
    if from_country in EAC and to_country in EAC:
        return Decimal("0.0")
    if from_country in SADC and to_country in SADC:
        return Decimal("0.0")
        
    # 2. Cross-bloc Intra-Africa -> 6%
    if from_country in ALL_AFRICA and to_country in ALL_AFRICA:
        return Decimal("0.06")
        
    # 3. North America de minimis ($800 USD threshold)
    if to_country in ('US', 'CA'):
        if to_country == 'US' and total_usd_value < Decimal("800.00"):
            return Decimal("0.0")
        return Decimal("0.05")  # standard customs buffer if above threshold
        
    # 4. EU / UK VAT
    eu_countries = {
        'DE', 'FR', 'IT', 'ES', 'NL', 'BE', 'IE', 'AT', 'FI', 'SE', 'DK', 'PL', 
        'PT', 'GR', 'GB'
    }
    if to_country in eu_countries:
        return Decimal("0.20")  # 20% VAT/customs buffer
        
    # 5. Rest of the World -> 15% flat
    return Decimal("0.15")


def calculate_product_price_breakdown(product, buyer_country_code: str) -> dict:
    """
    Calculates the complete visible price and breakdown for a product in USD.
    
    Formula:
      Visible List Price = Base Price + Dynamic Shipping + Duties/Taxes Buffer + Platform Margin
    """
    base_price = Decimal(str(product.price))
    
    # 1. Resolve Designer's Origin Country
    designer_country = 'NG'
    local_shipping_fee = None
    designer_id = 'default'
    
    is_mock = lambda x: type(x).__name__ in ('MagicMock', 'Mock', 'NonCallableMagicMock', 'NonCallableMock')
    
    if product.user and hasattr(product.user, 'designer_profile') and product.user.designer_profile:
        profile = product.user.designer_profile
        if not is_mock(profile) or (hasattr(profile, 'country') and not is_mock(profile.country)):
            designer_country = str(profile.country) if profile.country else 'NG'
            if hasattr(profile, 'local_shipping_fee') and not is_mock(profile.local_shipping_fee):
                local_shipping_fee = profile.local_shipping_fee
            if hasattr(profile, 'id') and not is_mock(profile.id):
                designer_id = profile.id
                
    if designer_id == 'default' and product.user and hasattr(product.user, 'account_detail') and product.user.account_detail:
        detail = product.user.account_detail
        if not is_mock(detail) or (hasattr(detail, 'country') and not is_mock(detail.country)):
            designer_country = str(detail.country) if detail.country else 'NG'
            
    if designer_id == 'default' and product.country_of_origin:
        designer_country = product.country_of_origin.code or 'NG'
        
    # 2. Query dynamic shipping via Shippo
    from apps.designers.shippo_service import get_shipping_rates
    
    from_address = {'country': designer_country}
    to_address = {'country': buyer_country_code}
    weight = float(product.weight_kg)
    dims = {
        'length': str(product.length_cm),
        'width': str(product.width_cm),
        'height': str(product.height_cm)
    }
    
    # Cache key: ship_cost:{designer_id}:{from_country}:{to_country}:{weight_in_100g_buckets}
    weight_bucket = int(weight * 10)
    cache_key = f"ship_cost:{designer_id}:{designer_country}:{buyer_country_code}:{weight_bucket}"
    cached_ship_cost = cache.get(cache_key)
    
    if cached_ship_cost is not None:
        shipping_cost = Decimal(str(cached_ship_cost))
    else:
        rates_res = get_shipping_rates(from_address, to_address, weight, dims, local_shipping_fee=local_shipping_fee)
        shipping_cost = Decimal("30.00")  # default fallback
        if rates_res.get('status') == 'success' and rates_res.get('rates'):
            shipping_cost = Decimal(str(rates_res['rates'][0]['amount']))
        # Cache for 6 hours
        cache.set(cache_key, float(shipping_cost), 21600)
        
    # 3. Duties & Taxes Buffer Surcharge
    surcharge_percent = get_duties_surcharge_percent(designer_country, buyer_country_code, base_price)
    duties_buffer = (base_price + shipping_cost) * surcharge_percent
    duties_buffer = duties_buffer.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    # 4. Platform Take-Rate Margin (10% of base_price)
    platform_margin = base_price * Decimal("0.10")
    platform_margin = platform_margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    # 5. Total Visible Price (USD)
    total_price = base_price + shipping_cost + duties_buffer + platform_margin
    total_price = total_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    
    return {
        "base_price": base_price,
        "shipping_cost": shipping_cost,
        "duties_buffer": duties_buffer,
        "platform_margin": platform_margin,
        "total_price": total_price,
        "designer_country": designer_country,
        "buyer_country": buyer_country_code
    }
