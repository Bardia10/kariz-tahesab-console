"""Read back the first coin receipt; never resend it; then test the inverse delivery."""
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


def header():
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    day = jdatetime.date.fromgregorian(date=today)
    return [1, CUSTOMER, -1, 1, day.year, day.month, day.day]


async def read_state(token, database, key):
    rows = tahesab.records(await tahesab._call(token, database, "balance", [str(CUSTOMER)]))
    item = next((row for row in rows if str(row.get("Code")) == str(CUSTOMER)), {})
    value = item.get(key)
    return Decimal(tahesab._decimal_text(value)) if value not in (None, "", "null") else Decimal(0)


async def inspect(token, database, factor, source_name, expected_direction, expected_balance, expected_weight):
    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
    row = next((item for item in docs if str(item.get("Factor_Code")) == factor), None)
    balance = await read_state(token, database, f"Seke_{source_name}")
    if not inquiry or not row:
        return None, {"inquiry": bool(inquiry), "document": bool(row), "balance": str(balance)}
    kind, direction = classify_document(row)
    checks = {
        "customer": tahesab.customer_code(row) == CUSTOMER,
        "id_and_radif": row.get("ID") not in (None, "") and row.get("Radif") not in (None, ""),
        "factor": str(row.get("Factor_Code")) == factor,
        "delivery_kind": kind == "delivery",
        "direction": direction == expected_direction,
        "count": tahesab.normalize(row.get("Count")) == "1",
        "weight": Decimal(tahesab._decimal_text(row.get("Vazn"))) == Decimal(str(expected_weight)),
        "source_balance": balance == expected_balance,
        "balance_effective": row.get("TasirBarMande") in (1, "1", True),
        "not_temporary": row.get("isSanadMovaghat") in (False, 0, "0", "false"),
    }
    result = {"checks": checks, "balance": str(balance), "document": {k: row.get(k) for k in (
        "ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn", "Ayar", "Count",
        "Name_az", "TasirBarMande", "isSanadMovaghat") if k in row}}
    return (row if all(checks.values()) else None), result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=("coin", "bar"), default="coin")
    family = parser.parse_args().family
    config = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = config.get("tahesab_token") or ""
    database = config.get("tahesab_dbname") or config.get("tahesab_database") or "TahesabDB"
    mapping = next(p for p in json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))["products"] if p["source_family"] == family)
    source_name = mapping["source_identifier"]
    key = f"Seke_{source_name}"
    prior_kind = f"contract_physical_{family}_incoming"
    outgoing_kind = f"contract_physical_{family}_outgoing"
    existing_outgoing = next((row for row in journal.list_recent() if row["kind"] == outgoing_kind), None)
    if existing_outgoing:
        print(json.dumps({"family": family, "outgoing": "not_run",
                          "reason": "an outgoing contract probe is already journaled; inquire/recover it instead of resending",
                          "existing_status": existing_outgoing["status"]}, ensure_ascii=True))
        return
    prior = next((row for row in journal.list_recent() if row["kind"] == prior_kind), None)
    if prior:
        factor = prior["factor_code"]
        existing, existing_result = await inspect(token, database, factor, source_name, "incoming", Decimal("1"), mapping["source_weight"])
        if not existing:
            journal.update(prior["operation_id"], "uncertain", "Existing receipt readback remains incomplete; no write was resent.", existing_result)
            print(json.dumps({"recovered_receipt": "uncertain", "details": existing_result}, ensure_ascii=True, default=str))
            return
        journal.update(prior["operation_id"], "verified", "Incoming physical product document and exact source-balance effect verified by factor, customer, ID/Radif, count, weight, and balance.", existing_result)
    else:
        baseline = await read_state(token, database, key)
        if baseline != Decimal("0"):
            print(json.dumps({"incoming": "not_run", "reason": "test balance is not zero; refusing to change the starting quantity"}, ensure_ascii=True))
            return
        factor = journal.factor_code()
        operation_id = str(uuid.uuid4())
        params = header() + [mapping["source_weight"], mapping["purity"], 1, source_name,
                             0, f"Tahesab console test {family} receipt", factor,
                             -1, -1, int(mapping["metal"])]
        journal.reserve(operation_id, prior_kind,
                        {"customer_code": CUSTOMER, "source_identifier": source_name,
                         "count": 1, "weight": mapping["source_weight"],
                         "balance_before": str(baseline), "params": params}, factor)
        response = await tahesab.write_call(token, database, "physical_coin" if family == "coin" else "physical_bar", params)
        journal.update(operation_id, "submitting", "Awaiting source readback.", response)
        existing, existing_result = await inspect(token, database, factor, source_name, "incoming", Decimal("1"), mapping["source_weight"])
        if not existing:
            journal.update(operation_id, "uncertain", "Incoming physical document readback incomplete; it was not resent.", {"response": response, **existing_result})
            print(json.dumps({"incoming": "uncertain", "details": existing_result}, ensure_ascii=True, default=str))
            return
        journal.update(operation_id, "verified", "Incoming physical product document and exact source-balance effect verified.", {"response": response, **existing_result})

    operation_id = str(uuid.uuid4())
    factor = journal.factor_code()
    params = header() + [mapping["source_weight"], mapping["purity"], 1, source_name,
                         1, f"Tahesab console test {family} inverse delivery", factor,
                         -1, -1, int(mapping["metal"])]
    before = await read_state(token, database, key)
    if before != Decimal("1"):
        print(json.dumps({"recovered_receipt": "verified", "inverse_delivery": "not_run",
                          "reason": "coin source balance is not exactly one before the inverse"}, ensure_ascii=True))
        return
    journal.reserve(operation_id, outgoing_kind,
                    {"customer_code": CUSTOMER, "source_identifier": source_name,
                     "count": 1, "weight": mapping["source_weight"],
                     "balance_before": str(before)}, factor)
    response = await tahesab.write_call(token, database, "physical_coin" if family == "coin" else "physical_bar", params)
    journal.update(operation_id, "submitting", "Awaiting source readback.", response)
    verified, result = await inspect(token, database, factor, source_name, "outgoing", Decimal("0"), mapping["source_weight"])
    status = "verified" if verified else "uncertain"
    journal.update(operation_id, status, "Inverse coin delivery source readback completed.", {"response": response, **result})
    print(json.dumps({"customer": CUSTOMER, "family": family, "source_identifier": source_name,
                      "recovered_receipt": "verified", "inverse_delivery": status,
                      "receipt": existing_result, "delivery": result}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
