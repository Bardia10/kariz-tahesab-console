"""One-time controlled gold-trade verification against the Tahesab test server.

The script writes a 0.001 g آبشده trade for the dedicated test customer in one
direction, verifies the source document and balances, then writes the reverse
trade only after the first record is fully verified. It never resends a factor.
"""
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
MESGHAL_TO_GRAM = Decimal("4.3318")
MAZANEH_TOMAN_PER_MESGHAL = Decimal("10000000")


def header():
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    day = jdatetime.date.fromgregorian(date=today)
    return [1, CUSTOMER, -1, 1, day.year, day.month, day.day]


def number(value):
    if value in (None, "", "null"):
        return None
    return Decimal(tahesab._decimal_text(value))


async def source_balance(token, database):
    rows = tahesab.records(await tahesab._call(token, database, "balance", [str(CUSTOMER)]))
    item = next((r for r in rows if str(r.get("Code")) == str(CUSTOMER)), None)
    if not item or item.get("MandeyeMali") in (None, "") or item.get("MandeyeVazni") in (None, ""):
        raise RuntimeError("required money or aggregate-gold balance is unavailable")
    misc = item.get("MandeMenhayeSeke")
    return number(item["MandeyeMali"]), number(item["MandeyeVazni"]), number(misc), item


async def source_labs(token, database):
    headers = {"Authorization": f"Bearer {token}", "DBname": database,
               "Content-Type": "application/json"}
    async with httpx.AsyncClient(base_url=tahesab.BASE_URL, headers=headers, timeout=20) as client:
        response = await client.post("/DoListNameReygiri", json={"DoListNameReygiri": []})
    if response.status_code >= 400:
        raise RuntimeError("Tahesab did not return its Reygiri reference list")
    rows = tahesab.records(response.json())
    names = [str(row.get("Name", "")).strip() for row in rows if row.get("Name")]
    if not names:
        raise RuntimeError("Tahesab returned no usable Reygiri names")
    return names


async def verify_one(token, database, direction, lab_name, mode="abshode", version="v2"):
    factor = journal.factor_code()
    operation_id = str(uuid.uuid4())
    rate_rial_per_mesghal = int(MAZANEH_TOMAN_PER_MESGHAL * 10)
    total_rial = int((GRAMS * rate_rial_per_mesghal / MESGHAL_TO_GRAM).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))
    buy_or_sale = 1 if direction == "customer_buys" else 0
    amount_signed = -total_rial if direction == "customer_buys" else total_rial
    before_money, before_gold, before_misc, _ = await source_balance(token, database)
    expected_money = before_money + amount_signed
    expected_gold = before_gold + (GRAMS if direction == "customer_buys" else -GRAMS)
    assay_number = factor[-6:]

    # Positional fields follow Kariz's existing DoNewSanadBuySaleGOLD builder:
    # weight, purity, assay number, source Reygiri name, direction, rate,
    # rate-unit flag (0=per mesghal), gold mode flag (1=آبشده), total, text,
    # factor, transfer target, currency, metal, settlement date.
    mode_flag = 1 if mode == "abshode" else 0
    params = header() + [float(GRAMS), 750, assay_number, lab_name, buy_or_sale,
                         rate_rial_per_mesghal, 0, mode_flag, total_rial,
                         "Tahesab console controlled آبشده trade verification",
                         factor, 0, -1, 0, ""]
    journal.reserve(operation_id, f"contract_trade_gold_{version}_{mode}_{direction}",
                    {"customer_code": CUSTOMER, "grams": str(GRAMS), "purity": 750,
                     "mode": mode, "rate_unit": "rial_per_mesghal",
                     "factor_gram_per_mesghal": str(MESGHAL_TO_GRAM),
                     "mazaneh_toman_per_mesghal": str(MAZANEH_TOMAN_PER_MESGHAL),
                     "mazaneh_rial_per_mesghal": rate_rial_per_mesghal,
                     "total_rial": total_rial, "direction": direction,
                     "reygiri": lab_name, "assay_number": assay_number,
                     "balance_before": {"money_rial": str(before_money),
                                        "aggregate_gold_grams": str(before_gold),
                                        "noncoin_gold_grams": str(before_misc)},
                     "total_rial": total_rial,
                     "mazaneh_rial_per_mesghal": rate_rial_per_mesghal,
                     "reygiri": lab_name}, factor)
    response = await tahesab.write_call(token, database, "trade_gold", params)
    journal.update(operation_id, "submitting", "Awaiting source readback.", response)

    inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
    docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
    row = next((r for r in docs if str(r.get("Factor_Code")) == factor), None)
    after_money, after_gold, after_misc, _ = await source_balance(token, database)
    category, actual_direction = classify_document(row or {})
    checks = {
        "factor_inquiry": bool(inquiry),
        "customer_document_identity": bool(row and tahesab.customer_code(row) == CUSTOMER
                                            and row.get("ID") not in (None, "")
                                            and row.get("Radif") not in (None, "")),
        "factor_code": str((row or {}).get("Factor_Code")) == factor,
        "document_type_and_direction": category == "trade" and actual_direction == direction,
        "gold_mode": (row or {}).get("IsAbshode") in ((True, 1, "1", "true") if mode_flag else (False, 0, "0", "false")),
        "weight_and_purity": number((row or {}).get("Vazn")) == GRAMS
                             and number((row or {}).get("Ayar")) == Decimal(750),
        "rate_and_total": number((row or {}).get("Mazaneh")) == Decimal(rate_rial_per_mesghal)
                          and number((row or {}).get("Mali")) == Decimal(amount_signed),
        "final_and_balance_effective": (row or {}).get("isSanadMovaghat") in (False, 0, "0", "false")
                                       and (row or {}).get("TasirBarMande") in (1, "1", True),
        "money_balance": after_money == expected_money,
        "aggregate_gold_balance": after_gold == expected_gold,
        "noncoin_gold_balance": after_misc == before_misc + (GRAMS if direction == "customer_buys" else -GRAMS),
    }
    detail = {"checks": checks, "total_rial": total_rial,
              "balance_before": {"money_rial": str(before_money), "aggregate_gold_grams": str(before_gold),
                                 "noncoin_gold_grams": str(before_misc)},
              "balance_after": {"money_rial": str(after_money), "aggregate_gold_grams": str(after_gold),
                                "noncoin_gold_grams": str(after_misc)},
              "document": {k: row.get(k) for k in (
                  "ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn", "Ayar", "Mali",
                  "Mazaneh", "MazanehType", "IsAbshode", "Ang_Number", "Name_az",
                  "TasirBarMande", "isSanadMovaghat") if row and k in row}}
    verified = all(checks.values())
    journal.update(operation_id, "verified" if verified else "uncertain",
                   "Gold trade source readback completed.", {"response": response, **detail})
    return {"status": "verified" if verified else "uncertain", **detail}


async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    if not token:
        raise RuntimeError("the existing test-server token is unavailable")
    prior = [r for r in journal.list_recent() if r["kind"].startswith("contract_trade_gold_v2_abshode_")]
    abshode_result = {}
    if prior:
        # Recover the first attempt by factor inquiry and exact source readback;
        # never resend its factor. Proceed to the reverse leg only if verified.
        buy = next((r for r in prior if r["kind"].endswith("customer_buys")), None)
        sell = next((r for r in prior if r["kind"].endswith("customer_sells")), None)
        if not buy:
            print(json.dumps({"status": "not_run", "reason": "prior probe exists without a customer-buys factor; inspect journal"}))
            return
        payload = json.loads(buy["payload"])
        factor = buy.get("factor_code")
        inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
        docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
        row = next((r for r in docs if str(r.get("Factor_Code")) == str(factor)), None)
        after_money, after_gold, after_noncoin, _ = await source_balance(token, database)
        before = payload["balance_before"]
        before_noncoin = before.get("noncoin_gold_grams", before.get("misc_gold_grams"))
        amount = int(payload["total_rial"])
        checks = {
            "factor_inquiry": bool(inquiry),
            "customer_document_identity": bool(row and tahesab.customer_code(row) == CUSTOMER
                                                and row.get("ID") not in (None, "")
                                                and row.get("Radif") not in (None, "")),
            "factor_code": str((row or {}).get("Factor_Code")) == str(factor),
            "document_type_and_direction": classify_document(row or {}) == ("trade", "customer_buys"),
            "abshode_mode": (row or {}).get("IsAbshode") in (True, 1, "1", "true"),
            "weight_and_purity": number((row or {}).get("Vazn")) == GRAMS
                                 and number((row or {}).get("Ayar")) == Decimal(750),
            "rate_and_total": number((row or {}).get("Mazaneh")) == Decimal(payload["mazaneh_rial_per_mesghal"])
                              and number((row or {}).get("Mali")) == Decimal(-amount),
            "final_and_balance_effective": (row or {}).get("isSanadMovaghat") in (False, 0, "0", "false")
                                           and (row or {}).get("TasirBarMande") in (1, "1", True),
            "money_balance": after_money == Decimal(before["money_rial"]) - amount,
            "aggregate_gold_balance": after_gold == Decimal(before["aggregate_gold_grams"]) + GRAMS,
            "noncoin_gold_balance": after_noncoin == Decimal(before_noncoin) + GRAMS,
        }
        detail = {"checks": checks,
                  "balance_before": {**before, "noncoin_gold_grams": str(before_noncoin)},
                  "balance_after": {"money_rial": str(after_money), "aggregate_gold_grams": str(after_gold),
                                    "noncoin_gold_grams": str(after_noncoin)},
                  "document": {k: row.get(k) for k in (
                      "ID", "Radif", "MCode", "Factor_Code", "NO", "Vazn", "Ayar", "Mali",
                      "Mazaneh", "MazanehType", "IsAbshode", "Ang_Number", "Name_az",
                      "TasirBarMande", "isSanadMovaghat") if row and k in row}}
        verified = all(checks.values())
        if verified and buy["status"] != "verified":
            journal.update(buy["operation_id"], "verified", "Gold trade verified by factor inquiry, document readback, and source balances.", detail)
        if not verified:
            if buy["status"] != "uncertain":
                journal.update(buy["operation_id"], "uncertain", "Gold trade readback remains incomplete.", detail)
            print(json.dumps({"status": "uncertain", "direction": "customer_buys", **detail}, ensure_ascii=False, default=str))
            return
        if sell:
            if sell["status"] != "verified":
                print(json.dumps({"status": sell["status"], "reason": "reverse factor already journaled; inspect it without resending"}))
                return
            reverse = {"status": "already_verified", "operation_id": sell["operation_id"]}
        else:
            reverse = await verify_one(token, database, "customer_sells", str(payload["reygiri"]))
            if reverse["status"] != "verified":
                print(json.dumps({"status": "uncertain", "recovered_first_trade": detail, "reverse_trade": reverse}, ensure_ascii=False, default=str))
                return
        abshode_result = {"recovered_first_trade": detail, "reverse_trade": reverse}

    misc_prior = [r for r in journal.list_recent() if r["kind"].startswith("contract_trade_gold_v3_motefareghe_")]
    if misc_prior:
        buy = next((r for r in misc_prior if r["kind"].endswith("customer_buys")), None)
        sell = next((r for r in misc_prior if r["kind"].endswith("customer_sells")), None)
        if not buy:
            print(json.dumps({"status": "not_run", "reason": "miscellaneous-gold probe exists without a first factor; inspect journal"}))
            return
        payload = json.loads(buy["payload"])
        factor = buy.get("factor_code")
        inquiry = tahesab.records(await tahesab.inquire_factor(token, database, factor))
        docs = await tahesab.documents_for_customer(token, database, CUSTOMER)
        row = next((r for r in docs if str(r.get("Factor_Code")) == str(factor)), None)
        after_money, after_gold, after_noncoin, _ = await source_balance(token, database)
        before = payload["balance_before"]
        amount = int(payload["total_rial"])
        checks = {
            "factor_and_document": bool(inquiry and row and tahesab.customer_code(row) == CUSTOMER
                                         and str(row.get("Factor_Code")) == str(factor)
                                         and row.get("ID") not in (None, "") and row.get("Radif") not in (None, "")),
            "direction_weight_amount": classify_document(row or {}) == ("trade", "customer_buys")
                                       and number((row or {}).get("Vazn")) == GRAMS
                                       and number((row or {}).get("Mali")) == Decimal(-amount),
            "source_mode_is_ambiguous": (row or {}).get("IsAbshode") in (None, ""),
            "money_balance": after_money == Decimal(before["money_rial"]) - amount,
            "aggregate_gold_balance": after_gold == Decimal(before["aggregate_gold_grams"]) + GRAMS,
            "noncoin_gold_balance": after_noncoin == Decimal(before["noncoin_gold_grams"]) + GRAMS,
        }
        if not all(checks.values()):
            print(json.dumps({"status": "uncertain", "checks": checks, **abshode_result}, ensure_ascii=False, default=str))
            return
        if not sell:
            reverse = await verify_one(token, database, "customer_sells", str(payload["reygiri"]),
                                       mode="motefareghe", version="v3")
        else:
            reverse = {"status": sell["status"], "operation_id": sell["operation_id"]}
        print(json.dumps({"status": "first_financial_effect_verified_but_mode_ambiguous",
                          "first_trade_checks": checks, "reverse_trade": reverse,
                          "abshode": abshode_result}, ensure_ascii=False, default=str))
        return
    labs = await source_labs(token, database)
    misc_results = [await verify_one(token, database, "customer_buys", labs[0], mode="motefareghe", version="v3")]
    if misc_results[0]["status"] == "verified":
        misc_results.append(await verify_one(token, database, "customer_sells", labs[0], mode="motefareghe", version="v3"))
    print(json.dumps({"customer": CUSTOMER, "grams": str(GRAMS), "abshode": abshode_result,
                      "miscellaneous_gold_trade_checks": misc_results}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
