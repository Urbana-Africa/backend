"""Flutterwave API constants — endpoints, payout corridors, charge matrices.

Pure data, no dependencies. Shared by ``client`` and by consuming apps that
need the supported-corridor tables (bank lists, checkout method ladders).
"""

V4_SANDBOX_BASE_URL = 'https://developersandbox-api.flutterwave.com'
V4_LIVE_BASE_URL = 'https://f4bexperience.flutterwave.com'
TOKEN_URL = 'https://idp.flutterwave.com/realms/flutterwave/protocol/openid-connect/token'

# Legacy v3 API — same base URL for test and live keys (v3 has no separate
# sandbox host; FLWSECK_TEST keys hit the same endpoints in test mode).
V3_BASE_URL = 'https://api.flutterwave.com/v3'

# ISO-3166 alpha-2 -> supported payout corridor.
# ``requires_branch`` marks corridors where Flutterwave needs a bank branch
# code (from GET /banks/{id}/branches) in the transfer recipient — per the
# Flutterwave transfer docs, that is Benin, Burkina Faso, Cameroon, Chad,
# Côte d'Ivoire, Republic of Congo, Gabon, Ghana, Malawi, Rwanda, Senegal,
# Sierra Leone, Tanzania and Uganda.
# International USD/EUR/GBP transfers use a different recipient shape
# (``international: True`` entries below) — free-text bank name plus
# routing/SWIFT and a recipient address instead of a bank-list code.
SUPPORTED_PAYOUT_COUNTRIES = {
    'BJ': {'name': 'Benin', 'currency': 'XOF', 'flw_code': 'BJ', 'requires_branch': True},
    'BF': {'name': 'Burkina Faso', 'currency': 'XOF', 'flw_code': 'BF', 'requires_branch': True},
    'CM': {'name': 'Cameroon', 'currency': 'XAF', 'flw_code': 'CM', 'requires_branch': True},
    'TD': {'name': 'Chad', 'currency': 'XAF', 'flw_code': 'TD', 'requires_branch': True},
    'CI': {'name': "Côte d'Ivoire", 'currency': 'XOF', 'flw_code': 'CI', 'requires_branch': True},
    # Republic of Congo (Congo-Brazzaville) — CEMAC/XAF. DR Congo ('CD') is
    # NOT a Flutterwave corridor: /banks?country=CD returns 400.
    'CG': {'name': 'Republic of Congo', 'currency': 'XAF', 'flw_code': 'CG', 'requires_branch': True},
    'EG': {'name': 'Egypt', 'currency': 'EGP', 'flw_code': 'EG', 'requires_branch': False},
    'ET': {'name': 'Ethiopia', 'currency': 'ETB', 'flw_code': 'ET', 'requires_branch': False},
    'GA': {'name': 'Gabon', 'currency': 'XAF', 'flw_code': 'GA', 'requires_branch': True},
    'GH': {'name': 'Ghana', 'currency': 'GHS', 'flw_code': 'GH', 'requires_branch': True},
    'KE': {'name': 'Kenya', 'currency': 'KES', 'flw_code': 'KE', 'requires_branch': False},
    'MW': {'name': 'Malawi', 'currency': 'MWK', 'flw_code': 'MW', 'requires_branch': True},
    'NG': {'name': 'Nigeria', 'currency': 'NGN', 'flw_code': 'NG', 'requires_branch': False},
    'RW': {'name': 'Rwanda', 'currency': 'RWF', 'flw_code': 'RW', 'requires_branch': True},
    'SN': {'name': 'Senegal', 'currency': 'XOF', 'flw_code': 'SN', 'requires_branch': True},
    'SL': {'name': 'Sierra Leone', 'currency': 'SLE', 'flw_code': 'SL', 'requires_branch': True},
    'ZA': {'name': 'South Africa', 'currency': 'ZAR', 'flw_code': 'ZA', 'requires_branch': False},
    'TZ': {'name': 'Tanzania', 'currency': 'TZS', 'flw_code': 'TZ', 'requires_branch': True},
    'UG': {'name': 'Uganda', 'currency': 'UGX', 'flw_code': 'UG', 'requires_branch': True},
    'ZM': {'name': 'Zambia', 'currency': 'ZMW', 'flw_code': 'ZM', 'requires_branch': False},
    # International corridors — no bank list (GET /banks?country= returns no
    # institutions); the recipient carries bank name + routing/SWIFT + address,
    # and EUR/GBP transfers additionally require a transfer sender (see
    # ``FlutterwaveConfig.payout_sender``).
    'US': {'name': 'United States', 'currency': 'USD', 'flw_code': 'US',
           'requires_branch': False, 'international': True},
    'GB': {'name': 'United Kingdom', 'currency': 'GBP', 'flw_code': 'GB',
           'requires_branch': False, 'international': True},
}

# SEPA member countries receive EUR payouts through the international corridor.
_SEPA_COUNTRY_NAMES = {
    'AD': 'Andorra', 'AT': 'Austria', 'BE': 'Belgium', 'BG': 'Bulgaria',
    'HR': 'Croatia', 'CY': 'Cyprus', 'CZ': 'Czechia', 'DK': 'Denmark',
    'EE': 'Estonia', 'FI': 'Finland', 'FR': 'France', 'DE': 'Germany',
    'GR': 'Greece', 'HU': 'Hungary', 'IS': 'Iceland', 'IE': 'Ireland',
    'IT': 'Italy', 'LV': 'Latvia', 'LI': 'Liechtenstein', 'LT': 'Lithuania',
    'LU': 'Luxembourg', 'MT': 'Malta', 'MC': 'Monaco', 'NL': 'Netherlands',
    'NO': 'Norway', 'PL': 'Poland', 'PT': 'Portugal', 'RO': 'Romania',
    'SM': 'San Marino', 'SK': 'Slovakia', 'SI': 'Slovenia', 'ES': 'Spain',
    'SE': 'Sweden', 'CH': 'Switzerland', 'VA': 'Vatican City',
}
for _sepa_code, _sepa_name in _SEPA_COUNTRY_NAMES.items():
    SUPPORTED_PAYOUT_COUNTRIES[_sepa_code] = {
        'name': _sepa_name, 'currency': 'EUR', 'flw_code': _sepa_code,
        'requires_branch': False, 'international': True,
    }
del _sepa_code, _sepa_name

# Payment method types accepted by the charge endpoints.
SUPPORTED_CHARGE_METHODS = (
    'card', 'bank_transfer', 'bank_account', 'mobile_money',
    'ussd', 'opay', 'applepay', 'googlepay',
)

# Apple Pay / Google Pay are card-rail wallets — wherever a card charge is
# accepted, the hosted wallet sheet works too (subject to per-method feature
# approval on the Flutterwave merchant account).
WALLET_METHODS = frozenset({'applepay', 'googlepay'})

# Collection-method ↔ currency matrix: which methods Flutterwave actually
# settles per currency. Distinct from the payout corridor table — USSD and
# OPay are NGN-only, mobile money is the East/West-African wallet corridor,
# bank_account is the direct-debit rail (ACH / Faster Payments / SEPA / SA).
# Unlisted currencies are card-only (plus wallets — see WALLET_METHODS).
CURRENCY_CHARGE_METHODS = {
    'NGN': frozenset({'card', 'bank_transfer', 'ussd', 'opay'}) | WALLET_METHODS,
    'GHS': frozenset({'card', 'mobile_money', 'bank_transfer'}) | WALLET_METHODS,
    'KES': frozenset({'card', 'mobile_money'}) | WALLET_METHODS,
    'UGX': frozenset({'card', 'mobile_money'}) | WALLET_METHODS,
    'RWF': frozenset({'card', 'mobile_money'}) | WALLET_METHODS,
    'TZS': frozenset({'card', 'mobile_money'}) | WALLET_METHODS,
    'ZMW': frozenset({'card', 'mobile_money'}) | WALLET_METHODS,
    'XOF': frozenset({'card', 'mobile_money'}) | WALLET_METHODS,
    'XAF': frozenset({'card', 'mobile_money'}) | WALLET_METHODS,
    'USD': frozenset({'card', 'bank_account'}) | WALLET_METHODS,
    'GBP': frozenset({'card', 'bank_account'}) | WALLET_METHODS,
    'EUR': frozenset({'card', 'bank_account'}) | WALLET_METHODS,
    # ZAR stays card+wallets — South Africa EFT/Capitec uses v3 charge endpoints,
    # not v4 direct-charges bank_account.
}

# ISO numeric country codes v4 requires on mobile_money payment methods,
# keyed by charge currency (the wallet's home market).
MOMO_COUNTRY_CODES = {
    'GHS': '233', 'KES': '254', 'UGX': '256', 'RWF': '250',
    'TZS': '255', 'ZMW': '260', 'XOF': '225', 'XAF': '237',
}

# Dialing codes per charge corridor — used to split an international-format
# phone into v4's {country_code, number} and as the default country_code
# when the payer typed a local-format number. Keyed by charge currency.
CURRENCY_DIAL_CODES = {
    'NGN': '234', 'GHS': '233', 'KES': '254', 'UGX': '256',
    'RWF': '250', 'TZS': '255', 'ZMW': '260', 'XOF': '225',
    'XAF': '237', 'ZAR': '27',
}

# Prefixes recognized in an explicit "+<cc><number>" input — the charge
# corridors plus major international codes so a typed international prefix
# still splits correctly on USD/GBP/EUR charges. Longest-first so a
# 3-digit code wins over a shorter prefix of the same digits.
_KNOWN_DIAL_CODES = tuple(sorted(
    set(CURRENCY_DIAL_CODES.values()) | {'1', '44'},
    key=len, reverse=True,
))

# ------------------------------------------------------------------
# v3 (legacy) collection matrix
# ------------------------------------------------------------------
# v3 charge-type slugs per currency for mobile money (the ``type`` query
# param on POST /v3/charges). Tanzania has no v3 momo endpoint; Kenya's
# momo is the dedicated mpesa type.
V3_MOMO_CHARGE_TYPES = {
    'GHS': 'mobile_money_ghana',
    'RWF': 'mobile_money_rwanda',
    'UGX': 'mobile_money_uganda',
    'ZMW': 'mobile_money_zambia',
    'XOF': 'mobile_money_franco',
    'XAF': 'mobile_money_franco',
    'KES': 'mpesa',
}

# v3 collection-method ↔ currency matrix. The legacy API has no hosted
# wallets, no OPay, no bank-account direct debit and no TZS momo — those
# corridors fall back to card. bank_transfer + ussd remain NGN-only.
V3_CURRENCY_CHARGE_METHODS = {
    'NGN': frozenset({'card', 'bank_transfer', 'ussd'}),
    'GHS': frozenset({'card', 'mobile_money'}),
    'KES': frozenset({'card', 'mobile_money'}),
    'UGX': frozenset({'card', 'mobile_money'}),
    'RWF': frozenset({'card', 'mobile_money'}),
    'ZMW': frozenset({'card', 'mobile_money'}),
    'XOF': frozenset({'card', 'mobile_money'}),
    'XAF': frozenset({'card', 'mobile_money'}),
}

# v3 tokenized-charges requires an ISO country on the payload.
V3_CURRENCY_COUNTRY = {
    'NGN': 'NG', 'GHS': 'GH', 'KES': 'KE', 'UGX': 'UG', 'RWF': 'RW',
    'ZMW': 'ZM', 'ZAR': 'ZA', 'TZS': 'TZ', 'XOF': 'CI', 'XAF': 'CM',
    'USD': 'US', 'GBP': 'GB', 'EUR': 'GB',
}

# ------------------------------------------------------------------
# Bill payments (v3-only, like /otps)
# ------------------------------------------------------------------
# Flutterwave runs bill payments on the legacy v3 API
# (``api.flutterwave.com/v3``) and debits the MERCHANT wallet — the
# platform's float, never the customer directly. Callers must collect
# the money first (wallet debit in this codebase) before executing the
# payment.
#
# IMPORTANT — two bill API generations exist and a merchant account may
# only have the legacy one enabled:
#   * The newer orchestrated flow — ``GET /top-bill-categories``,
#     ``GET /bills/{category}/billers``, ``GET /billers/{code}/items``,
#     ``POST /billers/{b}/items/{i}/payment`` — answers 400 "contact your
#     account administrator" when the bills product isn't provisioned.
#   * The legacy flat catalog — ``GET /bill-categories`` (optionally
#     filtered by ``airtime``/``data_bundle``/``power``/``cable``/… flags
#     or ``biller_code``), ``POST /bills`` with ``type`` = the item's
#     catalog ``name`` — works on the same accounts. This client uses
#     the legacy flow; it is the documented baseline and the one live
#     accounts actually expose.
#
# Category codes for the four mainstream bills: airtime, mobile data,
# cable TV and electricity/utility. ``DEFAULT_BILL_CATEGORIES`` is the
# surfaced allowlist and can be widened via ``config.bill_categories``.
DEFAULT_BILL_CATEGORIES = (
    'AIRTIME',       # airtime top-up
    'MOBILEDATA',    # data bundles
    'CABLEBILLS',    # DSTV/GOtv/StarTimes
    'UTILITYBILLS',  # electricity (prepaid tokens), water, waste
)
# Category code -> the ``GET /bill-categories`` boolean query flag that
# scopes the flat catalog to that lane (verified against a live NG
# merchant catalog).
BILL_CATEGORY_FILTERS = {
    'AIRTIME': 'airtime',
    'MOBILEDATA': 'data_bundle',
    'CABLEBILLS': 'cable',
    'UTILITYBILLS': 'power',
}
# Display strings for the surfaced categories.
BILL_CATEGORY_NAMES = {
    'AIRTIME': ('Airtime', 'Mobile airtime top-up'),
    'MOBILEDATA': ('Data', 'Mobile data bundles'),
    'CABLEBILLS': ('Cable TV', 'TV subscriptions'),
    'UTILITYBILLS': ('Electricity', 'Power & utility bills'),
}
# Alternate code spellings the provider has used for the same lanes —
# matched so the allowlist still filters if a catalog spells the code
# differently (e.g. the newer ``/top-bill-categories`` shape).
BILL_CATEGORY_ALIASES = {
    'DATA_BUNDLE': 'MOBILEDATA',
    'DATABUNDLE': 'MOBILEDATA',
    'MOBILE DATA': 'MOBILEDATA',
    'DATA': 'MOBILEDATA',
    'CABLE': 'CABLEBILLS',
    'CABLE TV': 'CABLEBILLS',
    'TV': 'CABLEBILLS',
    'POWER': 'UTILITYBILLS',
    'ELECTRICITY': 'UTILITYBILLS',
    'UTILITIES': 'UTILITYBILLS',
    'UTILITY': 'UTILITYBILLS',
}
# biller_code -> display name for the NG lanes we surface. The flat
# catalog has NO biller-name field — row ``biller_name`` is the product
# name (e.g. "MTN 1.5GB data purchase"), so biller labels come from this
# map with a common-token-prefix heuristic as fallback. Codes are
# account-scoped and can drift between merchant accounts — the heuristic
# keeps unmapped billers usable ("Biller BIL###").
BILLER_NAME_HINTS = {
    'BIL099': 'All Networks',
    'BIL100': 'Airtel', 'BIL102': 'Glo', 'BIL103': '9mobile',
    'BIL108': 'MTN Data', 'BIL109': 'Glo Data', 'BIL110': 'Airtel Data',
    'BIL111': '9mobile Data',
    'BIL112': 'EKEDC — Eko Electric', 'BIL113': 'IKEDC — Ikeja Electric',
    'BIL114': 'IBEDC — Ibadan Electric', 'BIL115': 'EEDC — Enugu Electric',
    'BIL116': 'PHEDC — Port Harcourt', 'BIL117': 'BEDC — Benin Electric',
    'BIL118': 'YEDC — Yola Electric', 'BIL119': 'KAEDCO — Kaduna Electric',
    'BIL120': 'KEDCO — Kano Electric', 'BIL204': 'AEDC — Abuja Electric',
    'BIL215': 'JEDC — Jos Electric',
    'BIL121': 'DStv', 'BIL122': 'GOtv', 'BIL123': 'StarTimes',
}
# Bill payments are currently a Nigeria-only corridor (NGN debit).
BILL_COUNTRIES = ('NG',)

# ------------------------------------------------------------------
# Stablecoin / crypto transfers (v4 — Treasury / StableRails)
# ------------------------------------------------------------------
# Flutterwave's stablecoin infrastructure operates through the
# ``POST /direct-transfers`` endpoint — the same one used for bank
# payouts. There is NO charge endpoint for stablecoins: collection
# (deposits / tips) uses a pay-to-address pattern where the payer
# sends the exact amount to the platform's stablecoin wallet, and
# the platform verifies receipt via balance checks or webhooks.
#
# Outbound transfers (payouts) use ``type: 'crypto'`` with a
# ``recipient.crypto: {network, address}`` block. Fiat-to-stablecoin
# conversion (funding the platform's own stablecoin wallet) uses
# ``type: 'wallet'`` targeting the merchant's Flutterwave wallet.

# Stablecoin currencies supported by Flutterwave Treasury.
STABLECOIN_CURRENCIES = frozenset({'USDT', 'USDC', 'RLUSD'})

# Pay-to-address collection is implemented by the consuming application; it
# is not accepted by Flutterwave's charge endpoints. Keep this contract apart
# from ``SUPPORTED_CHARGE_METHODS`` / ``CURRENCY_CHARGE_METHODS`` so checkout
# code cannot accidentally send a crypto method to ``orchestrate_charge``.
PAY_TO_ADDRESS_METHODS = frozenset({'crypto'})
STABLECOIN_COLLECTION_METHODS = {
    currency: PAY_TO_ADDRESS_METHODS for currency in STABLECOIN_CURRENCIES
}

# Network → stablecoins available on that network.
STABLECOIN_NETWORKS = {
    'SOLANA': frozenset({'USDT', 'USDC'}),
    'ETHEREUM': frozenset({'USDT', 'USDC', 'RLUSD'}),
    'BASE': frozenset({'USDC'}),
    'POLYGON': frozenset({'USDT', 'USDC'}),
}

# Stablecoin → networks it can be transferred on (inverse lookup).
STABLECOIN_SUPPORTED_NETWORKS = {
    'USDT': frozenset({'SOLANA', 'ETHEREUM', 'POLYGON'}),
    'USDC': frozenset({'SOLANA', 'ETHEREUM', 'BASE', 'POLYGON'}),
    'RLUSD': frozenset({'ETHEREUM'}),
}

# Preferred default network per stablecoin — cheapest / fastest first.
STABLECOIN_DEFAULT_NETWORK = {
    'USDT': 'SOLANA',
    'USDC': 'BASE',
    'RLUSD': 'ETHEREUM',
}

# Minimum transfer amount enforced by Flutterwave for stablecoin
# disbursements (in the stablecoin's major units, i.e. 10 USDT).
STABLECOIN_MIN_TRANSFER = 10

# Fiat currencies that can be converted to stablecoins on FLW.
# Attempting conversion from other fiats will fail.
STABLECOIN_FUNDING_CURRENCIES = frozenset({'NGN', 'USD', 'GBP', 'EUR', 'GHS'})

# Wallet address validation patterns per network family.
# EVM chains (Ethereum / Base / Polygon) share the same 0x… format;
# Solana uses base58-encoded 32-byte public keys.
CRYPTO_ADDRESS_PATTERNS = {
    'ETHEREUM': r'^0x[0-9a-fA-F]{40}$',
    'BASE': r'^0x[0-9a-fA-F]{40}$',
    'POLYGON': r'^0x[0-9a-fA-F]{40}$',
    'SOLANA': r'^[1-9A-HJ-NP-Za-km-z]{32,44}$',
}

# Transaction hash validation patterns per network family.
# EVM chains use 0x followed by 64 hex characters.
# Solana uses base58 transaction signatures (64-88 characters).
CRYPTO_TX_HASH_PATTERNS = {
    'ETHEREUM': r'^0x[0-9a-fA-F]{64}$',
    'BASE': r'^0x[0-9a-fA-F]{64}$',
    'POLYGON': r'^0x[0-9a-fA-F]{64}$',
    'SOLANA': r'^[1-9A-HJ-NP-Za-km-z]{64,88}$',
}


def validate_crypto_address(address: str, network: str) -> bool:
    """Validate a wallet address format for a given crypto network."""
    import re
    pattern = CRYPTO_ADDRESS_PATTERNS.get((network or '').upper())
    if not pattern:
        return False
    return bool(re.match(pattern, (address or '').strip()))


def validate_crypto_tx_hash(tx_hash: str, network: str) -> bool:
    """Validate a transaction hash format for a given crypto network."""
    import re
    pattern = CRYPTO_TX_HASH_PATTERNS.get((network or '').upper())
    if not pattern:
        return False
    return bool(re.match(pattern, (tx_hash or '').strip()))
