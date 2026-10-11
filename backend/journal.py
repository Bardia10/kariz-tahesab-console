"""Durable, single-instance journal for source writes; credentials are never stored here."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(__import__("os").getenv("OPERATION_DB", str(ROOT / ".local" / "operations.sqlite3")))

def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS operations (
      operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
      fingerprint TEXT NOT NULL, payload TEXT NOT NULL, factor_code TEXT,
      source_response TEXT, message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    return conn

def factor_code() -> str:
    # Tahesab's documented Factor_Code is 31 numeric characters.
    return f"{int(time.time() * 1000):013d}{secrets.randbelow(10**18):018d}"

def reserve(operation_id: str, kind: str, payload: dict[str, Any], factor: str | None):
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with closing(_db()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise ValueError("operation_id_conflict")
            return dict(old), False
        conn.execute("INSERT INTO operations(operation_id,kind,status,fingerprint,payload,factor_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     (operation_id, kind, "submitting", fingerprint, serialized, factor, now, now))
        return dict(conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()), True

def update(operation_id: str, status: str, message: str, response: Any = None):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    safe = json.dumps(response, ensure_ascii=False, default=str) if response is not None else None
    with closing(_db()) as conn, conn:
        conn.execute("UPDATE operations SET status=?,message=?,source_response=?,updated_at=? WHERE operation_id=?",
                     (status, message, safe, now, operation_id))
        return dict(conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone())

def get(operation_id: str):
    with closing(_db()) as conn, conn:
        row = conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        return dict(row) if row else None

def list_recent(limit: int = 100):
    with closing(_db()) as conn, conn:
        return [dict(row) for row in conn.execute("SELECT * FROM operations ORDER BY created_at DESC LIMIT ?", (limit,))]

def public(row: dict[str, Any]):
    payload = json.loads(row["payload"])
    response = json.loads(row["source_response"]) if row.get("source_response") else None
    return {"operation_id": row["operation_id"], "kind": row["kind"], "status": row["status"],
            "payload": payload, "factor_code": row.get("factor_code"), "message": row.get("message"),
            "source_response": response, "created_at": row["created_at"], "updated_at": row["updated_at"]}

def recover_pending():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with closing(_db()) as conn, conn:
        conn.execute("UPDATE operations SET status='uncertain', message='برنامه پیش از تأیید نتیجه متوقف شده بود؛ پیش از هر اقدام دوباره بررسی منبع لازم است.', updated_at=? WHERE status='submitting'", (now,))
