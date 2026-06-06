"""
Unit tests for apps.pay.services.pricing

Tests cover:
  - Currency conversion with 1.5% buffer
  - Duties / surcharge logic per economic bloc
  - Full product price breakdown
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase

from apps.pay.services.pricing import (
    convert_currency_with_buffer,
    get_currency_for_country,
    get_duties_surcharge_percent,
    calculate_product_price_breakdown,
)


class TestCurrencyConversion(TestCase):
    """Verify currency conversion with 1.5% volatility buffer."""

    def test_same_currency_returns_same_amount(self):
        amt = Decimal("100.00")
        result = convert_currency_with_buffer(amt, "USD", "USD")
        self.assertEqual(result, amt)

    @patch("apps.pay.services.pricing.get_exchange_rate", return_value=Decimal("1500.00"))
    def test_usd_to_ngn_includes_buffer(self, _mock_rate):
        amt = Decimal("10.00")
        result = convert_currency_with_buffer(amt, "USD", "NGN")
        # Expected: 10 * 1500 * 1.015 = 15225.00
        expected = Decimal("15225.00")
        self.assertEqual(result, expected)

    @patch("apps.pay.services.pricing.get_exchange_rate", return_value=None)
    def test_static_fallback_usd_ngn(self, _mock_rate):
        """When FX API is unreachable, uses static NGN rate."""
        amt = Decimal("1.00")
        result = convert_currency_with_buffer(amt, "USD", "NGN")
        # Static rate: 1500.00. Buffered: 1500 * 1.015 = 1522.50
        expected = Decimal("1522.50")
        self.assertEqual(result, expected)

    @patch("apps.pay.services.pricing.get_exchange_rate", return_value=None)
    def test_unknown_currency_fallback_1_to_1(self, _mock_rate):
        """Unknown currency pair without static fallback returns amount as-is."""
        amt = Decimal("55.00")
        result = convert_currency_with_buffer(amt, "USD", "XYZ")
        self.assertEqual(result, amt)


class TestCurrencyMapping(TestCase):

    def test_known_countries(self):
        self.assertEqual(get_currency_for_country("NG"), "NGN")
        self.assertEqual(get_currency_for_country("US"), "USD")
        self.assertEqual(get_currency_for_country("GB"), "GBP")
        self.assertEqual(get_currency_for_country("DE"), "EUR")

    def test_unknown_country_defaults_to_usd(self):
        self.assertEqual(get_currency_for_country("XX"), "USD")


class TestDutiesSurcharge(TestCase):
    """Verify economic-bloc surcharge matrix."""

    def test_ecowas_intra_bloc_zero(self):
        """ECOWAS -> ECOWAS = 0% duty."""
        self.assertEqual(
            get_duties_surcharge_percent("NG", "GH", Decimal("50")),
            Decimal("0.0"),
        )

    def test_eac_intra_bloc_zero(self):
        self.assertEqual(
            get_duties_surcharge_percent("KE", "UG", Decimal("50")),
            Decimal("0.0"),
        )

    def test_sadc_intra_bloc_zero(self):
        self.assertEqual(
            get_duties_surcharge_percent("ZA", "MZ", Decimal("50")),
            Decimal("0.0"),
        )

    def test_cross_bloc_intra_africa(self):
        """Nigeria (ECOWAS) -> Kenya (EAC) = 6% duty."""
        self.assertEqual(
            get_duties_surcharge_percent("NG", "KE", Decimal("50")),
            Decimal("0.06"),
        )

    def test_us_de_minimis_under_800(self):
        """Shipments to US under $800 should incur 0% duty."""
        self.assertEqual(
            get_duties_surcharge_percent("NG", "US", Decimal("500")),
            Decimal("0.0"),
        )

    def test_us_above_de_minimis(self):
        """Shipments to US above $800 threshold = 5% duty."""
        self.assertEqual(
            get_duties_surcharge_percent("NG", "US", Decimal("1000")),
            Decimal("0.05"),
        )

    def test_eu_vat_20_percent(self):
        self.assertEqual(
            get_duties_surcharge_percent("NG", "DE", Decimal("100")),
            Decimal("0.20"),
        )

    def test_uk_vat_20_percent(self):
        self.assertEqual(
            get_duties_surcharge_percent("NG", "GB", Decimal("100")),
            Decimal("0.20"),
        )

    def test_rest_of_world_15_percent(self):
        """Japan is not in any defined bloc -> 15%."""
        self.assertEqual(
            get_duties_surcharge_percent("NG", "JP", Decimal("100")),
            Decimal("0.15"),
        )


class TestPriceBreakdown(TestCase):
    """
    Integration-level test for the full price breakdown formula.
    Mocks Shippo and designer account to test arithmetic.
    """

    def _make_mock_product(self, price="50.00", weight=0.5, country_code="NG"):
        product = MagicMock()
        product.price = Decimal(price)
        product.weight_kg = weight
        product.length_cm = 30
        product.width_cm = 20
        product.height_cm = 5
        product.country_of_origin = MagicMock()
        product.country_of_origin.code = country_code
        product.user = MagicMock()
        product.user.account_detail = MagicMock()
        product.user.account_detail.country = country_code
        return product

    @patch("apps.pay.services.pricing.cache")
    @patch("apps.designers.shippo_service.get_shipping_rates")
    def test_ng_to_us_under_de_minimis(self, mock_ship, mock_cache):
        """Nigeria -> US, $50 product (under $800), shipping = $15."""
        mock_cache.get.return_value = None  # no cache hit
        mock_cache.set = MagicMock()
        mock_ship.return_value = {"status": "success", "rates": [{"amount": "15.00"}]}

        product = self._make_mock_product(price="50.00", country_code="NG")
        result = calculate_product_price_breakdown(product, "US")

        self.assertEqual(result["base_price"], Decimal("50.00"))
        self.assertEqual(result["shipping_cost"], Decimal("15.00"))
        # US de minimis < $800 -> 0% duties
        self.assertEqual(result["duties_buffer"], Decimal("0.00"))
        # Platform margin = 10% of 50 = 5.00
        self.assertEqual(result["platform_margin"], Decimal("5.00"))
        # Total = 50 + 15 + 0 + 5 = 70.00
        self.assertEqual(result["total_price"], Decimal("70.00"))

    @patch("apps.pay.services.pricing.cache")
    @patch("apps.designers.shippo_service.get_shipping_rates")
    def test_ng_to_de_eu_vat(self, mock_ship, mock_cache):
        """Nigeria -> Germany: 20% VAT buffer applied."""
        mock_cache.get.return_value = None
        mock_cache.set = MagicMock()
        mock_ship.return_value = {"status": "success", "rates": [{"amount": "20.00"}]}

        product = self._make_mock_product(price="100.00", country_code="NG")
        result = calculate_product_price_breakdown(product, "DE")

        self.assertEqual(result["base_price"], Decimal("100.00"))
        self.assertEqual(result["shipping_cost"], Decimal("20.00"))
        # Duties: (100 + 20) * 0.20 = 24.00
        self.assertEqual(result["duties_buffer"], Decimal("24.00"))
        # Platform margin: 10% of 100 = 10.00
        self.assertEqual(result["platform_margin"], Decimal("10.00"))
        # Total = 100 + 20 + 24 + 10 = 154.00
        self.assertEqual(result["total_price"], Decimal("154.00"))

    @patch("apps.pay.services.pricing.cache")
    @patch("apps.designers.shippo_service.get_shipping_rates")
    def test_ng_to_gh_ecowas_free_trade(self, mock_ship, mock_cache):
        """ECOWAS intra-bloc -> 0% duty."""
        mock_cache.get.return_value = None
        mock_cache.set = MagicMock()
        mock_ship.return_value = {"status": "success", "rates": [{"amount": "8.00"}]}

        product = self._make_mock_product(price="40.00", country_code="NG")
        result = calculate_product_price_breakdown(product, "GH")

        self.assertEqual(result["duties_buffer"], Decimal("0.00"))
        # Total = 40 + 8 + 0 + 4 = 52.00
        self.assertEqual(result["total_price"], Decimal("52.00"))

    @patch("apps.pay.services.pricing.cache")
    @patch("apps.designers.shippo_service.get_shipping_rates")
    def test_shippo_failure_uses_fallback(self, mock_ship, mock_cache):
        """When Shippo returns no rates, falls back to $30 shipping."""
        mock_cache.get.return_value = None
        mock_cache.set = MagicMock()
        mock_ship.return_value = {"status": "error", "rates": []}

        product = self._make_mock_product(price="60.00", country_code="NG")
        result = calculate_product_price_breakdown(product, "US")

        self.assertEqual(result["shipping_cost"], Decimal("30.00"))
