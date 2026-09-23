import React, { useEffect, useState } from "react";
import ManualUmbraPreview from "./ManualUmbraPreview.jsx";

const defaults = { order_value: "", value_basis: "per_stock", entry_time: "", profit_target_percent: "7" };

export default function Strategies({ apiBase }) {
  const [config, setConfig] = useState(defaults);
  const [state, setState] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);

  async function request(path = "", options = {}) {
    const response = await fetch(`${apiBase}/api/strategies/umbra${path}`, { ...options, signal: AbortSignal.timeout(30000) });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail :
        Array.isArray(data.detail) ? data.detail.map(item => item.msg).join(" ") : "Unable to load Umbra.");
    }
    return data;
  }

  useEffect(() => {
    let active = true;
    request().then(data => {
      if (!active) return;
      setState(data);
      setConfig({ ...defaults, ...data.settings, order_value: data.settings.order_value ?? "" });
    }).catch(() => { if (active) setError("Could not load strategy settings. Check that the backend is running."); });
    const refresh = setInterval(() => request().then(data => { if (active) setState(data); })
      .catch(() => { if (active) setError("Strategy status is unavailable. Check the backend and ShareConnect before taking action."); }), 5000);
    return () => { active = false; clearInterval(refresh); };
  }, [apiBase]);

  async function run(action) {
    setBusy(true); setMessage(""); setError("");
    try { await action(); }
    catch (err) {
      setError(err.message || "Could not reach the strategy service.");
      try { setState(await request()); } catch { /* Preserve the last known state until the next status poll. */ }
    }
    finally { setBusy(false); }
  }

  function save(event) {
    event.preventDefault();
    run(async () => {
      setState(await request("", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...config, profit_target_percent: config.profit_target_percent || null, order_value: config.order_value || null }) }));
      setMessage("Umbra settings saved. Turn Umbra on to arm the new entry time.");
      setDirty(false);
    });
  }

  const change = (key) => event => { setDirty(true); setConfig({ ...config, [key]: event.target.value }); };
  const runActionVerify = (day, run_id) => run(async () => setState(await request("/verify-closed", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ day, run_id })
  })));
  const locked = busy || !state || state.enabled || state.runs?.some(run => !["complete", "skipped"].includes(run.state));
  return <section className="panel strategies">
    <div className="panel-header"><div><h3>Strategies</h3><p className="muted">Scheduled entries from Stocks in News.</p></div>
      <span className="strategy-badge">IST · India time</span></div>
    <form onSubmit={save}>
      <div className="strategy-table-wrap"><table className="strategy-table">
        <thead><tr><th>Strategy</th><th>Order value / stock (₹)</th><th>Entry time (IST)</th><th>Stop-loss</th><th>Profit target (%)</th></tr></thead>
        <tbody><tr><td><div className="strategy-name"><strong>Umbra</strong>
          <button type="button" role="switch" aria-checked={state?.enabled ?? false} aria-label="Enable Umbra"
            aria-describedby="umbra-blocker" className={`strategy-switch ${state?.enabled ? "on" : ""}`}
            disabled={busy || !state || (!state.enabled && (!state.execution_ready || dirty))}
            onClick={() => run(async () => setState(await request("/enabled", { method: "PUT",
              headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: !state.enabled }) })))}>
            <span /></button><span className="muted">{state?.enabled ? "On" : "Off"}</span></div></td>
          <td><input aria-label="Order value per stock in rupees" type="number" min="0.01" step="0.01" required
            placeholder="Enter amount" value={config.order_value} onChange={change("order_value")} disabled={locked} /></td>
          <td><input aria-label="Entry time in IST" type="time" min="09:15" max="15:20" required value={config.entry_time} onChange={change("entry_time")} disabled={locked} /></td>
          <td>1%</td><td><input aria-label="BT+ profit target percentage" type="number" min="0.01" max="99.99" step="0.01" required value={config.profit_target_percent ?? ""} onChange={change("profit_target_percent")} disabled={locked} /></td>
        </tr></tbody>
      </table></div>
      <div className="strategy-actions"><button className="primary" type="submit" disabled={locked}>Save settings</button>
        <button className="secondary" type="button" disabled={busy || !state}
          onClick={() => run(async () => setPreview(await request("/preview")))}>Check today's stocks</button></div>
    </form>
    <ManualUmbraPreview request={request} run={run} busy={busy} />
    <div className="strategy-rules"><h4>Umbra rules</h4>
      <p>Today's date · BigTrade: Yes · BT+: Yes</p>
      <p><span className="positive">Positive influence</span> → Sell BT+ with a limit order</p>
      <p><span className="negative">Negative influence</span> → Buy BT+ with a limit order</p>
      <p className="muted">Broker bracket: 1% stop-loss and your chosen profit target. No scheduled exit. Use the amount above for each stock. Quantity is rounded down using a fresh price; the fresh last-traded price is used as the limit. Orders may remain unfilled; there is no market-order fallback.</p>
      <p className="muted">To reschedule today: turn Umbra off, enter a different future entry time, save settings, then turn Umbra on. Each entry time can run once per day. Existing orders or positions in a stock prevent duplicate entries. Places entries each weekday while enabled. Manage exits and pending orders directly in ShareConnect. Turning off stops new entries. Keep the backend and computer running for scheduled entries.</p>
    </div>
    <p id="umbra-blocker" className="feed-notice">{state?.blockers?.join(" ") || (dirty ? "Save your changes before enabling Umbra." : state?.message || "Ready. Turn Umbra on to schedule live BT+ entries through ShareConnect. No scheduled exits.")}</p>
    {error && <p className="feed-notice error" role="alert">{error}</p>}
    {message && <p className="status" role="status">{message}</p>}
    {preview && <div className="strategy-preview"><h4>Qualifying stocks · {preview.date}</h4>
      {preview.candidates.length ? <div className="strategy-table-wrap"><table className="strategy-table">
        <thead><tr><th>Symbol</th><th>Influence</th><th>Entry order</th></tr></thead>
        <tbody>{preview.candidates.map(stock => <tr key={stock.symbol}><td>{stock.symbol}</td><td>{stock.influence}</td><td>{stock.side} BT+ · Limit</td></tr>)}</tbody>
      </table></div> : <p className="muted">No stocks meet Umbra's rules for today.</p>}
      {preview.skipped.length > 0 && <details><summary>{preview.skipped.length} excluded stocks / rows</summary>
        <ul>{preview.skipped.map((stock, i) => <li key={i}>{stock.symbol || `Row ${stock.row}`}: {stock.reason}</li>)}</ul></details>}
    </div>}
    {state?.runs?.length > 0 && <div className="strategy-preview"><h4>Recent runs</h4>
      {state.runs.slice().reverse().map(run => <div key={run.id || run.day} className="strategy-rules">
        <strong>{run.day} · {run.settings?.entry_time} IST · {run.state}</strong>{run.message && <p>{run.message}</p>}
        {run.state === "attention" && <button type="button" className="secondary" disabled={busy}
          onClick={() => runActionVerify(run.day, run.id || run.day)}>Verify positions closed</button>}
        <ul>{run.orders.map(order => <li key={order.symbol}>{order.symbol} · {order.side} · {order.quantity || 0} shares · {order.state}
          {order.entry && ` · Entry ${order.entry.order_id}`}{order.exit && ` · Exit ${order.exit.order_id}`}
          {order.message && ` · ${order.message}`}</li>)}</ul>
      </div>)}
    </div>}
  </section>;
}
