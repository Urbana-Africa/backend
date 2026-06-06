"""
Unit tests for apps.pay.services.checkout escrow splitting.

Tests cover:
  - Dynamic pricing escrow splits (with base_price in item properties)
  - Legacy fallback (10% commission when no dynamic pricing data exists)
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock, PropertyMock

from django.test import TestCase

from apps.pay.services.checkout import complete_successful_payment


class TestEscrowSplits(TestCase):
    """Test the escrow distribution logic after a payment is confirmed."""

    def _make_mocks(self, item_props, quantity=1, sub_total=None):
        """
        Build mock payment, invoice, order, and order item.
        """
        payment = MagicMock()
        invoice = MagicMock()
        invoice.payment = None
        invoice.is_active = False
        invoice.is_used = False

        order_item = MagicMock()
        order_item.properties = item_props
        order_item.quantity = quantity
        order_item.sub_total = sub_total or Decimal("0.00")
        order_item.escrow = None  # No existing escrow
        order_item.designer = MagicMock()

        order = MagicMock()
        order.items.all.return_value = [order_item]
        order.customer = MagicMock()
        order.customer.user = MagicMock()

        return payment, invoice, order, order_item

    @patch("apps.pay.services.checkout.Escrow")
    @patch("apps.pay.services.checkout.Order")
    def test_dynamic_pricing_escrow_split(self, MockOrder, MockEscrow):
        """When item has dynamic pricing properties, escrow uses those splits."""
        item_props = {
            "base_price": "50.00",
            "platform_margin": "5.00",
            "duties_buffer": "12.00",
        }
        payment, invoice, order, order_item = self._make_mocks(
            item_props, quantity=2, sub_total=Decimal("134.00")
        )
        MockOrder.objects.get.return_value = order
        mock_escrow_instance = MagicMock()
        MockEscrow.objects.create.return_value = mock_escrow_instance

        complete_successful_payment(payment, invoice)

        # Verify the Escrow.objects.create was called with correct amounts
        create_kwargs = MockEscrow.objects.create.call_args[1]
        # designer_base = 50 * 2 = 100
        # commission = (5 + 12) * 2 = 34
        # escrow_amount = 100 + 34 = 134
        self.assertEqual(create_kwargs["amount"], Decimal("134.00"))
        self.assertEqual(create_kwargs["platform_commission"], Decimal("34.00"))
        self.assertEqual(create_kwargs["status"], "held")

    @patch("apps.pay.services.checkout.Escrow")
    @patch("apps.pay.services.checkout.Order")
    def test_legacy_fallback_10_percent(self, MockOrder, MockEscrow):
        """When item has NO dynamic pricing properties, fallback to 10% commission."""
        payment, invoice, order, order_item = self._make_mocks(
            item_props={}, quantity=1, sub_total=Decimal("100.00")
        )
        MockOrder.objects.get.return_value = order
        mock_escrow_instance = MagicMock()
        MockEscrow.objects.create.return_value = mock_escrow_instance

        complete_successful_payment(payment, invoice)

        create_kwargs = MockEscrow.objects.create.call_args[1]
        # Legacy: escrow_amount = sub_total = 100
        # commission = 100 * 0.10 = 10
        self.assertEqual(create_kwargs["amount"], Decimal("100.00"))
        self.assertEqual(create_kwargs["platform_commission"], Decimal("10.00"))
        self.assertEqual(create_kwargs["status"], "held")

    @patch("apps.pay.services.checkout.Escrow")
    @patch("apps.pay.services.checkout.Order")
    def test_existing_escrow_not_duplicated(self, MockOrder, MockEscrow):
        """If an item already has an escrow, it shouldn't create another one."""
        payment, invoice, order, order_item = self._make_mocks(
            item_props={}, quantity=1, sub_total=Decimal("50.00")
        )
        order_item.escrow = MagicMock()  # Already has escrow
        MockOrder.objects.get.return_value = order

        complete_successful_payment(payment, invoice)

        # Escrow.objects.create should NOT have been called
        MockEscrow.objects.create.assert_not_called()

    @patch("apps.pay.services.checkout.Escrow")
    @patch("apps.pay.services.checkout.Order")
    def test_no_order_for_invoice(self, MockOrder, MockEscrow):
        """If invoice has no corresponding order, should exit gracefully."""
        from apps.customers.models import Order as RealOrder

        MockOrder.DoesNotExist = RealOrder.DoesNotExist
        MockOrder.objects.get.side_effect = RealOrder.DoesNotExist
        payment = MagicMock()
        invoice = MagicMock()

        # Should not raise
        complete_successful_payment(payment, invoice)
        MockEscrow.objects.create.assert_not_called()
