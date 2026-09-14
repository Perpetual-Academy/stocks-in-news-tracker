"""Umbra configuration, selection and durable BT scheduling."""
import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, model_validator
from backend import google_sheets
from backend.umbra_broker import ShareConnectBT, order_state, integer

IST = timezone(timedelta(hours=5, minutes=30))
DB_PATH = google_sheets.DATA_DIR / "strategies.sqlite3"
HEADERS = ["Date", "Stock Name", "Symbol", "Influence", "BigTrade", "BT+"]
LOCK = threading.RLock()
STOP = threading.Event()
WORKER = None
WORKER_ACTIVE = False
LAST_ERROR = ""
log = logging.getLogger(__name__)


class UmbraSettings(BaseModel):
    order_value: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    value_basis: Literal["per_stock"] = "per_stock"
    entry_time: str = Field(default="", pattern=r"^(?:|(?:[01]\d|2[0-3]):[0-5]\d)$")

    @model_validator(mode="after")
    def validate_times(self):
        if self.entry_time and not "09:15" <= self.entry_time <= "15:20":
            raise ValueError("Use an entry time between 09:15 and 15:20 IST.")
        return self


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS runs (day TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            yield db
    finally:
        db.close()


def load_settings():
    with database() as db:
        row = db.execute("SELECT payload FROM settings WHERE id='umbra'").fetchone()
    return UmbraSettings.model_validate_json(row[0]) if row else UmbraSettings()


def status():
    settings = load_settings()
    control = load_control()
    blockers = []
    if not settings.order_value or not settings.entry_time:
        blockers.append("Save an order value and entry time first.")
    try:
        ShareConnectBT()
    except ValueError as exc:
        blockers.append(str(exc))
    if not WORKER_ACTIVE:
        blockers.append("The strategy scheduler is not running in this backend process.")
    runs = all_runs()
    if any(r["state"] == "attention" for r in runs):
        blockers.append("An order requires review in ShareConnect. New entries are paused.")
    return {"name": "Umbra", "enabled": control["enabled"], "settings": settings.model_dump(mode="json"),
            "timezone": "Asia/Kolkata", "execution_ready": not blockers,
            "blockers": blockers, "runs": runs[-10:], "scheduler_running": WORKER_ACTIVE,
            "message": LAST_ERROR or control.get("message", ""),
            "source_url": f"https://docs.google.com/spreadsheets/d/{google_sheets.SPREADSHEET_ID}/edit"}


def save_settings(payload):
    with LOCK:
        if load_control()["enabled"] or any(r["state"] not in {"complete", "skipped"} for r in all_runs()):
            raise ValueError("Turn Umbra off and wait for existing runs to finish before changing settings.")
        with database() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('umbra', ?)", (payload.model_dump_json(),))
    return status()


def parse_day(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return date(1899, 12, 30) + timedelta(days=int(value))
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def select_candidates(values, today):
    if not values or values[0] != HEADERS:
        raise ValueError("Tracker headers must be Date, Stock Name, Symbol, Influence, BigTrade, BT+.")
    grouped = {}
    skipped = []
    for number, raw in enumerate(values[1:], 2):
        row = list(raw) + [""] * max(0, 6 - len(raw))
        if parse_day(row[0]) != today:
            continue
        symbol = str(row[2]).strip().upper()
        if not symbol:
            skipped.append({"row": number, "symbol": "", "reason": "Missing symbol"})
            continue
        grouped.setdefault(symbol, []).append((number, row))
    candidates = []
    for symbol, entries in grouped.items():
        influences = {str(row[3]).strip().lower() for _, row in entries}
        eligible = all(str(row[4]).strip().lower() == "yes" and
                       str(row[5]).strip().lower() == "yes" for _, row in entries)
        reason = None
        if not eligible:
            reason = "Requires BigTrade = Yes and BT+ = Yes on every row for this symbol today"
        elif len(influences) != 1:
            reason = "Conflicting influence values for this symbol today"
        elif next(iter(influences)) not in {"positive", "negative"}:
            reason = "Influence must be Positive or Negative"
        if reason:
            skipped.append({"symbol": symbol, "rows": [n for n, _ in entries], "reason": reason})
        else:
            influence = next(iter(influences))
            candidates.append({"symbol": symbol, "name": str(entries[0][1][1]),
                               "influence": influence, "side": "SELL" if influence == "positive" else "BUY",
                               "product": "BT", "order_type": "Market", "rows": [n for n, _ in entries]})
    return {"date": today.isoformat(), "timezone": "Asia/Kolkata", "candidates": candidates, "skipped": skipped}


def preview():
    with google_sheets.session() as client:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{google_sheets.SPREADSHEET_ID}/values/" + quote("'Tracker'!A:F", safe="")
        response = client.get(url, params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"}, timeout=20)
        if not response.ok:
            google_sheets.google_error(response)
        values = response.json().get("values", [])
    return select_candidates(values, datetime.now(IST).date())


def load_control():
    with database() as db:
        row = db.execute("SELECT payload FROM settings WHERE id='control'").fetchone()
    return json.loads(row[0]) if row else {"enabled": False, "since": "", "message": ""}


def store_control(control):
    with database() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES ('control', ?)", (json.dumps(control),))


def all_runs():
    with database() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM runs ORDER BY day")]


def store_run(run):
    with database() as db:
        db.execute("INSERT OR REPLACE INTO runs VALUES (?, ?)", (run["day"], json.dumps(run)))


def toggle(enabled):
    with LOCK:
        if enabled:
            current = status()
            if current["blockers"]:
                raise ValueError(" ".join(current["blockers"]))
            if load_control()["enabled"]:
                return current
            broker = ShareConnectBT()
            broker.reports()  # Read-only check of account and session before arming.
            store_control({"enabled": True, "since": datetime.now(IST).isoformat(),
                           "customer_id": str(broker.customer_id),
                           "message": "Armed for the next entry time. Entry only, with no stop-loss or automatic exit. Keep the backend running."})
        else:
            control = load_control()
            control.update(enabled=False, message="New entries stopped. Manage existing positions directly in ShareConnect; automatic exits are disabled.")
            store_control(control)
    return status()


def matching(reports, symbol):
    return [r for r in reports if r.get("tradingSymbol") == symbol and r.get("exchange") == "NC"]


def verify_closed(day):
    """User-initiated, read-only broker reconciliation. Never submit an order."""
    with LOCK:
        runs = [r for r in all_runs() if r["day"] == day]
        if len(runs) != 1 or runs[0]["state"] != "attention":
            raise ValueError("Choose a run that requires attention.")
        run = runs[0]
        broker = ShareConnectBT()
        if run["customer_id"] != str(broker.customer_id):
            raise ValueError("Log into the account used by this run first.")
        reports = broker.reports()
        for order in run["orders"]:
            rows = matching(reports, order["symbol"])
            if day == datetime.now(IST).date().isoformat() and not rows and order["state"] != "skipped":
                raise ValueError("An uncertain order is still absent from today's broker report. Check ShareConnect; the run remains paused.")
            if any(not order_state(row)[1] for row in rows) or broker.net_position(order["symbol"]) != 0:
                raise ValueError("Positions or pending orders remain. Close or cancel them in ShareConnect before verifying.")
        for order in run["orders"]:
            order["state"] = "closed" if order["state"] != "skipped" else "skipped"
        run.update(state="complete", message="User requested verification: broker reports no remaining BT positions or pending orders for these stocks.")
        store_run(run)
    return status()


def lookup(reports, order_id):
    rows = [r for r in reports if str(r.get("orderId")) == order_id]
    if len(rows) != 1:
        raise ValueError("Order not uniquely found in ShareConnect. Check the broker order book.")
    return rows[0]


def enter(run, broker, now):
    selection = preview()
    if selection["date"] != run["day"]:
        raise ValueError("Tracker date changed during entry preparation.")
    symbols = [r["symbol"] for r in selection["candidates"]]
    instruments = broker.instruments(symbols) if symbols else {}
    run["orders"] = []
    store_run(run)
    for candidate in selection["candidates"]:
        symbol = candidate["symbol"]
        order = {"symbol": symbol, "side": candidate["side"], "state": "preparing", **instruments[symbol]}
        run["orders"].append(order)
        try:
            if not load_control()["enabled"]:
                order.update(state="skipped", message="Umbra was turned off before entry.")
                continue
            if matching(broker.reports(), symbol) or broker.net_position(symbol) != 0:
                order.update(state="skipped", message="Existing orders or position for this stock; skipped to keep Umbra separate.")
                continue
            price = broker.quote(order["code"])
            qty = int(Decimal(run["settings"]["order_value"]) / price / order["lot"]) * order["lot"]
            if qty < order["lot"]:
                order.update(state="skipped", message="Order value is below one tradable lot.")
                continue
            # Recheck wall clock after network calls; never catch up a missed entry later.
            current = datetime.now(IST)
            deadline = datetime.fromisoformat(run["entry_at"]) + timedelta(seconds=60)
            if current > deadline:
                order.update(state="skipped", message="Entry window passed before submission.")
                continue
            with LOCK:
                control = load_control()
                if not control["enabled"] or control.get("since") != run["armed_since"]:
                    order.update(state="skipped", message="Umbra was turned off before entry.")
                    continue
                order.update(quantity=qty, reference_price=str(price), state="entry_sending")
                store_run(run)  # Durable before the irreversible broker request.
                order["entry"] = broker.place(symbol, order["code"], order["side"], qty)
                order["state"] = "open"
        except Exception:
            if order["state"] == "entry_sending":
                order.update(state="attention", message="Entry outcome uncertain. Check ShareConnect; this order will not be retried.")
                run["state"] = "attention"
                break
            order.update(state="skipped", message="Could not verify instrument, account or fresh price. No entry submitted.")
        finally:
            store_run(run)
    if run["state"] != "attention":
        run["state"] = "open" if any(o["state"] == "open" for o in run["orders"]) else "complete"
    store_run(run)


def monitor_entries(run, broker):
    """Read-only reconciliation. Never cancel or place exit orders."""
    for order in run["orders"]:
        if order["state"] in {"filled", "closed", "skipped", "attention"}:
            continue
        try:
            if order.get("exit") or order["state"] in {"exit_pending", "cancel_pending"}:
                order.update(state="attention", message="A previous exit or cancellation may be pending. Check ShareConnect; automatic exits are now disabled.")
                continue
            if not order.get("entry"):
                order.update(state="skipped", message="Entry preparation was interrupted before submission.")
                continue
            entry = lookup(broker.reports(), order["entry"]["order_id"])
            if (integer(entry["orderQty"]) != order["quantity"] or
                    entry.get("tradingSymbol") != order["symbol"] or
                    entry.get("buySell") != ("B" if order["side"] == "BUY" else "S")):
                raise ValueError("Entry was changed outside Umbra. Check ShareConnect.")
            filled, terminal = order_state(entry)
            order["filled_quantity"] = filled
            if terminal:
                order.update(state="filled" if filled else "closed",
                             message=f"Entry finished with {filled} filled shares. Manage any position in ShareConnect; no automatic exit.")
            else:
                order["message"] = f"{filled} shares filled; entry still pending. Manage this order in ShareConnect."
        except ValueError as exc:
            order.update(state="attention", message=str(exc))
        except Exception:
            order["message"] = "Could not check entry status. Read-only verification will retry."
        finally:
            store_run(run)
    states = {o["state"] for o in run["orders"]}
    run["state"] = "attention" if "attention" in states else "complete" if states <= {"filled", "closed", "skipped"} else "open"
    if run["state"] == "complete":
        run["message"] = "Entry checks complete. Positions are not automatically closed by Umbra."
    store_run(run)


def tick(now=None, broker_factory=ShareConnectBT):
    now = now or datetime.now(IST)
    runs = all_runs()
    for run in runs:
        if run["state"] in {"complete", "skipped"}:
            continue
        if run["day"] != now.date().isoformat() or now.strftime("%H:%M") >= "15:30":
            run.update(state="attention", message="Session ended with unresolved orders. Check ShareConnect; no next-day exit will be sent.")
            store_run(run)
            continue
        if any(o["state"] in {"entry_sending", "exit_sending"} for o in run["orders"]):
            for order in run["orders"]:
                if order["state"] in {"entry_sending", "exit_sending"}:
                    order.update(state="attention", message="Submission interrupted. Reconcile this order in ShareConnect.")
            run["state"] = "attention"
            store_run(run)
        broker = broker_factory()
        if str(broker.customer_id) != run["customer_id"]:
            run.update(state="attention", message="Account changed. Restore the original account to check entry status.")
            store_run(run)
            continue
        monitor_entries(run, broker)
    if any(r["state"] == "attention" for r in all_runs()):
        return
    control = load_control()
    if not control["enabled"] or now.weekday() >= 5:
        return
    settings = load_settings()
    day = now.date().isoformat()
    if any(r["day"] == day for r in runs):
        return
    entry_at = datetime.fromisoformat(f"{day}T{settings.entry_time}:00+05:30")
    if now < entry_at or datetime.fromisoformat(control["since"]) > entry_at:
        return
    run = {"day": day, "state": "preparing", "orders": [], "settings": settings.model_dump(mode="json"), "armed_since": control["since"],
           "entry_at": entry_at.isoformat(), "customer_id": ""}
    if (now - entry_at).total_seconds() >= 60:
        run.update(state="skipped", message="Entry time was missed. No late entry orders were sent.")
        store_run(run)
        return
    broker = broker_factory()
    if str(broker.customer_id) != control.get("customer_id"):
        control.update(enabled=False, message="Account changed. Umbra was turned off. Manage existing positions in ShareConnect.")
        store_control(control)
        return
    run["customer_id"] = str(broker.customer_id)
    with LOCK:
        latest = load_control()
        if not latest["enabled"] or latest["since"] != control["since"] or load_settings() != settings:
            return
        store_run(run)
    try:
        enter(run, broker, now)
    except Exception:
        run.update(state="attention", message="Entry preparation failed. Check the account and Tracker connection; no automatic rerun.")
        store_run(run)


def worker_loop():
    global WORKER_ACTIVE, LAST_ERROR
    # An OS lock is released on process death. Never expire a lease while an order request is in flight.
    lock_file = None
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(str(DB_PATH) + ".worker", "a+b")
        lock_file.seek(0); lock_file.write(b"0"); lock_file.flush(); lock_file.seek(0)
        import os
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        WORKER_ACTIVE = True
        while not STOP.is_set():
            try:
                tick()
                LAST_ERROR = ""
            except Exception:
                LAST_ERROR = "Scheduler could not reach the broker or verify the account. Check ShareConnect and the backend connection."
                log.warning("Umbra could not complete its scheduler check; no unconfirmed submissions will be repeated.")
            STOP.wait(2)
    except (OSError, IOError):
        log.warning("Umbra scheduler is already owned by another backend process or its storage is unavailable.")
    finally:
        WORKER_ACTIVE = False
        if lock_file:
            lock_file.close()


def start_worker():
    global WORKER
    STOP.clear()
    WORKER = threading.Thread(target=worker_loop, name="umbra", daemon=True)
    WORKER.start()


def stop_worker():
    STOP.set()
    if WORKER:
        WORKER.join(timeout=15)
