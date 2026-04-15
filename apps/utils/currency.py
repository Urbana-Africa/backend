from decimal import Decimal

# Basic fixed rates for MVP stability in USD base.
# In a future phase, this would be hooked up to an API like OpenExchangeRates
EXCHANGE_RATES = {
    'USD': Decimal('1.00'),
    'NGN': Decimal('1500.00'),  # Approximate Nigerian Naira
    'GHS': Decimal('13.50'),    # Approximate Ghanaian Cedi
    'KES': Decimal('130.00'),   # Approximate Kenyan Shilling
    'ZAR': Decimal('19.00'),    # Approximate South African Rand
    'GBP': Decimal('0.79'),     # Approximate British Pound
    'EUR': Decimal('0.92'),     # Approximate Euro
}

def convert_currency(amount, from_currency, to_currency):
    """
    Converts an amount from one currency to another using the defined exchange rates.
    """
    if from_currency == to_currency:
        return Decimal(amount)
        
    try:
        from_rate = EXCHANGE_RATES.get(from_currency.upper())
        to_rate = EXCHANGE_RATES.get(to_currency.upper())
        
        if not from_rate or not to_rate:
            # Fallback to original amount if currency is not supported
            return Decimal(amount)
            
        # Convert to USD base first, then to target currency
        amount_usd = Decimal(amount) / from_rate
        converted_amount = amount_usd * to_rate
        
        return converted_amount.quantize(Decimal('0.01'))
    except Exception:
        # Fallback in case of invalid data
        return Decimal(amount)

def get_supported_currencies():
    return list(EXCHANGE_RATES.keys())
