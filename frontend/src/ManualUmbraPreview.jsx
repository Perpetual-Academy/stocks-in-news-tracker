import React, { useState } from "react";

export default function ManualUmbraPreview({ request, run, busy }) {
  const [amount, setAmount] = useState("");
  const [time, setTime] = useState("");
  const [preview, setPreview] = useState(null);
  return <div className="strategy-rules">
    <h4>Reschedule for manual review</h4>
    <p>Prepare another preview today, even after a completed run. This does not schedule or submit orders, or change the automatic strategy.</p>
    <form onSubmit={event => {
      event.preventDefault();
      run(async () => {
        setPreview(null);
        setPreview(await request("/manual-preview", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ order_value: amount, entry_time: time })
        }));
      });
    }}>
      <label>Order value per stock (INR)
        <input aria-label="Manual review order value" type="number" min="0.01" step="0.01" required
          value={amount} disabled={busy} onChange={event => { setPreview(null); setAmount(event.target.value); }} />
      </label>
      <label>Planned review time (IST)
        <input aria-label="Manual review time in IST" type="time" min="09:15" max="15:20" required
          value={time} disabled={busy} onChange={event => { setPreview(null); setTime(event.target.value); }} />
      </label>
      <button className="secondary" type="submit" disabled={busy}>Prepare manual preview</button>
    </form>
    {preview && <div className="strategy-preview">
      <h4>Manual review · {preview.date} · {preview.review_time} IST</h4>
      <p>Checked at {new Date(preview.checked_at).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })} IST.
        This is a snapshot, not a scheduled action. Refresh it before submitting manually in ShareConnect.</p>
      {preview.candidates.length ? <div className="strategy-table-wrap"><table className="strategy-table">
        <thead><tr><th>Stock</th><th>Side</th><th>Product / order</th><th>Value per stock</th></tr></thead>
        <tbody>{preview.candidates.map(stock => <tr key={stock.symbol}>
          <td>{stock.symbol}</td><td>{stock.side}</td><td>BT · Limit</td><td>INR {preview.order_value}</td>
        </tr>)}</tbody>
      </table></div> : <p>No stocks meet the rules for this date.</p>}
      <p>Confirm current price, whole-share quantity, existing positions and order details in ShareConnect before submitting. No stop-loss or automatic exit is included.</p>
      {preview.skipped.length > 0 && <ul>{preview.skipped.map((stock, i) =>
        <li key={i}>{stock.symbol || `Row ${stock.row}`}: {stock.reason}</li>)}</ul>}
    </div>}
  </div>;
}
