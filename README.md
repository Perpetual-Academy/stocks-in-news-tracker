# ShareConnect Dashboard

Dark-mode React + Vite frontend with a FastAPI backend for Sharekhan ShareConnect token generation.

## What it does

- Login with API Key, API Secret, and Request Token
- Generate the access token through the ShareConnect SDK
- Save the token back into `Credentials.txt`
- Reuse the saved token during local development
- Open a dashboard with tabs
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
