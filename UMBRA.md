# Umbra: scheduled entries only

Umbra submits BIGTRADEPLUS / BKT bracket orders with a limit entry at the fresh last-traded price, a 1% stop-loss and a 7% default profit target (editable). Scheduled exits are disabled. The broker manages the linked stop-loss and profit-target legs. Settings are rupees per stock, entry time in IST and profit target percentage. Existing settings without a target field use the approved 7% target; an explicitly cleared target blocks new BT+ orders. Positive influence maps to SELL; Negative influence maps to BUY. The previously supported exit-time setting is ignored, including in older saved settings and run receipts.

At entry time, Tracker!A:F in AutoTracker for Stocks in News is read. Eligible rows have today’s date in IST, BigTrade = Yes, BT+ = Yes, and Positive or Negative influence. Duplicate identical symbols are combined. Conflicting signals, neutral influence or missing eligibility flags are excluded. The workbook is not modified.

Log in again if the saved broker session lacks customerId and loginId. Save the value and entry time, then enable Umbra yourself. Keep the backend running. When hosted on the VPS, your browser and computer can be closed. Entries run on weekdays, only within the first minute after the scheduled time. Enabling after that time does not cause a catch-up order. Quantity is rounded down to whole tradable lots using a fresh quote; that same price is the limit price. Orders may remain unfilled; no market fallback or automatic repricing occurs.

Manage exits, stop-losses and pending orders directly in ShareConnect. Turning Umbra off stops new entries. Entry reconciliation is read-only: it records fills but never cancels an unfilled remainder or places an exit order. A run marked complete means its entry checks completed, not that its positions were closed. Broker-side intraday square-off rules still apply independently.

The SQLite ledger and single-worker OS lock prevent duplicate runs for each date and entry time and blind retries after interrupted submissions. Unknown outcomes and inconsistent reports require review. After handling positions and orders in ShareConnect, Verify positions closed performs read-only checks to clear an attention state. Existing orders/BT or BT+ positions in a stock cause new Umbra entries for that stock to be skipped.

Strategy settings, toggles and review controls are available remotely, including on the VPS. These endpoints have no dashboard authentication, as requested by the owner. Run a single backend worker. No live strategy was enabled or live order placed during development.

The entry payload follows the owner-provided BT+ example: orderType BKT, productType BIGTRADEPLUS, childSlPrice and bookProfitPrice. Stop/target prices are based on the entry limit, rounded towards entry using the instrument master tickSize. Missing or invalid tickSize skips entry; no tick-size default is guessed. The entry must itself be tick-aligned. Broker acceptance has not been tested with live orders. No market entry fallback, automatic bracket cancellation, or timed square-off is implemented. Existing historical BT receipts are preserved.

Verification:

    python -m unittest backend.test_strategies backend.test_umbra_execution backend.test_google_sheets

Build the frontend with npm run build in frontend. Tests use a simulated broker and temporary data, including legacy settings, partial fills, restart handling, and proving no automatic exits/cancellations occur.

Official API specification inspected:
https://www.sharekhan.com/trading-api/documentation

To reschedule on the same day, turn Umbra off, save a different future entry time in IST, then turn Umbra on. Each date/time runs once; prior receipts remain in history. Unresolved runs block settings changes, and existing broker orders or positions still prevent duplicate entries for a stock. Preparation failures remain visible as attention until explicitly reviewed.
