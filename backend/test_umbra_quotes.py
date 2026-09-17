"""Offline regression tests for ShareConnect's observed feed format."""
import json
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch

from backend import umbra_broker as broker


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 17, 12, 40, 10, tzinfo=broker.IST)


def quote(**changes):
    row = dict(exchangeCode="NC", scripCode=2885, ltp="1245.6",
               lastUpdatedTime="09/17/2026 12:40:09")
    row.update(changes)
    return row


class QuoteTests(unittest.TestCase):
    def fetch(self, messages):
        socket = Mock()
        socket.recv.side_effect = [json.dumps(m) for m in messages]
        adapter = object.__new__(broker.ShareConnectBT)
        adapter.token = "offline-test"
        # The last clock check ends the bounded receive loop.
        ticks = [0] + [1] * len(messages) + [9]
        with patch("websocket.create_connection", return_value=socket), \
             patch.object(broker, "datetime", Clock), \
             patch.object(broker.time, "monotonic", side_effect=ticks):
            try:
                return adapter.quote(2885)
            finally:
                socket.close.assert_called_once()

    def test_text_acknowledgements_before_month_first_quote(self):
        self.assertEqual(self.fetch([
            {"data": "subscribed"}, {"data": "feed acknowledged"},
            {"data": [quote()]},
        ]), Decimal("1245.6"))

    def test_malformed_and_unrelated_records_do_not_hide_next_quote(self):
        self.assertEqual(self.fetch([
            None, "heartbeat", {"data": {}},
            {"data": ["ack", None, quote(scripCode=3045),
                      quote(exchangeCode="BC"), quote(lastUpdatedTime=None),
                      quote(ltp="bad"), quote()]},
        ]), Decimal("1245.6"))

    def test_stale_future_and_invalid_prices_remain_rejected(self):
        for changes in [dict(lastUpdatedTime="09/17/2026 12:39:39"),
                        dict(lastUpdatedTime="09/17/2026 12:40:11"),
                        dict(lastUpdatedTime="17/09/2026 12:40:09"),
                        dict(ltp="NaN"), dict(ltp="Infinity"),
                        dict(ltp="0"), dict(ltp="-1")]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "No fresh market quote"):
                self.fetch([{"data": [quote(**changes)]}])


if __name__ == "__main__":
    unittest.main()
