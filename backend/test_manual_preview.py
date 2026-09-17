import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend import strategies
from backend.main import manual_umbra_preview


class ManualPreviewTests(unittest.TestCase):
    def test_repeat_preview_does_not_touch_live_strategy(self):
        selection = {"date": "2026-09-17", "timezone": "Asia/Kolkata",
                     "candidates": [{"symbol": "RELIANCE", "side": "SELL"}], "skipped": []}
        with patch.object(strategies, "preview", return_value=selection), \
             patch.object(strategies, "save_settings") as save, \
             patch.object(strategies, "store_run") as write_run, \
             patch.object(strategies, "toggle") as toggle, \
             patch.object(strategies, "ShareConnectBT") as broker:
            for time in ("11:40", "11:50"):
                result = manual_umbra_preview(strategies.UmbraSettings(order_value="5000", entry_time=time))
                self.assertTrue(result["manual_only"])
                self.assertEqual(result["review_time"], time)
                self.assertEqual(result["order_value"], "5000")
                self.assertEqual(result["candidates"], selection["candidates"])
            for mock in (save, write_run, toggle, broker):
                mock.assert_not_called()

    def test_missing_settings_rejected_before_reading_sheet(self):
        with patch.object(strategies, "preview") as preview:
            with self.assertRaises(HTTPException) as error:
                manual_umbra_preview(strategies.UmbraSettings())
            self.assertEqual(error.exception.status_code, 422)
            preview.assert_not_called()
