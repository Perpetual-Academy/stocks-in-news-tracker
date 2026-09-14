import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from pydantic import ValidationError
from backend.ai_settings import AISettings, load_settings, save_settings, public_settings
from backend.ai_influence import analyze


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch("backend.ai_settings.SETTINGS_PATH", Path(self.directory.name) / "AISettings.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_and_preserve_without_disclosing(self):
        result = save_settings(AISettings(api_key="test-key"))
        self.assertNotIn("api_key", result)
        self.assertTrue(result["has_key"])
        save_settings(AISettings(model="another-model"))
        self.assertEqual(load_settings().api_key, "test-key")
        self.assertNotIn("test-key", json.dumps(public_settings(load_settings())))

    def test_new_destination_requires_new_key(self):
        save_settings(AISettings(api_key="test-key"))
        with self.assertRaises(ValueError):
            save_settings(AISettings(base_url="https://example.com/v1"))
        self.assertEqual(load_settings().base_url, "https://api.openai.com/v1")

    def test_reject_unsafe_url(self):
        for url in ["http://example.com", "https://user:key@example.com", "https://example.com?key=secret"]:
            with self.assertRaises(ValidationError):
                AISettings(base_url=url)

    def test_provider_requests(self):
        answer = json.dumps({"stocks": [{"id": "1:TCS", "influence": "neutral", "reason": "No clear impact."}]})
        for protocol in ("chat", "anthropic"):
            save_settings(AISettings(provider="Custom", protocol=protocol, base_url="https://example.com/v1", model="test-model", api_key="test-key"))
            body = ({"choices": [{"finish_reason": "stop", "message": {"content": answer}}]} if protocol == "chat" else
                    {"stop_reason": "end_turn", "content": [{"type": "text", "text": answer}]})
            connection = MagicMock()
            connection.__enter__.return_value.read.return_value = json.dumps(body).encode()
            with patch("urllib.request.OpenerDirector.open", return_value=connection) as mock:
                self.assertEqual(analyze("Article", [{"id": "1:TCS"}])[0]["influence"], "neutral")
            request = mock.call_args.args[0]
            self.assertTrue(request.full_url.endswith("/messages" if protocol == "anthropic" else "/chat/completions"))
            self.assertEqual(request.get_header("X-api-key" if protocol == "anthropic" else "Authorization"),
                             "test-key" if protocol == "anthropic" else "Bearer test-key")
