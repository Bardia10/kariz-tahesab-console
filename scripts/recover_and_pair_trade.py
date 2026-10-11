"""Recover source trade readback and balance effects, then send only its inverse."""
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
CUSTOMER = 1891
AMOUNT_RIAL = 10000


def header():
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    day = jdatetime.date.fromgregorian(date=today)
    return [1, CUSTOMER, -1, 1, day.year, day.month, day.day]


def dec(value):
    if value in (None, "", "null"):
        return None
    try:
        return Decimal(tahesab._decimal_text(value))
    except (InvalidOperation, ValueError):
        return None


async def snapshot(token, database, source_name):
    payload = await tahesab._call(token, database, "balance", [str(CUSTOMER)])
    rows = tahesab.records(payload)
    item = next((row for row in rows if str(row.get("Code")) == str(CUSTOMER)), {})
    money = dec(item.get("MandeyeMali"))
    quantity = dec(item.get(f"Seke_{source_name}"))
    return money, quantity, item


def source_row(docs, factor):
    return next((row for row in docs if str(row.get("Factor_Code")) == factor), None)


async def verify(token, database, mapping, factor, expected_direction,
                 expected_quantity, expected_money, expected_mazaneh):
    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
    row = source_row(docs, factor)
    money, quantity, balance = await snapshot(token, database, mapping["source_identifier"])
    if not inquiry or not row:
        return None, {"inquiry": bool(inquiry), "document": bool(row),
                      "money_balance": str(money), "product_balance": str(quantity)}
    category, direction = classify_document(row)
    checks = {
        "customer": tahesab.customer_code(row) == CUSTOMER,
        "id_and_radif": row.get("ID") not in (None, "") and row.get("Radif") not in (None, ""),
        "factor": str(row.get("Factor_Code")) == factor,
        "trade_kind_and_direction": category == "trade" and direction == expected_direction,
        "count": dec(row.get("Count")) == Decimal(1),
        "weight": dec(row.get("Vazn")) == Decimal(str(mapping["source_weight"])),
        "unit_price_field": dec(row.get("Mazaneh")) == Decimal(expected_mazaneh),
        "amount_sign_and_total": dec(row.get("Mali")) == Decimal(expected_money),
        "product_balance_effect": quantity == expected_quantity,
        "final_document": row.get("isSanadMovaghat") in (False, 0, "0", "false"),
        "balance_effective": row.get("TasirBarMande") in (1, "1", True),
    }
    details = {"checks": checks, "money_balance": str(money),
               "product_balance": str(quantity), "balance_fields": sorted(balance),
               "document": {k: row.get(k) for k in (
                   "ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn", "Ayar", "Count",
                   "Name_Seke", "Name_Shemsh", "Mali", "Mazaneh", "MazanehType",
                   "TasirBarMande", "isSanadMovaghat") if k in row}}
    return (row if all(checks.values()) else None), details


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=("coin", "bar"), required=True)
    family = parser.parse_args().family
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    mappings = json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))["products"]
    mapping = next(p for p in mappings if p["source_family"] == family)
    source_name = mapping["source_identifier"]
    incoming_kind, outgoing_kind = f"contract_trade_{family}_incoming", f"contract_trade_{family}_outgoing"
    if not mapping.get("verified"):
        print(json.dumps({"status": "not_run", "reason": "configured source mapping not verified"}))
        return
    prior = next((r for r in journal.list_recent() if r["kind"] == incoming_kind), None)
    existing_out = next((r for r in journal.list_recent() if r["kind"] == outgoing_kind), None)
    before_money, before_quantity, _ = await snapshot(token, database, source_name)
    if before_money is None or before_quantity not in (None, Decimal(0), Decimal(1)):
        print(json.dumps({"status": "not_run", "reason": "test customer has an unexpected starting balance",
                          "money": str(before_money), "product": str(before_quantity)}, ensure_ascii=True))
        return

    if prior:
        factor = prior["factor_code"]
        # The already-run coin sale started from zero and should leave one unit and -10000 rial.
        existing, details = await verify(token, database, mapping, factor, "customer_buys",
                                         Decimal(1), Decimal(-AMOUNT_RIAL), AMOUNT_RIAL)
        if not existing:
            journal.update(prior["operation_id"], "uncertain", "Existing trade readback incomplete; no trade was resent.", details)
            print(json.dumps({"existing_trade": "uncertain", "details": details}, ensure_ascii=True, default=str))
            return
        journal.update(prior["operation_id"], "verified", "Trade and exact source product/money balance effects verified by readback.", details)
    else:
        if before_money != Decimal(0) or before_quantity not in (None, Decimal(0)):
            print(json.dumps({"status": "not_run", "reason": "baseline is not zero; no trade submitted",
                              "money": str(before_money), "product": str(before_quantity)}, ensure_ascii=True))
            return
        factor = journal.factor_code()
        operation_id = str(uuid.uuid4())
        params = header() + [mapping["source_weight"], mapping["purity"], 1,
                             source_name, 1, AMOUNT_RIAL, AMOUNT_RIAL,
                             "Tahesab console test counted-product sale", factor,
                             -1, -1, int(mapping["metal"]), "IRR"]
        journal.reserve(operation_id, incoming_kind,
                        {"customer_code": CUSTOMER, "source_identifier": source_name,
                         "count": 1, "weight": mapping["source_weight"],
                         "unit_price_rial": AMOUNT_RIAL, "total_rial": AMOUNT_RIAL,
                         "balance_before": {"money": str(before_money), "product": str(before_quantity)},
                         "params": params}, factor)
        response = await tahesab.write_call(token, database, "trade_coin" if family == "coin" else "trade_bar", params)
        journal.update(operation_id, "submitting", "Awaiting source readback.", response)
        existing, details = await verify(token, database, mapping, factor, "customer_buys",
                                         Decimal(1), Decimal(-AMOUNT_RIAL), AMOUNT_RIAL)
        if not existing:
            journal.update(operation_id, "uncertain", "Trade readback incomplete; it will not be resent.", {"response": response, **details})
            print(json.dumps({"incoming_trade": "uncertain", "details": details}, ensure_ascii=True, default=str))
            return
        journal.update(operation_id, "verified", "Trade and exact source product/money balance effects verified by readback.", {"response": response, **details})

    if existing_out:
        factor = existing_out["factor_code"]
        result_row, details = await verify(token, database, mapping, factor, "customer_sells",
                                           Decimal(0), Decimal(0), AMOUNT_RIAL)
        print(json.dumps({"incoming_trade": "verified", "inverse_trade": "verified" if result_row else "uncertain",
                          "details": details, "note": "An existing inverse was only read back; it was not resent."}, ensure_ascii=True, default=str))
        return

    factor = journal.factor_code()
    operation_id = str(uuid.uuid4())
    params = header() + [mapping["source_weight"], mapping["purity"], 1,
                         source_name, 0, AMOUNT_RIAL, AMOUNT_RIAL,
                         "Tahesab console test counted-product reverse sale", factor,
                         -1, -1, int(mapping["metal"]), "IRR"]
    current_money, current_quantity, _ = await snapshot(token, database, source_name)
    if current_money != Decimal(-AMOUNT_RIAL) or current_quantity != Decimal(1):
        print(json.dumps({"incoming_trade": "verified", "inverse_trade": "not_run",
                          "reason": "source balances are not exactly the expected pre-inverse values"}, ensure_ascii=True))
        return
    journal.reserve(operation_id, outgoing_kind,
                    {"customer_code": CUSTOMER, "source_identifier": source_name,
                     "count": 1, "weight": mapping["source_weight"],
                     "unit_price_rial": AMOUNT_RIAL, "total_rial": AMOUNT_RIAL,
                     "balance_before": {"money": str(current_money), "product": str(current_quantity)},
                     "params": params}, factor)
    response = await tahesab.write_call(token, database, "trade_coin" if family == "coin" else "trade_bar", params)
    journal.update(operation_id, "submitting", "Awaiting source readback.", response)
    result_row, details = await verify(token, database, mapping, factor, "customer_sells",
                                       Decimal(0), Decimal(0), AMOUNT_RIAL)
    status = "verified" if result_row else "uncertain"
    journal.update(operation_id, status, "Inverse trade source readback completed.", {"response": response, **details})
    print(json.dumps({"customer": CUSTOMER, "family": family, "source_identifier": source_name,
                      "incoming_trade": "verified", "inverse_trade": status,
                      "details": details}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
