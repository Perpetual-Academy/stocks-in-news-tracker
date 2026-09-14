import unittest
from unittest.mock import patch
from backend.ai_settings import AISettings
from backend.stock_selection import apply_selection, news_items, contradicts


class SelectionTests(unittest.TestCase):
    def test_direct_only_and_not_bundled_independent_of_influence(self):
        rows = [{"id": f"1:{symbol}", "article": 1, "symbol": symbol, "stock": symbol,
                 "influence": influence} for symbol, influence in [("INFY", "negative"), ("TCS", "positive"), ("SBIN", "neutral")]]
        answers = [{"id": "1:INFY", "direct_operations": True, "bundled": False, "selection_category": "eligible", "reason": "Standalone results."},
                   {"id": "1:TCS", "direct_operations": False, "bundled": False, "selection_category": "indirect", "reason": "Peer comparison only."},
                   {"id": "1:SBIN", "direct_operations": True, "bundled": True, "selection_category": "bundled", "reason": "Joint news item."}]
        with patch("backend.stock_selection.load_settings", return_value=AISettings(api_key="test")), patch("backend.stock_selection.analyze", return_value=answers):
            result = apply_selection("News", {"stocks": rows})
        self.assertEqual([r["auto_selected"] for r in result["stocks"]], [True, False, False])
        self.assertEqual(rows[0]["influence"], "negative")

    def test_unavailable_review_leaves_rows_unchecked(self):
        rows = [{"id": "1:TCS"}]
        with patch("backend.stock_selection.load_settings", return_value=AISettings(api_key="")):
            result = apply_selection("News", {"stocks": rows})
        self.assertFalse(rows[0]["auto_selected"])
        self.assertIn("selection_notice", result)

    def test_no_stocks_does_not_request_ai(self):
        with patch("backend.stock_selection.analyze") as mock:
            self.assertEqual(apply_selection("Weather", {"stocks": []}), {"stocks": []})
        mock.assert_not_called()


class RegressionTests(unittest.TestCase):
    def test_headed_roundup_preserves_paragraphs(self):
        text = "HUDCO: JCR upgraded its credit rating.\n\nThe outlook is stable.\n\nSterlite Technologies: Capacity will increase 50%."
        parts = news_items(text)
        self.assertEqual(len(parts), 2)
        self.assertIn("outlook", parts[0])
        self.assertNotIn("Sterlite", parts[0])

    def test_operational_exclusion_gets_second_review(self):
        for symbol, item in [("HUDCO", "HUDCO: JCR upgraded its credit rating."),
                             ("STLTECH", "Sterlite Technologies: Capacity will increase 50%.")]:
            self.assertTrue(contradicts({"selection_category": "indirect", "direct_operations": False, "bundled": False},
                                       {"symbol": symbol, "news_items": [item]}))

    def test_roundup_expected_selection_after_review(self):
        from backend.extractor import extract_articles
        text = "HUDCO: JCR upgraded its credit rating.\n\nSterlite Technologies: Capacity will increase 50%.\n\nCeigall India: Received a project LoI from REC Power Development and Consultancy.\n\nBSE: Sector-wide SEBI policy commentary."
        result = extract_articles(text)
        expected = {"HUDCO": True, "STLTECH": True, "CEIGALL": True, "RECLTD": False, "BSE": False}
        def answer(_, candidates, selection):
            return [{"id": c["id"], "selection_category": ("eligible" if expected[c["symbol"]] else "sector" if c["symbol"] == "BSE" else "indirect"),
                     "direct_operations": expected[c["symbol"]], "bundled": False, "reason": "Selection explanation.",
                     "selection_evidence": c["news_items"][0]} for c in candidates]
        with patch("backend.stock_selection.load_settings", return_value=AISettings(api_key="test")), patch("backend.stock_selection.analyze", side_effect=answer):
            actual = apply_selection(text, result)
        self.assertEqual({r["symbol"]: r["auto_selected"] for r in actual["stocks"]}, expected)
