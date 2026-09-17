"""Deterministic, offline lifecycle tests. Never construct a live broker."""
import copy
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from backend import strategies as s
from backend.umbra_broker import market_payload, receipt


class Clock(datetime):
    value = datetime(2026, 9, 14, 9, 30, 1, tzinfo=s.IST)

    @classmethod
    def now(cls, tz=None):
        return cls.value


class Broker:
    customer_id = "account-test"

    def __init__(self):
        self.book = []
        self.calls = []
        self.net = 0
        self.uncertain = False
        self.partial = False
        self.cancel_calls = 0

    def reports(self):
        return copy.deepcopy(self.book)

    def instruments(self, symbols):
        return {symbol: {"code": 123, "lot": 1} for symbol in symbols}

    def quote(self, code):
        return Decimal("101")

    def net_position(self, symbol):
        return self.net

    def place(self, symbol, code, side, quantity):
        self.calls.append((symbol, code, side, quantity))
        if self.uncertain:
            raise TimeoutError("Simulated response loss after submission")
        order_id = str(len(self.calls))
        filled = 3 if self.partial and len(self.calls) == 1 else quantity
        self.net += filled * (1 if side == "BUY" else -1)
        self.book.append({"orderId": order_id, "rmsCode": "test", "exchange": "NC", "tradingSymbol": symbol,
                          "orderQty": quantity, "execQty": filled, "buySell": "B" if side == "BUY" else "S",
                          "orderStatus": "PartiallyExecuted" if filled < quantity else "FullyExecuted"})
        return {"order_id": order_id, "rms_code": "test"}

    def cancel(self, order, row):
        self.cancel_calls += 1
        self.book[0]["orderStatus"] = "Cancelled"


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.broker = Broker()
        Clock.value = datetime(2026, 9, 14, 9, 30, 1, tzinfo=s.IST)
        for target, value in [("DB_PATH", Path(folder.name) / "test.sqlite3"), ("datetime", Clock)]:
            p = patch.object(s, target, value); p.start(); self.addCleanup(p.stop)
        self.settings = s.UmbraSettings(order_value="1000", entry_time="09:30")
        with s.database() as db:
            db.execute("INSERT INTO settings VALUES ('umbra', ?)", (self.settings.model_dump_json(),))
        s.store_control({"enabled": True, "since": "2026-09-14T09:00:00+05:30", "customer_id": "account-test"})
        p = patch.object(s, "preview", return_value={"date": "2026-09-14", "candidates": [{"symbol": "AAA", "side": "SELL"}]})
        p.start(); self.addCleanup(p.stop)

    def tick(self, hour=9, minute=30):
        Clock.value = Clock.value.replace(hour=hour, minute=minute)
        s.tick(Clock.value, lambda: self.broker)



    def test_disabled_never_enters(self):
        s.store_control({"enabled": False}); self.tick()
        self.assertEqual(self.broker.calls, [])

    def test_entries_only_even_at_old_exit_time_and_after_restart(self):
        self.tick(); self.tick()
        self.assertEqual(self.broker.calls, [("AAA", 123, "SELL", 9)])
        run = s.all_runs()[0]
        self.assertNotIn("exit_at", run)
        self.assertEqual(run["orders"][0]["filled_quantity"], 9)
        self.assertEqual(run["orders"][0]["state"], "filled")
        # A legacy receipt with an old scheduled exit must not reactivate exits.
        run.update(state="open", exit_at="2026-09-14T15:00:00+05:30")
        run["orders"][0]["state"] = "open"
        s.store_run(run)
        self.tick(15, 0); self.tick(15, 20)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(self.broker.cancel_calls, 0)
        self.assertEqual(self.broker.net, -9)

    def test_partial_entries_are_observed_without_cancel_or_exit(self):
        self.broker.partial = True
        self.tick(); self.tick(15, 0)
        self.assertEqual(self.broker.cancel_calls, 0)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(s.all_runs()[0]["orders"][0]["filled_quantity"], 3)

    def test_turning_off_does_not_close_position(self):
        self.tick()
        s.store_control({"enabled": False})
        self.tick(15, 0)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(self.broker.net, -9)

    def test_legacy_exit_setting_is_ignored(self):
        settings = s.UmbraSettings.model_validate({**self.settings.model_dump(), "exit_time": "15:00"})
        self.assertNotIn("exit_time", settings.model_dump())


    def test_uncertain_entry_is_not_retried_or_blindly_exited(self):
        self.broker.uncertain = True
        self.tick(); self.tick(); self.tick(15, 0)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(s.all_runs()[0]["state"], "attention")



    def test_preexisting_orders_skip_entry(self):
        self.broker.book = [{"exchange": "NC", "tradingSymbol": "AAA", "orderId": "manual"}]
        self.tick(); self.assertEqual(self.broker.calls, [])

    def test_changed_account_does_not_enter(self):
        self.broker.customer_id = "other-account"
        self.tick(); self.assertEqual(self.broker.calls, [])
        self.assertFalse(s.load_control()["enabled"])

    def test_missed_entry_and_weekend(self):
        self.tick(9, 32); self.assertEqual(self.broker.calls, [])
        self.assertEqual(s.all_runs()[0]["state"], "skipped")
        Clock.value = datetime(2026, 9, 19, 9, 30, 1, tzinfo=s.IST)
        self.tick(); self.assertEqual(self.broker.calls, [])

    def test_restart_with_pending_submission_never_repeats(self):
        self.tick()
        run = s.all_runs()[0]; run["orders"][0]["state"] = "entry_sending"; s.store_run(run)
        self.tick(); self.tick(15, 0)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(s.all_runs()[0]["state"], "attention")



    def test_market_bt_payload_has_no_stop(self):
        for side, expected in [("BUY", "B"), ("SELL", "S")]:
            payload = market_payload("account", "login", "AAA", 123, side, 9)
            self.assertEqual(payload["productType"], "BIGTRADE")
            self.assertEqual(payload["transactionType"], expected)
            self.assertEqual(payload["price"], "0")
            self.assertEqual(payload["triggerPrice"], "0")
            self.assertEqual(payload["requestType"], "NEW")
        with self.assertRaises(ValueError):
            receipt({"status": 200, "data": {"errormsg": "rejected"}})

    def test_broker_rejected_entry_has_no_exit(self):
        self.tick()
        self.broker.book[0].update(execQty=0, orderStatus="Rejected")
        self.broker.net = 0
        self.tick(15, 0)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(s.all_runs()[0]["state"], "complete")



    def test_account_change_does_not_exit_other_account(self):
        self.tick(); self.broker.customer_id = "different-account"
        self.tick(15, 0)
        self.assertEqual(len(self.broker.calls), 1)

    def test_enable_after_entry_does_not_trade_immediately(self):
        control = s.load_control(); control["since"] = "2026-09-14T09:30:01+05:30"; s.store_control(control)
        self.tick(); self.assertEqual(self.broker.calls, [])

    def test_below_one_share_value_does_not_submit(self):
        with patch.object(self.broker, "quote", return_value=Decimal("1001")):
            self.tick()
        self.assertEqual(self.broker.calls, [])

    def test_arming_checks_session_without_submitting(self):
        s.store_control({"enabled": False})
        with patch.object(s, "ShareConnectBT", return_value=self.broker), patch.object(s, "WORKER_ACTIVE", True):
            self.assertTrue(s.toggle(True)["enabled"])
            self.assertEqual(self.broker.calls, [])
            self.assertFalse(s.toggle(False)["enabled"])

    def test_old_stop_loss_setting_is_discarded(self):
        settings = s.UmbraSettings.model_validate({**self.settings.model_dump(), "stop_loss_percent": "1"})
        self.assertNotIn("stop_loss_percent", settings.model_dump())

    def test_review_requires_flat_broker_position(self):
        self.tick()
        run = s.all_runs()[0]; run["state"] = "attention"; s.store_run(run)
        with patch.object(s, "ShareConnectBT", return_value=self.broker):
            with self.assertRaises(ValueError):
                s.verify_closed(run["day"])
            self.broker.net = 0
            s.verify_closed(run["day"])
        self.assertEqual(s.all_runs()[0]["state"], "complete")
        self.assertEqual(len(self.broker.calls), 1)


    def reschedule(self, time):
        with patch.object(s, "ShareConnectBT", return_value=self.broker), patch.object(s, "WORKER_ACTIVE", True):
            s.toggle(False)
            s.save_settings(s.UmbraSettings(order_value="1000", entry_time=time))
            s.toggle(True)

    def test_empty_run_can_be_rescheduled_without_overwriting_history(self):
        with patch.object(s, "preview", return_value={"date": "2026-09-14", "candidates": []}):
            self.tick()
        self.reschedule("09:40")
        self.tick(9, 39)
        self.assertEqual(self.broker.calls, [])
        self.tick(9, 40); self.tick(9, 40)
        self.assertEqual(len(self.broker.calls), 1)
        runs = s.all_runs()
        self.assertEqual(len(runs), 2)
        self.assertIn("No qualifying stocks", runs[0]["message"])
        self.assertEqual(runs[0]["orders"], [])
        self.assertEqual(runs[1]["settings"]["entry_time"], "09:40")

    def test_reschedule_keeps_existing_position_duplicate_guard(self):
        self.tick(); self.tick()
        self.reschedule("09:40")
        self.tick(9, 40)
        self.assertEqual(len(self.broker.calls), 1)
        self.assertEqual(s.all_runs()[1]["orders"][0]["state"], "skipped")

    def test_same_time_cannot_be_rearmed(self):
        with patch.object(s, "preview", return_value={"date": "2026-09-14", "candidates": []}):
            self.tick()
        with self.assertRaisesRegex(ValueError, "already ran"):
            self.reschedule("09:30")
        self.assertFalse(s.load_control()["enabled"])

    def test_preparation_failure_remains_visible_and_blocks_reschedule(self):
        with patch.object(s, "preview", side_effect=ValueError("Sheet unavailable")):
            self.tick()
        self.tick()
        self.assertEqual(s.all_runs()[0]["state"], "attention")
        self.assertIn("Entry preparation failed", s.all_runs()[0]["message"])
        with self.assertRaises(ValueError):
            self.reschedule("09:40")
        self.assertEqual(self.broker.calls, [])

    def test_legacy_daily_record_still_deduplicates_old_time(self):
        run = {"day": "2026-09-14", "state": "complete", "orders": [],
               "settings": self.settings.model_dump(mode="json"), "entry_at": "2026-09-14T09:30:00+05:30"}
        s.store_run(run)
        self.tick()
        self.assertEqual(self.broker.calls, [])
        self.reschedule("09:40"); self.tick(9, 40)
        self.assertEqual(len(s.all_runs()), 2)
        self.assertEqual(len(self.broker.calls), 1)


if __name__ == "__main__":
    unittest.main()
