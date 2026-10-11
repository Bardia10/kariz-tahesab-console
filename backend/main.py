from __future__ import annotations

import json
import asyncio
import os
import re
import secrets
import time
import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from . import tahesab as adapter
from . import journal

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TAHESAB_URL = adapter.BASE_URL
DATABASE = os.getenv("TAHESAB_DATABASE", "TahesabDB").strip() or "TahesabDB"
TAHESAB_TOKEN = os.getenv("TAHESAB_TOKEN", "").strip()
CONSOLE_PASSWORD = os.getenv("CONSOLE_PASSWORD", "")
COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "tahesab_test_session")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "auto").lower()
APP_ENV = os.getenv("APP_ENV", "development").lower()
DEV_ORIGINS = set() if APP_ENV == "production" else {
    "http://localhost:53927", "http://127.0.0.1:53927"
}
MAX_BODY_BYTES = 32 * 1024
CACHE_SECONDS = 30
MAX_SESSIONS = 100
MAX_CACHE_ITEMS_PER_SESSION = 60
MAX_CACHE_ITEMS = 3000
MAX_RANGE_WIDTH = 2500
TEHRAN = ZoneInfo("Asia/Tehran")

app = FastAPI(title="Tahesab Test Console API", docs_url=None, redoc_url=None)
sessions: dict[str, tuple[str, float]] = {}
cache: dict[tuple[str, str], tuple[float, Any, str]] = {}
document_snapshot_locks: dict[str, asyncio.Lock] = {}
customer_snapshot_locks: dict[str, asyncio.Lock] = {}
login_failures: dict[str, tuple[int, float]] = {}


class BodyLimitMiddleware:
    """Bound request bodies without trusting Content-Length."""

    def __init__(self, inner, limit: int):
        self.inner, self.limit = inner, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.inner(scope, receive, send)
            return
        body = bytearray()
        more = True
        while more:
            message = await receive()
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                response = JSONResponse({"detail": "درخواست بیش از اندازه بزرگ است."}, status_code=413)
                await response(scope, receive, send)
                return
            more = message.get("more_body", False)
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.inner(scope, replay, send)


app.add_middleware(BodyLimitMiddleware, limit=MAX_BODY_BYTES)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(DEV_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if not origin or (origin.rstrip("/") != expected.rstrip("/") and origin not in DEV_ORIGINS):
            return JSONResponse({"detail": "درخواست از مبدأ نامعتبر پذیرفته نشد."}, status_code=403)
    return await call_next(request)


class ConnectIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class OperationIn(BaseModel):
    operation_id: str = Field(min_length=20, max_length=80)
    kind: str
    payload: dict[str, Any]


TahesabFailure = adapter.TahesabFailure


@app.on_event("startup")
async def mark_interrupted_writes_uncertain():
    journal.recover_pending()


def normalize(value: Any) -> str:
    return adapter.normalize(value)


customer_code = adapter.customer_code


def now_label() -> str:
    return datetime.now(TEHRAN).isoformat(timespec="seconds")


def forget_session(sid: str) -> None:
    sessions.pop(sid, None)
    document_snapshot_locks.pop(sid, None)
    customer_snapshot_locks.pop(sid, None)
    for key in [key for key in cache if key[0] == sid]:
        cache.pop(key, None)


def require_session(request: Request) -> tuple[str, str]:
    sid = request.cookies.get(COOKIE_NAME)
    session = sessions.get(sid or "")
    if not sid or not session or session[1] <= time.monotonic():
        if sid:
            forget_session(sid)
        raise HTTPException(401, "برای ادامه، به سرور آزمایشی وصل شوید.")
    return sid, session[0]


def cookie_is_secure(request: Request) -> bool:
    if COOKIE_SECURE in {"true", "1", "yes"}:
        return True
    if COOKIE_SECURE in {"false", "0", "no"}:
        return False
    return request.url.scheme == "https"


def prune_expired_state() -> None:
    now = time.monotonic()
    expired_sessions = [sid for sid, (_, expires) in sessions.items() if expires <= now]
    for sid in expired_sessions:
        forget_session(sid)
    for key, (created, _, _) in list(cache.items()):
        if now - created > CACHE_SECONDS or key[0] not in sessions:
            cache.pop(key, None)


def read_cache(sid: str, key: str):
    prune_expired_state()
    item = cache.get((sid, key))
    if not item:
        return None
    created, value, _ = item
    if time.monotonic() - created > CACHE_SECONDS:
        cache.pop((sid, key), None)
        return None
    return value


def write_cache(sid: str, key: str, value: Any) -> None:
    prune_expired_state()
    cache[(sid, key)] = (time.monotonic(), value, now_label())
    same_session = sorted(
        ((item_key, item[0]) for item_key, item in cache.items() if item_key[0] == sid),
        key=lambda pair: pair[1],
    )
    while len(same_session) > MAX_CACHE_ITEMS_PER_SESSION:
        oldest, _ = same_session.pop(0)
        cache.pop(oldest, None)
    while len(cache) > MAX_CACHE_ITEMS:
        oldest = min(cache, key=lambda item_key: cache[item_key][0])
        cache.pop(oldest, None)


def cache_fetched_at(sid: str, key: str) -> str | None:
    item = cache.get((sid, key))
    return item[2] if item else None


def invalidate_document_snapshots(sid: str, code: int) -> None:
    cache.pop((sid, f"docs:{code}"), None)
    cache.pop((sid, "docs:all"), None)


def invalidate_customer_snapshots(sid: str, code: int) -> None:
    for key in list(cache):
        if key[0] == sid and (key[1].startswith("customers:") or key[1] == f"customer:{code}"
                              or key[1].startswith("docs:")):
            cache.pop(key, None)


async def fetch_customer_snapshot(sid: str, token: str, code: int) -> list[dict[str, Any]]:
    key = f"docs:{code}"
    cached = read_cache(sid, key)
    if cached is not None:
        return cached
    rows = await read_source(adapter.documents_for_customer(token, DATABASE, code))
    write_cache(sid, key, rows)
    return rows


async def fetch_all_customer_snapshot(sid: str, token: str) -> list[dict[str, Any]]:
    key = "customers:all"
    cached = read_cache(sid, key)
    if cached is not None:
        return cached
    lock = customer_snapshot_locks.setdefault(sid, asyncio.Lock())
    async with lock:
        cached = read_cache(sid, key)
        if cached is not None:
            return cached
        rows = await read_source(adapter.all_customers(token, DATABASE))
        rows.sort(key=lambda row: (customer_code(row) is None, customer_code(row) or 0))
        write_cache(sid, key, rows)
        return rows


def customer_search_match(row: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    fields = [row.get(name) for name in ("Name", "Moshtari_Name", "Mobile", "Tel", "Phone", "Tel1")]
    code = customer_code(row)
    if code is not None:
        fields.append(code)
    return any(query in normalize(str(value)) for value in fields if value not in (None, ""))


def classify_document(row: dict[str, Any]) -> tuple[str, str]:
    label = normalize(row.get("NO"))
    if any(word in label for word in (
        "واریز وجه", "ورود وجه", "واریز به بانک", "ورود بانک",
        "واریز نقد به بانک", "واریز نقدی به بانک", "واریز نقد (از مشتری)",
    )):
        return "payment", "incoming"
    if any(word in label for word in (
        "برداشت از بانک", "برداشت نقد از بانک", "برداشت بانک", "برداشت وجه",
        "پرداخت وجه", "پرداخت نقد به مشتری", "خروج وجه", "خروج بانک",
    )):
        return "payment", "outgoing"
    if any(word in label for word in (
        "ورود متفرقه", "ورود آبشده", "ورود کارساخته", "ورود سکه", "ورود شمش",
    )):
        return "delivery", "incoming"
    if any(word in label for word in (
        "خروج متفرقه", "خروج آبشده", "خروج کارساخته", "خروج سکه", "خروج شمش",
    )):
        return "delivery", "outgoing"
    if any(word in label for word in (
        "فروش طلا", "فروش آبشده", "فروش طلای آبشده", "فروش سکه", "فروش شمش",
    )):
        return "trade", "customer_buys"
    if any(word in label for word in (
        "خرید طلا", "خرید آبشده", "خرید طلای آبشده", "خرید سکه", "خرید شمش",
    )):
        return "trade", "customer_sells"
    return "other", "unknown"


def parse_jalali(value: Any) -> datetime | None:
    match = re.fullmatch(r"\s*(\d{3,4})[-/](\d{1,2})[-/](\d{1,2})\s*", normalize(value))
    if not match:
        return None
    try:
        import jdatetime
        year, month, day = map(int, match.groups())
        return jdatetime.date(year, month, day).togregorian()
    except (ValueError, OverflowError):
        return None


def nullable_source_value(value: Any):
    # This source sends the JSON string "null" for some missing scalar fields.
    if value is None or isinstance(value, str) and value.strip().casefold() == "null":
        return None
    return value


def select_doc_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = nullable_source_value(row.get(key))
        if value not in (None, ""):
            return str(value)
    return ""


async def read_source(operation):
    try:
        return await operation
    except TahesabFailure as exc:
        raise HTTPException(502, "ارتباط با ته حساب برقرار نشد یا پاسخ آن قابل استفاده نبود.") from exc


@app.get("/api/session")
async def session_status(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    session = sessions.get(sid or "")
    connected = bool(session and session[1] > time.monotonic())
    if sid and not connected:
        forget_session(sid)
    return {"connected": connected, "server": TAHESAB_URL,
            "database": DATABASE}


@app.post("/api/connect")
async def connect(data: ConnectIn, request: Request, response: Response):
    if not CONSOLE_PASSWORD or not TAHESAB_TOKEN:
        raise HTTPException(503, "تنظیمات ورود یا اتصال ته حساب روی سرور کامل نیست.")
    now = time.monotonic()
    for key, (_, started) in list(login_failures.items()):
        if now - started >= 60:
            login_failures.pop(key, None)
    address = request.client.host if request.client else "unknown"
    attempts, started = login_failures.get(address, (0, now))
    if attempts >= 6:
        raise HTTPException(429, "تلاش‌های ورود زیاد بود. یک دقیقه بعد دوباره امتحان کنید.")
    if not secrets.compare_digest(data.password.encode(), CONSOLE_PASSWORD.encode()):
        if len(login_failures) >= 1000:
            login_failures.pop(next(iter(login_failures)))
        login_failures[address] = (attempts + 1, started)
        raise HTTPException(401, "رمز عبور نادرست است.")
    login_failures.pop(address, None)
    # A database-backed reference-list read is the connection check; health alone is not evidence.
    try:
        payload = await adapter.verify_database(TAHESAB_TOKEN, DATABASE)
        if not isinstance(payload, (dict, list)):
            raise TahesabFailure("upstream_invalid_response")
    except TahesabFailure as exc:
        raise HTTPException(502, "اتصال سرور به ته حساب آزمایشی تأیید نشد. تنظیمات اتصال سرور باید بررسی شود.") from exc
    prune_expired_state()
    if len(sessions) >= MAX_SESSIONS:
        oldest_sid = min(sessions, key=lambda session_id: sessions[session_id][1])
        forget_session(oldest_sid)
    sid = secrets.token_urlsafe(32)
    previous_sid = request.cookies.get(COOKIE_NAME)
    if previous_sid:
        forget_session(previous_sid)
    sessions[sid] = (TAHESAB_TOKEN, time.monotonic() + 60 * 60 * 12)
    response.set_cookie(COOKIE_NAME, sid, httponly=True, secure=cookie_is_secure(request),
                        samesite="strict", max_age=60 * 60 * 12, path="/")
    return {"connected": True, "database": DATABASE, "checked_at": now_label()}


@app.post("/api/disconnect")
async def disconnect(request: Request, response: Response):
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        forget_session(sid)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True,
                           secure=cookie_is_secure(request), samesite="strict")
    return {"connected": False}


@app.get("/api/customers")
async def customers(request: Request, from_code: int = 1, to_code: int = 250):
    sid, token = require_session(request)
    if from_code < 0 or to_code < from_code or to_code - from_code + 1 > MAX_RANGE_WIDTH:
        raise HTTPException(422, "بازه کد مشتری نامعتبر است؛ هر بار حداکثر ۲۵۰۰ کد را بررسی کنید.")
    key = f"customers:{from_code}:{to_code}"
    rows = read_cache(sid, key)
    if rows is None:
        rows = []
        # Bounded chunks; the visible result always carries its exact requested range.
        for start in range(from_code, to_code + 1, 250):
            end = min(to_code, start + 249)
            rows.extend(await read_source(adapter.customers_in_range(token, DATABASE, start, end)))
        rows.sort(key=lambda row: customer_code(row) or -1)
        write_cache(sid, key, rows)
    return {"items": rows, "from_code": from_code, "to_code": to_code,
            "complete_for_range": False,
            "coverage_note": "نمایش این بازه به معنی پوشش همه مشتریان یا تأیید سقف پاسخ منبع نیست.",
            "fetched_at": cache_fetched_at(sid, key)}


@app.get("/api/customer-search")
async def customer_search(request: Request, q: str = "", page: int = 1, page_size: int = 25):
    sid, token = require_session(request)
    if page < 1 or page_size not in {25, 50, 100}:
        raise HTTPException(422, "شماره صفحه یا اندازه صفحه معتبر نیست.")
    if len(q) > 100:
        raise HTTPException(422, "عبارت جست‌وجو بیش از اندازه طولانی است.")
    key = "customers:all"
    rows = await fetch_all_customer_snapshot(sid, token)
    normalized_query = normalize(q)
    matches = [row for row in rows if customer_search_match(row, normalized_query)]
    start = (page - 1) * page_size
    return {"items": matches[start:start + page_size], "total": len(matches),
            "page": page, "page_size": page_size, "complete_snapshot": True,
            "fetched_at": cache_fetched_at(sid, key)}


@app.get("/api/customers/{code}")
async def customer_detail(code: int, request: Request):
    sid, token = require_session(request)
    key = f"customer:{code}"
    rows = read_cache(sid, key)
    if rows is None:
        rows = await read_source(adapter.customer_by_code(token, DATABASE, code))
        write_cache(sid, key, rows)
    record = next((row for row in rows if customer_code(row) == code), None)
    if record is None:
        raise HTTPException(404, "مشتری با این کد در بازخوانی منبع پیدا نشد.")
    return {"item": record, "fetched_at": cache_fetched_at(sid, key)}


@app.get("/api/customers/{code}/documents")
async def customer_documents(request: Request, code: int, kind: str = "all", direction: str = "all",
                             product: str = "", document_id: str = "", search: str = "",
                             from_date: str = "", to_date: str = "", page: int = 1,
                             page_size: int = 25, refresh: bool = False):
    sid, token = require_session(request)
    if page < 1 or page_size not in {25, 50, 100}:
        raise HTTPException(422, "شماره صفحه یا اندازه صفحه معتبر نیست.")
    if len(search) > 100 or len(product) > 100 or len(document_id) > 64:
        raise HTTPException(422, "عبارت فیلتر بیش از اندازه طولانی است.")
    key = f"docs:{code}"
    if refresh:
        cache.pop((sid, key), None)
    rows = await fetch_customer_snapshot(sid, token, code)
    start_date, end_date = parse_jalali(from_date), parse_jalali(to_date)
    if (from_date and start_date is None) or (to_date and end_date is None):
        raise HTTPException(422, "تاریخ باید یک روز معتبر شمسی به شکل سال/ماه/روز باشد.")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "تاریخ شروع نباید بعد از تاریخ پایان باشد.")
    selected = []
    for row in rows:
        category, doc_direction = classify_document(row)
        if kind != "all" and category != kind:
            continue
        if direction != "all" and doc_direction != direction:
            continue
        item_product = select_doc_text(row, "Name_az", "Name_Seke", "Name_Shemsh", "No_Kar")
        if product and normalize(product) not in normalize(item_product):
            continue
        if document_id and normalize(document_id) not in normalize(row.get("ID", row.get("_source_key", ""))):
            continue
        text = " ".join(str(v) for v in row.values() if v is not None)
        if search and normalize(search) not in normalize(text):
            continue
        raw_date = nullable_source_value(row.get("Tarikh"))
        parsed_date = parse_jalali(raw_date)
        if start_date and (parsed_date is None or parsed_date < start_date):
            continue
        if end_date and (parsed_date is None or parsed_date > end_date):
            continue
        selected.append({"category": category, "direction": doc_direction,
                         "id": row.get("ID", row.get("_source_key")), "radif": row.get("Radif"),
                         "date": raw_date, "time": nullable_source_value(row.get("Tarikh_Time")),
                         "label": nullable_source_value(row.get("NO")), "product": item_product,
                         "quantity": row.get("Count") if row.get("Count") not in (None, "") else row.get("Vazn"),
                         "amount": nullable_source_value(row.get("Mali")), "currency": nullable_source_value(row.get("Arz")),
                         "factor_code": row.get("Factor_Code"),
                         "temporary": row.get("isSanadMovaghat"),
                         "affects_balance": row.get("TasirBarMande"), "source": row})
    selected.sort(key=lambda row: (normalize(row["date"]), normalize(row["time"]),
                                   normalize(row["id"]), normalize(row["radif"])), reverse=True)
    offset = (page - 1) * page_size
    return {"items": selected[offset:offset + page_size], "total": len(selected),
            "page": page, "page_size": page_size, "complete_snapshot": True, "snapshot_note": "count=0 was compared with count=1 on the test server; this is the complete history for the selected customer, not a global history endpoint.",
            "fetched_at": cache_fetched_at(sid, key), "customer_code": code}


@app.get("/api/documents")
async def all_customer_documents(request: Request, kind: str = "all", direction: str = "all",
                                 product: str = "", document_id: str = "", search: str = "",
                                 from_date: str = "", to_date: str = "", customer: int | None = None,
                                 page: int = 1, page_size: int = 25, refresh: bool = False):
    """Read the verified all-customer source snapshot, then filter and paginate it."""
    sid, token = require_session(request)
    if kind not in {"all", "payment", "delivery", "trade", "other"} or direction not in {
            "all", "incoming", "outgoing", "customer_buys", "customer_sells"}:
        raise HTTPException(422, "فیلتر نوع یا جهت معتبر نیست.")
    if page < 1 or page_size not in {25, 50, 100}:
        raise HTTPException(422, "شماره صفحه یا اندازه صفحه معتبر نیست.")
    if len(search) > 100 or len(product) > 100 or len(document_id) > 64:
        raise HTTPException(422, "عبارت فیلتر بیش از اندازه طولانی است.")
    start_date, end_date = parse_jalali(from_date), parse_jalali(to_date)
    if (from_date and start_date is None) or (to_date and end_date is None):
        raise HTTPException(422, "تاریخ باید یک روز معتبر شمسی به شکل سال/ماه/روز باشد.")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "تاریخ شروع نباید بعد از تاریخ پایان باشد.")

    snapshot_key = "docs:all"
    lock = document_snapshot_locks.setdefault(sid, asyncio.Lock())
    async with lock:
        rows = None if refresh else read_cache(sid, snapshot_key)
        if rows is None:
            rows = await read_source(adapter.documents_all(token, DATABASE))
            write_cache(sid, snapshot_key, rows)
        fetched_at = cache_fetched_at(sid, snapshot_key)

    selected = []
    for row in rows:
        # An outer document key is never a customer code. Preserve missing context.
        try:
            code_value = int(normalize(row["MCode"])) if row.get("MCode") not in (None, "") else None
        except (ValueError, TypeError):
            code_value = None
        if customer is not None and code_value != customer:
            continue
        category, doc_direction = classify_document(row)
        if kind != "all" and category != kind:
            continue
        if direction != "all" and doc_direction != direction:
            continue
        item_product = select_doc_text(row, "Name_az", "Name_Seke", "Name_Shemsh", "No_Kar")
        if product and normalize(product) not in normalize(item_product):
            continue
        if document_id and normalize(document_id) not in normalize(row.get("ID", row.get("_source_key", ""))):
            continue
        text = " ".join(str(value) for value in row.values() if value is not None)
        if search and normalize(search) not in normalize(text):
            continue
        raw_date = nullable_source_value(row.get("Tarikh"))
        parsed_date = parse_jalali(raw_date)
        if start_date and (parsed_date is None or parsed_date < start_date):
            continue
        if end_date and (parsed_date is None or parsed_date > end_date):
            continue
        selected.append({"category": category, "direction": doc_direction,
                         "id": row.get("ID", row.get("_source_key")), "radif": row.get("Radif"),
                         "date": raw_date, "time": nullable_source_value(row.get("Tarikh_Time")),
                         "label": nullable_source_value(row.get("NO")), "product": item_product,
                         "quantity": row.get("Count") if row.get("Count") not in (None, "") else row.get("Vazn"),
                         "amount": nullable_source_value(row.get("Mali")), "currency": nullable_source_value(row.get("Arz")),
                         "factor_code": row.get("Factor_Code"), "temporary": row.get("isSanadMovaghat"),
                         "affects_balance": row.get("TasirBarMande"), "customer_code": code_value,
                         "description": select_doc_text(row, "Sharh1", "Sharh2", "Baabat"),
                         "customer_name": None,
                         "source": row})
    def numeric_identity(value):
        try:
            return int(normalize(value))
        except (ValueError, TypeError):
            return -1

    def chronological_identity(row):
        date_value = parse_jalali(row["date"])
        try:
            time_parts = [int(part) for part in normalize(row["time"]).split(":")]
            time_value = tuple((time_parts + [0, 0, 0])[:3])
        except ValueError:
            time_value = (0, 0, 0)
        return (date_value.toordinal() if date_value else 0, time_value,
                numeric_identity(row["id"]), numeric_identity(row["radif"]))

    selected.sort(key=chronological_identity, reverse=True)
    offset = (page - 1) * page_size
    items = selected[offset:offset + page_size]
    # Resolve names only for visible rows; never scan every customer for a document list.
    codes = {row["customer_code"] for row in items if row["customer_code"] is not None}
    semaphore = asyncio.Semaphore(6)

    async def read_name(code_value):
        key = f"customer:{code_value}"
        customer_rows = read_cache(sid, key)
        if customer_rows is None:
            async with semaphore:
                try:
                    customer_rows = await adapter.customer_by_code(token, DATABASE, code_value)
                    write_cache(sid, key, customer_rows)
                except TahesabFailure:
                    return code_value, None
        record = next((record for record in customer_rows if customer_code(record) == code_value), {})
        return code_value, select_doc_text(record, "Name", "FullName", "Name_Moshtari") or None

    names = dict(await asyncio.gather(*(read_name(code_value) for code_value in codes)))
    for item in items:
        item["customer_name"] = names.get(item["customer_code"])
    return {"items": items, "total": len(selected), "source_total": len(rows),
            "page": page, "page_size": page_size, "complete_snapshot": True,
            "fetched_at": fetched_at}


@app.get("/api/customers/{code}/balance")
async def customer_balance(request: Request, code: int, refresh: bool = False):
    sid, token = require_session(request)
    key = f"balance:{code}"
    result = None if refresh else read_cache(sid, key)
    if result is None:
        try:
            result = await adapter.balance_for_customer(token, DATABASE, code)
        except TahesabFailure as exc:
            raise HTTPException(502, "مانده الزامی از ته حساب دریافت نشد یا پاسخ آن معتبر نبود.") from exc
        write_cache(sid, key, result)
    return {**result, "fetched_at": cache_fetched_at(sid, key)}


@app.get("/api/catalogue")
async def catalogue(request: Request, refresh: bool = False):
    sid, token = require_session(request)
    key = "source-catalogues"
    source = None if refresh else read_cache(sid, key)
    if source is None:
        source = await adapter.reference_catalogues(token, DATABASE)
        write_cache(sid, key, source)
    try:
        mappings = json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        mappings = {"version": None, "products": []}
    verified = sum(1 for p in mappings.get("products", []) if p.get("verified"))
    return {"mapping": mappings, "source_catalogues": source,
            "mapping_status": "configured" if verified else "needs_verification",
            "verified_mapping_count": verified,
            "fetched_at": cache_fetched_at(sid, key)}


@app.get("/api/capabilities")
async def capabilities(request: Request):
    require_session(request)
    return {"writes_enabled": True, "items": [
        {"operation": "customer_create", "verified": True, "enabled": True},
        {"operation": "customer_edit", "verified": True, "enabled": True,
         "reason": "ویرایش و بازخوانی مشتری آزمایشی بررسی شد؛ سایر مشخصات مشتری حفظ می‌شود."},
        {"operation": "cash_payment", "verified": True, "enabled": True},
        {"operation": "bank_payment", "verified": True, "enabled": True},
        {"operation": "physical_gold_delivery", "verified": True, "enabled": True,
         "reason": "دریافت و تحویل فیزیکی متفرقه و آبشده ۷۵۰ با بازخوانی سند و مانده تأیید شده است."},
        {"operation": "physical_coin_receipt_delivery", "verified": True, "enabled": True},
        {"operation": "physical_bar_receipt_delivery", "verified": True, "enabled": True},
        {"operation": "coin_accounting_trade", "verified": True, "enabled": True},
        {"operation": "bar_accounting_trade", "verified": True, "enabled": True},
        {"operation": "gold_accounting_trade", "verified": True, "enabled": True,
         "reason": "فقط حالت آبشده ۷۵۰ با مظنه بر مثقال یا قیمت هر گرم فعال است؛ جهت، وزن، مبلغ و اثر مانده تأیید شده است."},
        {"operation": "gold_misc_accounting_trade", "verified": False, "enabled": False,
         "reason": "ته حساب حالت سند معامله متفرقه را در بازخوانی مشخص نمی‌کند؛ این عمل برای جلوگیری از ثبت با نوع اشتباه غیرفعال است."},
    ], "note": "عملیات مالی با شناسه پایدار و ژورنال محلی ثبت می‌شوند؛ نتیجه نامشخص دوباره ارسال نمی‌شود."}


def _operation_response(row):
    return journal.public(row)


def _source_customer_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    key = next((candidate for candidate in aliases if candidate in row), None)
    return row.get(key) if key else None


def _validated_jalali(date_text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", normalize(date_text))
    if not match:
        raise HTTPException(422, "تاریخ شمسی را به شکل سال/ماه/روز وارد کنید.")
    try:
        import jdatetime
        year, month, day = map(int, match.groups())
        jdatetime.date(year, month, day)
    except (ImportError, ValueError):
        raise HTTPException(422, "تاریخ شمسی معتبر نیست.")
    return year, month, day


def _jalali_header(code: int, date_text: str) -> list[Any]:
    year, month, day = _validated_jalali(date_text)
    return [1, int(code), -1, 1, year, month, day]


def _int_amount(value: Any) -> int:
    try:
        amount = Decimal(normalize(value).replace(",", "").replace("٬", "").replace("٫", "."))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, "مبلغ معتبر نیست.")
    if not amount.is_finite() or amount <= 0 or amount * 10 != (amount * 10).to_integral_value():
        raise HTTPException(422, "مبلغ باید مثبت باشد و حداکثر یک رقم اعشار داشته باشد.")
    rial = amount * 10
    if rial > Decimal("999999999999999999"):
        raise HTTPException(422, "مبلغ از حد مجاز بزرگ‌تر است.")
    return int(rial)


async def _refresh_balance_after_write(sid: str, token: str, code: int) -> bool:
    try:
        balance_value = await adapter.balance_for_customer(token, DATABASE, code)
    except TahesabFailure:
        cache.pop((sid, f"balance:{code}"), None)
        return False
    write_cache(sid, f"balance:{code}", balance_value)
    return True


def _verified_mapping(product_key: Any, family: str) -> dict[str, Any]:
    try:
        mappings = json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8")).get("products", [])
    except (OSError, json.JSONDecodeError):
        mappings = []
    item = next((p for p in mappings if p.get("key") == product_key), None)
    if (not item or not item.get("verified") or item.get("source_family") != family
            or not item.get("source_identifier") or item.get("source_weight") is None):
        raise HTTPException(422, "نگاشت تأییدشده این محصول در تنظیم فعلی وجود ندارد؛ سندی ارسال نشد.")
    return item


def _verified_gold_mode(product_key: Any, action: str) -> dict[str, Any]:
    try:
        mapping = json.loads((ROOT / "catalogue.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        mapping = {}
    entries = list(mapping.get("products", [])) + list(mapping.get("source_gold_modes", []))
    item = next((p for p in entries if p.get("key") == product_key and p.get("source_family") == "gold"), None)
    allowed_field = "physical_enabled" if action == "physical" else "trade_enabled"
    if not item or not item.get("verified") or not item.get(allowed_field):
        raise HTTPException(422, "نگاشت یا حالت سند طلای تأییدشده برای این عمل وجود ندارد؛ سندی ارسال نشد.")
    if item.get("source_document_mode") not in (0, 1):
        raise HTTPException(422, "حالت سند طلا در پیکربندی مشخص نیست؛ سندی ارسال نشد.")
    return item


async def _product_balance(token: str, code: int, source_name: str) -> Decimal:
    payload = await adapter._call(token, DATABASE, "balance", [str(code)])
    rows = adapter.records(payload)
    source = next((row for row in rows if str(row.get("Code", row.get("MCode", ""))) == str(code)), None)
    if source is None:
        raise HTTPException(502, "ردیف مانده مشتری برای کنترل محصول در پاسخ ته حساب پیدا نشد.")
    key = f"Seke_{source_name}"
    if key not in source or source.get(key) in (None, ""):
        raise HTTPException(409, "فیلد مانده این محصول در پاسخ ته حساب وجود ندارد؛ سندی ارسال نشد.")
    try:
        value = Decimal(adapter._decimal_text(source[key]))
    except (InvalidOperation, ValueError):
        raise HTTPException(502, "مانده محصول در پاسخ ته حساب عدد معتبر نیست؛ سندی ارسال نشد.")
    if not value.is_finite():
        raise HTTPException(502, "مانده محصول در پاسخ ته حساب عدد معتبر نیست؛ سندی ارسال نشد.")
    return value


async def _verify_document(token: str, factor: str, code: int, expected_direction: str,
                           expected_amount: int | None = None, expected_quantity: str | None = None,
                           expected_product: str | None = None, expected_bank: str | None = None,
                           expected_reference: str | None = None, expected_count: int | None = None,
                           expected_weight: str | None = None, expected_unit_price: int | None = None,
                           expected_signed_amount: int | None = None, require_effective: bool = False):
    inquiry = await adapter.inquire_factor(token, DATABASE, factor)
    rows = adapter.records(inquiry)
    if not rows:
        return None
    candidates = await adapter.documents_for_customer(token, DATABASE, code)
    row = next((item for item in candidates if str(item.get("Factor_Code", "")) == factor), None)
    if row is None or adapter.customer_code(row) != code:
        return None
    if row.get("ID") in (None, "") or row.get("Radif") in (None, ""):
        return None
    _, direction = classify_document(row)
    if direction != expected_direction:
        return None
    if expected_amount is not None:
        try:
            if abs(Decimal(adapter._decimal_text(row.get("Mali")))) != expected_amount:
                return None
        except (InvalidOperation, ValueError):
            return None
    if expected_signed_amount is not None:
        try:
            if Decimal(adapter._decimal_text(row.get("Mali"))) != expected_signed_amount:
                return None
        except (InvalidOperation, ValueError):
            return None
    if expected_quantity is not None:
        actual = row.get("Vazn") if row.get("Vazn") not in (None, "") else row.get("Count")
        if actual is None or Decimal(adapter._decimal_text(actual)) != Decimal(expected_quantity):
            return None
    if expected_count is not None:
        if row.get("Count") in (None, "") or Decimal(adapter._decimal_text(row["Count"])) != Decimal(expected_count):
            return None
    if expected_weight is not None:
        if row.get("Vazn") in (None, "") or Decimal(adapter._decimal_text(row["Vazn"])) != Decimal(expected_weight):
            return None
    if expected_unit_price is not None:
        if row.get("Mazaneh") in (None, "") or Decimal(adapter._decimal_text(row["Mazaneh"])) != Decimal(expected_unit_price):
            return None
    if require_effective:
        if row.get("TasirBarMande") not in (1, "1", True):
            return None
        if row.get("isSanadMovaghat") not in (False, 0, "0", "false"):
            return None
    if expected_product is not None:
        actual_product = select_doc_text(row, "Name_Seke", "Name_Shemsh", "Name_az")
        if normalize(actual_product) != normalize(expected_product):
            return None
    if expected_bank is not None and normalize(row.get("Name_Bank")) != normalize(expected_bank):
        return None
    if expected_reference is not None:
        actual_reference = row.get("Sh_Cheq") or row.get("Code_Rahgiri") or ""
        if normalize(actual_reference) != normalize(expected_reference):
            return None
    return row


@app.post("/api/operations")
async def create_operation(data: OperationIn, request: Request):
    sid, token = require_session(request)
    try:
        uuid.UUID(data.operation_id)
    except ValueError:
        raise HTTPException(422, "شناسه عملیات معتبر نیست.")
    payload = data.payload
    factor = journal.factor_code() if data.kind in {
        "cash_payment", "bank_payment", "gold_receipt_750",
        "physical_gold", "trade_gold", "physical_coin", "physical_bar", "trade_coin", "trade_bar",
    } else None
    try:
        row, created = journal.reserve(data.operation_id, data.kind, payload, factor)
    except ValueError:
        raise HTTPException(409, "شناسه عملیات قبلاً با اطلاعات دیگری استفاده شده است.")
    if not created:
        return _operation_response(row)

    submitted = False
    edit_context = None
    try:
        if data.kind == "customer_create":
            name = str(payload.get("full_name", "")).strip()
            group = str(payload.get("group_name", "")).strip()
            mobile_value = str(payload.get("mobile", "")).strip()
            if not name or not group or len(name) > 150 or len(group) > 120 or len(mobile_value) > 32:
                journal.update(data.operation_id, "failed", "نام کامل و گروه منبع الزامی است.")
                raise HTTPException(422, "نام کامل و گروه منبع الزامی است.")
            group_rows = (await adapter.reference_catalogues(token, DATABASE)).get("groups")
            allowed_groups = {normalize(g.get("Name") or g.get("Gorooh") or g.get("GoroohName") or g.get("GroupName")) for g in group_rows or []}
            if normalize(group) not in allowed_groups:
                journal.update(data.operation_id, "failed", "گروه باید از فهرست فعلی ته حساب انتخاب شود.")
                raise HTTPException(422, "گروه باید از فهرست فعلی ته حساب انتخاب شود.")
            birthdate = str(payload.get("birthdate", "")).strip()
            if birthdate:
                year, month, day = _validated_jalali(birthdate)
                birthdate = f"{year:04d}-{month:02d}-{day:02d}"
            params = [name, group, mobile_value, str(payload.get("address", "")),
                      str(payload.get("national_id", "")), birthdate,
                      "", "", -1, 0]
            submitted = True
            invalidate_customer_snapshots(sid, -1)
            result = await adapter.write_call(token, DATABASE, "customer", params)
            invalidate_customer_snapshots(sid, -1)
            code_value = result.get("OK") if isinstance(result, dict) else None
            try:
                new_code = int(normalize(code_value))
            except (TypeError, ValueError):
                journal.update(data.operation_id, "uncertain", "منبع پاسخ داد، اما کد واقعی مشتری در پاسخ قابل تشخیص نبود.", result)
                return _operation_response(journal.get(data.operation_id))
            readback = await adapter.customer_by_code(token, DATABASE, new_code)
            customer = next((r for r in readback if adapter.customer_code(r) == new_code), None)
            if customer and normalize(customer.get("Name")) == normalize(name) and normalize(customer.get("GoroupName") or customer.get("GroupName") or customer.get("Gorooh") or group) == normalize(group) and (not mobile_value or normalize(customer.get("Mobile") or "") == normalize(mobile_value)):
                result = {"response": result, "customer": customer}
                journal.update(data.operation_id, "verified", "مشتری با کد منبع بازخوانی و بررسی شد.", result)
                invalidate_customer_snapshots(sid, new_code)
            else:
                journal.update(data.operation_id, "uncertain", "ثبت پاسخ داده شد، اما بازخوانی مشتری آن را تأیید نکرد.", result)
            return _operation_response(journal.get(data.operation_id))

        if data.kind == "customer_edit":
            try:
                code = int(payload.get("customer_code"))
            except (TypeError, ValueError):
                raise HTTPException(422, "کد مشتری معتبر نیست.")
            if code <= 0:
                raise HTTPException(422, "کد مشتری معتبر نیست.")
            source_rows = await adapter.customer_by_code(token, DATABASE, code)
            current = next((item for item in source_rows if adapter.customer_code(item) == code), None)
            if current is None:
                journal.update(data.operation_id, "failed", "مشتری در ته حساب پیدا نشد.")
                raise HTTPException(404, "مشتری در ته حساب پیدا نشد.")
            aliases = {
                "full_name": ("Name",),
                "group_name": ("GoroupName",),
                "mobile": ("Mobile",),
                "address": ("Address",),
                "national_id": ("CodeMelli",),
            }
            values: dict[str, str] = {}
            for field, keys in aliases.items():
                source_key = next((key for key in keys if key in current), None)
                if source_key is None:
                    journal.update(data.operation_id, "failed", "اطلاعات مشتری در پاسخ ته حساب کامل نیست؛ تغییری ارسال نشد.")
                    raise HTTPException(409, "اطلاعات مشتری در پاسخ ته حساب کامل نیست؛ تغییری ارسال نشد.")
                source_value = current.get(source_key) if source_key else ""
                values[field] = str(payload[field]).strip() if field in payload else str(source_value or "")
            name, group = values["full_name"], values["group_name"]
            if not name or not group or len(name) > 150 or len(group) > 120 or len(values["mobile"]) > 32 or len(values["address"]) > 300 or len(values["national_id"]) > 32:
                raise HTTPException(422, "مقادیر اطلاعات مشتری معتبر نیست.")
            group_rows = (await adapter.reference_catalogues(token, DATABASE)).get("groups")
            allowed_groups = {normalize(g.get("Name") or g.get("Gorooh") or g.get("GoroohName") or g.get("GroupName")) for g in group_rows or []}
            if group and allowed_groups and normalize(group) not in allowed_groups:
                raise HTTPException(422, "گروه مشتری را از فهرست ته حساب انتخاب کنید.")
            try:
                params = adapter.edit_customer_params(name, group, values["mobile"], values["address"],
                                                      values["national_id"], code, current)
            except TahesabFailure as exc:
                journal.update(data.operation_id, "failed", "اطلاعات لازم برای حفظ سایر مشخصات مشتری دریافت نشد؛ تغییری ارسال نشد.")
                raise HTTPException(409, "اطلاعات لازم برای حفظ سایر مشخصات مشتری دریافت نشد؛ تغییری ارسال نشد.") from exc
            edit_context = {"pre_write": {"expected": values,
                            "preserved": {key: current[key] for key in ("BDate", "City", "Rasteh") if key in current}}}
            journal.update(data.operation_id, "submitting", "در حال ذخیره اطلاعات مشتری…", edit_context)
            submitted = True
            invalidate_customer_snapshots(sid, code)
            result = await adapter.write_call(token, DATABASE, "customer_edit", params)
            invalidate_customer_snapshots(sid, code)
            readback_rows = await adapter.customer_by_code(token, DATABASE, code)
            after = next((item for item in readback_rows if adapter.customer_code(item) == code), None)
            matches = bool(after and all(normalize(_source_customer_value(after, aliases[key])) == normalize(value)
                                         for key, value in values.items())
                           and all(key in after and normalize(after[key]) == normalize(value)
                                   for key, value in edit_context["pre_write"]["preserved"].items()))
            saved = {**edit_context, "response": result, "customer": after}
            if matches:
                journal.update(data.operation_id, "verified", "اطلاعات مشتری ذخیره شد و از ته حساب بازخوانی و بررسی شد.", saved)
                invalidate_customer_snapshots(sid, code)
            else:
                journal.update(data.operation_id, "uncertain", "بازخوانی ته حساب همه تغییرات را تأیید نکرد؛ نتیجه نیازمند بررسی است و درخواست دوباره ارسال نشد.", saved)
            return _operation_response(journal.get(data.operation_id))

        if data.kind == "cash_payment":
            code = int(payload.get("customer_code"))
            direction = payload.get("direction")
            if direction not in {"deposit", "payout"}:
                raise HTTPException(422, "جهت پرداخت معتبر نیست.")
            rial = _int_amount(payload.get("amount_toman"))
            header = _jalali_header(code, str(payload.get("date", "")))
            params = header + [0 if direction == "deposit" else 1, rial,
                               str(payload.get("description", ""))[:300], factor, 0, -1, "IRR"]
            submitted = True
            result = await adapter.write_call(token, DATABASE, "cash", params)
            verified = await _verify_document(token, factor, code,
                                              "incoming" if direction == "deposit" else "outgoing", rial)
            if verified:
                balance_refreshed = await _refresh_balance_after_write(sid, token, code)
                message = "سند با شناسه مشتری، کد پیگیری، جهت و مبلغ بازخوانی و تأیید شد."
                if not balance_refreshed:
                    message += " مانده به‌روز نشد؛ پاسخ منبع فیلد الزامی مبلغ را نداشت."
                journal.update(data.operation_id, "verified", message, {"response": result, "document": verified, "balance_refreshed": balance_refreshed})
                invalidate_document_snapshots(sid, code)
                cache.pop((sid, f"balance:{code}"), None)
            else:
                journal.update(data.operation_id, "uncertain", "ارسال انجام شد، اما بازخوانی منبع هنوز سند را کامل تأیید نکرده است.", result)
            return _operation_response(journal.get(data.operation_id))

        if data.kind == "bank_payment":
            code = int(payload.get("customer_code"))
            direction = payload.get("direction")
            bank_name = str(payload.get("bank_name", "")).strip()
            reference = str(payload.get("reference", "")).strip()
            if direction not in {"deposit", "payout"}:
                journal.update(data.operation_id, "failed", "جهت سند بانکی معتبر نیست.")
                raise HTTPException(422, "جهت سند بانکی معتبر نیست.")
            if not bank_name or not reference or len(reference) > 100:
                journal.update(data.operation_id, "failed", "انتخاب حساب بانکی و شماره پیگیری الزامی است.")
                raise HTTPException(422, "انتخاب حساب بانکی و شماره پیگیری الزامی است.")
            try:
                rial = _int_amount(payload.get("amount_toman"))
                year, month, day = _validated_jalali(str(payload.get("date", "")))
            except HTTPException:
                journal.update(data.operation_id, "failed", "مبلغ یا تاریخ معتبر نیست؛ سندی ارسال نشد.")
                raise
            accounts = (await adapter.reference_catalogues(token, DATABASE)).get("bank_accounts") or []
            allowed = {normalize(account.get("Name_Bank")) for account in accounts}
            if normalize(bank_name) not in allowed:
                journal.update(data.operation_id, "failed", "حساب بانکی در فهرست فعلی منبع نیست؛ سندی ارسال نشد.")
                raise HTTPException(422, "حساب بانکی باید از فهرست ته حساب انتخاب شود.")
            operation_date = f"{year:04d}-{month:02d}-{day:02d}"
            header = _jalali_header(code, str(payload.get("date", "")))
            params = header + [0 if direction == "deposit" else 1, rial,
                               str(payload.get("description", ""))[:300], bank_name,
                               reference, factor, -1, -1, "IRR", operation_date]
            submitted = True
            result = await adapter.write_call(token, DATABASE, "bank", params)
            verified = await _verify_document(
                token, factor, code, "incoming" if direction == "deposit" else "outgoing",
                expected_amount=rial, expected_bank=bank_name, expected_reference=reference)
            if verified:
                balance_refreshed = await _refresh_balance_after_write(sid, token, code)
                message = "سند بانکی با کد پیگیری، حساب، جهت، مبلغ و شناسه سند از منبع بازخوانی و تأیید شد."
                if not balance_refreshed:
                    message += " مانده به‌روز نشد."
                journal.update(data.operation_id, "verified", message,
                               {"response": result, "document": verified, "balance_refreshed": balance_refreshed})
                invalidate_document_snapshots(sid, code)
                cache.pop((sid, f"balance:{code}"), None)
            else:
                journal.update(data.operation_id, "uncertain", "ارسال انجام شد، اما بازخوانی منبع سند بانکی را کامل تأیید نکرد. ارسال دوباره انجام نمی‌شود.", result)
            return _operation_response(journal.get(data.operation_id))

        if data.kind == "physical_gold":
            code = int(payload.get("customer_code"))
            direction = payload.get("direction")
            if direction not in {"incoming", "outgoing"}:
                raise HTTPException(422, "جهت دریافت یا تحویل طلای فیزیکی معتبر نیست.")
            mapping = _verified_gold_mode(payload.get("product_key"), "physical")
            try:
                grams = Decimal(adapter._decimal_text(payload.get("grams")))
                if not grams.is_finite() or grams <= 0 or grams > Decimal("10000"):
                    raise InvalidOperation
                _validated_jalali(str(payload.get("date", "")))
            except (InvalidOperation, ValueError):
                raise HTTPException(422, "وزن یا تاریخ معتبر نیست؛ سندی ارسال نشد.")
            before_balance = await adapter.balance_for_customer(token, DATABASE, code)
            if before_balance.get("aggregate_gold_weight") in (None, "") or before_balance.get("noncoin_gold_grams") in (None, ""):
                raise HTTPException(409, "مانده طلای لازم برای کنترل دریافت یا تحویل در پاسخ ته حساب وجود ندارد؛ سندی ارسال نشد.")
            before_gold = Decimal(adapter._decimal_text(before_balance["aggregate_gold_weight"]))
            before_noncoin = Decimal(adapter._decimal_text(before_balance["noncoin_gold_grams"]))
            before_money = Decimal(before_balance["money_rial"])
            sign = Decimal(1) if direction == "incoming" else Decimal(-1)
            expected_gold = before_gold + sign * grams
            expected_noncoin = before_noncoin + sign * grams
            expected_no = ("ورود " if direction == "incoming" else "خروج ") + str(mapping["source_mode_label"])
            header = _jalali_header(code, str(payload.get("date", "")))
            params = header + [float(grams), int(mapping["purity"]), 0, "",
                               0 if direction == "incoming" else 1,
                               int(mapping["source_document_mode"]),
                               str(payload.get("description", ""))[:300], factor, 0, -1,
                               int(mapping.get("metal") or 0), False]
            context = {"pre_write": {"money_rial": str(before_money),
                                     "aggregate_gold_grams": str(before_gold),
                                     "noncoin_gold_grams": str(before_noncoin),
                                     "expected_aggregate_gold_grams": str(expected_gold),
                                     "expected_noncoin_gold_grams": str(expected_noncoin)}}
            journal.update(data.operation_id, "submitting", "در انتظار پاسخ ته حساب و بازخوانی سند.", context)
            submitted = True
            result = await adapter.write_call(token, DATABASE, "gold_receipt", params)
            document = await _verify_document(token, factor, code, direction,
                                              expected_quantity=str(grams), require_effective=True)
            if document and normalize(document.get("NO")) == normalize(expected_no):
                mode_ok = (document.get("IsAbshode") in (True, 1, "1", "true")
                           if mapping["source_document_mode"] == 1
                           else document.get("IsAbshode") in (None, "", False, 0, "0", "false"))
            else:
                mode_ok = False
            after_balance = await adapter.balance_for_customer(token, DATABASE, code)
            try:
                after_money = Decimal(after_balance["money_rial"])
                after_gold = Decimal(adapter._decimal_text(after_balance.get("aggregate_gold_weight")))
                after_noncoin = Decimal(adapter._decimal_text(after_balance.get("noncoin_gold_grams")))
            except (KeyError, InvalidOperation, ValueError, TypeError):
                after_money = after_gold = after_noncoin = None
            verified = bool(document and mode_ok and after_money == before_money
                            and after_gold == expected_gold and after_noncoin == expected_noncoin)
            if verified:
                refreshed = await _refresh_balance_after_write(sid, token, code)
                message = "سند دریافت یا تحویل طلا با نوع، جهت، وزن، شناسه و اثر مانده از منبع تأیید شد."
                if not refreshed:
                    message += " مانده به‌روز نشد."
                journal.update(data.operation_id, "verified", message,
                               {**context, "response": result, "document": document,
                                "balance_after": {"money_rial": str(after_money),
                                                  "aggregate_gold_grams": str(after_gold),
                                                  "noncoin_gold_grams": str(after_noncoin)},
                                "balance_refreshed": refreshed})
                invalidate_document_snapshots(sid, code)
                cache.pop((sid, f"balance:{code}"), None)
            else:
                journal.update(data.operation_id, "uncertain", "سند ارسال شد، اما نوع یا اثر دقیق مانده پس از بازخوانی تأیید نشد؛ ارسال دوباره انجام نمی‌شود.",
                               {**context, "response": result, "document": document,
                                "balance_after": {"money_rial": str(after_money),
                                                  "aggregate_gold_grams": str(after_gold),
                                                  "noncoin_gold_grams": str(after_noncoin)}})
            return _operation_response(journal.get(data.operation_id))

        if data.kind == "trade_gold":
            code = int(payload.get("customer_code"))
            direction = payload.get("direction")
            if direction not in {"customer_buys", "customer_sells"}:
                raise HTTPException(422, "جهت معامله معتبر نیست؛ سندی ارسال نشد.")
            mapping = _verified_gold_mode(payload.get("product_key"), "trade")
            if int(mapping["source_document_mode"]) != 1:
                raise HTTPException(422, "معامله این حالت طلا از بازخوانی ته حساب قابل تشخیص نیست؛ سندی ارسال نشد.")
            try:
                grams = Decimal(adapter._decimal_text(payload.get("grams")))
                if not grams.is_finite() or grams <= 0 or grams > Decimal("10000"):
                    raise InvalidOperation
                price_basis = str(payload.get("price_basis", "mesghal"))
                if price_basis not in {"mesghal", "gram"}:
                    raise InvalidOperation
                rate_rial = _int_amount(payload.get("price_toman"))
                factor_gram = Decimal(str(mapping["mesghal_to_gram_factor"]))
                total_unrounded = (grams * rate_rial / factor_gram) if price_basis == "mesghal" else (grams * rate_rial)
                total_rial = int(total_unrounded.quantize(Decimal("1"), rounding=__import__("decimal").ROUND_HALF_UP))
                if total_rial <= 0 or total_rial > 999999999999999999:
                    raise InvalidOperation
                _validated_jalali(str(payload.get("date", "")))
            except (InvalidOperation, ValueError, TypeError, KeyError):
                raise HTTPException(422, "وزن، مظنه یا تاریخ معتبر نیست؛ سندی ارسال نشد.")
            before_balance = await adapter.balance_for_customer(token, DATABASE, code)
            if before_balance.get("aggregate_gold_weight") in (None, "") or before_balance.get("noncoin_gold_grams") in (None, ""):
                raise HTTPException(409, "مانده طلای لازم برای کنترل معامله در پاسخ ته حساب وجود ندارد؛ سندی ارسال نشد.")
            before_money = Decimal(before_balance["money_rial"])
            before_gold = Decimal(adapter._decimal_text(before_balance["aggregate_gold_weight"]))
            before_noncoin = Decimal(adapter._decimal_text(before_balance["noncoin_gold_grams"]))
            sign = Decimal(1) if direction == "customer_buys" else Decimal(-1)
            if direction == "customer_sells" and (before_gold - grams <= 0 or before_noncoin - grams < 0):
                raise HTTPException(409, "مانده طلای آبشده برای این معامله کافی نیست؛ سندی ارسال نشد.")
            signed_total = -total_rial if direction == "customer_buys" else total_rial
            expected_money = before_money + signed_total
            expected_gold = before_gold + sign * grams
            expected_noncoin = before_noncoin + sign * grams
            header = _jalali_header(code, str(payload.get("date", "")))
            params = header + [float(grams), int(mapping["purity"]), 0, "",
                               1 if direction == "customer_buys" else 0,
                               rate_rial, 0 if price_basis == "mesghal" else 1,
                               int(mapping["source_document_mode"]), total_rial,
                               str(payload.get("description", ""))[:300], factor,
                               0, -1, int(payload.get("metal", 0)), ""]
            context = {"pre_write": {"money_rial": str(before_money),
                                     "aggregate_gold_grams": str(before_gold),
                                     "noncoin_gold_grams": str(before_noncoin),
                                     "expected_money_rial": str(expected_money),
                                     "expected_aggregate_gold_grams": str(expected_gold),
                                     "expected_noncoin_gold_grams": str(expected_noncoin)}}
            journal.update(data.operation_id, "submitting", "در انتظار پاسخ ته حساب و بازخوانی سند.", context)
            submitted = True
            result = await adapter.write_call(token, DATABASE, "trade_gold", params)
            document = await _verify_document(token, factor, code, direction,
                                              expected_weight=str(grams),
                                              expected_unit_price=rate_rial,
                                              expected_signed_amount=signed_total,
                                              require_effective=True)
            expected_rate_type = "mesghal" if price_basis == "mesghal" else "gram"
            verified = bool(document and document.get("IsAbshode") in (True, 1, "1", "true")
                            and normalize(document.get("MazanehType")) == expected_rate_type)
            after_balance = await adapter.balance_for_customer(token, DATABASE, code)
            try:
                after_money = Decimal(after_balance["money_rial"])
                after_gold = Decimal(adapter._decimal_text(after_balance.get("aggregate_gold_weight")))
                after_noncoin = Decimal(adapter._decimal_text(after_balance.get("noncoin_gold_grams")))
            except (KeyError, InvalidOperation, ValueError, TypeError):
                after_money = after_gold = after_noncoin = None
            verified = bool(verified and after_money == expected_money
                            and after_gold == expected_gold and after_noncoin == expected_noncoin)
            if verified:
                refreshed = await _refresh_balance_after_write(sid, token, code)
                message = "معامله آبشده با جهت، وزن، عیار، واحد قیمت، مبلغ، شناسه سند و اثر مانده از منبع تأیید شد."
                if not refreshed:
                    message += " مانده به‌روز نشد."
                journal.update(data.operation_id, "verified", message,
                               {**context, "response": result, "document": document,
                                "balance_after": {"money_rial": str(after_money),
                                                  "aggregate_gold_grams": str(after_gold),
                                                  "noncoin_gold_grams": str(after_noncoin)},
                                "balance_refreshed": refreshed})
                invalidate_document_snapshots(sid, code)
                cache.pop((sid, f"balance:{code}"), None)
            else:
                journal.update(data.operation_id, "uncertain", "معامله ارسال شد، اما نوع یا اثر دقیق مانده پس از بازخوانی تأیید نشد؛ ارسال دوباره انجام نمی‌شود.",
                               {**context, "response": result, "document": document,
                                "balance_after": {"money_rial": str(after_money),
                                                  "aggregate_gold_grams": str(after_gold),
                                                  "noncoin_gold_grams": str(after_noncoin)}})
            return _operation_response(journal.get(data.operation_id))

        if data.kind in {"physical_coin", "physical_bar"}:
            code = int(payload.get("customer_code"))
            direction = payload.get("direction")
            family = "coin" if data.kind == "physical_coin" else "bar"
            if direction not in {"incoming", "outgoing"}:
                raise HTTPException(422, "جهت دریافت یا تحویل کالا معتبر نیست.")
            mapping = _verified_mapping(payload.get("product_key"), family)
            try:
                count_decimal = Decimal(adapter._decimal_text(payload.get("count")))
                if not count_decimal.is_finite() or count_decimal != count_decimal.to_integral_value() or not 1 <= count_decimal <= 100:
                    raise InvalidOperation
                count = int(count_decimal)
                year, month, day = _validated_jalali(str(payload.get("date", "")))
                weight = Decimal(str(mapping["source_weight"])) * count
                if not weight.is_finite() or weight <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                raise HTTPException(422, "تعداد یا تاریخ معتبر نیست؛ سندی ارسال نشد.")
            source_name = str(mapping["source_identifier"])
            try:
                before = await _product_balance(token, code, source_name)
            except HTTPException as exc:
                journal.update(data.operation_id, "failed", str(exc.detail))
                raise
            after_expected = before + count if direction == "incoming" else before - count
            if direction == "outgoing" and after_expected <= 0:
                journal.update(data.operation_id, "failed", "برای تأیید مانده، موجودی منبع باید پس از تحویل همچنان مثبت بماند؛ سندی ارسال نشد.")
                raise HTTPException(409, "موجودی برای تحویل این تعداد کافی نیست یا پاسخ منبع پس از صفرشدن فیلد را حذف می‌کند.")
            header = _jalali_header(code, str(payload.get("date", "")))
            params = header + [float(weight), int(mapping["purity"]), count, source_name,
                               0 if direction == "incoming" else 1,
                               str(payload.get("description", ""))[:300], factor,
                               -1, -1, int(mapping.get("metal") or 0)]
            context = {"pre_write": {"product_count": str(before), "expected_product_count": str(after_expected)}}
            journal.update(data.operation_id, "submitting", "در انتظار پاسخ ته حساب و بازخوانی سند.", context)
            submitted = True
            result = await adapter.write_call(token, DATABASE, data.kind, params)
            verified = await _verify_document(
                token, factor, code, direction, expected_count=count,
                expected_weight=str(weight), require_effective=True)
            after = await _product_balance(token, code, source_name)
            if verified and after == after_expected:
                balance_refreshed = await _refresh_balance_after_write(sid, token, code)
                message = "سند فیزیکی با مشتری، شناسه و ردیف، جهت، تعداد، وزن و تغییر مانده محصول از منبع تأیید شد."
                if not balance_refreshed:
                    message += " مانده کامل به‌روز نشد."
                journal.update(data.operation_id, "verified", message,
                               {**context, "response": result, "document": verified,
                                "balance_before": str(before), "balance_after": str(after),
                                "balance_refreshed": balance_refreshed})
                invalidate_document_snapshots(sid, code)
                cache.pop((sid, f"balance:{code}"), None)
            else:
                journal.update(data.operation_id, "uncertain", "سند ارسال شد، اما شناسه یا اثر دقیق مانده محصول تأیید نشد. ارسال دوباره انجام نمی‌شود.",
                               {**context, "response": result, "document": verified,
                                "balance_before": str(before), "balance_after": str(after)})
            return _operation_response(journal.get(data.operation_id))

        if data.kind in {"trade_coin", "trade_bar"}:
            code = int(payload.get("customer_code"))
            direction = payload.get("direction")
            family = "coin" if data.kind == "trade_coin" else "bar"
            if direction not in {"customer_buys", "customer_sells"}:
                raise HTTPException(422, "جهت معامله معتبر نیست.")
            mapping = _verified_mapping(payload.get("product_key"), family)
            try:
                count_decimal = Decimal(adapter._decimal_text(payload.get("count")))
                if not count_decimal.is_finite() or count_decimal != count_decimal.to_integral_value() or not 1 <= count_decimal <= 100:
                    raise InvalidOperation
                count = int(count_decimal)
                unit_price_rial = _int_amount(payload.get("unit_price_toman"))
                total_rial = unit_price_rial * count
                if total_rial > 999999999999999999:
                    raise InvalidOperation
                _validated_jalali(str(payload.get("date", "")))
                weight = Decimal(str(mapping["source_weight"])) * count
                if not weight.is_finite() or weight <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                raise HTTPException(422, "تعداد، قیمت یا تاریخ معتبر نیست؛ سندی ارسال نشد.")
            source_name = str(mapping["source_identifier"])
            try:
                before = await _product_balance(token, code, source_name)
            except HTTPException as exc:
                journal.update(data.operation_id, "failed", str(exc.detail))
                raise
            after_expected = before + count if direction == "customer_buys" else before - count
            if direction == "customer_sells" and after_expected <= 0:
                journal.update(data.operation_id, "failed", "برای تأیید مانده، موجودی منبع باید پس از معامله همچنان مثبت بماند؛ سندی ارسال نشد.")
                raise HTTPException(409, "موجودی کافی نیست یا پاسخ منبع پس از صفرشدن فیلد را حذف می‌کند.")
            money_before = None
            try:
                balance = await adapter.balance_for_customer(token, DATABASE, code)
                money_before = Decimal(balance["money_rial"])
            except (adapter.TahesabFailure, KeyError, InvalidOperation):
                journal.update(data.operation_id, "failed", "مانده پولی الزامی برای کنترل معامله از منبع دریافت نشد؛ معامله ارسال نشد.")
                raise HTTPException(409, "مانده پولی الزامی برای کنترل معامله در پاسخ ته حساب موجود نیست.")
            year, month, day = _validated_jalali(str(payload.get("date", "")))
            header = _jalali_header(code, str(payload.get("date", "")))
            is_sale = direction == "customer_buys"
            signed_total = -total_rial if is_sale else total_rial
            money_expected = money_before + signed_total
            params = header + [float(weight), int(mapping["purity"]), count, source_name,
                               1 if is_sale else 0, unit_price_rial, total_rial,
                               str(payload.get("description", ""))[:300], factor,
                               -1, -1, int(mapping.get("metal") or 0), "IRR"]
            context = {"pre_write": {"money_rial": str(money_before), "product_count": str(before),
                                     "expected_money_rial": str(money_expected),
                                     "expected_product_count": str(after_expected)}}
            journal.update(data.operation_id, "submitting", "در انتظار پاسخ ته حساب و بازخوانی سند.", context)
            submitted = True
            result = await adapter.write_call(token, DATABASE, data.kind, params)
            verified = await _verify_document(
                token, factor, code, direction, expected_amount=total_rial,
                expected_count=count, expected_weight=str(weight),
                expected_unit_price=unit_price_rial, expected_signed_amount=signed_total,
                require_effective=True)
            after = await _product_balance(token, code, source_name)
            after_balance = await adapter.balance_for_customer(token, DATABASE, code)
            money_after = Decimal(after_balance["money_rial"])
            if verified and after == after_expected and money_after == money_expected:
                balance_refreshed = await _refresh_balance_after_write(sid, token, code)
                message = "معامله با جهت، تعداد، وزن، قیمت واحد، مبلغ کل، شناسه سند و اثر مانده پول و محصول تأیید شد."
                if not balance_refreshed:
                    message += " مانده کامل به‌روز نشد."
                journal.update(data.operation_id, "verified", message,
                               {**context, "response": result, "document": verified,
                                "balance_before": {"money_rial": str(money_before), "product_count": str(before)},
                                "balance_after": {"money_rial": str(money_after), "product_count": str(after)},
                                "balance_refreshed": balance_refreshed})
                invalidate_document_snapshots(sid, code)
                cache.pop((sid, f"balance:{code}"), None)
            else:
                journal.update(data.operation_id, "uncertain", "سند معامله ارسال شد، اما بازخوانی سند یا اثر دقیق مانده پول/محصول تأیید نشد. ارسال دوباره انجام نمی‌شود.",
                               {**context, "response": result, "document": verified,
                                "balance_before": {"money_rial": str(money_before), "product_count": str(before)},
                                "balance_after": {"money_rial": str(money_after), "product_count": str(after)}})
            return _operation_response(journal.get(data.operation_id))

        if data.kind == "gold_receipt_750":
            code = int(payload.get("customer_code"))
            grams = Decimal(str(payload.get("grams", "")))
            if not grams.is_finite() or grams <= 0 or grams > Decimal("10000"):
                raise HTTPException(422, "وزن گرم معتبر نیست.")
            header = _jalali_header(code, str(payload.get("date", "")))
            params = header + [float(grams), 750, 0, "Kariz", 0, 0,
                               str(payload.get("description", ""))[:300], factor, 0, -1, 0, False]
            submitted = True
            result = await adapter.write_call(token, DATABASE, "gold_receipt", params)
            verified = await _verify_document(token, factor, code, "incoming", expected_quantity=str(grams))
            if verified:
                balance_refreshed = await _refresh_balance_after_write(sid, token, code)
                message = "دریافت فیزیکی با کد پیگیری، جهت و وزن بازخوانی و تأیید شد."
                if not balance_refreshed:
                    message += " مانده به‌روز نشد؛ پاسخ منبع فیلد الزامی مبلغ را نداشت."
                journal.update(data.operation_id, "verified", message, {"response": result, "document": verified, "balance_refreshed": balance_refreshed})
                invalidate_document_snapshots(sid, code)
                cache.pop((sid, f"balance:{code}"), None)
            else:
                journal.update(data.operation_id, "uncertain", "ارسال انجام شد، اما بازخوانی منبع هنوز سند را کامل تأیید نکرده است.", result)
            return _operation_response(journal.get(data.operation_id))

        journal.update(data.operation_id, "failed", "این عملیات فعال نیست؛ قرارداد منبع هنوز تأیید نشده است.")
        raise HTTPException(409, "این عملیات فعال نیست؛ قرارداد منبع یا نگاشت محصول هنوز تأیید نشده است.")
    except adapter.TahesabWriteUncertain:
        journal.update(data.operation_id, "uncertain", "نتیجه ثبت نامشخص است. ارسال دوباره انجام نشد؛ عملیات را از منبع بررسی کنید.", edit_context)
        return _operation_response(journal.get(data.operation_id))
    except HTTPException as exc:
        if submitted:
            journal.update(data.operation_id, "uncertain", "ارسال انجام شد، اما بازخوانی یا بررسی کامل نشد. ارسال دوباره انجام نمی‌شود.", edit_context)
        else:
            journal.update(data.operation_id, "failed", str(exc.detail))
        raise
    except adapter.TahesabFailure:
        if submitted:
            journal.update(data.operation_id, "uncertain", "ارسال انجام شد، اما بازخوانی منبع ناموفق بود. ارسال دوباره انجام نمی‌شود.", edit_context)
            return _operation_response(journal.get(data.operation_id))
        journal.update(data.operation_id, "failed", "منبع درخواست را نپذیرفت.")
        raise HTTPException(502, "ته حساب درخواست را نپذیرفت. نتیجه را در صفحه عملیات بررسی کنید.")


@app.post("/api/operations/{operation_id}/verify")
async def verify_operation(operation_id: str, request: Request):
    sid, token = require_session(request)
    row = journal.get(operation_id)
    if not row:
        raise HTTPException(404, "عملیات محلی پیدا نشد.")
    if row["status"] not in {"uncertain", "submitting"}:
        return _operation_response(row)
    payload = json.loads(row["payload"])
    if row["kind"] == "customer_create":
        phone = str(payload.get("mobile", "")).strip()
        if not phone:
            return _operation_response(journal.update(operation_id, "uncertain", "برای بررسی بی‌خطر مشتری، شماره تماس ثبت نشده؛ نتیجه نامشخص می‌ماند."))
        invalidate_customer_snapshots(sid, -1)
        matches = await adapter.customer_by_phone(token, DATABASE, phone)
        exact = [r for r in matches if normalize(r.get("Name")) == normalize(payload.get("full_name"))]
        if len(exact) == 1:
            journal.update(operation_id, "verified", "یک مشتری با نام و شماره یکسان از منبع پیدا شد.", exact[0])
            invalidate_customer_snapshots(sid, customer_code(exact[0]) or -1)
        return _operation_response(journal.get(operation_id))
    if row["kind"] == "customer_edit":
        saved = json.loads(row.get("source_response") or "{}")
        context = saved.get("pre_write") or {}
        if not context.get("expected"):
            return _operation_response(journal.update(operation_id, "uncertain", "اطلاعات پیش از ویرایش برای بررسی کامل موجود نیست؛ درخواست دوباره ارسال نشد.", saved))
        fields = {
            "full_name": ("Name",),
            "group_name": ("GoroupName",),
            "mobile": ("Mobile",),
            "address": ("Address",),
            "national_id": ("CodeMelli",),
        }
        try:
            code = int(payload["customer_code"])
            rows = await adapter.customer_by_code(token, DATABASE, code)
        except (KeyError, TypeError, ValueError, adapter.TahesabFailure):
            return _operation_response(journal.update(operation_id, "uncertain", "بازخوانی مشتری ممکن نشد؛ نتیجه نیازمند بررسی است و درخواست دوباره ارسال نشد.", saved))
        customer = next((item for item in rows if adapter.customer_code(item) == code), None)
        if (customer and all(normalize(_source_customer_value(customer, fields[key])) == normalize(value)
                            for key, value in context["expected"].items() if key in fields)
                and all(key in customer and normalize(customer[key]) == normalize(value)
                        for key, value in context.get("preserved", {}).items())):
            journal.update(operation_id, "verified", "تغییرات مشتری از ته حساب بازخوانی و تأیید شد.",
                           {**saved, "customer": customer})
            invalidate_customer_snapshots(sid, code)
        return _operation_response(journal.get(operation_id))
    if row["kind"] in {"physical_coin", "physical_bar", "trade_coin", "trade_bar"}:
        try:
            payload = json.loads(row["payload"])
            saved = json.loads(row.get("source_response") or "{}")
            context = saved.get("pre_write") or {}
            code = int(payload["customer_code"])
            family = "coin" if row["kind"].endswith("coin") else "bar"
            mapping = _verified_mapping(payload.get("product_key"), family)
            count = int(payload["count"])
            weight = str(Decimal(str(mapping["source_weight"])) * count)
            if row["kind"].startswith("physical_"):
                before = Decimal(context["product_count"])
                expected = Decimal(context["expected_product_count"])
                direction = payload["direction"]
                document = await _verify_document(token, row["factor_code"], code, direction,
                                                  expected_count=count, expected_weight=weight,
                                                  require_effective=True)
                after = await _product_balance(token, code, str(mapping["source_identifier"]))
                if document and after == expected:
                    await _refresh_balance_after_write(sid, token, code)
                    invalidate_document_snapshots(sid, code)
                    cache.pop((sid, f"balance:{code}"), None)
                    return _operation_response(journal.update(operation_id, "verified", "سند فیزیکی و اثر دقیق مانده محصول پس از بررسی مجدد تأیید شد.",
                                                              {**saved, "document": document, "balance_before": str(before), "balance_after": str(after)}))
            else:
                before_product = Decimal(context["product_count"])
                expected_product = Decimal(context["expected_product_count"])
                before_money = Decimal(context["money_rial"])
                expected_money = Decimal(context["expected_money_rial"])
                direction = payload["direction"]
                unit_price = _int_amount(payload.get("unit_price_toman"))
                total = unit_price * count
                signed_total = -total if direction == "customer_buys" else total
                document = await _verify_document(token, row["factor_code"], code, direction,
                                                  expected_amount=total, expected_count=count,
                                                  expected_weight=weight, expected_unit_price=unit_price,
                                                  expected_signed_amount=signed_total, require_effective=True)
                after_product = await _product_balance(token, code, str(mapping["source_identifier"]))
                after_balance = await adapter.balance_for_customer(token, DATABASE, code)
                after_money = Decimal(after_balance["money_rial"])
                if document and after_product == expected_product and after_money == expected_money:
                    await _refresh_balance_after_write(sid, token, code)
                    invalidate_document_snapshots(sid, code)
                    cache.pop((sid, f"balance:{code}"), None)
                    details = {**saved, "document": document,
                               "balance_before": {"money_rial": str(before_money), "product_count": str(before_product)},
                               "balance_after": {"money_rial": str(after_money), "product_count": str(after_product)}}
                    return _operation_response(journal.update(operation_id, "verified", "سند معامله و اثر دقیق مانده پول و محصول پس از بررسی مجدد تأیید شد.", details))
        except (KeyError, TypeError, ValueError, InvalidOperation, adapter.TahesabFailure, HTTPException):
            pass
        return _operation_response(journal.update(operation_id, "uncertain", "بازخوانی هنوز سند و اثر مانده را هم‌زمان ثابت نکرد؛ سند دوباره ارسال نشد."))
    if row["kind"] in {"physical_gold", "trade_gold"}:
        try:
            code = int(payload["customer_code"])
            saved = json.loads(row.get("source_response") or "{}")
            context = saved.get("pre_write") or {}
            mode = _verified_gold_mode(payload.get("product_key"), "physical" if row["kind"] == "physical_gold" else "trade")
            grams = Decimal(adapter._decimal_text(payload.get("grams")))
            before_money = Decimal(adapter._decimal_text(context["money_rial"]))
            before_aggregate = Decimal(adapter._decimal_text(context["aggregate_gold_grams"]))
            before_noncoin = Decimal(adapter._decimal_text(context["noncoin_gold_grams"]))
            direction = payload["direction"]
            if row["kind"] == "physical_gold":
                signed = grams if direction == "incoming" else -grams
                expected_money = before_money
                expected_amount = None
                expected_signed_amount = None
                unit_price = None
            else:
                rate = _int_amount(payload["price_toman"])
                basis = payload["price_basis"]
                factor = Decimal(str(mode["mesghal_to_gram_factor"])) if basis == "mesghal" else Decimal(1)
                amount = int((grams * Decimal(rate) / factor).quantize(Decimal("1"), rounding=__import__("decimal").ROUND_HALF_UP))
                expected_signed_amount = -amount if direction == "customer_buys" else amount
                expected_amount = amount
                unit_price = rate
                signed = grams if direction == "customer_sells" else -grams
                expected_money = before_money + expected_signed_amount
            expected_aggregate = before_aggregate + signed
            expected_noncoin = before_noncoin + signed
            document = await _verify_document(token, row["factor_code"], code, direction,
                                              expected_amount=expected_amount,
                                              expected_quantity=str(grams),
                                              expected_signed_amount=expected_signed_amount,
                                              expected_unit_price=unit_price,
                                              require_effective=True)
            is_abshode = document and document.get("IsAbshode") in (True, 1, "1", "true")
            expected_no = (("ورود " if direction == "incoming" else "خروج ") + str(mode["source_mode_label"])) if row["kind"] == "physical_gold" else None
            expected_rate_type = ("Mesghal" if payload.get("price_basis") == "mesghal" else "Gram") if row["kind"] == "trade_gold" else None
            document_matches = bool(document and is_abshode == (mode["source_document_mode"] == 1)
                                    and (expected_no is None or normalize(document.get("NO")) == normalize(expected_no))
                                    and (expected_rate_type is None or normalize(document.get("MazanehType")) == normalize(expected_rate_type)))
            if document_matches:
                after = await adapter.balance_for_customer(token, DATABASE, code)
                after_money = Decimal(adapter._decimal_text(after["money_rial"]))
                after_aggregate = Decimal(adapter._decimal_text(after["aggregate_gold_weight"]))
                after_noncoin = Decimal(adapter._decimal_text(after["noncoin_gold_grams"]))
                if after_money == expected_money and after_aggregate == expected_aggregate and after_noncoin == expected_noncoin:
                    await _refresh_balance_after_write(sid, token, code)
                    invalidate_document_snapshots(sid, code)
                    return _operation_response(journal.update(operation_id, "verified", "سند طلا از منبع بازخوانی و اثر مانده تأیید شد.",
                                                              {**saved, "document": document, "balance_after": after}))
        except (KeyError, TypeError, ValueError, InvalidOperation, adapter.TahesabFailure, HTTPException):
            pass
        return _operation_response(journal.update(operation_id, "uncertain", "بازخوانی سند طلا و اثر مانده هنوز هم‌زمان تأیید نشد؛ سند دوباره ارسال نشد."))
    if row.get("factor_code"):
        try:
            code = int(payload.get("customer_code"))
            direction = "incoming" if payload.get("direction") == "deposit" or row["kind"] == "gold_receipt_750" else "outgoing"
            amount = _int_amount(payload.get("amount_toman")) if row["kind"] in {"cash_payment", "bank_payment"} else None
            document = await _verify_document(
                token, row["factor_code"], code, direction, amount,
                str(payload.get("grams")) if row["kind"] == "gold_receipt_750" else None,
                expected_bank=payload.get("bank_name") if row["kind"] == "bank_payment" else None,
                expected_reference=payload.get("reference") if row["kind"] == "bank_payment" else None)
            if document:
                balance_refreshed = await _refresh_balance_after_write(sid, token, code)
                return _operation_response(journal.update(operation_id, "verified", "سند از منبع بازخوانی و تأیید شد." + (" مانده به‌روز نشد." if not balance_refreshed else ""), {"document": document, "balance_refreshed": balance_refreshed}))
        except (adapter.TahesabFailure, HTTPException, ValueError):
            pass
    return _operation_response(journal.update(operation_id, "uncertain", "سندی که نتیجه را قطعی کند پیدا نشد؛ عملیات دوباره ارسال نشد."))


@app.get("/api/operations")
async def operations(request: Request):
    require_session(request)
    client_kinds = {"customer_create", "customer_edit", "cash_payment", "bank_payment", "gold_receipt_750", "physical_gold", "trade_gold",
                    "physical_coin", "physical_bar", "trade_coin", "trade_bar"}
    rows = [row for row in journal.list_recent() if row.get("kind") in client_kinds]
    return {"items": [_operation_response(row) for row in rows], "note": "ثبت محلی؛ نتیجه نامشخص هیچ‌گاه خودکار دوباره ارسال نمی‌شود."}


@app.exception_handler(TahesabFailure)
async def handle_upstream_failure(_: Request, exc: TahesabFailure):
    return JSONResponse({"detail": "ارتباط با ته حساب برقرار نشد یا پاسخ آن قابل استفاده نبود."}, status_code=502)


FRONTEND_DIST = ROOT / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

