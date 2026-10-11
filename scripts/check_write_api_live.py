"""Exercise the client write routes against the designated Tahesab test customer."""
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import jdatetime
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.main import app, sessions
from backend.tahesab import BASE_URL

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
CUSTOMER = 1891


def today():
    day = jdatetime.date.fromgregorian(date=datetime.now(ZoneInfo("Asia/Tehran")).date())
    return f"{day.year:04d}/{day.month:02d}/{day.day:02d}"


async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    if not token:
        raise SystemExit("Existing test token configuration is unavailable; no writes were sent.")
    sessions["live-write-check"] = (token, time.monotonic() + 3600)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("tahesab_test_session", "live-write-check")
        headers = {"Origin": "http://testserver"}
        catalogue = await client.get("/api/catalogue?refresh=true", headers=headers)
        catalogue.raise_for_status()
        products = [p for p in catalogue.json()["mapping"]["products"] if p.get("verified") and p.get("source_family") in {"coin", "bar"}]
        if len(products) != 2:
            raise SystemExit(f"Expected two verified coin/bar mappings; found {len(products)}. No writes were sent.")
        balance = await client.get(f"/api/customers/{CUSTOMER}/balance?refresh=true", headers=headers)
        balance.raise_for_status()
        for product in products:
            key = f"Seke_{product['source_identifier']}"
            if key not in balance.json().get("source", {}):
                raise SystemExit(f"Required starting field {key!r} is missing; no writes were sent.")

        results = []

        async def create(kind, payload, op_id=None):
            operation_id = op_id or str(uuid.uuid4())
            response = await client.post("/api/operations", headers=headers,
                                         json={"operation_id": operation_id, "kind": kind, "payload": payload})
            if response.status_code >= 400:
                raise RuntimeError(f"{kind} returned HTTP {response.status_code}: {response.text}")
            body = response.json()
            source_response = body.get("source_response") or {}
            document = source_response.get("document") or {}
            results.append({"kind": kind, "status": body.get("status"), "message": body.get("message"),
                            "document_id": document.get("ID"), "radif": document.get("Radif")})
            if body.get("status") != "verified":
                raise RuntimeError(f"{kind} was not verified: {json.dumps(body, ensure_ascii=False)}")
            return operation_id, body

        for product in products:
            family = product["source_family"]
            physical_kind = f"physical_{family}"
            trade_kind = f"trade_{family}"
            key = product["key"]
            for direction in ("incoming", "outgoing"):
                await create(physical_kind, {"customer_code": CUSTOMER, "product_key": key,
                                             "direction": direction, "count": 1, "date": today(),
                                             "description": "آزمایش مسیر ثبت پنل مستقل"})
            trade_op, trade_result = await create(trade_kind, {"customer_code": CUSTOMER, "product_key": key,
                                                               "direction": "customer_buys", "count": 1,
                                                               "unit_price_toman": "1000", "date": today(),
                                                               "description": "آزمایش مسیر ثبت پنل مستقل"})
            duplicate = await client.post("/api/operations", headers=headers,
                                          json={"operation_id": trade_op, "kind": trade_kind,
                                                "payload": trade_result["payload"]})
            duplicate.raise_for_status()
            if duplicate.json().get("source_response") != trade_result.get("source_response"):
                raise RuntimeError("Duplicate operation ID did not return the existing journal result.")
            changed = dict(trade_result["payload"])
            changed["unit_price_toman"] = "1001"
            conflict = await client.post("/api/operations", headers=headers,
                                         json={"operation_id": trade_op, "kind": trade_kind, "payload": changed})
            if conflict.status_code != 409:
                raise RuntimeError(f"Changed duplicate payload should return 409, got {conflict.status_code}.")
            await create(trade_kind, {"customer_code": CUSTOMER, "product_key": key,
                                      "direction": "customer_sells", "count": 1,
                                      "unit_price_toman": "1000", "date": today(),
                                      "description": "آزمایش مسیر ثبت پنل مستقل"})

    print(json.dumps({"customer_code": CUSTOMER, "server": BASE_URL,
                      "live_actions": results, "duplicate_same_payload": "reused existing verified result",
                      "duplicate_changed_payload": "rejected with HTTP 409"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
