"""Single-workbook service-account integration and durable append receipts."""
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import UUID
from threading import Lock

from pydantic import BaseModel, Field
from backend.extractor import COMPANIES

DATA_DIR = Path(os.environ.get("CREDENTIALS_PATH", Path(__file__).resolve().parent.parent / "Credentials.txt")).parent
CONFIG = DATA_DIR / "GoogleSheetsSettings.json"
LEDGER = DATA_DIR / "sheets-appends.sqlite3"
SPREADSHEET_ID = "1u-wz-a9fSaBfD9Sp33Kyj2oP41qwQRHpW-i0MH_xtIc"
HEADERS = ["Date", "Stock Name", "Symbol", "Influence"]
WRITE_LOCK = Lock()


@contextmanager
def database():
    connection = sqlite3.connect(LEDGER, timeout=30)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


class SheetsSettings(BaseModel):
    service_account_json: str = Field(default="", max_length=20000)


class StockRow(BaseModel):
    symbol: str = Field(min_length=1, max_length=30)
    influence: Literal["positive", "negative", "neutral"]


class AppendRequest(BaseModel):
    request_id: UUID
    news_date: date
    stocks: list[StockRow] = Field(min_length=1, max_length=100)


def settings():
    info = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else None
    return {"configured": bool(info), "service_account_email": info.get("client_email", "") if info else "",
            "spreadsheet_id": SPREADSHEET_ID, "sheet": "Tracker",
            "url": f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=0"}


def save_settings(payload):
    from google.oauth2.service_account import Credentials
    try:
        info = json.loads(payload.service_account_json)
        if (info.get("type") != "service_account" or
            not re.fullmatch(r"[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com", info.get("client_email", "")) or
            info.get("token_uri") != "https://oauth2.googleapis.com/token"):
            raise ValueError()
        # Store only credential fields needed for signing. Never honor alternate token destinations.
        info = {key: info[key] for key in ("type", "project_id", "private_key_id", "private_key", "client_email", "token_uri")}
        Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    except Exception:
        raise ValueError("Choose a valid Google service-account JSON key file.") from None
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=CONFIG.parent, prefix=".google-sheets-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(info, target)
        os.replace(name, CONFIG)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return settings()


def session():
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession
    if not CONFIG.exists():
        raise ValueError("Configure the Google service account in Setup first.")
    credentials = Credentials.from_service_account_file(str(CONFIG), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return AuthorizedSession(credentials, max_refresh_attempts=0, refresh_timeout=15)


def google_error(response):
    messages = {401: "Google rejected the service-account credentials. Upload a valid key in Setup.",
                403: "Google denied access. Enable the Google Sheets API and share the spreadsheet with the service-account email as Editor.",
                404: "The spreadsheet or Tracker is unavailable. Check sharing with the service account.",
                429: "Google's request limit was reached. Please wait and retry."}
    raise ValueError(messages.get(response.status_code, "Google Sheets could not complete the request."))


def check_headers(client):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/" + quote("'Tracker'!A1:D1", safe="")
    response = client.get(url, timeout=20)
    if not response.ok:
        google_error(response)
    if response.json().get("values") != [HEADERS]:
        raise ValueError("Tracker headers must be: Date, Stock Name, Symbol, Influence. No rows were appended.")


def test_connection():
    with session() as client:
        check_headers(client)
    return {"message": "Connected. Tracker headers verified. Share as Editor to allow appending; this check does not write any rows."}


def append_stocks(payload):
    with WRITE_LOCK:
        return write_stocks(payload)


def available_range(client, count):
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
    metadata = client.get(base, params={"fields": "sheets.properties"}, timeout=20)
    if not metadata.ok:
        google_error(metadata)
    props = next((s["properties"] for s in metadata.json()["sheets"] if s["properties"]["title"] == "Tracker"), None)
    if not props:
        raise ValueError("Tracker tab is unavailable.")
    response = client.get(base + "/values/" + quote("'Tracker'!B2:D", safe=""),
                          params={"valueRenderOption": "FORMULA"}, timeout=20)
    if not response.ok:
        google_error(response)
    start = 2
    for row_number, row in enumerate(response.json().get("values", []), 2):
        if (row and row[0] != "") or (len(row) > 2 and row[2] != ""):
            start = row_number + 1
    end = start + count - 1
    if end > props["gridProperties"]["rowCount"]:
        raise ValueError("Not enough existing empty rows in Tracker. Add blank rows in the sheet before trying again. No cells were changed.")
    return start, end


def write_stocks(payload):
    values, seen = [], set()
    for stock in payload.stocks:
        if stock.symbol not in COMPANIES:
            raise ValueError("A selected symbol is outside the configured stock list.")
        if stock.symbol in seen:
            raise ValueError("Each stock can appear only once in an append request.")
        seen.add(stock.symbol)
        values.append([payload.news_date.isoformat(), COMPANIES[stock.symbol][0], stock.influence.capitalize()])
    fingerprint = hashlib.sha256(json.dumps(values).encode()).hexdigest()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.execute("CREATE TABLE IF NOT EXISTS appends (id TEXT PRIMARY KEY, fingerprint TEXT, status TEXT, receipt TEXT)")
        prior = db.execute("SELECT fingerprint,status,receipt FROM appends WHERE id=?", (str(payload.request_id),)).fetchone()
    if prior:
        if prior[0] != fingerprint:
            raise ValueError("This append request was already used for different rows.")
        if prior[1] == "done":
            return json.loads(prior[2])
        raise ValueError("This append may already have reached Google. Check Tracker before submitting a new request; it will not be retried automatically.")
    with session() as client:
        check_headers(client)
        start, end = available_range(client, len(values))
        # Commit a pending receipt BEFORE sending to Google. Concurrent/retried calls cannot duplicate it.
        try:
            with database() as db:
                db.execute("INSERT INTO appends VALUES (?,?,?,?)", (str(payload.request_id), fingerprint, "pending", ""))
        except sqlite3.IntegrityError:
            raise ValueError("This append is already being processed. Check Tracker before trying again.") from None
        try:
            url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values:batchUpdate"
            ranges = [f"'Tracker'!A{start}:B{end}", f"'Tracker'!D{start}:D{end}"]
            response = client.post(url, json={"valueInputOption": "RAW", "data": [
                {"range": ranges[0], "values": [v[:2] for v in values]},
                {"range": ranges[1], "values": [[v[2]] for v in values]},
            ]}, timeout=25)
            if not response.ok:
                if response.status_code in {400, 401, 403, 404, 429}:
                    with database() as db:
                        db.execute("DELETE FROM appends WHERE id=?", (str(payload.request_id),))
                    google_error(response)
                raise RuntimeError()
            updates = response.json()
            if updates.get("totalUpdatedCells") != len(values) * 3:
                raise RuntimeError()
            receipt = {"appended": len(values), "range": ", ".join(ranges), "url": settings()["url"]}
            with database() as db:
                db.execute("UPDATE appends SET status='done',receipt=? WHERE id=?", (json.dumps(receipt), str(payload.request_id)))
            return receipt
        except ValueError:
            raise
        except Exception:
            raise ValueError("Append outcome is uncertain. Check Tracker before submitting again; this request is blocked from automatic retry to prevent duplicates.") from None
