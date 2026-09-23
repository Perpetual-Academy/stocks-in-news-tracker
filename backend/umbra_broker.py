"""ShareConnect BT adapter. No broker writes occur on import or construction."""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_CEILING

IST = timezone(timedelta(hours=5, minutes=30))


def data_of(response, expected, *, allow_no_records=False):
    if isinstance(response, str):
        response = json.loads(response)
    if not isinstance(response, dict) or str(response.get("status")) != "200":
        raise ValueError("ShareConnect rejected the request. Check the session and broker account.")
    data = response.get("data")
    # Order/trade history uses this sentinel for a successful empty book.
    if allow_no_records and expected is list and data == "no_records":
        return []
    if not isinstance(data, expected):
        raise ValueError("ShareConnect returned an unrecognized response; no further orders sent.")
    return data


def integer(value):
    number = Decimal(str(value))
    if not number.is_finite() or number != int(number):
        raise ValueError("Invalid broker quantity or instrument code.")
    return int(number)


def order_state(row):
    qty, filled = integer(row["orderQty"]), integer(row["execQty"])
    if not 0 <= filled <= qty:
        raise ValueError("Inconsistent broker fill quantities.")
    state = str(row.get("orderStatus", "")).replace(" ", "").lower()
    terminal = state in {"fullyexecuted", "cancelled", "canceled", "rejected", "expired"}
    if state == "fullyexecuted" and filled != qty:
        raise ValueError("Broker fill status and quantity disagree.")
    return filled, terminal


def bracket_prices(price, side, target_percent, tick_size):
    try:
        price, target, tick = map(lambda v: Decimal(str(v)), (price, target_percent, tick_size))
    except InvalidOperation as exc:
        raise ValueError("A BT+ profit target and verified instrument tick size are required.") from exc
    if (side not in {"BUY", "SELL"} or
            any(not n.is_finite() or n <= 0 for n in (price, target, tick)) or target >= 100):
        raise ValueError("Valid price, profit target and instrument tick size are required for BT+.")
    if price % tick:
        raise ValueError("Entry price is not aligned with the instrument tick size.")
    sign = Decimal(1 if side == "BUY" else -1)
    stop = price * (1 - sign * Decimal("0.01"))
    profit = price * (1 + sign * target / 100)
    # Round towards entry so the configured distance is not exceeded.
    stop = (stop / tick).to_integral_value(rounding=ROUND_CEILING if side == "BUY" else ROUND_FLOOR) * tick
    profit = (profit / tick).to_integral_value(rounding=ROUND_FLOOR if side == "BUY" else ROUND_CEILING) * tick
    if stop <= 0 or profit <= 0 or not ((stop < price < profit) if side == "BUY" else (profit < price < stop)):
        raise ValueError("BT+ stop and target must be on opposite sides of the entry price.")
    return stop, profit


def limit_payload(customer_id, login_id, symbol, code, side, quantity, limit_price, *, stop_price, target_price):
    try:
        price = Decimal(str(limit_price))
    except InvalidOperation as exc:
        raise ValueError("A valid positive limit price is required.") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError("A valid positive limit price is required.")
    if side not in {"BUY", "SELL"} or quantity <= 0:
        raise ValueError("Invalid order direction or quantity.")
    stop, target = Decimal(str(stop_price)), Decimal(str(target_price))
    if any(not n.is_finite() or n <= 0 for n in (stop, target)) or not (
            (stop < price < target) if side == "BUY" else (target < price < stop)):
        raise ValueError("Invalid BT+ stop-loss or profit target.")
    return {"orderId": "", "customerId": customer_id, "scripCode": integer(code), "tradingSymbol": symbol,
            "exchange": "NC", "transactionType": "B" if side == "BUY" else "S",
            "quantity": integer(quantity), "disclosedQty": 0, "price": format(price, "f"), "triggerPrice": "0",
            "rmsCode": "ANY", "afterHour": "N", "orderType": "BKT", "channelUser": login_id,
            "validity": "GFD", "requestType": "NEW", "productType": "BIGTRADEPLUS",
            "childSlPrice": format(stop, "f"), "bookProfitPrice": format(target, "f")}


class OrderSubmissionError(ValueError):
    def __init__(self, diagnostic, *, rejected=False):
        self.diagnostic = diagnostic
        self.rejected = rejected
        super().__init__(diagnostic["message"])


def safe_broker_text(value, secrets=()):
    """Never persist raw responses, request echoes, URLs, or credential values."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    text = str(value)
    for secret in sorted({str(v) for v in secrets if v}, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"https?://\S+", "[redacted URL]", text, flags=re.I)
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:access[_-]?token|api[_-]?key|vendor[_-]?key|secret|password|authorization|customerId|channelUser)\s*[\"']?\s*[:=]\s*)[^,;\s}]+", r"\1[redacted]", text)
    return " ".join(text.split())[:500]


def receipt(response, *, secrets=()):
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except (ValueError, TypeError):
            response = None
    if not isinstance(response, dict):
        raise OrderSubmissionError({"stage": "response", "message": "ShareConnect returned an unreadable response."})
    raw_data = response.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    diagnostic = {"stage": "response", "status": safe_broker_text(response.get("status"), secrets)}
    for key in ("errorCode", "code", "errormsg", "errorMsg", "message", "orderStatus"):
        value = data.get(key) or response.get(key)
        if value:
            diagnostic[key] = safe_broker_text(value, secrets)
    order_id = data.get("orderId")
    rms = data.get("rmscode") or data.get("rmsCode")
    # Only an explicit rejection without any order ID is definitive. HTTP errors,
    # timeouts and unfamiliar response shapes must never authorize a retry.
    rejected = isinstance(raw_data, dict) and str(data.get("orderStatus") or response.get("orderStatus") or "").lower() == "rejected"
    has_id = any(v for obj in (response, data) for k, v in obj.items() if "order" in k.lower() and "id" in k.lower())
    if (str(response.get("status")) != "200" or rejected or
            diagnostic.get("errormsg") or diagnostic.get("errorMsg") or not order_id or not rms):
        reason = diagnostic.get("errormsg") or diagnostic.get("errorMsg") or diagnostic.get("message")
        diagnostic["message"] = reason or "Broker did not return a complete order receipt."
        code = diagnostic.get("errorCode") or diagnostic.get("code")
        if code:
            diagnostic["message"] += " (code: " + code + ")"
        diagnostic["receipt_has_order_id"] = bool(has_id)
        diagnostic["receipt_has_rms_code"] = bool(rms)
        raise OrderSubmissionError(diagnostic, rejected=rejected and not has_id)
    return {"order_id": str(order_id), "rms_code": str(rms)}


class ShareConnectBT:
    def __init__(self):
        from backend.main import read_credentials, extract_access_token, build_shareconnect_client
        creds = read_credentials()
        token = extract_access_token(creds.get("access_token", ""))
        self.customer_id = creds.get("customer_id", "")
        self.login_id = creds.get("login_id", "")
        if not token or not creds.get("api_key") or not self.customer_id or not self.login_id:
            raise ValueError("Log in again to save your ShareConnect customer ID, login ID and access token.")
        self.client = build_shareconnect_client(creds["api_key"], token, creds.get("vendor_key") or None)
        self.token = token
        self._diagnostic_secrets = [v for v in creds.values() if isinstance(v, str)] + [token]

    def reports(self):
        rows = data_of(self.client.reports(self.customer_id), list, allow_no_records=True)
        if any(not isinstance(r, dict) or not r.get("orderId") or not r.get("tradingSymbol") for r in rows):
            raise ValueError("Incomplete broker order book; trading paused.")
        return rows

    def positions(self):
        return data_of(self.client.trades(self.customer_id), list, allow_no_records=True)

    def instruments(self, symbols):
        rows = data_of(self.client.master("NC"), list)
        result = {}
        for symbol in symbols:
            matches = [r for r in rows if r.get("tradingSymbol") == symbol and r.get("instType") == "EQ"]
            if len(matches) != 1:
                raise ValueError(f"{symbol}: no unique NSE equity instrument found in ShareConnect.")
            result[symbol] = {"code": integer(matches[0]["scripCode"]),
                              "tick_size": matches[0].get("tickSize"),
                              "lot": max(1, integer(matches[0].get("lotSize", 1)))}
        return result

    def quote(self, code):
        import websocket
        socket = websocket.create_connection(
            f"wss://stream.sharekhan.com/skstream/api/stream?ACCESS_TOKEN={self.token}", timeout=5)
        try:
            socket.send(json.dumps({"action": "subscribe", "key": ["feed"], "value": [""]}))
            socket.send(json.dumps({"action": "feed", "key": ["ltp"], "value": [f"NC{code}"]}))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    message = json.loads(socket.recv())
                except (json.JSONDecodeError, TypeError):
                    continue
                # Subscription acknowledgements contain text, not quote records.
                if not isinstance(message, dict) or not isinstance(message.get("data"), list):
                    continue
                for item in message["data"]:
                    if not isinstance(item, dict):
                        continue
                    if item.get("exchangeCode") != "NC" or str(item.get("scripCode")) != str(code):
                        continue
                    try:
                        # ShareConnect timestamps are month/day/year in IST.
                        stamp = datetime.strptime(item["lastUpdatedTime"], "%m/%d/%Y %H:%M:%S").replace(tzinfo=IST)
                        price = Decimal(str(item["ltp"]))
                    except (KeyError, TypeError, ValueError, InvalidOperation):
                        continue
                    age = (datetime.now(IST) - stamp).total_seconds()
                    if 0 <= age <= 30 and price.is_finite() and price > 0:
                        return price
            raise ValueError("No fresh market quote. Entry skipped.")
        finally:
            socket.close()

    def place(self, symbol, code, side, quantity, limit_price, *, stop_price, target_price):
        payload = limit_payload(self.customer_id, self.login_id, symbol, code, side, quantity, limit_price,
                                stop_price=stop_price, target_price=target_price)
        try:
            response = self.client.placeOrder(payload)
        except Exception as exc:
            # Exception strings can contain authenticated URLs or request bodies.
            category = "timeout" if "timeout" in type(exc).__name__.lower() else "transport_or_sdk_error"
            raise OrderSubmissionError({"stage": "submission", "code": category,
                                        "message": "No usable ShareConnect response was received (" + category + ")."}) from None
        return receipt(response, secrets=getattr(self, "_diagnostic_secrets", [self.customer_id, self.login_id]))

    def cancel(self, order, row):
        raise ValueError("Manage BT+ bracket cancellations in ShareConnect; automatic cancellation is disabled.")

    def net_position(self, symbol):
        rows = [r for r in self.positions() if r.get("tradingSymbol") == symbol and r.get("exchange") == "NC"]
        if any("productType" not in r for r in rows):
            raise ValueError("Broker position product is missing.")
        return sum(abs(integer(r["netQty"])) for r in rows if r["productType"] in {"BIGTRADE", "BT", "BIGTRADEPLUS", "BT+"})
