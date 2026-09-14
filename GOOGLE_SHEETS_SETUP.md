# Connect the tracker to Google Sheets

The tracker targets **AutoTracker for Stocks in News**, spreadsheet ID
`1u-wz-a9fSaBfD9Sp33Kyj2oP41qwQRHpW-i0MH_xtIc`, tab **Tracker**.
The header row must be exactly: Date, Stock Name, Symbol, Influence.

## One-time Google setup

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select or create a project.
2. Enable **Google Sheets API** under APIs & Services > Library.
3. Under IAM & Admin > Service Accounts, create a service account. No project-wide Editor role or domain-wide delegation is required.
4. Open that account, choose Keys > Add key > Create new key > JSON, and download it.
5. In this dashboard, open Setup > Google Sheets connection. Upload the JSON file and save.
6. Copy the service-account email displayed by the dashboard. Open the target Google Sheet and share it with this email as **Editor**.
7. Click **Test connection**. This reads the headers without adding sample rows. Successful read access does not prove Editor access, so keep the Editor sharing role.

## Append selected stocks

Extract news, review the checkboxes, choose the news date, and click **Append selected stocks**.
Date (column A), stock name (column B), and influence (column D) are written. Symbol and other columns remain untouched. The date defaults to today in India and is saved as YYYY-MM-DD.
Company names are resolved from the CSV; only Positive, Negative and Neutral labels can be submitted.
Existing cells are updated using RAW values after the last occupied B or D cell. Formulas in these columns count as occupied. No rows are inserted or expanded; insufficient existing rows causes an error. Writes from this server process are serialized. Avoid editing destination cells while a save is in progress.

Each append has a durable request ID. Repeating the same request returns its receipt without appending again.
If a network failure makes the result uncertain, the request is blocked from retry; inspect Tracker before creating a new extraction/request.
Fresh extractions are new requests: identical stocks on the same date are not globally deduplicated.

## Hostinger VPS

Rebuild and recreate the existing Docker Compose service after deployment. The new dependency is included in backend/requirements.txt.
The uploaded credential is stored as GoogleSheetsSettings.json beside Credentials.txt.
The append receipt database is sheets-appends.sqlite3 in the same directory.
The existing /data volume persists both across container rebuilds. Back up both together and restrict filesystem access.
The JSON file contains a private key; it is never returned by the settings API and is excluded from Git.
The site needs no Codex plugin or interactive Google sign-in at runtime.

This app currently lacks per-user authorization for its API routes. Before exposing it publicly, protect the tracker with authenticated access at the VPS/reverse proxy (including /api routes). Otherwise visitors could change settings or append rows.

References: [cell update API](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate),
[service-account authentication](https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.service_account.html).
