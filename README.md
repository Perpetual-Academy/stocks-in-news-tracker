# ShareConnect Dashboard

Dark-mode React + Vite frontend with a FastAPI backend for Sharekhan ShareConnect token generation.

## What it does

- Login with API Key, API Secret, and Request Token
- Generate the access token through the ShareConnect SDK
- Save the token back into `Credentials.txt`
- Reuse the saved token during local development
- Open a dashboard with tabs
- Access all tabs from the Login page
- Configure Umbra in Strategies for scheduled BT entries, without automatic exits (see [UMBRA.md](UMBRA.md))
- Show a User tab that calls the backend profile API

## Project layout

- `backend/` FastAPI service
- `frontend/` React + Vite app
- `Credentials.txt` local credential store

## Setup

### 1. Backend

Create an isolated Python environment, then install backend dependencies:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r backend/requirements.txt
```

Run the backend:

```bash
.venv\Scripts\python -m uvicorn backend.main:app --reload --port 8000
```

### 2. Frontend

Install frontend dependencies:

```bash
cd frontend
npm install
```

Run the frontend:

```bash
npm run dev
```

## Notes

- The backend stores `api_key`, `api_secret`, `request_token`, `access_token`, `state`, `vendor_key`, and `version_id` in `Credentials.txt`.
- If the SDK profile method is not available in your local package, the backend returns a safe placeholder response so the UI still works.
- If you want the profile tab to call a specific ShareConnect method, I can wire it to the exact endpoint once you confirm the SDK method name in your environment.


## News extractor

The Extractor tab accepts pasted news and shows numbered stock rows with selection checkboxes, influence labels and article evidence. Stocks are recognized exclusively from `backend/data/EQUITY_L.csv` (2,389 symbols in the supplied file). Company names are case-insensitive and can omit Limited/Ltd; symbols are case-sensitive. Replace the CSV and restart the backend to refresh the universe.

Keyword rules run first. Clear positive or negative signals remain unchanged; mixed keyword signals remain Neutral. When no usable signal exists, the pasted text and unresolved stocks are sent to the selected AI provider. AI returns Positive, Negative or Neutral with a short reason. Failed or unconfigured analysis is shown as Unavailable. Labels estimate influence and do not predict prices.

## AI connection settings

Open Setup > AI connection, select a provider, enter its model ID and API key, and save. OpenAI, Gemini, Groq and Anthropic have address presets. Groq uses the OpenAI-compatible Chat Completions format at https://api.groq.com/openai/v1; enter a model ID available in your Groq account. Custom providers must support OpenAI Responses, OpenAI-compatible Chat Completions, or Anthropic Messages. An arbitrary non-AI API key is not sufficient. Enter the HTTPS base URL without the final /responses, /chat/completions or /messages path.

Settings apply immediately to new extractions. Saving does not test provider access or make a paid request. A blank key preserves the current key only for the same provider, protocol and URL; changing those requires a new key. Model IDs must be available to your provider account.

Settings are stored server-side in AISettings.json beside Credentials.txt (in the persistent data volume for Docker). This file contains the key in plaintext and is excluded from Git, as are .env files. Keys are never returned by the settings API or saved in browser storage. Protect dashboard access and the data directory: the app does not implement per-user authorization for its settings API.

When no saved settings exist, OPENAI_API_KEY and OPENAI_MODEL (default gpt-4o-mini) remain environment fallbacks. Docker Compose forwards these variables. API usage is billed by the selected provider.

AI analysis batches up to 50 unresolved stocks, uses a 20-second network timeout and no automatic retries. Responses are validated against requested stock IDs. Article instructions are treated as untrusted data. OpenAI Responses uses store: false; other providers follow their own retention policies. HTTP redirects are rejected to avoid forwarding keys to another destination.

Run checks with `python -m unittest backend.test_extractor backend.test_ai_influence backend.test_ai_settings` and `npm --prefix frontend run build`.

References:
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://ai.google.dev/gemini-api/docs/openai

For Groq, enter your API key and click **Load models**, select a returned model, then save. Loading also works with a saved Groq key and does not save unsaved settings. Known audio-only and safety-classifier model families are excluded. Model listing does not guarantee inference access or compatibility; manual entry remains available.

Automatic selection: a separate AI assessment reviews all recognized stocks (up to 50), selecting only standalone news about the company's own operations. Bundled news and indirect mentions remain unchecked with an explanation. Influence does not determine selection. All checkboxes remain editable. If selection review fails, all rows stay unchecked with a notice. Selection adds one AI call even when keyword influence is conclusive.

Selection review separates colon-headed news items while retaining continuation paragraphs. Each stock is assessed only against items mentioning it. Credit ratings, planned capacity, investment, targets and order books explicitly qualify as direct company news. Decisions include an eligibility category and verified source quote. Contradictory exclusions receive one additional AI review; unresolved contradictions remain unchecked for manual review. The browser allows up to 90 seconds for influence, selection and review calls.
