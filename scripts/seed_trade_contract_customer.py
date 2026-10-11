"""Seed the dedicated test customer with a small cash credit and one silver bar."""
import asyncio
import json
import sys
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import jdatetime
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import journal, tahesab
from backend.main import app, classify_document

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
CUSTOMER = 1891


def jalali():
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    return jdatetime.date.fromgregorian(date=today).strftime("%Y/%m/%d")


async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    date = jalali()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://testserver") as client:
        connected = await client.post("/api/connect", headers={"Origin": "http://testserver"}, json={"token": token})
        if connected.status_code != 200:
            print(json.dumps({"status": "not_run", "connect_http": connected.status_code}))
            return
        balance = await client.get(f"/api/customers/{CUSTOMER}/balance?refresh=true")
        if balance.status_code != 200:
            op = {"operation_id": str(uuid.uuid4()), "kind": "cash_payment", "payload": {
                "customer_code": CUSTOMER, "direction": "deposit", "amount_toman": "1",
                "date": date, "description": "Test-console trade contract balance seed",
            }}
            response = await client.post("/api/operations", headers={"Origin": "http://testserver"}, json=op)
            body = response.json()
            if response.status_code != 200 or body.get("status") != "verified":
                print(json.dumps({"status": body.get("status", "error"), "step": "cash_seed",
                                  "http": response.status_code}, ensure_ascii=True))
                return

    mapping = next(p for p in json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))["products"] if p["key"] == "silver_bar_nadir")
    source_name = mapping["source_identifier"]
    response_balance = tahesab.records(await tahesab._call(token, database, "balance", [str(CUSTOMER)]))
    current = next((r for r in response_balance if str(r.get("Code")) == str(CUSTOMER)), {})
    before_product = current.get(f"Seke_{source_name}")
    if current.get("MandeyeMali") in (None, ""):
        print(json.dumps({"status": "not_run", "step": "bar_seed",
                          "reason": "a required money balance field is still absent"}, ensure_ascii=True))
        return
    factor = journal.factor_code()
    operation_id = str(uuid.uuid4())
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    day = jdatetime.date.fromgregorian(date=today)
    params = [1, CUSTOMER, -1, 1, day.year, day.month, day.day,
              mapping["source_weight"], mapping["purity"], 1, source_name,
              0, "Test-console silver bar trade-contract balance seed", factor,
              -1, -1, int(mapping["metal"])]
    journal.reserve(operation_id, "contract_seed_bar_balance",
                    {"customer_code": CUSTOMER, "source_identifier": source_name,
                     "previous_source_balance": before_product, "count": 1,
                     "weight": mapping["source_weight"]}, factor)
    result = await tahesab.write_call(token, database, "physical_bar", params)
    journal.update(operation_id, "submitting", "Awaiting source readback.", result)
    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
    row = next((r for r in docs if str(r.get("Factor_Code")) == factor), None)
    after_rows = tahesab.records(await tahesab._call(token, database, "balance", [str(CUSTOMER)]))
    after = next((r for r in after_rows if str(r.get("Code")) == str(CUSTOMER)), {})
    category, direction = classify_document(row or {})
    checks = {"inquiry": bool(inquiry), "document": row is not None,
              "customer": tahesab.customer_code(row or {}) == CUSTOMER,
              "id_radif": bool(row and row.get("ID") not in (None, "") and row.get("Radif") not in (None, "")),
              "direction": category == "delivery" and direction == "incoming",
              "count": str((row or {}).get("Count")) == "1",
              "weight": Decimal(str((row or {}).get("Vazn"))) == Decimal(str(mapping["source_weight"])),
              "product_balance_now_one": after.get(f"Seke_{source_name}") == "1",
              "balance_effective": (row or {}).get("TasirBarMande") in (1, "1", True),
              "not_temporary": (row or {}).get("isSanadMovaghat") in (False, 0, "0", "false")}
    verified = all(checks.values())
    journal.update(operation_id, "verified" if verified else "uncertain",
                   "Bar source balance seed readback completed.", {"response": result, "document": row, "checks": checks})
    print(json.dumps({"status": "verified" if verified else "uncertain",
                      "cash_balance_seed": "verified" if balance.status_code != 200 else "not_needed",
                      "cash_rial": current.get("MandeyeMali"), "product_before": before_product,
                      "product_after": after.get(f"Seke_{source_name}"), "checks": checks}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
