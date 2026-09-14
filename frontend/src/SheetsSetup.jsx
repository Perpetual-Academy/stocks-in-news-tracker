import React, { useEffect, useState } from "react";

export default function SheetsSetup({ apiBase }) {
  const [settings, setSettings] = useState(null);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function request(path, options) {
    const response = await fetch(`${apiBase}/api/google-sheets/${path}`, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Google Sheets request failed.");
    return data;
  }
  async function load() {
    try { setSettings(await request("settings")); }
    catch { setMessage("Could not load Google Sheets settings. Please retry."); }
  }
  useEffect(() => { load(); }, []);
  async function save(event) {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      if (!file || file.size > 20000) throw new Error("Choose a service-account JSON file smaller than 20 KB.");
      setSettings(await request("settings", { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ service_account_json: await file.text() }) }));
      setFile(null); event.target.reset();
      setMessage("Service account saved. Share the spreadsheet with the email below as Editor, then test the connection.");
    } catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  }
  async function test() {
    setBusy(true); setMessage("");
    try { setMessage((await request("test", { method: "POST" })).message); }
    catch (err) { setMessage(err.message); }
    finally { setBusy(false); }
  }
  return <section className="ai-setup">
    <h4>Google Sheets connection</h4>
    <p><a href={settings?.url || "https://docs.google.com/spreadsheets/d/1u-wz-a9fSaBfD9Sp33Kyj2oP41qwQRHpW-i0MH_xtIc/edit#gid=0"} target="_blank" rel="noreferrer">AutoTracker for Stocks in News</a> · Tracker</p>
    <p className="muted">Date, Stock Name, and Influence are filled in existing rows. Symbols and other cells are left unchanged.</p>
    <ol className="muted">
      <li>In Google Cloud, enable the Google Sheets API and create a service account.</li>
      <li>Under the service account’s Keys tab, create and download a JSON key.</li>
      <li>Upload that file here and share the spreadsheet with the displayed service-account email as Editor.</li>
    </ol>
    <form className="form" onSubmit={save}>
      <label>Service-account JSON key<input type="file" accept=".json,application/json" disabled={busy} onChange={event => setFile(event.target.files[0] || null)} /></label>
      <button className="primary" disabled={busy || !file} type="submit">Save service account</button>
    </form>
    <p className="muted">The private key is stored on the server and is never returned to your browser.</p>
    {settings?.service_account_email && <p>Share with: <strong>{settings.service_account_email}</strong></p>}
    <button className="secondary" type="button" disabled={busy || !settings?.configured} onClick={test}>{busy ? "Working…" : "Test connection"}</button>
    {!settings && <button className="secondary" onClick={load}>Reload settings</button>}
    {message && <p role="status" className="feed-notice">{message}</p>}
  </section>;
}
