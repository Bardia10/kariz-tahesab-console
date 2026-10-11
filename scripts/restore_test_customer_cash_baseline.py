"""Restore the dedicated test customer's original cash balance after the invalid gold probe."""
import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
import jdatetime
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.main import app

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
CUSTOMER = 1891
EXPECTED_BEFORE = "-23075"
RESTORE_TOMAN = "2308.5"
EXPECTED_AFTER = "10"


async def main():
    config = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = config.get("tahesab_token") or ""
    if not token:
        raise SystemExit("Existing test-server configuration is unavailable; no write was sent.")
    headers = {"Origin": "http://testserver"}
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        connected = await client.post("/api/connect", headers=headers, json={"token": token})
        connected.raise_for_status()
        before_response = await client.get(f"/api/customers/{CUSTOMER}/balance?refresh=true")
        before_response.raise_for_status()
        before = before_response.json().get("money_rial")
        if str(before) != EXPECTED_BEFORE:
            raise SystemExit(f"Expected {EXPECTED_BEFORE} rial before this controlled adjustment; found {before}. No write was sent.")
        operation = {"operation_id": str(uuid.uuid4()), "kind": "cash_payment", "payload": {
            "customer_code": CUSTOMER, "direction": "deposit", "amount_toman": RESTORE_TOMAN,
            "date": jdatetime.date.today().strftime("%Y/%m/%d"),
            "description": "آزمایش: بازگردانی مانده نقدی مشتری پنل پس از پروب نامعتبر طلای متفرقه",
        }}
        response = await client.post("/api/operations", headers=headers, json=operation)
        body = response.json()
        after_response = await client.get(f"/api/customers/{CUSTOMER}/balance?refresh=true")
        after = after_response.json().get("money_rial")
        result = {"write_http": response.status_code, "status": body.get("status"),
                  "message": body.get("message"), "document_id": ((body.get("source_response") or {}).get("document") or {}).get("ID"),
                  "money_before_rial": before, "restored_toman": RESTORE_TOMAN,
                  "money_after_rial": after, "baseline_restored": str(after) == EXPECTED_AFTER}
        print(json.dumps(result, ensure_ascii=False))
        if response.status_code != 200 or body.get("status") != "verified" or str(after) != EXPECTED_AFTER:
            raise SystemExit("The adjustment was not fully verified. It was not resent.")


if __name__ == "__main__":
    asyncio.run(main())
