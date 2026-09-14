import React, { useRef, useState } from "react";

export default function Extractor({ apiBase }) {
  const [text, setText] = useState("");
  const [result, setResult] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [newsDate, setNewsDate] = useState(() => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()));
  const [appending, setAppending] = useState(false);
  const [appendMessage, setAppendMessage] = useState("");
  const [appended, setAppended] = useState(false);
  const appendAttempt = useRef(null);

  async function appendSelected() {
    if (appending || appended) return;
    const stocks = result.stocks.filter(stock => selected.has(stock.id)).map(({ symbol, influence }) => ({ symbol, influence }));
    if (!stocks.length) return;
    if (stocks.some(stock => !["positive", "negative", "neutral"].includes(stock.influence))) {
      setAppendMessage("Some selected stocks have unavailable influence. Deselect them or extract again before appending."); return;
    }
    const body = { news_date: newsDate, stocks };
    const fingerprint = JSON.stringify(body);
    if (!appendAttempt.current || appendAttempt.current.fingerprint !== fingerprint) {
      appendAttempt.current = { fingerprint, request_id: crypto.randomUUID() };
    }
    setAppending(true); setAppendMessage("");
    try {
      const response = await fetch(`${apiBase}/api/google-sheets/append`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, request_id: appendAttempt.current.request_id }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not append selected stocks.");
      setAppended(true); setAppendMessage(`${data.appended} stocks saved to Tracker (${data.range}).`);
    } catch (err) { setAppendMessage(err.message || "Connection lost. Check Tracker before retrying."); }
    finally { setAppending(false); }
  }

  async function extract(event) {
    event.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError("");
    setResult(null);
    setAppended(false); setAppendMessage(""); appendAttempt.current = null;
    setSelected(new Set());
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 90000);
    try {
      const response = await fetch(`${apiBase}/api/extract`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }), signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Unable to extract stocks. Please check the article text.");
      setResult(data);
      setSelected(new Set(data.stocks.filter(stock => stock.auto_selected).map(stock => stock.id)));
    } catch (err) {
      setError(err.name === "AbortError" ? "Extraction timed out. Please try again." : err.message === "Failed to fetch" ? "Could not reach the extraction service. Please try again." : err.message);
    } finally {
      window.clearTimeout(timeout);
      setBusy(false);
    }
  }

  return <section className="panel extractor" aria-busy={busy}>
    <h3>Extractor</h3>
    <p className="muted">Paste a news article to identify stocks and their estimated price influence.</p>
    <form onSubmit={extract} className="form">
      <label htmlFor="article-text">News article</label>
      <textarea id="article-text" rows={10} maxLength={50000} value={text} disabled={busy || appending}
        aria-describedby="extractor-help"
        placeholder={"Infosys reports revenue growth and record profits."}
        onChange={(event) => { setText(event.target.value); setResult(null); setSelected(new Set()); setError(""); }} />
      <div className="extractor-actions">
        <button className="primary" type="submit" disabled={busy || appending || !text.trim()}>{busy ? "Extracting…" : "Extract"}</button>
        <span className="muted">{text.length.toLocaleString()} / 50,000 characters</span>
      </div>
    </form>
    <p id="extractor-help" className="muted">Recognizes company names and stock symbols from your EQUITY_L.csv list, including names without Limited or Ltd. Keyword rules run first. When no usable positive or negative signal is found, the article is sent to the AI provider selected in Setup for analysis. Mixed keyword signals remain Neutral. AI can return Positive, Negative, or Neutral with a reason. They are estimates, not price forecasts.</p>
    {error && <p className="feed-notice error" role="alert">{error}</p>}
    {result && <div>
      {result.ai_notice && <p className="feed-notice" role="status">{result.ai_notice}</p>}
      {result.selection_notice && <p className="feed-notice" role="status">{result.selection_notice}</p>}
      <p className="muted">Standalone news about a company's own operations is selected automatically. Indirect mentions and stocks bundled into one news item stay unchecked. You can change any checkbox.</p>
      <p role="status">{result.stocks.length} stock {result.stocks.length === 1 ? "entry" : "entries"} · {selected.size} selected</p>
      <div className="extractor-actions">
        <label>News date<input type="date" required value={newsDate} disabled={appending || appended} onChange={event => setNewsDate(event.target.value)} /></label>
        <button className="primary" type="button" disabled={appending || appended || !selected.size || !newsDate} onClick={appendSelected}>{appending ? "Appending…" : appended ? "Appended to Google Sheets" : "Append selected stocks"}</button>
      </div>
      {appendMessage && <p className="feed-notice" role="status">{appendMessage}</p>}
      {result.unmatched_articles.length > 0 && <p className="feed-notice">No recognized stocks in this article. Use a company name or stock symbol included in EQUITY_L.csv.</p>}
      {result.stocks.length > 0 && <div className="extractor-table-wrap"><table className="extractor-table">
        <caption className="muted">Extracted stocks</caption>
        <thead><tr>{["Select", "#", "Stock", "Influence", "Article evidence"].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
        <tbody>{result.stocks.map((stock, index) => <tr key={stock.id} className={selected.has(stock.id) ? "selected" : ""}>
          <td><input type="checkbox" disabled={appending || appended} aria-label={`Select ${stock.stock}`} checked={selected.has(stock.id)} onChange={() => setSelected(previous => { const next = new Set(previous); next.has(stock.id) ? next.delete(stock.id) : next.add(stock.id); return next; })} /></td>
          <td>{index + 1}</td>
          <td><strong>{stock.stock}</strong><div className="muted">{stock.symbol}</div><p className="muted">{stock.selection_reason}</p>{stock.selection_evidence && <blockquote className="muted">{stock.selection_evidence}</blockquote>}</td>
          <td><span className={`influence ${stock.influence}`}>{stock.influence}</span><div className="muted">{stock.analysis_source === "ai" ? "AI analysis" : stock.analysis_source === "keywords" ? "Keyword rules" : "Not analyzed"}</div></td>
          <td className="evidence">{stock.evidence || "Insufficient context."}{stock.reason && <p className="muted">{stock.reason}</p>}</td>
        </tr>)}</tbody>
      </table></div>}
    </div>}
  </section>;
}
