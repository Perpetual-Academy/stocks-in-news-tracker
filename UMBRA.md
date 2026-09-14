# Umbra: scheduled entries only

Umbra uses BT market entry orders, without a stop-loss or automatic exit. Settings are rupees per stock and entry time in IST. Positive influence maps to SELL; Negative influence maps to BUY. The previously supported exit-time setting is ignored, including in older saved settings and run receipts.

At entry time, Tracker!A:F in AutoTracker for Stocks in News is read. Eligible rows have today’s date in IST, BigTrade = Yes, BT+ = Yes, and Positive or Negative influence. Duplicate identical symbols are combined. Conflicting signals, neutral influence or missing eligibility flags are excluded. The workbook is not modified.

Log in again if the saved broker session lacks customerId and loginId. Save the value and entry time, then enable Umbra yourself. Keep the backend running. When hosted on the VPS, your browser and computer can be closed. Entries run on weekdays, only within the first minute after the scheduled time. Enabling after that time does not cause a catch-up order. Quantity is rounded down to whole tradable lots using a fresh quote; final market value may differ.

Manage exits, stop-losses and pending orders directly in ShareConnect. Turning Umbra off stops new entries. Entry reconciliation is read-only: it records fills but never cancels an unfilled remainder or places an exit order. A run marked complete means its entry checks completed, not that its positions were closed. Broker-side intraday square-off rules still apply independently.

The SQLite ledger and single-worker OS lock prevent duplicate daily runs and blind retries after interrupted submissions. Unknown outcomes and inconsistent reports require review. After handling positions and orders in ShareConnect, Verify positions closed performs read-only checks to clear an attention state. Existing orders/BT positions in a stock cause new Umbra entries for that stock to be skipped.

Strategy settings, toggles and review controls are available remotely, including on the VPS. These endpoints have no dashboard authentication, as requested by the owner. Run a single backend worker. No live strategy was enabled or live order placed during development.

BT+ submission is not enabled. The inspected public ShareConnect request schema does not establish a separate BT+ stop-loss field. No assumption is made that triggerPrice attaches a BT+ protective stop. Removing timed exits resolves only the automatic-exit portion of the requested simplification.

Verification:

    python -m unittest backend.test_strategies backend.test_umbra_execution backend.test_google_sheets

Build the frontend with npm run build in frontend. Tests use a simulated broker and temporary data, including legacy settings, partial fills, restart handling, and proving no automatic exits/cancellations occur.

Official API specification inspected:
https://www.sharekhan.com/trading-api/documentation
