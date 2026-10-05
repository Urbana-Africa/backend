from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

from flutterwave import (
    CURRENCY_CHARGE_METHODS,
    PAY_TO_ADDRESS_METHODS,
    STABLECOIN_COLLECTION_METHODS,
    SUPPORTED_CHARGE_METHODS,
    FlutterwaveClient,
    FlutterwaveConfig,
    FlutterwaveConfigurationError,
    from_minor_units,
    to_minor_units,
)
from flutterwave.client import _token_cache


def response(data, status_code=200):
    return Mock(
        status_code=status_code,
        json=Mock(return_value=data),
        raise_for_status=Mock(),
    )


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        _token_cache.clear()
        _token_cache.update({
            'access_token': None,
            'expires_at': 0.0,
            'cache_key': None,
            'tokens': {},
        })

    def test_missing_credentials_fail_closed(self):
        client = FlutterwaveClient(FlutterwaveConfig())

        self.assertFalse(client.mock)
        self.assertFalse(client.payout_mock)
        with self.assertRaises(FlutterwaveConfigurationError):
            client.orchestrate_charge(
                amount=10,
                currency='NGN',
                reference='payment1',
                customer={'email': 'payer@example.com'},
                payment_method={'type': 'bank_transfer'},
            )
        with self.assertRaises(FlutterwaveConfigurationError):
            client.initiate_transfer(
                amount=10,
                currency='NGN',
                account_bank='044',
                account_number='0123456789',
            )

    def test_mock_mode_is_explicit(self):
        client = FlutterwaveClient(FlutterwaveConfig(mock=True))

        charge = client.orchestrate_charge(
            amount=10,
            currency='NGN',
            reference='payment1',
            customer={},
            payment_method={},
        )
        payout = client.initiate_transfer(amount=10, currency='NGN')

        self.assertTrue(charge['mock'])
        self.assertTrue(payout['mock'])

    def test_environment_can_explicitly_enable_mock_mode(self):
        config = FlutterwaveConfig.from_env({'FLW_MOCK': 'true'})

        self.assertIs(config.mock, True)
        self.assertTrue(FlutterwaveClient(config).mock)

    def test_oauth_cache_is_scoped_to_credentials(self):
        merchant_a = FlutterwaveClient(FlutterwaveConfig(
            client_id='merchant-a', client_secret='secret-a'))
        merchant_b = FlutterwaveClient(FlutterwaveConfig(
            client_id='merchant-b', client_secret='secret-b'))

        with patch('flutterwave.client.requests.post', side_effect=[
            response({'access_token': 'token-a', 'expires_in': 600}),
            response({'access_token': 'token-b', 'expires_in': 600}),
        ]) as post:
            self.assertEqual(merchant_a._access_token(), 'token-a')
            self.assertEqual(merchant_b._access_token(), 'token-b')
            self.assertEqual(merchant_a._access_token(), 'token-a')

        self.assertEqual(post.call_count, 2)

    def test_customer_search_requires_exact_email_match(self):
        client = FlutterwaveClient(FlutterwaveConfig(
            client_id='merchant', client_secret='secret'))
        client._v4_request = Mock(return_value=response({
            'data': [{'id': 'wrong-id', 'email': 'other@example.com'}],
        }))

        self.assertIsNone(client.find_customer_by_email('right@example.com'))

    def test_refund_retries_reuse_a_stable_idempotency_key(self):
        client = FlutterwaveClient(FlutterwaveConfig(
            client_id='merchant', client_secret='secret'))
        seen_keys = []

        def request(*args, **kwargs):
            seen_keys.append(kwargs['idempotency_key'])
            return response({'data': {'id': 'refund1', 'status': 'pending'}})

        client._v4_request = request
        client.refund_transaction('charge1', Decimal('10.00'), 'NGN', 'duplicate')
        client.refund_transaction('charge1', Decimal('10.00'), 'NGN', 'duplicate')

        self.assertEqual(seen_keys[0], seen_keys[1])

    def test_refund_accepts_a_persisted_operation_key(self):
        client = FlutterwaveClient(FlutterwaveConfig(
            client_id='merchant', client_secret='secret'))
        seen_keys = []

        def request(*args, **kwargs):
            seen_keys.append(kwargs['idempotency_key'])
            return response({'data': {'id': 'refund1', 'status': 'pending'}})

        client._v4_request = request
        client.refund_transaction(
            'charge1', 10, idempotency_key='refund-operation-123')

        self.assertEqual(seen_keys, ['refund-operation-123'])

    def test_hosted_verification_uses_order_amount_without_processor_fee(self):
        client = FlutterwaveClient(FlutterwaveConfig(v3_secret_key='test-key'))
        client._v3_request = Mock(return_value=response({'data': {
            'id': 123, 'tx_ref': 'flw-order', 'status': 'successful',
            'amount': 1000, 'charged_amount': 1015, 'currency': 'NGN',
            'customer': {'email': 'buyer@example.com'},
        }}))

        verified = client.verify_hosted_checkout(123)

        self.assertEqual(verified['amount_minor'], 100000)
        self.assertEqual(verified['customer_email'], 'buyer@example.com')

    def test_hosted_refund_uses_v3_transaction_endpoint(self):
        client = FlutterwaveClient(FlutterwaveConfig(v3_secret_key='test-key'))
        client._v3_request = Mock(return_value=response({
            'status': 'success',
            'data': {'id': 45, 'tx_id': 123, 'amount_refunded': 500,
                     'status': 'processing'},
        }))

        result = client.refund_hosted_checkout(123, amount=Decimal('500.00'))

        self.assertEqual(result['refund_id'], '45')
        self.assertEqual(result['status'], 'processing')
        self.assertIn('/transactions/123/refund', client._v3_request.call_args.args[1])

    def test_hosted_lookup_requires_the_requested_reference(self):
        client = FlutterwaveClient(FlutterwaveConfig(v3_secret_key='test-key'))
        client._v3_request = Mock(return_value=response({
            'status': 'success', 'data': {'id': 123, 'tx_ref': 'other'},
        }))

        with self.assertRaises(RuntimeError):
            client.find_hosted_checkout('expected')

    def test_minor_unit_round_trip_respects_currency_exponent(self):
        for currency, minor in (
            ('KWD', 1234),
            ('BHD', 1001),
            ('XOF', 1234),
            ('NGN', 1234),
        ):
            with self.subTest(currency=currency):
                major = from_minor_units(minor, currency)
                self.assertEqual(to_minor_units(major, currency), minor)

    def test_pay_to_address_methods_are_not_charge_methods(self):
        self.assertNotIn('crypto', SUPPORTED_CHARGE_METHODS)
        self.assertTrue(all(
            'crypto' not in methods
            for methods in CURRENCY_CHARGE_METHODS.values()
        ))
        self.assertEqual(PAY_TO_ADDRESS_METHODS, frozenset({'crypto'}))
        self.assertEqual(
            STABLECOIN_COLLECTION_METHODS['USDC'],
            frozenset({'crypto'}),
        )


if __name__ == '__main__':
    unittest.main()
