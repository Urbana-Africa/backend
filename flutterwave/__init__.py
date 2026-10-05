"""flutterwave — self-contained Flutterwave v4/v3 client package.

Pure Python, framework-agnostic. Dependencies: ``requests`` + ``cryptography``.
Drop this folder into any project and configure via ``FlutterwaveConfig``::

    from flutterwave import FlutterwaveClient, FlutterwaveConfig

    client = FlutterwaveClient(FlutterwaveConfig.from_env())
    # or: FlutterwaveConfig(client_id='...', client_secret='...', ...)

See README.md for the full surface.
"""
from .config import FlutterwaveConfig
from .client import FlutterwaveClient, FlutterwaveConfigurationError
from .otp import FlutterwaveOTPClient, FlutterwaveOTPError
from .constants import (
    TOKEN_URL,
    V3_BASE_URL,
    V4_LIVE_BASE_URL,
    V4_SANDBOX_BASE_URL,
    BILL_CATEGORY_ALIASES,
    BILL_CATEGORY_FILTERS,
    BILL_CATEGORY_NAMES,
    BILLER_NAME_HINTS,
    BILL_COUNTRIES,
    CURRENCY_CHARGE_METHODS,
    CURRENCY_DIAL_CODES,
    DEFAULT_BILL_CATEGORIES,
    MOMO_COUNTRY_CODES,
    SUPPORTED_CHARGE_METHODS,
    SUPPORTED_PAYOUT_COUNTRIES,
    V3_CURRENCY_CHARGE_METHODS,
    V3_CURRENCY_COUNTRY,
    V3_MOMO_CHARGE_TYPES,
    WALLET_METHODS,
    STABLECOIN_CURRENCIES,
    PAY_TO_ADDRESS_METHODS,
    STABLECOIN_COLLECTION_METHODS,
    STABLECOIN_NETWORKS,
    STABLECOIN_SUPPORTED_NETWORKS,
    STABLECOIN_DEFAULT_NETWORK,
    STABLECOIN_MIN_TRANSFER,
    CRYPTO_ADDRESS_PATTERNS,
    CRYPTO_TX_HASH_PATTERNS,
    validate_crypto_address,
    validate_crypto_tx_hash,
)
from .currency import (
    CURRENCY_MINOR_EXPONENT,
    from_minor_units,
    minor_unit_exponent,
    to_minor_units,
)

__version__ = '1.0.0'

__all__ = [
    'FlutterwaveConfig',
    'FlutterwaveClient',
    'FlutterwaveConfigurationError',
    'FlutterwaveOTPClient',
    'FlutterwaveOTPError',
    'TOKEN_URL',
    'V3_BASE_URL',
    'V4_LIVE_BASE_URL',
    'V4_SANDBOX_BASE_URL',
    'CURRENCY_CHARGE_METHODS',
    'CURRENCY_DIAL_CODES',
    'MOMO_COUNTRY_CODES',
    'SUPPORTED_CHARGE_METHODS',
    'SUPPORTED_PAYOUT_COUNTRIES',
    'V3_CURRENCY_CHARGE_METHODS',
    'V3_CURRENCY_COUNTRY',
    'V3_MOMO_CHARGE_TYPES',
    'WALLET_METHODS',
    'STABLECOIN_CURRENCIES',
    'PAY_TO_ADDRESS_METHODS',
    'STABLECOIN_COLLECTION_METHODS',
    'STABLECOIN_NETWORKS',
    'STABLECOIN_SUPPORTED_NETWORKS',
    'STABLECOIN_DEFAULT_NETWORK',
    'STABLECOIN_MIN_TRANSFER',
    'CRYPTO_ADDRESS_PATTERNS',
    'CRYPTO_TX_HASH_PATTERNS',
    'validate_crypto_address',
    'validate_crypto_tx_hash',
    'BILL_CATEGORY_ALIASES',
    'BILL_CATEGORY_FILTERS',
    'BILL_CATEGORY_NAMES',
    'BILLER_NAME_HINTS',
    'BILL_COUNTRIES',
    'DEFAULT_BILL_CATEGORIES',
    'CURRENCY_MINOR_EXPONENT',
    'from_minor_units',
    'minor_unit_exponent',
    'to_minor_units',
    '__version__',
]
