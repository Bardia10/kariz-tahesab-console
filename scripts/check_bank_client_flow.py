"""Exercise the authenticated console bank-payment route on its test customer."""
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
    date = jdatetime.date.today().strftime("%Y/%m/%d")
    results = []
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        connected = await client.post("/api/connect", headers={"Origin": "http://testserver"}, json={"token": token})
        if connected.status_code != 200:
            print(json.dumps({"connect_http": connected.status_code, "result": "not_run"}))
            return
        catalogue = (await client.get("/api/catalogue")).json()
        accounts = catalogue.get("source_catalogues", {}).get("bank_accounts") or []
        if not accounts:
            print(json.dumps({"connect_http": connected.status_code, "result": "not_run", "reason": "no source bank accounts"}))
            return
        bank_name = accounts[0].get("Name_Bank")
        for direction in ("deposit", "payout"):
            op = {"operation_id": str(uuid.uuid4()), "kind": "bank_payment", "payload": {
                "customer_code": TEST_CUSTOMER, "direction": direction,
                "amount_toman": "1", "bank_name": bank_name,
                "reference": f"AP{uuid.uuid4().hex[:12]}", "date": date,
                "description": f"Console API bank contract check {direction}",
            }}
            response = await client.post("/api/operations", headers={"Origin": "http://testserver"}, json=op)
            body = response.json()
            results.append({"direction": direction, "http": response.status_code,
                            "status": body.get("status"), "message": body.get("message"),
                            "document_id": (body.get("source_response") or {}).get("document", {}).get("ID"),
                            "radif": (body.get("source_response") or {}).get("document", {}).get("Radif")})
            if response.status_code != 200 or body.get("status") != "verified":
                break
    print(json.dumps({"connected": connected.status_code == 200, "customer": TEST_CUSTOMER,
                      "bank_account_from_source_list": bool(accounts), "paired_route_checks": results},
                     ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
