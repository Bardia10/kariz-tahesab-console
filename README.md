# Tahesab Test Console

Standalone Persian RTL client for the Tahesab test accounting system. It has its own React frontend and FastAPI service. It does not import Kariz's application startup, access Kariz's database, start workers, call Kohan, or execute trades outside Tahesab accounting.

## What works

- Connects only to `https://api-test.tahesab.ir`. The database is configured server-side and defaults to `TahesabDB`.
- Lists customers in a bounded code range, supports search and 25-row pages, and shows source details.
- Opens the documents page immediately after connecting and automatically shows documents across all customers, newest first. Search, document-type buttons, pagination, and a details button work without selecting a customer or entering code ranges. Optional filters are collapsed.
- Uses the verified global `DoListAsnad` read: `[0, "", "", "", "", 0]`. On the test server this returned 4,054 rows; counts 25 and 50 returned recent subsets across multiple customers, and samples matched their customer histories. A separate selected-customer history remains available.
- Shows current Kariz product titles alongside the configured Tahesab source identifier and, separately, the Tahesab catalogue label. The current configured Emami coin and Nadir silver-bar identifiers are confirmed in source balance fields; miscellaneous gold has no configured Tahesab product identifier.
- Reads customer balances, aggregate source metals, Tahesab’s combined `MandeMenhayeSeke` non-coin gold value, and available `Seke_*` product positions. Missing required money data is an error, not zero.
- Creates customers, cash and bank documents, miscellaneous and آبشده 750-purity gold receipts/deliveries, and physical coin/bar receipts/deliveries. Each action requires confirmation, is journaled locally before submission, and is checked through source readback.
- Creates accounting trades for the verified Emami coin, Nadir silver bar, and آبشده 750 mode. The gold form supports a quote per mesghal or per gram and uses the configured conversion factor. Miscellaneous-gold accounting trades remain individually disabled because Tahesab readback does not identify that mode.
- Shows uncertain writes in the operation page. The client never automatically resends an uncertain write.

Physical receipts and deliveries are verified for miscellaneous 750 gold and آبشده 750. Accounting trades are enabled for آبشده 750 at per-mesghal or per-gram rates. Miscellaneous-gold accounting trades stay disabled because Tahesab readback does not identify that mode. Coin and bar actions are enabled for the verified configured mappings. See [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) for the verification results and test data notes.

## Run locally on Windows

From this project directory, in one PowerShell window:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 47183
```

In another PowerShell window:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:53927`. Vite proxies `/api` to FastAPI. The dev server is configured to fail if that port is already occupied instead of silently switching ports. For deployment, serve both through the same HTTPS origin, set `APP_ENV=production`, and set `COOKIE_SECURE=true` when TLS terminates at a reverse proxy.

## Connection and local state

- Enter the test-server token on the connection page. It is kept in server memory only, behind an `HttpOnly`, `SameSite=Strict` cookie; a process restart requires reconnecting.
- The local SQLite operation journal is `.local/operations.sqlite3` and is not committed. It stores submitted form data, factor codes, source results, and statuses so pending results can be recovered after restart.
- Every API route requires a session. Write requests enforce same-origin checks and a 32 KiB request limit. Errors do not include the token.
- Reads are cached per session for 30 seconds. Customer ranges are fetched in chunks of at most 250 codes and limited to 2,500 codes per request.

## Refresh the product mapping

The existing authenticated Kariz configuration is exported without importing `app.main` or starting workers:

```powershell
.venv\Scripts\python -m pip install -r scripts\requirements-catalogue.txt
.venv\Scripts\python scripts\export_catalogue.py
.venv\Scripts\python scripts\inspect_connection.py
.venv\Scripts\python scripts\build_catalogue.py
```

The export reads Kariz's existing local configuration and writes only to ignored `.local/`. Review `catalogue.json` before committing changes. Keep display names, Kariz IDs, Kohan IDs, and exact Tahesab source names as separate fields.

See [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) for live verification and remaining limits.
