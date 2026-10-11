"""Verify per-gram pricing for آبشده trades and restore test balances."""
import asyncio
import json
import sys
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import jdatetime
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import journal, tahesab
from backend.main import classify_document

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"
CUSTOMER = 1891
GRAMS = Decimal("0.001")
PRICE_TOMAN_PER_GRAM = Decimal("1000000")
PRICE_RIAL_PER_GRAM = int(PRICE_TOMAN_PER_GRAM * 10)
FACTOR = Decimal("4.3318")


def header():
    day = jdatetime.date.fromgregorian(date=datetime.now(ZoneInfo("Asia/Tehran")).date())
    return [1, CUSTOMER, -1, 1, day.year, day.month, day.day]


def decimal(value):
    return None if value in (None, "", "null") else Decimal(tahesab._decimal_text(value))


async def balance(token, db):
    item = await tahesab.balance_for_customer(token, db, CUSTOMER)
    return Decimal(item["money_rial"]), decimal(item["aggregate_gold_weight"]), decimal(item["noncoin_gold_grams"])


async def labs(token, db):
    h = {"Authorization": f"Bearer {token}", "DBname": db, "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url=tahesab.BASE_URL, headers=h, timeout=20) as c:
        r = await c.post("/DoListNameReygiri", json={"DoListNameReygiri": []})
    r.raise_for_status()
    rows = tahesab.records(r.json())
    return next(str(x["Name"]) for x in rows if x.get("Name"))


async def one(token, db, direction, lab):
    factor = journal.factor_code()
    op_id = str(uuid.uuid4())
    total = int((GRAMS * PRICE_RIAL_PER_GRAM).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    signed = -total if direction == "customer_buys" else total
    before_money, before_gold, before_noncoin = await balance(token, db)
    gold_sign = Decimal(1) if direction == "customer_buys" else Decimal(-1)
    assay_number = factor[-6:]
    buy_or_sale = 1 if direction == "customer_buys" else 0
    params = header() + [float(GRAMS), 750, assay_number, lab, buy_or_sale,
                         PRICE_RIAL_PER_GRAM, 1, 1, total,
                         "Tahesab console controlled per-gram آبشده trade verification",
                         factor, 0, -1, 0, ""]
    journal.reserve(op_id, f"contract_trade_gold_gram_{direction}",
                    {"customer_code": CUSTOMER, "grams": str(GRAMS), "direction": direction,
                     "mode": "abshode", "price_basis": "gram", "price_toman_per_gram": str(PRICE_TOMAN_PER_GRAM),
                     "balance_before": {"money_rial": str(before_money), "aggregate_gold_grams": str(before_gold),
                                        "noncoin_gold_grams": str(before_noncoin)}}, factor)
    response = await tahesab.write_call(token, db, "trade_gold", params)
    journal.update(op_id, "submitting", "Awaiting source readback.", response)
    inquiry = tahesab.records(await tahesab.inquire_factor(token, db, factor))
    docs = await tahesab.documents_for_customer(token, db, CUSTOMER)
    row = next((r for r in docs if str(r.get("Factor_Code")) == factor), None)
    after_money, after_gold, after_noncoin = await balance(token, db)
    category, actual_direction = classify_document(row or {})
    checks = {
        "factor_customer_id_radif": bool(inquiry and row and tahesab.customer_code(row) == CUSTOMER
            and str(row.get("Factor_Code")) == factor and row.get("ID") not in (None, "") and row.get("Radif") not in (None, "")),
        "direction_and_mode": category == "trade" and actual_direction == direction
                            and (row or {}).get("IsAbshode") in (True, 1, "1", "true"),
        "weight_purity_and_price_unit": decimal((row or {}).get("Vazn")) == GRAMS
                                       and decimal((row or {}).get("Ayar")) == Decimal(750)
                                       and tahesab.normalize((row or {}).get("MazanehType")) == "gram",
        "price_total_and_flags": decimal((row or {}).get("Mazaneh")) == Decimal(PRICE_RIAL_PER_GRAM)
                                 and decimal((row or {}).get("Mali")) == Decimal(signed)
                                 and (row or {}).get("TasirBarMande") in (1, "1", True)
                                 and (row or {}).get("isSanadMovaghat") in (False, 0, "0", "false"),
        "balances": after_money == before_money + signed
                    and after_gold == before_gold + gold_sign * GRAMS
                    and after_noncoin == before_noncoin + gold_sign * GRAMS,
    }
    detail = {"checks": checks, "balance_before": {"money_rial": str(before_money), "aggregate_gold_grams": str(before_gold), "noncoin_gold_grams": str(before_noncoin)},
              "balance_after": {"money_rial": str(after_money), "aggregate_gold_grams": str(after_gold), "noncoin_gold_grams": str(after_noncoin)},
              "document": {k: row.get(k) for k in ("ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn", "Ayar", "Mali", "Mazaneh", "MazanehType", "IsAbshode", "TasirBarMande", "isSanadMovaghat") if row and k in row}}
    verified = all(checks.values())
    journal.update(op_id, "verified" if verified else "uncertain", "Per-gram gold trade readback completed.", {"response": response, **detail})
    return {"status": "verified" if verified else "uncertain", **detail}


async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    db = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    if not token:
        raise RuntimeError("the existing test-server token is unavailable")
    prior = [r for r in journal.list_recent() if r["kind"].startswith("contract_trade_gold_gram_")]
    if prior:
        buy = next((r for r in prior if r["kind"].endswith("customer_buys")), None)
        sell = next((r for r in prior if r["kind"].endswith("customer_sells")), None)
        if not buy:
            print(json.dumps({"status": "not_run", "reason": "per-gram factor exists without first direction; inspect journal"}))
            return
        payload = json.loads(buy["payload"])
        factor = buy["factor_code"]
        inquiry = tahesab.records(await tahesab.inquire_factor(token, db, factor))
        docs = await tahesab.documents_for_customer(token, db, CUSTOMER)
        row = next((r for r in docs if str(r.get("Factor_Code")) == str(factor)), None)
        after_money, after_gold, after_noncoin = await balance(token, db)
        before = payload["balance_before"]
        amount = int(Decimal(GRAMS * PRICE_RIAL_PER_GRAM))
        checks = {
            "factor_and_customer_document": bool(inquiry and row and tahesab.customer_code(row) == CUSTOMER
                and str(row.get("Factor_Code")) == str(factor) and row.get("ID") not in (None, "") and row.get("Radif") not in (None, "")),
            "direction_weight_price_basis_and_mode": classify_document(row or {}) == ("trade", "customer_buys")
                and decimal((row or {}).get("Vazn")) == GRAMS
                and decimal((row or {}).get("Mazaneh")) == Decimal(PRICE_RIAL_PER_GRAM)
                and tahesab.normalize((row or {}).get("MazanehType")) == "gram"
                and (row or {}).get("IsAbshode") in (True, 1, "1", "true"),
            "money_total_and_gold_balances": decimal((row or {}).get("Mali")) == Decimal(-amount)
                and after_money == Decimal(before["money_rial"]) - amount
                and after_gold == Decimal(before["aggregate_gold_grams"]) + GRAMS
                and after_noncoin == Decimal(before["noncoin_gold_grams"]) + GRAMS,
        }
        detail = {"checks": checks, "document": {k: row.get(k) for k in ("ID", "Radif", "Factor_Code", "NO", "Vazn", "Ayar", "Mali", "Mazaneh", "MazanehType", "IsAbshode") if row and k in row},
                  "balance_before": before, "balance_after": {"money_rial": str(after_money), "aggregate_gold_grams": str(after_gold), "noncoin_gold_grams": str(after_noncoin)}}
        if not all(checks.values()):
            print(json.dumps({"status": "uncertain", **detail}, ensure_ascii=False, default=str))
            return
        if buy["status"] != "verified":
            journal.update(buy["operation_id"], "verified", "Per-gram trade verified by saved factor, source document, and balances.", detail)
        if sell:
            print(json.dumps({"status": sell["status"], "reason": "reverse factor exists; inspect it instead of resubmitting", **detail}, ensure_ascii=False, default=str))
            return
        reverse = await one(token, db, "customer_sells", await labs(token, db))
        print(json.dumps({"status": "recovered_first_and_submitted_reverse", "first": detail, "reverse": reverse}, ensure_ascii=False, default=str))
        return
    lab = await labs(token, db)
    results = []
    for direction in ("customer_buys", "customer_sells"):
        result = await one(token, db, direction, lab)
        results.append(result)
        if result["status"] != "verified":
            break
    print(json.dumps({"customer": CUSTOMER, "grams": str(GRAMS), "price_toman_per_gram": str(PRICE_TOMAN_PER_GRAM),
                      "reygiri": lab, "results": results}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
