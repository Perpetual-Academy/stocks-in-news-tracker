from __future__ import annotations

import ast
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
CREDENTIALS_PATH = Path(os.environ.get("CREDENTIALS_PATH", ROOT_DIR / "Credentials.txt"))
FRONTEND_DIST_PATH = ROOT_DIR / "frontend" / "dist"
LIVE_INSTRUMENTS = {
    2885: "RELIANCE",
    3045: "SBIN",
    11483: "LT",
}


class LoginPayload(BaseModel):
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    request_token: str = Field(min_length=1)
    state: str = "12345"
    vendor_key: str | None = None
    version_id: str | None = None


app = FastAPI(title="ShareConnect Token Service")
app.add_middleware(
    CORSMiddleware,
    # Vite selects the next available port during local development, and the
    # app may be opened through either localhost or 127.0.0.1.
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def read_credentials() -> dict[str, str]:
    values: dict[str, str] = {}
    if not CREDENTIALS_PATH.exists():
        return values
    for raw_line in CREDENTIALS_PATH.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_credentials(updated: dict[str, str]) -> None:
    existing = read_credentials()
    existing.update({k: v for k, v in updated.items() if v is not None})
    lines = [f"{key} = {value}" for key, value in existing.items()]
    CREDENTIALS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def extract_access_token(result: Any) -> str:
    """Read the token from both current SDK responses and older saved envelopes."""
    value = result
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                try:
                    value = ast.literal_eval(stripped)
                except (SyntaxError, ValueError):
                    return stripped
        else:
            return stripped

    if isinstance(value, dict):
        direct = value.get("access_token") or value.get("accessToken") or value.get("token")
        if direct:
            return str(direct)
        data = value.get("data")
        if isinstance(data, dict):
            token = data.get("access_token") or data.get("accessToken") or data.get("token")
            if token:
                return str(token)
    return ""


def build_shareconnect_client(api_key: str, access_token: str, vendor_key: str | None = None) -> Any:
    try:
        from SharekhanApi.sharekhanConnect import SharekhanConnect  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency error surfaced to UI
        raise HTTPException(
            status_code=500,
            detail=(
                "ShareConnect SDK is not installed or could not be imported. "
                "Install the package named 'shareconnect' before running the backend."
            ),
        ) from exc

    if vendor_key:
        return SharekhanConnect(api_key=api_key, access_token=access_token, vendor_key=vendor_key)
    return SharekhanConnect(api_key=api_key, access_token=access_token)


def generate_access_token(payload: LoginPayload) -> str:
    try:
        from SharekhanApi.sharekhanConnect import SharekhanConnect  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=500,
            detail="ShareConnect SDK is not installed or could not be imported.",
        ) from exc

    try:
        login = SharekhanConnect(api_key=payload.api_key)
        if payload.version_id:
            session = login.generate_session(payload.request_token, payload.api_secret)
            result = login.get_access_token(
                payload.api_key,
                session,
                payload.state,
                versionId=payload.version_id,
            )
        else:
            session = login.generate_session_without_versionId(payload.request_token, payload.api_secret)
            result = login.get_access_token(payload.api_key, session, payload.state)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ShareConnect token request failed: {exc}",
        ) from exc

    token = extract_access_token(result)
    if not token:
        raise HTTPException(status_code=502, detail="ShareConnect did not return an access token.")
    return token


def market_status() -> dict[str, str | bool]:
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)
    is_weekday = now.weekday() < 5
    is_trading_time = (now.hour, now.minute) >= (9, 15) and (now.hour, now.minute) < (15, 30)
    is_open = is_weekday and is_trading_time
    return {
        "is_open": is_open,
        "label": "Market open" if is_open else "Market closed",
        "checked_at": now.isoformat(),
    }


def fetch_live_prices(access_token: str) -> list[dict[str, Any]]:
    try:
        import websocket  # type: ignore
    except Exception as exc:
        raise HTTPException(status_code=500, detail="The WebSocket feed dependency is unavailable.") from exc

    socket = None
    try:
        socket = websocket.create_connection(
            f"wss://stream.sharekhan.com/skstream/api/stream?ACCESS_TOKEN={access_token}",
            timeout=10,
        )
        socket.send(json.dumps({"action": "subscribe", "key": ["feed"], "value": [""]}))
        socket.send(
            json.dumps(
                {
                    "action": "feed",
                    "key": ["ltp"],
                    "value": [",".join(f"NC{code}" for code in LIVE_INSTRUMENTS)],
                }
            )
        )

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            raw_message = socket.recv()
            if not isinstance(raw_message, str):
                continue
            message = json.loads(raw_message)
            data = message.get("data")
            if not isinstance(data, list):
                continue
            prices = []
            for item in data:
                code = item.get("scripCode")
                if code not in LIVE_INSTRUMENTS:
                    continue
                prices.append(
                    {
                        "symbol": LIVE_INSTRUMENTS[code],
                        "scrip_code": code,
                        "ltp": item.get("ltp"),
                        "change": item.get("rsChange"),
                        "percent_change": item.get("perChange"),
                        "last_traded_at": item.get("ltt") or item.get("lastUpdatedTime"),
                    }
                )
            if prices:
                return sorted(prices, key=lambda item: list(LIVE_INSTRUMENTS).index(item["scrip_code"]))
        raise TimeoutError("No price data was returned by the live feed.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ShareConnect live feed failed: {exc}") from exc
    finally:
        if socket is not None:
            socket.close()


@app.get("/api/credentials")
def get_credentials() -> dict[str, str]:
    return read_credentials()


@app.post("/api/login")
def login(payload: LoginPayload) -> dict[str, Any]:
    access_token = generate_access_token(payload)
    write_credentials(
        {
            "api_key": payload.api_key,
            "api_secret": payload.api_secret,
            "request_token": payload.request_token,
            "access_token": access_token,
            "state": payload.state,
            "vendor_key": payload.vendor_key or "",
            "version_id": payload.version_id or "",
        }
    )
    return {"access_token": access_token}


@app.get("/api/session")
def session() -> dict[str, Any]:
    creds = read_credentials()
    access_token = extract_access_token(creds.get("access_token", ""))
    if access_token and access_token != creds.get("access_token"):
        write_credentials({"access_token": access_token})
    return {
        "api_key": creds.get("api_key", ""),
        "api_secret": creds.get("api_secret", ""),
        "access_token": access_token,
        "has_token": bool(access_token),
    }


@app.get("/api/profile")
def profile() -> dict[str, Any]:
    creds = read_credentials()
    api_key = creds.get("api_key", "")
    access_token = extract_access_token(creds.get("access_token", ""))
    if not api_key or not access_token:
        raise HTTPException(status_code=400, detail="Missing api_key or access_token in Credentials.txt")

    client = build_shareconnect_client(api_key, access_token, creds.get("vendor_key") or None)
    for method_name in ("getProfile", "profile", "get_profile"):
        method = getattr(client, method_name, None)
        if callable(method):
            try:
                response = method()
                if hasattr(response, "to_dict"):
                    return response.to_dict()  # type: ignore[no-any-return]
                if hasattr(response, "to_json"):
                    return json.loads(response.to_json())  # type: ignore[arg-type]
                if isinstance(response, dict):
                    return response
                return {"raw": str(response)}
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Profile API call failed: {exc}") from exc

    return {
        "user_name": "Unavailable",
        "user_id": "Unavailable",
        "products": [],
        "exchanges": [],
        "note": "The installed ShareConnect SDK does not expose a profile method in the local environment.",
    }


@app.get("/api/live-prices")
def live_prices() -> dict[str, Any]:
    creds = read_credentials()
    access_token = extract_access_token(creds.get("access_token", ""))
    if not access_token:
        raise HTTPException(status_code=400, detail="Missing access_token in Credentials.txt")
    return {
        "market": market_status(),
        "prices": fetch_live_prices(access_token),
    }


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    if FRONTEND_DIST_PATH.exists():
        requested = FRONTEND_DIST_PATH / full_path
        if requested.is_file():
            return FileResponse(requested)
        index_path = FRONTEND_DIST_PATH / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Frontend build not found.")
