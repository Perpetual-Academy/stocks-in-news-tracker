"""ShareConnect BT adapter. No broker writes occur on import or construction."""
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

IST = timezone(timedelta(hours=5, minutes=30))


def data_of(response, expected):
    if isinstance(response, str):
        response = json.loads(response)
    if not isinstance(response, dict) or str(response.get("status")) != "200":
        raise ValueError("ShareConnect rejected the request. Check the session and broker account.")
    data = response.get("data")
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


def market_payload(customer_id, login_id, symbol, code, side, quantity):
    if side not in {"BUY", "SELL"} or quantity <= 0:
        raise ValueError("Invalid order direction or quantity.")
    return {"customerId": customer_id, "scripCode": integer(code), "tradingSymbol": symbol,
            "exchange": "NC", "transactionType": "B" if side == "BUY" else "S",
            "quantity": integer(quantity), "disclosedQty": 0, "price": "0", "triggerPrice": "0",
            "rmsCode": "ANY", "afterHour": "N", "orderType": "NORMAL", "channelUser": login_id,
            "validity": "GFD", "requestType": "NEW", "productType": "BIGTRADE"}


def receipt(response):
    data = data_of(response, dict)
    order_id = data.get("orderId")
    rms = data.get("rmscode") or data.get("rmsCode")
    if data.get("errormsg") or data.get("errorMsg") or not order_id or not rms:
        raise ValueError("Broker did not confirm an order receipt. Check the order book before taking action.")
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

    def reports(self):
        rows = data_of(self.client.reports(self.customer_id), list)
        if any(not isinstance(r, dict) or not r.get("orderId") or not r.get("tradingSymbol") for r in rows):
            raise ValueError("Incomplete broker order book; trading paused.")
        return rows

    def positions(self):
        return data_of(self.client.trades(self.customer_id), list)

    def instruments(self, symbols):
        rows = data_of(self.client.master("NC"), list)
        result = {}
        for symbol in symbols:
            matches = [r for r in rows if r.get("tradingSymbol") == symbol and r.get("instType") == "EQ"]
            if len(matches) != 1:
                raise ValueError(f"{symbol}: no unique NSE equity instrument found in ShareConnect.")
            result[symbol] = {"code": integer(matches[0]["scripCode"]),
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
                message = json.loads(socket.recv())
                for item in message.get("data", []):
                    if item.get("exchangeCode") != "NC" or str(item.get("scripCode")) != str(code):
                        continue
                    stamp = datetime.strptime(item["lastUpdatedTime"], "%d/%m/%Y %H:%M:%S").replace(tzinfo=IST)
                    age = (datetime.now(IST) - stamp).total_seconds()
                    price = Decimal(str(item["ltp"]))
                    if 0 <= age <= 30 and price.is_finite() and price > 0:
                        return price
            raise ValueError("No fresh market quote. Entry skipped.")
        finally:
            socket.close()

    def place(self, symbol, code, side, quantity):
        return receipt(self.client.placeOrder(market_payload(self.customer_id, self.login_id, symbol, code, side, quantity)))

    def cancel(self, order, row):
        payload = market_payload(self.customer_id, self.login_id, order["symbol"], order["code"], order["side"], integer(row["orderQty"]))
        payload.update(orderId=order["entry"]["order_id"], rmsCode=row["rmsCode"], requestType="CANCEL")
        self.client.cancelOrder(payload)
        # The engine waits for a terminal order-book status, not this acknowledgement.

    def net_position(self, symbol):
        rows = [r for r in self.positions() if r.get("tradingSymbol") == symbol and r.get("exchange") == "NC"]
        if any("productType" not in r for r in rows):
            raise ValueError("Broker position product is missing.")
        return sum(integer(r["netQty"]) for r in rows if r["productType"] in {"BIGTRADE", "BT"})
