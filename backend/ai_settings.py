"""Persist one active AI connection without returning secrets to the browser."""
import json
import os
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

SETTINGS_PATH = Path(os.environ.get("CREDENTIALS_PATH", Path(__file__).resolve().parent.parent / "Credentials.txt")).with_name("AISettings.json")


class AISettings(BaseModel):
    provider: str = Field(default="OpenAI", min_length=1, max_length=80)
    protocol: Literal["responses", "chat", "anthropic"] = "responses"
    base_url: str = "https://api.openai.com/v1"
    model: str = Field(default="gpt-4o-mini", min_length=1, max_length=150)
    api_key: str = Field(default="", max_length=4096)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Use an HTTPS API base URL without credentials, query parameters, or fragments.")
        return value.rstrip("/")

    @field_validator("provider", "model")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("This field cannot be blank.")
        return value.strip()


def load_settings():
    if SETTINGS_PATH.exists():
        return AISettings(**json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
    return AISettings(api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
                      model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))


def public_settings(settings):
    return {**settings.model_dump(exclude={"api_key"}), "has_key": bool(settings.api_key)}


def save_settings(settings):
    previous = load_settings()
    if not settings.api_key.strip():
        if (settings.provider, settings.protocol, settings.base_url) != (previous.provider, previous.protocol, previous.base_url):
            raise ValueError("Enter a new API key when changing the provider or API address.")
        settings.api_key = previous.api_key
    else:
        settings.api_key = settings.api_key.strip()
    if not settings.api_key:
        raise ValueError("Enter an API key.")
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=SETTINGS_PATH.parent, prefix=".ai-settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(settings.model_dump(), target)
        os.replace(name, SETTINGS_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return public_settings(settings)
