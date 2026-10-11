"""Verify both accounting trade directions with non-zero source balances throughout."""
import asyncio
import argparse
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
UNIT_PRICE_RIAL = 10000


def header():
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    day = jdatetime.date.fromgregorian(date=today)
    return [1, CUSTOMER, -1, 1, day.year, day.month, day.day]


def dec(value):
    if value in (None, "", "null"):
        return None
    return Decimal(tahesab._decimal_text(value))


async def state(token, database, source):
    payload = await tahesab._call(token, database, "balance", [str(CUSTOMER)])
    rows = tahesab.records(payload)
    row = next((x for x in rows if str(x.get("Code")) == str(CUSTOMER)), {})
    return dec(row.get("MandeyeMali")), dec(row.get(f"Seke_{source}")), row


async def submit(token, database, mapping, direction, expected_money, expected_product):
    is_sale = direction == "customer_buys"
    operation_id = str(uuid.uuid4())
    factor = journal.factor_code()
    operation_name = f"contract_trade_{mapping['source_family']}_seeded_{direction}"
    before_money, before_product, _ = await state(token, database, mapping["source_identifier"])
    if before_money is None or before_product is None:
        return {"status": "not_run", "reason": "required starting balance field is missing"}
    expected_after_money = Decimal(expected_money)
    expected_after_product = Decimal(expected_product)
    params = header() + [mapping["source_weight"], mapping["purity"], 1,
                         mapping["source_identifier"], 1 if is_sale else 0,
                         UNIT_PRICE_RIAL, UNIT_PRICE_RIAL,
                         f"Test console bar trade {direction}", factor,
                         -1, -1, int(mapping["metal"]), "IRR"]
    journal.reserve(operation_id, operation_name,
                    {"customer_code": CUSTOMER, "source_identifier": mapping["source_identifier"],
                     "direction": direction, "unit_price_rial": UNIT_PRICE_RIAL,
                     "count": 1, "weight": mapping["source_weight"],
                     "balance_before": {"money_rial": str(before_money), "product_count": str(before_product)}}, factor)
    response = await tahesab.write_call(token, database, "trade_coin" if mapping["source_family"] == "coin" else "trade_bar", params)
    journal.update(operation_id, "submitting", "Awaiting source readback.", response)
    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
    row = next((r for r in docs if str(r.get("Factor_Code")) == factor), None)
    after_money, after_product, _ = await state(token, database, mapping["source_identifier"])
    category, actual_direction = classify_document(row or {})
    checks = {
        "inquiry": bool(inquiry),
        "customer": tahesab.customer_code(row or {}) == CUSTOMER,
        "document_identity": bool(row and row.get("ID") not in (None, "") and row.get("Radif") not in (None, "")),
        "factor": str((row or {}).get("Factor_Code")) == factor,
        "kind_direction": category == "trade" and actual_direction == direction,
        "source_product_balance_key": True,
        "count": dec((row or {}).get("Count")) == Decimal(1),
        "weight": dec((row or {}).get("Vazn")) == Decimal(str(mapping["source_weight"])),
        "unit_price": dec((row or {}).get("Mazaneh")) == Decimal(UNIT_PRICE_RIAL),
        "document_total_sign": dec((row or {}).get("Mali")) == Decimal(-UNIT_PRICE_RIAL if is_sale else UNIT_PRICE_RIAL),
        "product_balance": after_product == expected_after_product,
        "money_balance": after_money == expected_after_money,
        "balance_effective": (row or {}).get("TasirBarMande") in (1, "1", True),
        "not_temporary": (row or {}).get("isSanadMovaghat") in (False, 0, "0", "false"),
    }
    details = {"checks": checks, "balance_before": {"money_rial": str(before_money), "product_count": str(before_product)},
               "balance_after": {"money_rial": str(after_money), "product_count": str(after_product)},
               "document": {k: row.get(k) for k in (
                   "ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn", "Ayar", "Count",
                   "Name_Seke", "Name_Shemsh", "Mali", "Mazaneh", "MazanehType", "TasirBarMande", "isSanadMovaghat") if row and k in row}}
    verified = all(checks.values())
    journal.update(operation_id, "verified" if verified else "uncertain",
                   "Trade source document and non-zero balance readback completed.",
                   {"response": response, **details})
    return {"status": "verified" if verified else "uncertain", **details}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=("coin", "bar"), default="bar")
    family = parser.parse_args().family
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    mapping = next(p for p in json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))["products"] if p["source_family"] == family)
    if any(r["kind"].startswith(f"contract_trade_{family}_seeded_") for r in journal.list_recent()):
        print(json.dumps({"status": "not_run", "reason": "these exact contract probes already have journal records; recover by factor inquiry instead of resubmitting"}))
        return
    money, product, _ = await state(token, database, mapping["source_identifier"])
    if money is None or product is None:
        print(json.dumps({"status": "not_run", "reason": "non-zero seed balances are required", "money": str(money), "product": str(product)}, ensure_ascii=True))
        return
    sale = await submit(token, database, mapping, "customer_buys", money - UNIT_PRICE_RIAL, product + 1)
    if sale["status"] != "verified":
        print(json.dumps({"customer_buys": sale, "customer_sells": "not_run"}, ensure_ascii=True, default=str))
        return
    purchase = await submit(token, database, mapping, "customer_sells", money, product)
    print(json.dumps({"customer": CUSTOMER, "family": family, "source_identifier": mapping["source_identifier"],
                      "customer_buys_from_us": sale, "customer_sells_to_us": purchase}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
