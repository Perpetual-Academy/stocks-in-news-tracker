import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4
from backend import google_sheets as gs


class SheetsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for key, value in {"DATA_DIR": Path(temporary.name), "LEDGER": Path(temporary.name) / "ledger.sqlite3",
                           "CONFIG": Path(temporary.name) / "settings.json"}.items():
            p = patch.object(gs, key, value); p.start(); self.addCleanup(p.stop)
        self.client = MagicMock()
        self.client.get.return_value.ok = True
        self.rows = []
        self.row_count = 1000
        def read(url, **kwargs):
            response = MagicMock(ok=True)
            if url.endswith(gs.SPREADSHEET_ID):
                data = {"sheets": [{"properties": {"title": "Tracker", "gridProperties": {"rowCount": self.row_count}}}]}
            elif "B2" in url:
                data = {"values": self.rows}
            else:
                data = {"values": [gs.HEADERS]}
            response.json.return_value = data
            return response
        self.client.get.side_effect = read
        self.client.post.return_value.ok = True
        self.client.post.return_value.json.return_value = {"totalUpdatedCells": 3}
        p = patch.object(gs, "session"); mock = p.start(); self.addCleanup(p.stop)
        mock.return_value.__enter__.return_value = self.client
        self.payload = gs.AppendRequest(request_id=uuid4(), news_date="2026-09-05", stocks=[{"symbol": "INFY", "influence": "positive"}])

    def test_append_mapping_and_retry(self):
        first = gs.append_stocks(self.payload)
        self.assertEqual(gs.append_stocks(self.payload), first)
        self.client.post.assert_called_once()
        request = self.client.post.call_args.kwargs
        self.assertTrue(self.client.post.call_args.args[0].endswith('/values:batchUpdate'))
        self.assertEqual(request["json"], {"valueInputOption": "RAW", "data": [
            {"range": "'Tracker'!A2:B2", "values": [["2026-09-05", "Infosys Limited"]]},
            {"range": "'Tracker'!D2:D2", "values": [["Positive"]]},
        ]})

    def test_existing_values_and_formulas_are_preserved(self):
        self.rows = [["Existing", "=SYMBOL()", "Positive"], [], ["", "", "=IF(TRUE,\"\",\"\")"], ["", "=SYMBOL()"]]
        gs.append_stocks(self.payload)
        data = self.client.post.call_args.kwargs['json']['data']
        self.assertEqual([item['range'] for item in data], ["'Tracker'!A5:B5", "'Tracker'!D5:D5"])

    def test_capacity_does_not_insert_rows(self):
        self.row_count = 2
        self.rows = [["Existing"]]
        with self.assertRaisesRegex(ValueError, 'Not enough existing'):
            gs.append_stocks(self.payload)
        self.client.post.assert_not_called()

    def test_headers_block_write(self):
        self.client.get.side_effect = None
        self.client.get.return_value.json.return_value = {"values": [["Wrong header"]]}
        with self.assertRaisesRegex(ValueError, "headers"):
            gs.append_stocks(self.payload)
        self.client.post.assert_not_called()

    def test_timeout_blocks_duplicate(self):
        self.client.post.side_effect = TimeoutError()
        with self.assertRaisesRegex(ValueError, "uncertain"):
            gs.append_stocks(self.payload)
        with self.assertRaisesRegex(ValueError, "may already"):
            gs.append_stocks(self.payload)
        self.client.post.assert_called_once()

    def test_unknown_symbol_and_changed_retry(self):
        gs.append_stocks(self.payload)
        self.payload.stocks[0].influence = "negative"
        with self.assertRaisesRegex(ValueError, "different rows"):
            gs.append_stocks(self.payload)
        self.payload.stocks[0].symbol = "BOGUS_STOCK"
        with self.assertRaisesRegex(ValueError, "outside"):
            gs.append_stocks(self.payload)

    def test_credentials_reject_other_token_host(self):
        with self.assertRaisesRegex(ValueError, "valid Google"):
            gs.save_settings(gs.SheetsSettings(service_account_json='{"type":"service_account","token_uri":"https://example.com/token"}'))
        self.assertFalse(gs.CONFIG.exists())

    def test_connection_only_reads(self):
        gs.test_connection()
        self.client.post.assert_not_called()
