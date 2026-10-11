"""Recover the previously uncertain bank receipt by readback, never resend it."""
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
TEST_CUSTOMER = 1891


async def main():
    config = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = config.get("tahesab_token") or ""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        connected = await client.post("/api/connect", headers={"Origin": "http://testserver"}, json={"token": token})
        if connected.status_code != 200:
            print(json.dumps({"connect_http": connected.status_code, "recovery": "not_run"}))
            return
        ops = (await client.get("/api/operations")).json().get("items", [])
        uncertain = next((op for op in ops if op.get("kind") == "bank_payment" and op.get("status") == "uncertain" and op.get("payload", {}).get("customer_code") == TEST_CUSTOMER), None)
        if not uncertain:
            print(json.dumps({"connect_http": connected.status_code, "recovery": "no_uncertain_bank_operation"}))
            return
        recovery = await client.post(f"/api/operations/{uncertain['operation_id']}/verify", headers={"Origin": "http://testserver"}, json={})
        recovered = recovery.json()
        result = [{"step": "readback_existing_receipt", "http": recovery.status_code, "status": recovered.get("status"),
                   "document_id": (recovered.get("source_response") or {}).get("document", {}).get("ID")}]
        if recovery.status_code == 200 and recovered.get("status") == "verified":
            payload = uncertain["payload"]
            operation_date = jdatetime.date.today().strftime("%Y/%m/%d")
            op = {"operation_id": str(uuid.uuid4()), "kind": "bank_payment", "payload": {
                "customer_code": TEST_CUSTOMER, "direction": "payout", "amount_toman": "1",
                "bank_name": payload["bank_name"], "reference": f"API{uuid.uuid4().hex[:12]}",
                "date": operation_date, "description": "Console API bank contract check payout",
            }}
            response = await client.post("/api/operations", headers={"Origin": "http://testserver"}, json=op)
            body = response.json()
            result.append({"step": "matching_payout", "http": response.status_code,
                           "status": body.get("status"),
                           "document_id": (body.get("source_response") or {}).get("document", {}).get("ID")})
    print(json.dumps({"connected": True, "customer": TEST_CUSTOMER, "steps": result}, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
