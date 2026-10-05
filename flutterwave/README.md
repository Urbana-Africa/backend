# flutterwave

Self-contained Flutterwave client package — **v4 by default, optional legacy
v3 direct-charge rail**. Pure Python, no framework dependencies.

```
Dependencies: requests, cryptography
```

Drop this folder into any Python project's source root and it's importable —
no install step. (It ships as a plain directory; wrap it in your own
packaging if you want `pip install`.)

## What it covers

| Area | API | Entry points |
|---|---|---|
| One-shot charges | v4 `/orchestration/direct-charges` | `orchestrate_charge` |
| Charge lifecycle | v4 `/charges` | `create_charge`, `authorize_charge`, `get_charge`, `verify_transaction` |
| Legacy charges | v3 `/charges` (3DES-ECB) | same methods — `charge_api='v3'` |
| Customers | v4 `/customers` | `create_customer`, `find_customer_by_email` |
| Tokenization | v4 `/payment-methods` | `create_payment_method` (+ `create_charge(recurring=True)`) |
| Bank transfer | v4 `/virtual-accounts` | `create_virtual_account` |
| Payouts | v4 `/direct-transfers`, `/transfers` | `initiate_transfer`, `get_transfer`, `get_transfer_rate`, `get_wallet_balances` |
| Banks | v4 `/banks` | `list_banks`, `list_bank_branches`, `resolve_account` |
| Refunds | v4 `/refunds` | `refund_transaction` |
| Webhooks | `flutterwave-signature` HMAC | `verify_webhook_signature` |
| OTP (SMS/WhatsApp) | v3 `/otps` | `FlutterwaveOTPClient.create_verification` / `check_verification` |
| Bill payments | v3 `/bill-categories`, `/bill-items/…`, `/bills/…` | `list_bill_categories`, `list_billers`, `list_bill_items`, `validate_bill_customer`, `create_bill_payment`, `get_bill_payment` |

Also exports the corridor/method matrices (`SUPPORTED_PAYOUT_COUNTRIES`,
`CURRENCY_CHARGE_METHODS`, `MOMO_COUNTRY_CODES`, …) and minor-unit currency
helpers (`to_minor_units`, `from_minor_units`).

`SUPPORTED_CHARGE_METHODS` and `CURRENCY_CHARGE_METHODS` contain only methods
accepted by Flutterwave charge endpoints. Application-managed stablecoin
collection is exposed separately through `PAY_TO_ADDRESS_METHODS` and
`STABLECOIN_COLLECTION_METHODS`.

## Quick start

```python
from flutterwave import FlutterwaveClient, FlutterwaveConfig

client = FlutterwaveClient(FlutterwaveConfig(
    client_id='…',
    client_secret='…',
    encryption_key='…',       # base64 AES-256-GCM key (dashboard)
    webhook_hash='…',         # webhook secret hash
    v4_base_url='https://f4bexperience.flutterwave.com',  # live
    brand_name='MyApp',
    reference_prefix='myapp', # mints myappflw… / myappva… / myapppayout…
    payout_sender={           # required for EUR/GBP/EGP/INR transfers
        'name': {'first': 'MyApp', 'last': 'Ltd'},
        'email': 'ops@example.com',
        'address': {'line1': '…', 'city': '…', 'state': '…',
                    'postal_code': '…', 'country': 'NG'},
        'phone': {'country_code': '234', 'number': '…'},
    },
))
```

Or from environment variables — `FlutterwaveConfig.from_env()` reads:

```
FLUTTERWAVE_CLIENT_ID / FLUTTERWAVE_CLIENT_SECRET
FLUTTERWAVE_V4_BASE_URL          (default: sandbox URL)
FLUTTERWAVE_ENCRYPTION_KEY
FLUTTERWAVE_WEBHOOK_HASH
FLW_CHARGE_API                   ('v4' default | 'v3')
FLUTTERWAVE_V3_SECRET_KEY        (needed for charge_api='v3' and OTP)
FLUTTERWAVE_V3_BASE_URL          (default: https://api.flutterwave.com/v3)
FLUTTERWAVE_V3_ENCRYPTION_KEY    (falls back to FLUTTERWAVE_ENCRYPTION_KEY)
FLW_OTP_SENDER                   (registered SMS sender ID / OTP sender name)
FLW_BRAND_NAME / FLW_REFERENCE_PREFIX / FLW_OTP_CUSTOMER_NAME
BILL_CATEGORIES                  (CSV of v3 category codes to surface,
                                  default AIRTIME,MOBILEDATA,CABLEBILLS,UTILITYBILLS)
BILL_COUNTRIES                   (CSV — bills are Nigeria-only; default NG)
PAYOUT_SENDER_JSON               (JSON dict — EUR/GBP corridor sender)
FX_RATES_TO_NGN_JSON             (JSON dict — mock rate quotes only)
FLW_TESTING                      ('1'/'true'/'yes' → mock mode)
FLW_MOCK                         (explicit mock responses outside a test run)
FLW_DEBUG                        (enables documented development-only behavior)
```

`from_dict(d)` accepts the same keys, so a host framework can pass its
settings dict directly.

## Notes

- **Mock mode**: set `mock=True`, `testing=True`, `FLW_MOCK=true`, or
  `FLW_TESTING=true` to return canned charge and payout responses. Missing
  credentials fail closed with `FlutterwaveConfigurationError`; they never
  imply a successful payment. The OTP client also fails closed. Bills mock
  under explicit mock/testing or under `debug` when the v3 key is absent
  (`bills_mock`): a live deploy missing `FLUTTERWAVE_V3_SECRET_KEY` raises
  rather than faking fulfillment against a real wallet.
- **Bill payments**: v3-only, debits the **merchant wallet** — collect the
  money from the user first, then call `create_bill_payment`. The client
  speaks the **legacy flat-catalog API**: `GET /bill-categories` (filtered
  by `airtime`/`data_bundle`/`power`/`cable` flags or `biller_code`) is
  one list of purchasable items grouped into billers by `biller_code`;
  `create_bill_payment` posts `{country, customer, amount, recurrence:
  'ONCE', type, reference}` to `POST /bills` where `type` is the item's
  catalog `name` (pass it via `item_type`). The newer orchestrated
  endpoints (`/top-bill-categories`, `/bills/{cat}/billers`,
  `/billers/{code}/items`, `/billers/…/payment`) return 400 "contact your
  account administrator" on accounts without that product — the legacy
  surface is the portable baseline. `reference` is the dedup key (pass a
  persisted merchant reference); the status webhook posts to
  `callback_url`, and `get_bill_payment` is the reconciliation poll.
  Prepaid-electricity tokens arrive on `recharge_token`/`extra`. Because
  the flat catalog reuses `item_code` across rows (e.g. every NG airtime
  network is `BIL099`/`AT099`), `list_bill_items` appends a `#name-slug`
  suffix where codes collide — it never reaches the provider.
- **v3 charge rail**: set `charge_api='v3'` to route collection through the
  legacy encrypted-charge endpoints; payouts/banks/refunds always stay on v4.
- **Refund retries**: pass a persisted `idempotency_key` to
  `refund_transaction` and reuse it after an uncertain response. If omitted,
  the client derives a stable key from the charge, amount, currency and reason.
  Supply a distinct key for separate partial refunds with otherwise identical
  details.
- **Branding**: `brand_name` feeds narrations (`'{brand} payout'`,
  `'{brand} subscription renewal'`) and OTP sender fallbacks;
  `name_fallback_*` is the >=2-char name v4 requires when a customer name
  can't be split.
- **Hooks**: `_is_testing()`, `_is_debug()`, `_get_payout_sender()`,
  `_get_fx_rates()` are override seams for frameworks that want live-read
  settings.
- **OAuth token cache**: tokens are cached in-process and isolated by endpoint
  and credentials. `invalidate_token_cache()` clears only the calling client's
  entry.

## Standalone verification

After installing `requests` and `cryptography`, run the package tests without
Django:

```bash
python -m unittest discover -s flutterwave/tests -v
```
