"""Verify physical delivery and receipt contracts for both Tahesab gold modes.

Uses the dedicated test customer and 0.001 g documents. Each delivery is
followed by its receipt only after exact document and balance readback succeeds.
"""
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

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
CUSTOMER = 1891
GRAMS = Decimal("0.001")


def header():
    day = jdatetime.date.fromgregorian(date=datetime.now(ZoneInfo("Asia/Tehran")).date())
    return [1, CUSTOMER, -1, 1, day.year, day.month, day.day]


def decimal(value):
    return None if value in (None, "", "null") else Decimal(tahesab._decimal_text(value))


async def balance(token, database):
    item = await tahesab.balance_for_customer(token, database, CUSTOMER)
    return Decimal(item["money_rial"]), decimal(item["aggregate_gold_weight"]), decimal(item.get("noncoin_gold_grams")), item


async def one(token, database, mode, direction):
    mode_flag = 1 if mode == "abshode" else 0
    source_mode_name = "آبشده" if mode_flag else "متفرقه"
    direction_name = "خروج" if direction == "outgoing" else "ورود"
    expected_no = f"{direction_name} {source_mode_name}"
    before_money, before_gold, before_noncoin, _ = await balance(token, database)
    expected_sign = Decimal(-1) if direction == "outgoing" else Decimal(1)
    expected_gold = before_gold + expected_sign * GRAMS
    expected_noncoin = before_noncoin + expected_sign * GRAMS
    factor = journal.factor_code()
    op_id = str(uuid.uuid4())
    assay_number = factor[-6:]
    lab_name = "Kariz" if mode == "motefareghe" else "آخرت تبريز"
    params = header() + [float(GRAMS), 750, assay_number, lab_name,
                         1 if direction == "outgoing" else 0, mode_flag,
                         "Tahesab console controlled gold delivery verification",
                         factor, 0, -1, 0, False]
    journal.reserve(op_id, f"contract_delivery_gold_v1_{mode}_{direction}",
                    {"customer_code": CUSTOMER, "mode": mode, "direction": direction,
                     "grams": str(GRAMS), "assay_number": assay_number, "reygiri": lab_name,
                     "balance_before": {"money_rial": str(before_money),
                                        "aggregate_gold_grams": str(before_gold),
                                        "noncoin_gold_grams": str(before_noncoin)}}, factor)
    response = await tahesab.write_call(token, database, "gold_receipt", params)
    journal.update(op_id, "submitting", "Awaiting source readback.", response)
    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
    row = next((r for r in docs if str(r.get("Factor_Code")) == factor), None)
    after_money, after_gold, after_noncoin, _ = await balance(token, database)
    checks = {
        "factor_and_customer_document_identity": bool(inquiry and row
            and tahesab.customer_code(row) == CUSTOMER
            and str(row.get("Factor_Code")) == factor
            and row.get("ID") not in (None, "") and row.get("Radif") not in (None, "")),
        "document_type_and_direction": tahesab.normalize((row or {}).get("NO")) == tahesab.normalize(expected_no),
        "gold_mode": ((row or {}).get("IsAbshode") in (True, 1, "1", "true")
                      if mode_flag else (row or {}).get("IsAbshode") in (None, "", False, 0, "0", "false")),
        "weight_and_purity": decimal((row or {}).get("Vazn")) == GRAMS
                             and decimal((row or {}).get("Ayar")) == Decimal(750),
        "final_and_balance_effective": (row or {}).get("isSanadMovaghat") in (False, 0, "0", "false")
                                       and (row or {}).get("TasirBarMande") in (1, "1", True),
        "money_unchanged": after_money == before_money,
        "aggregate_gold_balance": after_gold == expected_gold,
        "noncoin_gold_balance": after_noncoin == expected_noncoin,
    }
    detail = {"checks": checks,
              "balance_before": {"money_rial": str(before_money), "aggregate_gold_grams": str(before_gold),
                                 "noncoin_gold_grams": str(before_noncoin)},
              "balance_after": {"money_rial": str(after_money), "aggregate_gold_grams": str(after_gold),
                                "noncoin_gold_grams": str(after_noncoin)},
              "document": {k: row.get(k) for k in ("ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn",
                                                     "Ayar", "IsAbshode", "TasirBarMande", "isSanadMovaghat")
                           if row and k in row}}
    verified = all(checks.values())
    journal.update(op_id, "verified" if verified else "uncertain",
                   "Gold delivery source readback completed.", {"response": response, **detail})
    return {"status": "verified" if verified else "uncertain", **detail}


async def recover(token, database, operation, mode, direction):
    payload = json.loads(operation["payload"])
    factor = operation.get("factor_code")
    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    rows = await tahesab.documents_for_customer(token, database, CUSTOMER)
    row = next((r for r in rows if str(r.get("Factor_Code")) == str(factor)), None)
    after_money, after_gold, after_noncoin, _ = await balance(token, database)
    before = payload["balance_before"]
    expected_no = f"{'خروج' if direction == 'outgoing' else 'ورود'} {'آبشده' if mode == 'abshode' else 'متفرقه'}"
    sign = Decimal(-1) if direction == "outgoing" else Decimal(1)
    checks = {
        "factor_customer_id_radif": bool(inquiry and row and tahesab.customer_code(row) == CUSTOMER
                                         and str(row.get("Factor_Code")) == str(factor)
                                         and row.get("ID") not in (None, "") and row.get("Radif") not in (None, "")),
        "document_mode_and_direction": tahesab.normalize((row or {}).get("NO")) == tahesab.normalize(expected_no),
        "weight_purity_and_flags": decimal((row or {}).get("Vazn")) == GRAMS
                                    and decimal((row or {}).get("Ayar")) == Decimal(750)
                                    and (row or {}).get("TasirBarMande") in (1, "1", True)
                                    and (row or {}).get("isSanadMovaghat") in (False, 0, "0", "false"),
        "gold_mode_flag": ((row or {}).get("IsAbshode") in (True, 1, "1", "true")
                           if mode == "abshode" else (row or {}).get("IsAbshode") in (None, "", False, 0, "0", "false")),
        "money_unchanged": after_money == Decimal(before["money_rial"]),
        "aggregate_gold_balance": after_gold == Decimal(before["aggregate_gold_grams"]) + sign * GRAMS,
        "noncoin_gold_balance": after_noncoin == Decimal(before["noncoin_gold_grams"]) + sign * GRAMS,
    }
    detail = {"checks": checks, "balance_before": before,
              "balance_after": {"money_rial": str(after_money), "aggregate_gold_grams": str(after_gold),
                                "noncoin_gold_grams": str(after_noncoin)},
              "document": {k: row.get(k) for k in ("ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn",
                                                     "Ayar", "IsAbshode", "TasirBarMande", "isSanadMovaghat")
                           if row and k in row}}
    if all(checks.values()) and operation["status"] != "verified":
        journal.update(operation["operation_id"], "verified", "Gold physical document verified by source readback and balances.", detail)
    return {"verified": all(checks.values()), **detail}


async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    if not token:
        raise RuntimeError("the existing test-server token is unavailable")
    prior = [r for r in journal.list_recent() if r["kind"].startswith("contract_delivery_gold_v1_")]
    results = []
    for mode in ("motefareghe", "abshode"):
        mode_ops = [r for r in prior if r["kind"].startswith(f"contract_delivery_gold_v1_{mode}_")]
        delivery_op = next((r for r in mode_ops if r["kind"].endswith("outgoing")), None)
        receipt_op = next((r for r in mode_ops if r["kind"].endswith("incoming")), None)
        if delivery_op:
            recovered = await recover(token, database, delivery_op, mode, "outgoing")
            delivered = {"status": "verified" if recovered["verified"] else "uncertain", **recovered}
        else:
            delivered = await one(token, database, mode, "outgoing")
        results.append({"mode": mode, "outgoing": delivered})
        if delivered["status"] != "verified":
            break
        if receipt_op:
            recovered = await recover(token, database, receipt_op, mode, "incoming")
            received = {"status": "verified" if recovered["verified"] else "uncertain", **recovered}
        else:
            received = await one(token, database, mode, "incoming")
        results[-1]["incoming_restore"] = received
        if received["status"] != "verified":
            break
    print(json.dumps({"customer": CUSTOMER, "grams": str(GRAMS), "results": results}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
