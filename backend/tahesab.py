"""Tahesab test-server adapter: paths, positional bodies, and source parsing live here."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api-test.tahesab.ir"

# Keep exact path and JSON body-key spelling centralized.
OPERATIONS = {
    "verify_database": ("/DoListNameARZ", "DoListNameARZ"),
    "customer_range": ("/DoListMoshtari-ByCode", "Dolistmoshtari"),
    "customer_search": ("/DoListMoshtari", "DoListMoshtari"),
    "customer_phone": ("/DoListMoshtari-ByPhone", "DoListMoshtari"),
    "balance": ("/GetMandeHesabByCode", "getmandehesabbycode"),
    "documents": ("/DoListAsnad", "DoListAsnad"),
    "groups": ("/DoListGorooh", "DoListGorooh"),
    "currencies": ("/DoListNameARZ", "DoListNameARZ"),
    "coins": ("/DoListNameSekeh", "DoListNameSekeh"),
    "bars": ("/DoListNameShemsh", "DoListNameShemsh"),
    "handmade": ("/DoListNameKarsakhte", "DoListNameKarsakhte"),
    "reygiri": ("/DoListNameReygiri", "DoListNameReygiri"),
    "bank_accounts": ("/DoListHesabBanki", "DoListHesabBanki"),
}

WRITE_OPERATIONS = {
    "customer": ("/DoNewMoshtari", "DoNewMoshtari"),
    "customer_edit": ("/DoEditMoshtari", "DoEditMoshtari"),
    "cash": ("/DoNewSanadVKHVaghNaghd", "DoNewSanadVKHVaghNaghd"),
    "gold_receipt": ("/DoNewSanadVKHGOLD", "DoNewSanadVKHGOLD"),
    "physical_coin": ("/DoNewSanadVKHSEKEH", "DoNewSanadVKHSEKEH"),
    "physical_bar": ("/DoNewSanadVKHShemsh", "DoNewSanadVKHShemsh"),
    "trade_gold": ("/DoNewSanadBuySaleGOLD", "DoNewSanadBuySaleGOLD"),
    "trade_coin": ("/DoNewSanadBuySaleSEKEH", "DoNewSanadBuySaleSEKEH"),
    "trade_bar": ("/DoNewSanadBuySaleShemsh", "DoNewSanadBuySaleShemsh"),
    "bank": ("/DoNewSanadVKHBank", "DoNewSanadVKHBank"),
}


def edit_customer_params(name: str, group_name: str, mobile: str, address: str,
                         national_id: str, customer_code_value: int,
                         existing: dict[str, Any]) -> list[Any]:
    """Build DoEditMoshtari's documented 9-slot tuple.

    Tahesab returns BDate but omits Moaref/Code_Moaref from customer readback.
    Its API example uses "-1" for both referral arguments when they are not supplied.
    Preserve those values if a source version does return them; otherwise use that
    documented placeholder. City and Rasteh are readback fields, not edit arguments.
    """
    birthdate = _first_present(existing, ("BDate",))
    if birthdate is _MISSING:
        raise TahesabFailure("customer_edit_birthdate_unavailable")
    referrer = _first_present(existing, ("Moaref",))
    referrer_code = _first_present(existing, ("Code_Moaref",))
    if referrer is _MISSING:
        referrer = "-1"
    if referrer_code is _MISSING:
        referrer_code = "-1"
    return [name, group_name, mobile, address, national_id,
            birthdate, referrer, referrer_code, int(customer_code_value)]


_MISSING = object()


def _first_present(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return _MISSING


class TahesabFailure(Exception):
    pass


class TahesabWriteUncertain(Exception):
    """The write may have reached Tahesab; callers must verify, never resend."""
    pass


def normalize(value: Any) -> str:
    digit_map = {chr(0x0660 + i): str(i) for i in range(10)}
    digit_map.update({chr(0x06F0 + i): str(i) for i in range(10)})
    digit_map.update({"\u064a": "\u06cc", "\u0643": "\u06a9"})
    text = str(value or "").translate(str.maketrans(digit_map))
    text = re.sub(r"[\u064b-\u065f\u0670\u200c\u200e\u200f]", "", text)
    return " ".join(text.split()).casefold()


def _no_records(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    message = normalize(payload.get("ERROR")).rstrip(" .")
    return message in {"هیچ رکوردی یافت نشد", "رکوردی یافت نشد"}


def _check_business_error(payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    for key in ("ERROR", "error"):
        value = payload.get(key)
        if value not in (None, "", False, 0):
            if _no_records(payload):
                return
            raise TahesabFailure("upstream_business_error")
    status = payload.get("Status", payload.get("status"))
    if status not in (None, "") and normalize(status) not in {"ok", "success", "200", "true"}:
        raise TahesabFailure("upstream_business_error")


def records(payload: Any) -> list[dict[str, Any]]:
    """Unwrap arrays, common value envelopes, and keyed document objects."""
    if payload in ({}, None, []) or _no_records(payload):
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("value", "Value", "data", "Data", "Moshakhasat", "MandeHesab", "Asnad", "Customers"):
        if key in payload:
            return records(payload[key])
    if payload and all(isinstance(value, dict) for value in payload.values()):
        rows = []
        for outer_key, value in payload.items():
            row = dict(value)
            row.setdefault("_source_key", str(outer_key))
            rows.append(row)
        return rows
    return [payload]


def customer_code(row: dict[str, Any]) -> int | None:
    for key in ("Code", "MCode", "code", "MoshtariCode", "CustomerCode"):
        value = row.get(key)
        if value not in (None, ""):
            try:
                return int(normalize(str(value)))
            except ValueError:
                return None
    if row.get("_source_key") not in (None, ""):
        try:
            return int(normalize(str(row["_source_key"])))
        except ValueError:
            return None
    return None


def _decimal_text(value: Any) -> str:
    return normalize(value).replace(",", "").replace("٬", "").replace("٫", ".")


async def _call(token: str, database: str, operation: str, params: list[Any]) -> Any:
    try:
        path, body_key = OPERATIONS[operation]
    except KeyError as exc:
        raise ValueError("unsupported Tahesab read operation") from exc
    headers = {"Authorization": f"Bearer {token}", "DBname": database,
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=20) as client:
            response = await client.post(path, json={body_key: params})
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise TahesabFailure("upstream_unavailable") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise TahesabFailure("upstream_invalid_response") from exc
    if response.status_code >= 400:
        if response.status_code == 404 and _no_records(payload):
            return {}
        raise TahesabFailure("upstream_rejected_request")
    _check_business_error(payload)
    return payload


async def write_call(token: str, database: str, operation: str, params: list[Any]) -> Any:
    """One allowlisted source write. Transport failures are uncertain, not retryable."""
    if operation not in WRITE_OPERATIONS:
        raise ValueError("unsupported Tahesab write operation")
    path, body_key = WRITE_OPERATIONS[operation]
    headers = {"Authorization": f"Bearer {token}", "DBname": database,
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=25) as client:
            response = await client.post(path, json={body_key: params})
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise TahesabWriteUncertain("write_transport_uncertain") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise TahesabWriteUncertain("write_response_uncertain") from exc
    if response.status_code >= 500:
        raise TahesabWriteUncertain("write_server_uncertain")
    if response.status_code >= 400:
        raise TahesabFailure("upstream_rejected_request")
    _check_business_error(payload)
    if isinstance(payload, dict) and any(k in payload for k in ("ERROR", "error")):
        raise TahesabFailure("upstream_business_error")
    return payload


async def inquire_factor(token: str, database: str, factor: str) -> Any:
    headers = {"Authorization": f"Bearer {token}", "DBname": database,
               "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=20) as client:
            response = await client.post("/DoNewSanadInquiry-By-Factor-Code",
                                         json={"DoNewSanadInquiry": [factor]})
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise TahesabFailure("upstream_unavailable") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise TahesabFailure("upstream_invalid_response") from exc
    if response.status_code >= 400 and not _no_records(payload):
        raise TahesabFailure("upstream_rejected_request")
    if response.status_code >= 400:
        return {}
    _check_business_error(payload)
    return payload


async def verify_database(token: str, database: str) -> Any:
    # A source reference-table read is required; health and generic Status: OK
    # envelopes do not prove that the selected database can return records.
    payload = await _call(token, database, "verify_database", [])
    rows = records(payload)
    metadata = {"status", "ok", "message", "error", "db_status", "database_status"}
    if not rows or not any(
        any(str(key).casefold() not in metadata for key in row)
        for row in rows
    ):
        raise TahesabFailure("database_access_not_confirmed")
    return payload


async def customers_in_range(token: str, database: str, first: int, last: int) -> list[dict[str, Any]]:
    payload = await _call(token, database, "customer_range", [int(first), int(last)])
    return records(payload)


async def all_customers(token: str, database: str) -> list[dict[str, Any]]:
    # An empty customer-name filter returns the complete source list. Verified
    # against the test server: all 1,897 records, including code 1,896, are returned.
    payload = await _call(token, database, "customer_search", [""])
    return records(payload)


async def customer_by_code(token: str, database: str, code: int) -> list[dict[str, Any]]:
    return await customers_in_range(token, database, code, code)


async def customer_by_phone(token: str, database: str, phone: str) -> list[dict[str, Any]]:
    return records(await _call(token, database, "customer_phone", [phone]))


async def documents_for_customer(token: str, database: str, code: int) -> list[dict[str, Any]]:
    # count=0 is an existing integration convention. Completeness is not assumed by callers.
    payload = await _call(token, database, "documents", [0, int(code), "", "", "", 0])
    return records(payload)


async def documents_all(token: str, database: str) -> list[dict[str, Any]]:
    # Verified on the test server: the empty customer selector means all customers.
    # Count=0 returns the full snapshot; counts 25 and 50 return bounded recent reads.
    payload = await _call(token, database, "documents", [0, "", "", "", "", 0])
    return records(payload)


async def balance_for_customer(token: str, database: str, code: int) -> dict[str, Any]:
    payload = await _call(token, database, "balance", [str(code)])
    rows = records(payload)
    item = next((row for row in rows if str(customer_code(row) or row.get("Code") or code) == str(code)), None)
    if item is None and isinstance(payload, dict) and any(
            field in payload for field in ("MandeyeMali", "MandeMenhayeSeke", "MandeyeVazni")):
        item = payload
    if not isinstance(item, dict) or item.get("MandeyeMali") in (None, ""):
        raise TahesabFailure("missing_required_money_balance")
    try:
        amount = Decimal(_decimal_text(item["MandeyeMali"]))
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise TahesabFailure("invalid_money_balance")
    noncoin_gold = item.get("MandeMenhayeSeke")
    if noncoin_gold in (None, "") and isinstance(item.get("details"), list):
        for detail in item["details"]:
            if isinstance(detail, dict) and normalize(detail.get("Name")) == normalize("مانده منهای سکه"):
                noncoin_gold = detail.get("Value")
                break
    try:
        mappings = json.loads((Path(__file__).resolve().parents[1] / "catalogue.json").read_text(encoding="utf-8")).get("products", [])
    except (OSError, ValueError):
        mappings = []
    product_balances = []
    for key, value in item.items():
        if not str(key).startswith("Seke_") or value in (None, ""):
            continue
        try:
            quantity = Decimal(_decimal_text(value))
        except (InvalidOperation, ValueError):
            continue
        if quantity == 0:
            continue
        source_name = str(key)[5:]
        mapping = next((p for p in mappings if p.get("verified") and any(
            normalize(candidate) == normalize(source_name)
            for candidate in (p.get("source_name"), p.get("source_catalogue_name"))
            if candidate
        )), None)
        product_balances.append({"source_name": source_name,
                                 "title": mapping.get("title_fa") if mapping else source_name,
                                 "unit": mapping.get("unit") if mapping else "source",
                                 "signed_amount": str(quantity),
                                 "mapping_verified": bool(mapping)})
    return {"customer_code": code, "money_rial": str(int(amount)),
            "noncoin_gold_grams": noncoin_gold,
            "aggregate_gold_weight": item.get("MandeyeVazni"),
            "aggregate_silver_weight": item.get("MandeyeVazniNoghre"),
            "aggregate_platinum_weight": item.get("MandeyeVazniPelatin"),
            "product_balances": product_balances,
            "details": item.get("details", []), "source": item}


async def reference_catalogues(token: str, database: str) -> dict[str, list[dict[str, Any]] | None]:
    result = {}
    for name in ("groups", "currencies", "coins", "bars", "handmade", "reygiri", "bank_accounts"):
        try:
            result[name] = records(await _call(token, database, name, []))
        except TahesabFailure:
            result[name] = None
    return result

