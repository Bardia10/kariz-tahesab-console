"""Controlled receive/pay pair against the designated Tahesab test customer."""
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jdatetime
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import journal, tahesab

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
EXPECTED_HOST = "https://api-test.tahesab.ir"
TEST_CUSTOMER = 1891
AMOUNT_RIAL = 10


def date_parts():
    day = datetime.now(ZoneInfo("Asia/Tehran")).date()
    jalali = jdatetime.date.fromgregorian(date=day)
    return (jalali.year, jalali.month, jalali.day), jalali.strftime("%Y-%m-%d")


async def submit_and_verify(token, database, bank_name, direction, operation_date):
    header_date, _ = date_parts()
    factor = journal.factor_code()
    op_id = str(uuid.uuid4())
    is_payout = 0 if direction == "receive" else 1
    reference = f"TC{uuid.uuid4().hex[:12]}"
    description = f"Tahesab test console bank contract {direction} {op_id[:8]}"
    payload = {"customer_code": TEST_CUSTOMER, "direction": direction,
               "amount_rial": AMOUNT_RIAL, "bank_name": bank_name,
               "reference": reference, "date": operation_date}
    journal.reserve(op_id, f"probe_bank_{direction}", payload, factor)
    params = [1, TEST_CUSTOMER, -1, 1, *header_date,
              is_payout, AMOUNT_RIAL, description, bank_name, reference,
              factor, -1, -1, "IRR", operation_date]
    result = await tahesab.write_call(token, database, "bank", params)
    journal.update(op_id, "submitting", "Awaiting Tahesab source readback.", result)
    return await verify_saved(token, database, bank_name, direction, reference, factor, result, op_id)


async def verify_saved(token, database, bank_name, direction, reference, factor, response, op_id):
    inquiry = await tahesab.inquire_factor(token, database, factor)
    docs = await tahesab.documents_for_customer(token, database, TEST_CUSTOMER)
    row = next((r for r in docs if str(r.get("Factor_Code")) == factor), None)
    inquiry_rows = tahesab.records(inquiry)
    if not inquiry_rows or not row:
        journal.update(op_id, "uncertain", "Write response received; factor inquiry/customer history did not prove the document.", {"response": response})
        return {"direction": direction, "status": "uncertain", "factor_present_in_inquiry": bool(inquiry_rows), "document_present": bool(row)}
    checks = {
        "customer": tahesab.customer_code(row) == TEST_CUSTOMER,
        "identity": row.get("ID") not in (None, "") and row.get("Radif") not in (None, ""),
        "factor": str(row.get("Factor_Code")) == factor,
        "amount": abs(tahesab.Decimal(tahesab._decimal_text(row.get("Mali")))) == AMOUNT_RIAL,
        "bank": tahesab.normalize(row.get("Name_Bank")) == tahesab.normalize(bank_name),
        "reference": tahesab.normalize(row.get("Sh_Cheq") or row.get("Code_Rahgiri") or "") == tahesab.normalize(reference),
        "direction": ("به بانک" in tahesab.normalize(row.get("NO")) and "از مشتری" in tahesab.normalize(row.get("NO"))) if direction == "receive" else ("به مشتری" in tahesab.normalize(row.get("NO")) and "از بانک" in tahesab.normalize(row.get("NO"))),
    }
    verified = all(checks.values())
    journal.update(op_id, "verified" if verified else "uncertain",
                   "Bank source readback checks completed.", {"response": response, "document": row, "checks": checks})
    return {"direction": direction, "status": "verified" if verified else "uncertain",
            "checks": checks, "document": {k: row.get(k) for k in (
                "ID", "Radif", "MCode", "Factor_Code", "NO", "Mali", "Name_Bank",
                "Sh_Cheq", "Operation_Date", "Tarikh", "TasirBarMande", "isSanadMovaghat") if k in row}}


async def main():
    config = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = config.get("tahesab_token") or ""
    host = str(config.get("tahesab_base_url") or EXPECTED_HOST).rstrip("/")
    database = config.get("tahesab_dbname") or config.get("tahesab_database") or "TahesabDB"
    if not token or host != EXPECTED_HOST:
        print(json.dumps({"result": "not_run", "reason": "configured connection is not the expected test host"}))
        return
    banks = tahesab.records(await tahesab._call(token, database, "bank_accounts", []))
    if not banks or not banks[0].get("Name_Bank"):
        print(json.dumps({"result": "not_run", "reason": "source bank account list has no usable account"}))
        return
    _, operation_date = date_parts()
    bank_name = str(banks[0]["Name_Bank"])
    pair = []
    existing = {row["kind"]: row for row in journal.list_recent() if row["kind"] in {"probe_bank_receive", "probe_bank_payout"}}
    for direction in ("receive", "payout"):
        kind = f"probe_bank_{direction}"
        if kind in existing:
            saved = existing[kind]
            payload = json.loads(saved["payload"])
            item = await verify_saved(token, database, bank_name, direction,
                                      payload.get("reference", ""), saved.get("factor_code"),
                                      json.loads(saved["source_response"]) if saved.get("source_response") else None,
                                      saved["operation_id"])
        else:
            item = await submit_and_verify(token, database, bank_name, direction, operation_date)
        pair.append(item)
        if item["status"] != "verified":
            break
    print(json.dumps({"test_customer": TEST_CUSTOMER, "test_bank": True,
                      "amount_rial_per_document": AMOUNT_RIAL, "pair": pair}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
