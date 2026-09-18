"""Limit-only submission tests; the broker client is always mocked."""
import unittest
from decimal import Decimal
from unittest.mock import Mock

from backend.umbra_broker import ShareConnectBT


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.broker = object.__new__(ShareConnectBT)
        self.broker.customer_id = "test"
        self.broker.login_id = "test"
        self.broker.client = Mock()
        self.broker.client.placeOrder.return_value = {
            "status": 200, "data": {"orderId": "test", "rmsCode": "test"}}

    def test_both_sides_send_explicit_positive_price(self):
        for side in ("BUY", "SELL"):
            self.broker.place("AAA", 123, side, 9, Decimal("101.05"))
            payload = self.broker.client.placeOrder.call_args.args[0]
            self.assertEqual(payload["price"], "101.05")
            self.assertEqual(payload["orderType"], "NORMAL")
            self.assertEqual(payload["transactionType"], "B" if side == "BUY" else "S")

    def test_invalid_price_never_reaches_broker(self):
        for price in (None, "", "bad", "0", "-1", "NaN", "Infinity"):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.broker.place("AAA", 123, "BUY", 1, price)
        self.broker.client.placeOrder.assert_not_called()

    def test_missing_price_never_reaches_broker(self):
        with self.assertRaises(TypeError):
            self.broker.place("AAA", 123, "BUY", 1)
        self.broker.client.placeOrder.assert_not_called()

    def test_rejection_has_no_market_fallback(self):
        self.broker.client.placeOrder.return_value = {"status": 400}
        with self.assertRaises(ValueError):
            self.broker.place("AAA", 123, "BUY", 1, "101.05")
        self.assertEqual(self.broker.client.placeOrder.call_count, 1)
