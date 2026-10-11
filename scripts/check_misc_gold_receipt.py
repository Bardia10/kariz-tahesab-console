"""Verify the supported physical miscellaneous-gold receipt through the client API."""
import asyncio
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from dotenv import dotenv_values
import httpx
import jdatetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.main import app

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
CUSTOMER = 1891


async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    date = jdatetime.date.today().strftime("%Y/%m/%d")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://testserver") as client:
        connected = await client.post("/api/connect", headers={"Origin": "http://testserver"}, json={"token": token})
        if connected.status_code != 200:
            print(json.dumps({"status": "not_run", "connect_http": connected.status_code}))
            return
        before_response = await client.get(f"/api/customers/{CUSTOMER}/balance?refresh=true")
        before_body = before_response.json()
        before = before_body.get("noncoin_gold_grams")
        if before is None:
            print(json.dumps({"status": "not_run", "reason": "source has not returned a separate miscellaneous-gold balance"}))
            return
        op = {"operation_id": str(uuid.uuid4()), "kind": "gold_receipt_750", "payload": {
            "customer_code": CUSTOMER, "grams": "0.01", "date": date,
            "description": "Test-console miscellaneous-gold balance contract check",
        }}
        response = await client.post("/api/operations", headers={"Origin": "http://testserver"}, json=op)
        result = response.json()
        balance = await client.get(f"/api/customers/{CUSTOMER}/balance?refresh=true")
        body = balance.json()
        after = body.get("noncoin_gold_grams")
        expected = Decimal(str(before)) + Decimal("0.01")
        print(json.dumps({"connected": True, "write_http": response.status_code,
                          "operation_status": result.get("status"), "operation_message": result.get("message"),
                          "document": (result.get("source_response") or {}).get("document"),
                          "balance_http": balance.status_code,
                          "misc_gold_before": before, "misc_gold_expected_after": str(expected),
                          "misc_gold_delta_verified": after is not None and Decimal(str(after)) == expected,
                          "noncoin_gold_grams": body.get("noncoin_gold_grams"),
                          "aggregate_gold_weight": body.get("aggregate_gold_weight"),
                          "product_positions": body.get("product_balances")}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
