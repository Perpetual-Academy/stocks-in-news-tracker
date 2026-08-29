import React, { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
const tabs = ["User", "Live Feed", "Setup"];

export default function App() {
  const [screen, setScreen] = useState("login");
  const [tab, setTab] = useState("User");
  const [form, setForm] = useState({ api_key: "", api_secret: "", request_token: "" });
  const [status, setStatus] = useState("");
  const [isSigningIn, setIsSigningIn] = useState(false);
  const [profile, setProfile] = useState(null);
  const [liveFeed, setLiveFeed] = useState(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/session`)
      .then((r) => r.json())
      .then((data) => {
        setForm({
          api_key: data.api_key || "",
          api_secret: data.api_secret || "",
          request_token: "",
        });
        if (data.has_token) {
          setScreen("dashboard");
          loadProfile();
        }
      })
      .catch(() => {});
  }, []);

  async function loadProfile() {
    const res = await fetch(`${API_BASE}/api/profile`);
    const data = await res.json();
    if (!res.ok) {
      setStatus(data.detail || "Unable to load profile");
      return;
    }
    setProfile(data);
  }

  async function loadLiveFeed() {
    try {
      const res = await fetch(`${API_BASE}/api/live-prices`);
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.detail || "Unable to load live prices");
        return;
      }
      setLiveFeed(data);
    } catch {
      setStatus("Could not reach the live price service.");
    }
  }

  useEffect(() => {
    if (screen !== "dashboard" || tab !== "Live Feed") return undefined;
    loadLiveFeed();
    const refresh = window.setInterval(loadLiveFeed, 5000);
    return () => window.clearInterval(refresh);
  }, [screen, tab]);

  async function handleLogin(e) {
    e.preventDefault();
    setStatus("Signing in...");
    setIsSigningIn(true);
    try {
      const res = await fetch(`${API_BASE}/api/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json();
      if (!res.ok) {
        setStatus(data.detail || "Login failed");
        return;
      }
      setStatus("Access token saved.");
      setScreen("dashboard");
      await loadProfile();
    } catch {
      setStatus("Could not reach the token service. Confirm the FastAPI backend is running on port 8000.");
    } finally {
      setIsSigningIn(false);
    }
  }

  if (screen === "login") {
    return React.createElement(
      "div",
      { className: "shell center" },
      React.createElement(
        "div",
        { className: "card login-card" },
        React.createElement(
          "div",
          { className: "brand" },
          React.createElement("span", { className: "brand-pill" }),
          "ShareConnect Access"
        ),
        React.createElement("h1", null, "Login to generate an access token"),
        React.createElement(
          "p",
          { className: "muted" },
          "Your token is stored in Credentials.txt and reused locally until you log in again."
        ),
        React.createElement(
          "form",
          { onSubmit: handleLogin, className: "form" },
          field("API Key", form.api_key, (v) => setForm({ ...form, api_key: v })),
          field("API Secret", form.api_secret, (v) => setForm({ ...form, api_secret: v }), "password"),
          field("Request Token", form.request_token, (v) => setForm({ ...form, request_token: v })),
          React.createElement(
            "button",
            { className: "primary", type: "submit", disabled: isSigningIn },
            isSigningIn ? "Signing in..." : "Login"
          )
        ),
        status ? React.createElement("p", { className: "status" }, status) : null
      )
    );
  }

  return React.createElement(
    "div",
    { className: "shell dashboard" },
    React.createElement(
      "aside",
      { className: "sidebar card" },
      React.createElement(
        "div",
        null,
        React.createElement(
          "div",
          { className: "brand" },
          React.createElement("span", { className: "brand-pill" }),
          "ShareConnect Dashboard"
        ),
        React.createElement("h2", null, "Professional trading workspace"),
        React.createElement("p", { className: "muted" }, "Dark mode, easy to customize, and ready for local development.")
      ),
      React.createElement("button", { className: "secondary", onClick: () => setScreen("login") }, "Log in again")
    ),
    React.createElement(
      "main",
      { className: "card content" },
      React.createElement(
        "div",
        { className: "tabs" },
        tabs.map((name) =>
          React.createElement(
            "button",
            {
              key: name,
              className: tab === name ? "tab active" : "tab",
              onClick: () => setTab(name),
            },
            name
          )
        )
      ),
      tab === "User"
        ? React.createElement(
            "section",
            { className: "panel" },
            React.createElement(
              "div",
              { className: "panel-header" },
              React.createElement("h3", null, "User"),
              React.createElement("button", { className: "secondary", onClick: loadProfile }, "Refresh")
            ),
            React.createElement(
              "div",
              { className: "grid" },
              Metric("User Name", profile?.user_name || profile?.userName || "Loading..."),
              Metric("User ID", profile?.user_id || profile?.userId || "Loading..."),
              Metric(
                "Products",
                Array.isArray(profile?.products) ? profile.products.join(", ") : profile?.products || "Loading..."
              ),
              Metric(
                "Exchanges",
                Array.isArray(profile?.exchanges) ? profile.exchanges.join(", ") : profile?.exchanges || "Loading..."
              )
            )
          )
        : tab === "Live Feed"
          ? React.createElement(
              "section",
              { className: "panel" },
              React.createElement(
                "div",
                { className: "panel-header" },
                React.createElement(
                  "div",
                  null,
                  React.createElement("h3", null, "Live Feed"),
                  React.createElement("p", { className: "muted" }, "Prices refresh every 5 seconds using ShareConnect.")
                ),
                React.createElement(
                  "div",
                  { className: liveFeed?.market?.is_open ? "market-status open" : "market-status closed" },
                  liveFeed?.market?.label || "Checking market status"
                )
              ),
              React.createElement(
                "div",
                { className: "quote-grid" },
                (liveFeed?.prices || ["RELIANCE", "SBIN", "LT"]).map((quote) => {
                  const waiting = typeof quote === "string";
                  return React.createElement(
                    "article",
                    { className: "quote-card", key: waiting ? quote : quote.symbol },
                    React.createElement("div", { className: "quote-symbol" }, waiting ? quote : quote.symbol),
                    React.createElement("div", { className: "quote-price" }, waiting ? "Loading..." : `Rs. ${Number(quote.ltp).toLocaleString("en-IN", { minimumFractionDigits: 2 })}`),
                    waiting
                      ? React.createElement("div", { className: "muted" }, "Connecting to live feed")
                      : React.createElement(
                          "div",
                          { className: Number(quote.percent_change) >= 0 ? "quote-change positive" : "quote-change negative" },
                          `${Number(quote.percent_change).toFixed(2)}% (${Number(quote.change).toFixed(2)})`
                        ),
                    !waiting ? React.createElement("div", { className: "quote-time" }, `Last trade: ${quote.last_traded_at}`) : null
                  );
                })
              ),
              React.createElement("button", { className: "secondary", onClick: loadLiveFeed }, "Refresh now")
            )
          : React.createElement(
            "section",
            { className: "panel" },
            React.createElement("h3", null, "Setup"),
            React.createElement(
              "ul",
              { className: "setup-list" },
              React.createElement("li", null, "Run the FastAPI backend on port 8000."),
              React.createElement("li", null, "Run the Vite frontend on port 5173."),
              React.createElement("li", null, "Keep using the stored access token during local edits.")
            ),
            React.createElement("p", { className: "muted" }, status)
          )
    )
  );
}

function field(label, value, onChange, type = "text") {
  return React.createElement(
    "label",
    null,
    label,
    React.createElement("input", {
      type,
      value,
      onChange: (e) => onChange(e.target.value),
    })
  );
}

function Metric(label, value) {
  return React.createElement(
    "div",
    { className: "metric" },
    React.createElement("div", { className: "metric-label" }, label),
    React.createElement("div", { className: "metric-value" }, value)
  );
}
