"""Fetch Groq model choices without saving or returning the API key."""
import json
import urllib.request
import urllib.error
from backend.ai_settings import load_settings
from backend.ai_influence import NoRedirect


def groq_models(settings):
    if (settings.provider, settings.protocol, settings.base_url) != (
        "Groq", "chat", "https://api.groq.com/openai/v1"
    ):
        raise ValueError("Model loading requires the standard Groq API address and Chat Completions format.")
    key = settings.api_key.strip()
    if not key:
        saved = load_settings()
        if (saved.provider, saved.protocol, saved.base_url) == (settings.provider, settings.protocol, settings.base_url):
            key = saved.api_key
    if not key:
        raise ValueError("Enter your Groq API key before loading models.")
    request = urllib.request.Request(settings.base_url + "/models", headers={
        "Authorization": f"Bearer {key}", "User-Agent": "StocksInNewsTracker/1.0",
        "Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        messages = {401: "Groq rejected the API key. Check that you entered a valid Groq key.",
                    403: "Groq blocked the request. Check your account permissions or network access.",
                    429: "Groq is rate-limiting requests. Wait briefly, then load models again."}
        raise ValueError(messages.get(exc.code, "Groq could not return models. Please retry shortly.")) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError("The server could not connect to Groq. Check its internet connection and retry.") from exc
    if not isinstance(data.get("data"), list):
        raise RuntimeError("Invalid model list")
    # Exclude known audio-only and safety-classifier families from article analysis.
    excluded = ("whisper", "orpheus", "prompt-guard", "safeguard")
    models = sorted({item["id"] for item in data["data"] if isinstance(item, dict)
                     and isinstance(item.get("id"), str) and item["id"]
                     and item.get("active", True) is not False
                     and not any(word in item["id"].lower() for word in excluded)})
    return {"models": models}
