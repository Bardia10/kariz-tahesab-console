"""Controlled paired physical/trade contract probes for the Tahesab test account."""
import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import jdatetime
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import journal, tahesab
from backend.main import classify_document

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
TEST_CUSTOMER = 1891


def header():
    day = datetime.now(ZoneInfo("Asia/Tehran")).date()
    j = jdatetime.date.fromgregorian(date=day)
    return [1, TEST_CUSTOMER, -1, 1, j.year, j.month, j.day]


def number(value):
    try:
        return Decimal(tahesab._decimal_text(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


async def raw_balance(token, database):
    payload = await tahesab._call(token, database, "balance", [str(TEST_CUSTOMER)])
    return tahesab.records(payload)


async def send_one(token, database, family, kind, mapping, direction, description):
    factor = journal.factor_code()
    operation_id = str(uuid.uuid4())
    incoming = direction == "incoming"
    if kind == "physical":
        op = "physical_coin" if family == "coin" else "physical_bar" if family == "bar" else "gold_receipt"
        if family in {"coin", "bar"}:
            params = header() + [mapping["source_weight"], mapping["purity"], 1,
                                 mapping["source_identifier"], 0 if incoming else 1,
                                 description, factor, -1, -1, int(mapping["metal"])]
            expected_product = mapping["source_identifier"]
            quantity_fields = {"count": 1, "weight": Decimal(str(mapping["source_weight"]))}
        else:
            params = header() + ["0.01", 750, 0, "Kariz", 0 if incoming else 1, 0,
                                 description, factor, 0, -1, int(mapping["metal"]), False]
            expected_product = "Kariz"
            quantity_fields = {"weight": Decimal("0.01")}
    else:
        op = "trade_coin" if family == "coin" else "trade_bar" if family == "bar" else "trade_gold"
        buy_or_sale = 1 if incoming else 0  # customer buys from us -> source sale
        if family in {"coin", "bar"}:
            unit_price = 10000
            total = unit_price
            params = header() + [mapping["source_weight"], mapping["purity"], 1,
                                 mapping["source_identifier"], buy_or_sale, unit_price,
                                 total, description, factor, -1, -1, int(mapping["metal"]), "IRR"]
            expected_product = mapping["source_identifier"]
            quantity_fields = {"count": 1, "weight": Decimal(str(mapping["source_weight"])), "amount": total}
        else:
            unit_rate_mesghal = 10000000  # toman per mesghal, explicit source unit flag=1
            factor_gram = Decimal(str(mapping["mesghal_to_gram_factor"]))
            grams = Decimal("0.001")
            amount_rial = int((grams * Decimal(unit_rate_mesghal) / factor_gram * 10).to_integral_value())
            params = header() + [str(grams), 750, 123456, "Reygiri", buy_or_sale,
                                 unit_rate_mesghal, 1, 1, amount_rial, description,
                                 factor, 0, -1, int(mapping["metal"]), ""]
            expected_product = "Reygiri"
            quantity_fields = {"weight": grams, "amount": amount_rial, "mazaneh": unit_rate_mesghal}
    journal.reserve(operation_id, f"contract_{kind}_{family}_{direction}",
                    {"customer_code": TEST_CUSTOMER, "family": family, "kind": kind,
                     "direction": direction, "product": expected_product,
                     "factor_code": factor, "params": params}, factor)
    before = await raw_balance(token, database)
    response = await tahesab.write_call(token, database, op, params)
    journal.update(operation_id, "submitting", "Awaiting source readback.", response)
    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    docs = await tahesab.documents_for_customer(token, database, TEST_CUSTOMER)
    row = next((item for item in docs if str(item.get("Factor_Code")) == factor), None)
    after = await raw_balance(token, database)
    if not inquiry or not row:
        journal.update(operation_id, "uncertain", "Source inquiry/history did not prove the submitted test document.", {"response": response})
        return {"status": "uncertain", "family": family, "kind": kind, "direction": direction,
                "factor_present": bool(inquiry), "document_present": bool(row)}
    category, actual_direction = classify_document(row)
    product_key = next((key for key in ("Name_Seke", "Name_Sekeh", "Name_Shemsh", "Name_az") if row.get(key) not in (None, "")), None)
    actual_product = row.get(product_key) if product_key else None
    balance_after_row = next((item for item in after if str(item.get("Code")) == str(TEST_CUSTOMER)), {})
    product_balance_key = f"Seke_{mapping['source_identifier']}"
    returned_product_balance = balance_after_row.get(product_balance_key)
    checks = {
        "customer": tahesab.customer_code(row) == TEST_CUSTOMER,
        "identity_id_radif": row.get("ID") not in (None, "") and row.get("Radif") not in (None, ""),
        "factor": str(row.get("Factor_Code")) == factor,
        "direction": actual_direction == ("incoming" if kind == "physical" and incoming else "outgoing" if kind == "physical" else "customer_buys" if incoming else "customer_sells"),
        "document_kind": category == ("delivery" if kind == "physical" else "trade"),
        "product": (tahesab.normalize(actual_product) == tahesab.normalize(expected_product)
                    if actual_product is not None and family in {"coin", "bar"}
                    else returned_product_balance not in (None, "") if family in {"coin", "bar"}
                    else actual_product is not None),
        "not_temporary": row.get("isSanadMovaghat") in (False, 0, "0", "false"),
    }
    count = number(row.get("Count"))
    weight = number(row.get("Vazn"))
    if "count" in quantity_fields:
        checks["count"] = count == Decimal(quantity_fields["count"])
    checks["weight"] = weight == quantity_fields["weight"]
    if "amount" in quantity_fields:
        checks["amount"] = number(row.get("Mali")) is not None and abs(number(row.get("Mali"))) == Decimal(quantity_fields["amount"])
    if "mazaneh" in quantity_fields:
        checks["mazaneh"] = number(row.get("Mazaneh")) == Decimal(quantity_fields["mazaneh"])
    checked = all(checks.values())
    journal.update(operation_id, "verified" if checked else "uncertain",
                   "Source test-document readback checks completed.",
                   {"response": response, "document": row, "checks": checks})
    return {"status": "verified" if checked else "uncertain", "family": family,
            "kind": kind, "direction": direction, "checks": checks,
            "balance_before": before, "balance_after": after,
            "document": {k: row.get(k) for k in (
                "ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn", "Ayar", "Count",
                "Name_Seke", "Name_Shemsh", "Name_az", "Mali", "Mazaneh", "MazanehType",
                "TasirBarMande", "isSanadMovaghat") if k in row}}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("physical", "trade"), required=True)
    parser.add_argument("--family", choices=("coin", "bar", "gold"), required=True)
    args = parser.parse_args()
    config = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = config.get("tahesab_token") or ""
    host = str(config.get("tahesab_base_url") or tahesab.BASE_URL).rstrip("/")
    database = config.get("tahesab_dbname") or config.get("tahesab_database") or "TahesabDB"
    if not token or host != tahesab.BASE_URL:
        print(json.dumps({"status": "not_run", "reason": "not connected to the configured test host"}))
        return
    mappings = json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))["products"]
    mapping = next(p for p in mappings if p["source_family"] == args.family)
    prior = [row for row in journal.list_recent()
             if row["kind"].startswith(f"contract_{args.kind}_{args.family}_")]
    if prior:
        print(json.dumps({"status": "not_run", "reason": "this contract probe already has durable operation records; recover by inquiry instead of resending",
                          "prior_statuses": [row["status"] for row in prior]}, ensure_ascii=True))
        return
    if args.kind == "trade" and args.family in {"coin", "bar"} and not mapping.get("verified"):
        print(json.dumps({"status": "not_run", "reason": "source product mapping is not verified"}))
        return
    if args.kind == "trade" and args.family == "gold" and not mapping.get("mesghal_to_gram_factor"):
        print(json.dumps({"status": "not_run", "reason": "configured mesghal-to-gram factor missing"}))
        return
    results = []
    for direction in ("incoming", "outgoing"):
        result = await send_one(token, database, args.family, args.kind, mapping, direction,
                                f"Tahesab console {args.kind} {args.family} contract {direction} {uuid.uuid4().hex[:8]}")
        results.append(result)
        if result["status"] != "verified":
            break
    print(json.dumps({"test_customer": TEST_CUSTOMER, "kind": args.kind,
                      "family": args.family, "checks": results}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
