"""Seed one mapped coin in the designated test customer for trade direction checks."""
import asyncio
import json
import sys
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import jdatetime
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import journal, tahesab
from backend.main import classify_document

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
CUSTOMER = 1891


async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    mapping = next(p for p in json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))["products"] if p["key"] == "coin_emami")
    name = mapping["source_identifier"]
    raw = tahesab.records(await tahesab._call(token, database, "balance", [str(CUSTOMER)]))
    before = next((r for r in raw if str(r.get("Code")) == str(CUSTOMER)), {})
    money = before.get("MandeyeMali")
    product = before.get(f"Seke_{name}")
    if money in (None, "") or str(product or "0") != "0":
        print(json.dumps({"status": "not_run", "reason": "expected money seed and zero coin balance not present"}, ensure_ascii=True))
        return
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    day = jdatetime.date.fromgregorian(date=today)
    factor = journal.factor_code()
    op_id = str(uuid.uuid4())
    params = [1, CUSTOMER, -1, 1, day.year, day.month, day.day,
              mapping["source_weight"], mapping["purity"], 1, name, 0,
              "Test-console coin trade-contract balance seed", factor,
              -1, -1, int(mapping["metal"])]
    journal.reserve(op_id, "contract_seed_coin_balance",
                    {"customer_code": CUSTOMER, "source_identifier": name,
                     "balance_before": {"money_rial": money, "product_count": product},
                     "count": 1, "weight": mapping["source_weight"]}, factor)
    response = await tahesab.write_call(token, database, "physical_coin", params)
    journal.update(op_id, "submitting", "Awaiting source readback.", response)
    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
    row = next((r for r in docs if str(r.get("Factor_Code")) == factor), None)
    after_rows = tahesab.records(await tahesab._call(token, database, "balance", [str(CUSTOMER)]))
    after = next((r for r in after_rows if str(r.get("Code")) == str(CUSTOMER)), {})
    category, direction = classify_document(row or {})
    checks = {"inquiry": bool(inquiry), "document_identity": bool(row and row.get("ID") not in (None, "") and row.get("Radif") not in (None, "")),
              "customer": tahesab.customer_code(row or {}) == CUSTOMER,
              "direction": category == "delivery" and direction == "incoming",
              "count": str((row or {}).get("Count")) == "1",
              "weight": Decimal(str((row or {}).get("Vazn"))) == Decimal(str(mapping["source_weight"])),
              "money_unchanged": after.get("MandeyeMali") == money,
              "coin_balance_increased": after.get(f"Seke_{name}") == "1",
              "balance_effective": (row or {}).get("TasirBarMande") in (1, "1", True),
              "not_temporary": (row or {}).get("isSanadMovaghat") in (False, 0, "0", "false")}
    verified = all(checks.values())
    journal.update(op_id, "verified" if verified else "uncertain", "Coin contract seed readback completed.",
                   {"response": response, "document": row, "checks": checks})
    print(json.dumps({"status": "verified" if verified else "uncertain",
                      "money_rial_before": money, "money_rial_after": after.get("MandeyeMali"),
                      "coin_count_before": product, "coin_count_after": after.get(f"Seke_{name}"),
                      "checks": checks}, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
