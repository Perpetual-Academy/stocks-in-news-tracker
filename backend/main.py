from __future__ import annotations

import ast
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from pydantic import BaseModel, Field
from backend.extractor import extract_articles
from backend.ai_influence import apply_ai_fallback
from backend.ai_settings import AISettings, load_settings, public_settings, save_settings
from backend.ai_models import groq_models
from backend.stock_selection import apply_selection
from backend import google_sheets
from backend import strategies

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


@asynccontextmanager
async def lifespan(app):
    strategies.start_worker()
    yield
    strategies.stop_worker()


app = FastAPI(title="ShareConnect Token Service", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path.startswith("/api/google-sheets"):
        return JSONResponse(status_code=422, content={"detail": "Check the service-account file, date, and selected stocks. Only Positive, Negative, or Neutral influence can be appended."})
    if request.url.path in {"/api/ai-settings", "/api/ai-models"}:
        return JSONResponse(status_code=422, content={"detail": "Check the provider, API format, HTTPS base URL, model, and API key."})
    return await request_validation_exception_handler(request, exc)


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
    envelope = result
    if isinstance(envelope, str):
        try:
            envelope = json.loads(envelope)
        except (TypeError, ValueError):
            envelope = {}
    account = envelope.get("data", {}) if isinstance(envelope, dict) else {}
    if not isinstance(account, dict):
        account = {}
    with strategies.LOCK:
        write_credentials({"customer_id": str(account.get("customerId") or ""),
                           "login_id": str(account.get("loginId") or ""), "access_token": token,
                           "api_key": payload.api_key, "vendor_key": payload.vendor_key or ""})
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
        socket.send(
            json.dumps(
                {
                    "action": "feed",
                    "key": ["depth"],
                    "value": [",".join(f"NC{code}" for code in LIVE_INSTRUMENTS)],
                }
            )
        )

        deadline = time.monotonic() + 12
        latest_by_code: dict[int, dict[str, Any]] = {}
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
                existing = latest_by_code.get(code, {})
                merged = {
                    "symbol": LIVE_INSTRUMENTS[code],
                    "scrip_code": code,
                    "ltp": item.get("ltp", existing.get("ltp")),
                    "change": item.get("rsChange", existing.get("change")),
                    "percent_change": item.get("perChange", existing.get("percent_change")),
                    "last_traded_at": item.get("ltt") or item.get("lastUpdatedTime") or existing.get("last_traded_at"),
                    "market_depth": _extract_market_depth(item) or existing.get("market_depth") or {},
                }
                latest_by_code[code] = merged
                prices.append(merged)
            if prices and len(latest_by_code) == len(LIVE_INSTRUMENTS):
                return sorted(prices, key=lambda item: list(LIVE_INSTRUMENTS).index(item["scrip_code"]))
        raise TimeoutError("No price data was returned by the live feed.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ShareConnect live feed failed: {exc}") from exc
    finally:
        if socket is not None:
            socket.close()


def _extract_market_depth(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize depth data from the websocket payload into a small UI-friendly shape."""
    candidates = (
        item.get("marketDepth"),
        item.get("market_depth"),
        item.get("depth"),
        item.get("bidAsk"),
        item.get("bid_offer"),
    )
    for candidate in candidates:
        if not candidate:
            continue
        if isinstance(candidate, dict):
            bids = candidate.get("buy") or candidate.get("bids") or candidate.get("bid") or []
            asks = candidate.get("sell") or candidate.get("asks") or candidate.get("offer") or []
        elif isinstance(candidate, list):
            bids = candidate[:5]
            asks = candidate[5:10]
        else:
            continue

        return {
            "best_bid": _extract_level(bids, reverse=True),
            "best_ask": _extract_level(asks, reverse=False),
        }
    return {}


def _extract_level(levels: Any, reverse: bool) -> dict[str, Any] | None:
    if not isinstance(levels, list) or not levels:
        return None

    preferred = None
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = level.get("price") or level.get("rate") or level.get("bidPrice") or level.get("offerPrice")
        qty = level.get("quantity") or level.get("qty") or level.get("bidQuantity") or level.get("offerQuantity")
        orders = level.get("orders") or level.get("count") or level.get("noOfOrders")
        if price is None and qty is None and orders is None:
            continue
        preferred = {"price": price, "qty": qty, "orders": orders}
        break

    if preferred:
        return preferred

    # Some payloads encode depth levels as compact arrays.
    if isinstance(levels[0], (list, tuple)) and len(levels[0]) >= 2:
        ordered = sorted(levels, key=lambda x: x[1], reverse=reverse) if all(len(x) >= 2 for x in levels if isinstance(x, (list, tuple))) else levels
        level = ordered[0]
        return {"qty": level[0], "price": level[1], "orders": level[2] if len(level) > 2 else None}

    return None


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

    market = market_status()
    if not market["is_open"]:
        return {
            "market": market,
            "prices": [],
            "message": "Live prices and market depth are available during NSE trading hours (09:15-15:30 IST, Monday-Friday).",
        }

    return {
        "market": market,
        "prices": fetch_live_prices(access_token),
    }


class ExtractPayload(BaseModel):
    text: str = Field(min_length=1, max_length=50000)


@app.get("/api/ai-settings")
def get_ai_settings():
    return public_settings(load_settings())


def sheets_action(action, *args):
    try:
        return action(*args)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach Google Sheets. Check the service-account setup and network connection.") from None


@app.get("/api/google-sheets/settings")
def get_sheets_settings():
    return sheets_action(google_sheets.settings)


@app.put("/api/google-sheets/settings")
def save_sheets_settings(payload: google_sheets.SheetsSettings):
    return sheets_action(google_sheets.save_settings, payload)


@app.post("/api/google-sheets/test")
def test_sheets_connection():
    return sheets_action(google_sheets.test_connection)


@app.post("/api/google-sheets/append")
def append_to_sheets(payload: google_sheets.AppendRequest):
    return sheets_action(google_sheets.append_stocks, payload)


@app.post("/api/ai-models")
def get_ai_models(payload: AISettings):
    try:
        return groq_models(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not load Groq models. Check your API key and connection, then retry or enter a model ID manually.") from exc


@app.put("/api/ai-settings")
def update_ai_settings(payload: AISettings):
    try:
        return save_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/extract")
def extract_news(payload: ExtractPayload) -> dict[str, Any]:
    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="Paste at least one news article.")
    return apply_selection(payload.text, apply_ai_fallback(payload.text, extract_articles(payload.text)))


@app.get("/api/strategies/umbra")
def umbra_status():
    return strategies.status()


@app.put("/api/strategies/umbra")
def save_umbra(payload: strategies.UmbraSettings):
    try:
        return strategies.save_settings(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.get("/api/strategies/umbra/preview")
def preview_umbra():
    return sheets_action(strategies.preview)


class UmbraToggle(BaseModel):
    enabled: bool


@app.put("/api/strategies/umbra/enabled")
def toggle_umbra(payload: UmbraToggle):
    try:
        return strategies.toggle(payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=502, detail="Could not verify the ShareConnect session. Umbra was not enabled.") from None


class UmbraReview(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


@app.post("/api/strategies/umbra/verify-closed")
def verify_umbra_closed(payload: UmbraReview):
    try:
        return strategies.verify_closed(payload.day)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except Exception:
        raise HTTPException(status_code=502, detail="Could not verify positions with ShareConnect. The run remains paused.") from None


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
