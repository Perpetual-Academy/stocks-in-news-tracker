import React, { useEffect, useState } from "react";

const presets = {
  OpenAI: { protocol: "responses", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  Gemini: { protocol: "chat", base_url: "https://generativelanguage.googleapis.com/v1beta/openai", model: "" },
  Groq: { protocol: "chat", base_url: "https://api.groq.com/openai/v1", model: "" },
  Anthropic: { protocol: "anthropic", base_url: "https://api.anthropic.com/v1", model: "" },
  Custom: { protocol: "chat", base_url: "", model: "" },
};

export default function AISetup({ apiBase }) {
  const [form, setForm] = useState({ provider: "OpenAI", ...presets.OpenAI, api_key: "" });
  const [models, setModels] = useState([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [manualModel, setManualModel] = useState(false);
  const [hasKey, setHasKey] = useState(false);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function load() {
    setError("");
    try {
      const response = await fetch(`${apiBase}/api/ai-settings`);
      if (!response.ok) throw new Error("Could not load AI settings.");
      const data = await response.json();
      setForm({ provider: data.provider, protocol: data.protocol, base_url: data.base_url, model: data.model, api_key: "" });
      setHasKey(data.has_key);
      setReady(true);
    } catch { setError("Could not load AI settings. Please retry."); }
  }
  useEffect(() => { load(); }, []);
  function change(key, value) {
    setForm(previous => ({ ...previous, [key]: value }));
    setMessage("");
    setError("");
    if (["base_url", "protocol", "api_key"].includes(key)) { setModels([]); setManualModel(false); }
    if (key === "base_url" || key === "protocol") {
      setHasKey(false);
      setForm(previous => ({ ...previous, api_key: "" }));
    }
  }
  async function loadModels() {
    setLoadingModels(true); setError(""); setMessage(""); setModels([]);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 20000);
    try {
      const { model, ...connection } = form;
      const response = await fetch(`${apiBase}/api/ai-models`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(connection), signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not load models.");
      setModels(data.models); setManualModel(false);
      setMessage(data.models.length ? "Models loaded. Choose a model, then save your settings." : "No suitable models returned. You can enter a model ID manually.");
    } catch (err) { setError(err.name === "AbortError" ? "Loading models timed out. Please retry." : err.message); }
    finally { window.clearTimeout(timer); setLoadingModels(false); }
  }
  async function save(event) {
    event.preventDefault();
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${apiBase}/api/ai-settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Check the API address, model, and key.");
      setForm(previous => ({ ...previous, api_key: "" }));
      setHasKey(data.has_key);
      setMessage("AI settings saved. The extractor will use this connection immediately. Connection has not been tested.");
    } catch (err) { setError(err.message || "Could not save AI settings."); }
    finally { setBusy(false); }
  }
  return <section className="ai-setup">
    <h4>AI connection</h4>
    <p className="muted">Choose the provider used when keyword rules cannot determine influence. One connection is active at a time.</p>
    {!ready && <button className="secondary" onClick={load}>Reload settings</button>}
    <form className="form" onSubmit={save}>
      <fieldset disabled={!ready || busy || loadingModels}>
        <label>Provider<select value={form.provider} onChange={event => {
          const provider = event.target.value;
          setForm({ provider, ...presets[provider], api_key: "" }); setHasKey(false); setModels([]); setManualModel(false); setMessage(""); setError("");
        }}>{Object.keys(presets).map(name => <option key={name}>{name}</option>)}</select></label>
        <label>API format<select value={form.protocol} onChange={event => change("protocol", event.target.value)}>
          <option value="responses">OpenAI Responses</option>
          <option value="chat">OpenAI-compatible Chat Completions</option>
          <option value="anthropic">Anthropic Messages</option>
        </select></label>
        <label>API base URL<input type="url" required value={form.base_url} placeholder="https://your-provider.com/v1" onChange={event => change("base_url", event.target.value)} /></label>
        <p className="muted">Use the base address from your provider, without /responses, /chat/completions, or /messages. Custom providers must support one of the formats above.</p>
        <label>API key<input type="password" autoComplete="new-password" required={!hasKey} value={form.api_key} placeholder={hasKey ? "Key configured — leave blank to keep it" : "Enter your provider’s API key"} onChange={event => change("api_key", event.target.value)} /></label>
        {form.provider === "Groq" && <button className="secondary" type="button" disabled={!form.api_key.trim() && !hasKey} onClick={loadModels}>{loadingModels ? "Loading models…" : "Load models"}</button>}
        {models.length > 0 && !manualModel ? <label>Model<select required value={form.model} onChange={event => change("model", event.target.value)}>
          <option value="">Choose a model</option>
          {form.model && !models.includes(form.model) && <option value={form.model}>{form.model} (current; not in returned list)</option>}
          {models.map(model => <option key={model} value={model}>{model}</option>)}
        </select></label> : <label>Model<input required value={form.model} placeholder={form.provider === "Groq" ? "Load models above or enter a model ID" : "Enter the model ID from your provider"} onChange={event => change("model", event.target.value)} /></label>}
        {models.length > 0 && <button type="button" className="secondary" onClick={() => setManualModel(value => !value)}>{manualModel ? "Choose from loaded models" : "Enter model ID manually"}</button>}
        <p className="muted">The key is saved on the server and is never returned to this form. Article text will be sent to the API address above when AI analysis is needed.</p>
        <button className="primary" type="submit">{busy ? "Saving…" : "Save AI settings"}</button>
      </fieldset>
    </form>
    {message && <p className="status" role="status">{message}</p>}
    {error && <p className="feed-notice error" role="alert">{error}</p>}
  </section>;
}
