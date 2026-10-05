"""ISO 4217 minor-unit helpers — vendored so the kit is self-contained.

This mirrors the client conversion helpers and
``apps/web/lib/utils/currency.ts`` — keep all copies in sync.
"""
from decimal import Decimal

# ISO 4217 minor-unit exponents — 2 for most currencies. Zero-decimal
# currencies (XOF, XAF, RWF, UGX, JPY…) have no fractional unit; a few
# (BHD, KWD, OMR…) use 3.
CURRENCY_MINOR_EXPONENT = {
    'BIF': 0, 'CLP': 0, 'DJF': 0, 'GNF': 0, 'ISK': 0, 'JPY': 0, 'KMF': 0,
    'KRW': 0, 'MGA': 0, 'PYG': 0, 'RWF': 0, 'UGX': 0, 'VND': 0, 'VUV': 0,
    'XAF': 0, 'XOF': 0, 'XPF': 0,
    'BHD': 3, 'IQD': 3, 'JOD': 3, 'KWD': 3, 'LYD': 3, 'OMR': 3, 'TND': 3,
}


def minor_unit_exponent(currency='USD'):
    return CURRENCY_MINOR_EXPONENT.get((currency or 'USD').upper(), 2)


def to_minor_units(amount, currency='USD'):
    """Convert a major-unit amount to minor units — kobo/cents for
    2-decimal currencies, whole units for zero-decimal (XOF, RWF, UGX…)."""
    factor = Decimal(10) ** minor_unit_exponent(currency)
    return int((Decimal(str(amount)) * factor).quantize(Decimal('1')))


def from_minor_units(amount_minor, currency='USD'):
    """Convert a minor-unit amount back to major units."""
    exponent = minor_unit_exponent(currency)
    factor = Decimal(10) ** exponent
    quantum = Decimal(1).scaleb(-exponent)
    return (Decimal(str(amount_minor)) / factor).quantize(quantum)
