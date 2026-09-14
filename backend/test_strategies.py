import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

from pydantic import ValidationError
from backend import strategies as s


class UmbraTests(unittest.TestCase):
    def test_selection_direction_and_eligibility(self):
        rows = [s.HEADERS,
                ["2026-09-12", "Alpha", "AAA", "Positive", "Yes", "Yes"],
                ["2026-09-12", "Beta", "BBB", "Negative", "Yes", "Yes"],
                ["2026-09-11", "Old", "OLD", "Positive", "Yes", "Yes"],
                ["2026-09-12", "Excluded", "BIG", "Positive", "No", "Yes"],
                ["2026-09-12", "Excluded", "NO", "Positive", "No", "No"],
                ["2026-09-12", "Unknown", "UNK", "Positive", "", "Yes"],
                ["2026-09-12", "Neutral", "NEU", "Neutral", "Yes", "Yes"]]
        result = s.select_candidates(rows, date(2026, 9, 12))
        self.assertEqual([(r["symbol"], r["side"]) for r in result["candidates"]], [("AAA", "SELL"), ("BBB", "BUY")])
        self.assertEqual(len(result["skipped"]), 4)

    def test_duplicate_and_conflicting_rows(self):
        row = ["2026-09-12", "Alpha", "AAA", "Positive", "Yes", "Yes"]
        self.assertEqual(len(s.select_candidates([s.HEADERS, row, row], date(2026, 9, 12))["candidates"]), 1)
        other = row.copy(); other[3] = "Negative"
        self.assertEqual(s.select_candidates([s.HEADERS, row, other], date(2026, 9, 12))["candidates"], [])
        other = row.copy(); other[4] = "No"
        self.assertEqual(s.select_candidates([s.HEADERS, row, other], date(2026, 9, 12))["candidates"], [])

    def test_dates_and_schema_fail_closed(self):
        expected = date(2026, 9, 12)
        for raw in ["2026-09-12", "12/09/2026", "12-09-2026", (expected - date(1899, 12, 30)).days]:
            self.assertEqual(s.parse_day(raw), expected)
        self.assertIsNone(s.parse_day("#VALUE!"))
        with self.assertRaises(ValueError):
            s.select_candidates([s.HEADERS[:4]], expected)

    def test_settings_validation_and_persistence(self):
        for fields in [{"order_value": -1}, {"order_value": "NaN"}, {"entry_time": "08:30"},
                       {"entry_time": "25:00"}]:
            with self.assertRaises(ValidationError):
                s.UmbraSettings(**fields)
        with tempfile.TemporaryDirectory() as folder, patch.object(s, "DB_PATH", Path(folder) / "test.sqlite3"):
            settings = s.UmbraSettings(order_value="1000", entry_time="09:30")
            state = s.save_settings(settings)
            self.assertEqual(s.load_settings(), settings)
            self.assertNotIn("stop_loss_percent", state["settings"])
            self.assertFalse(state["enabled"])
            self.assertFalse(state["execution_ready"])

    def test_preview_reads_only_tracker(self):
        client = MagicMock()
        client.get.return_value.ok = True
        client.get.return_value.json.return_value = {"values": [s.HEADERS]}
        with patch.object(s.google_sheets, "session") as session:
            session.return_value.__enter__.return_value = client
            self.assertEqual(s.preview()["candidates"], [])
        self.assertIn("%27Tracker%27%21A%3AF", client.get.call_args.args[0])
        client.post.assert_not_called()

    def test_enable_without_required_settings_is_rejected(self):
        from backend.main import toggle_umbra, UmbraToggle
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as folder, patch.object(s, "DB_PATH", Path(folder) / "test.sqlite3"):
            with self.assertRaises(HTTPException) as error:
                toggle_umbra(UmbraToggle(enabled=True))
            self.assertEqual(error.exception.status_code, 409)
            self.assertFalse(toggle_umbra(UmbraToggle(enabled=False))["enabled"])


if __name__ == "__main__":
    unittest.main()
