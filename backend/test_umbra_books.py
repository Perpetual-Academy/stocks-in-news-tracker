"""Offline tests for successful empty ShareConnect order/trade history."""
import json
import unittest
from unittest.mock import Mock

from backend.umbra_broker import ShareConnectBT, data_of, receipt


class BookTests(unittest.TestCase):
    def setUp(self):
        self.broker = object.__new__(ShareConnectBT)
        self.broker.customer_id = "test-account"
        self.broker.client = Mock()

    def test_successful_no_records_is_empty_for_both_books(self):
        self.broker.client.reports.return_value = json.dumps(
            {"status": 200, "message": "orders_history", "data": "no_records"})
        self.broker.client.trades.return_value = {
            "status": 200, "message": "trades_history", "data": "no_records"}
        self.assertEqual(self.broker.reports(), [])
        self.assertEqual(self.broker.positions(), [])
        self.assertEqual(self.broker.net_position("RELIANCE"), 0)
        self.broker.client.placeOrder.assert_not_called()

    def test_existing_orders_remain_visible(self):
        rows = [{"orderId": "test-order", "tradingSymbol": "RELIANCE"}]
        self.broker.client.reports.return_value = {"status": "200", "data": rows}
        self.assertEqual(self.broker.reports(), rows)

    def test_unknown_or_failed_responses_still_block(self):
        for response in [
            {"status": 401, "data": "no_records"},
            {"status": 500, "data": "no_records"},
            {"status": 200, "data": "session_expired"},
            {"status": 200, "data": None},
            {"status": 200, "data": {}},
            {"status": 200},
        ]:
            for method in ["reports", "trades"]:
                getattr(self.broker.client, method).return_value = response
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    self.broker.reports()
                with self.assertRaises(ValueError):
                    self.broker.positions()

    def test_sentinel_not_accepted_for_instruments_or_order_receipts(self):
        response = {"status": 200, "data": "no_records"}
        with self.assertRaises(ValueError):
            data_of(response, list)
        with self.assertRaises(ValueError):
            receipt(response)

    def test_incomplete_order_rows_still_block(self):
        self.broker.client.reports.return_value = {"status": 200, "data": [{}]}
        with self.assertRaisesRegex(ValueError, "Incomplete broker order book"):
            self.broker.reports()


if __name__ == "__main__":
    unittest.main()
