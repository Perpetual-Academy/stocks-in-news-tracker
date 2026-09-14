import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch
from backend.ai_models import groq_models
from backend.ai_settings import AISettings


class ModelTests(unittest.TestCase):
    def settings(self, **kwargs):
        return AISettings(provider="Groq", protocol="chat", base_url="https://api.groq.com/openai/v1", **kwargs)

    def test_fetch_filters_and_does_not_expose_key(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"data": [
            {"id": "text-model"}, {"id": "text-model"}, {"id": "whisper-large-v3"},
            {"id": "inactive", "active": False}, {"id": "canopylabs/orpheus-v1"}]}).encode()
        with patch("urllib.request.OpenerDirector.open", return_value=response) as mock:
            result = groq_models(self.settings(api_key="test-secret"))
        self.assertEqual(result, {"models": ["text-model"]})
        self.assertNotIn("test-secret", json.dumps(result))
        self.assertEqual(mock.call_args.args[0].full_url, "https://api.groq.com/openai/v1/models")
        self.assertEqual(mock.call_args.args[0].get_header("User-agent"), "StocksInNewsTracker/1.0")

    def test_provider_errors_are_actionable_and_redacted(self):
        for code, phrase in [(401, "rejected the API key"), (403, "blocked the request"), (429, "rate-limiting")]:
            with patch("urllib.request.OpenerDirector.open", side_effect=urllib.error.HTTPError(
                "https://api.groq.com/openai/v1/models", code, "private provider message", {}, None)):
                with self.assertRaisesRegex(ValueError, phrase):
                    groq_models(self.settings(api_key="test-secret"))

    def test_missing_key_and_wrong_destination(self):
        with patch("backend.ai_models.load_settings", return_value=AISettings(api_key="different-secret")), patch("urllib.request.OpenerDirector.open") as mock:
            with self.assertRaises(ValueError):
                groq_models(self.settings())
            with self.assertRaises(ValueError):
                groq_models(AISettings(api_key="test-secret"))
            mock.assert_not_called()

    def test_saved_groq_key(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data": []}'
        with patch("backend.ai_models.load_settings", return_value=self.settings(api_key="saved-key")), patch("urllib.request.OpenerDirector.open", return_value=response) as mock:
            self.assertEqual(groq_models(self.settings()), {"models": []})
        self.assertEqual(mock.call_args.args[0].get_header("Authorization"), "Bearer saved-key")
