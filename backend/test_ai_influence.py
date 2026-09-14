import json
import os
import unittest
from unittest.mock import patch, MagicMock

from backend.extractor import extract_articles
from backend.ai_settings import AISettings
from backend.ai_influence import apply_ai_fallback, analyze


class AIFallbackTests(unittest.TestCase):
    def test_only_unresolved_stocks_sent(self):
        text = "Infosys revenue grew. TCS opens a new delivery centre."
        with patch("backend.ai_influence.analyze", return_value=[{
            "id": "1:TCS", "influence": "positive", "reason": "Additional delivery capacity."}]) as mock:
            result = apply_ai_fallback(text, extract_articles(text))
        self.assertEqual([r["symbol"] for r in mock.call_args.args[1]], ["TCS"])
        self.assertEqual([r["analysis_source"] for r in result["stocks"]], ["keywords", "ai"])

    def test_mixed_signals_do_not_call_ai(self):
        text = "Infosys revenue grew but Infosys profit fell."
        with patch("backend.ai_influence.analyze") as mock:
            result = apply_ai_fallback(text, extract_articles(text))
        mock.assert_not_called()
        self.assertEqual(result["stocks"][0]["influence"], "neutral")

    def test_missing_key_and_provider_failure_are_not_neutral(self):
        for error in (ValueError("Missing key"), TimeoutError(), ValueError("Malformed response")):
            with patch("backend.ai_influence.analyze", side_effect=error):
                result = apply_ai_fallback("TCS announces a meeting.", extract_articles("TCS announces a meeting."))
            self.assertEqual(result["stocks"][0]["influence"], "unavailable")
            self.assertIn("ai_notice", result)

    def test_response_validation_and_request(self):
        candidate = {"id": "1:TCS", "symbol": "TCS"}
        for response_id in ("1:TCS", "1:UNKNOWN"):
            body = {"status": "completed", "output": [{"type": "message", "content": [{
                "type": "output_text", "text": json.dumps({"stocks": [{"id": response_id,
                    "influence": "neutral", "reason": "No clear impact."}]})}]}]}
            connection = MagicMock()
            connection.__enter__.return_value.read.return_value = json.dumps(body).encode()
            with patch("backend.ai_influence.load_settings", return_value=AISettings(api_key="test-only")), patch(
                "backend.ai_influence.urllib.request.OpenerDirector.open", return_value=connection) as mock:
                if response_id == "1:TCS":
                    self.assertEqual(analyze("Article", [candidate])[0]["influence"], "neutral")
                    self.assertFalse(json.loads(mock.call_args.args[0].data)["store"])
                else:
                    with self.assertRaises(ValueError):
                        analyze("Article", [candidate])


if __name__ == "__main__":
    unittest.main()
