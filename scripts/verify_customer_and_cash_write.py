"""Controlled test-server write check for customer creation and a balanced cash pair."""
import asyncio
import json
import sys
import uuid
from pathlib import Path
import httpx
import jdatetime
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend" / "main-backend-opencode"
sys.path.insert(0, str(ROOT))

async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(BACKEND / ".env").items()}
    host = str(cfg.get("tahesab_base_url") or "https://api-test.tahesab.ir").rstrip("/")
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or "TahesabDB"
    if host != "https://api-test.tahesab.ir" or not token:
        print(json.dumps({"configured_test_server": host == "https://api-test.tahesab.ir", "token_present": bool(token)}))
        return
    groups = json.loads((ROOT / ".local" / "DoListGorooh.json").read_text(encoding="utf-8"))
    group_rows = list(groups.values()) if isinstance(groups, dict) else groups
    group = group_rows[0].get("Name") or group_rows[0].get("Gorooh") or group_rows[0].get("GroupName")
    if not group:
        print(json.dumps({"customer_create": "not_run: source group name unavailable"}))
        return
    today = jdatetime.date.today()
    date = f"{today.year:04d}/{today.month:02d}/{today.day:02d}"
    full_name = "کنسول آزمایشی " + uuid.uuid4().hex[:12]
    records = []
    transport = httpx.ASGITransport(app=__import__("backend.main", fromlist=["app"]).app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as local:
        connected = await local.post("/api/connect", headers={"Origin": "http://testserver"}, json={"token": token})
        if connected.status_code != 200:
            print(json.dumps({"connect_http": connected.status_code, "customer_create": "not_run"}))
            return
        customer_op = {"operation_id": str(uuid.uuid4()), "kind": "customer_create",
                       "payload": {"full_name": full_name, "group_name": group, "mobile": "",
                                   "address": "", "national_id": "", "birthdate": ""}}
        created = await local.post("/api/operations", headers={"Origin": "http://testserver"}, json=customer_op)
        customer_result = created.json()
        records.append(customer_result)
        customer = (customer_result.get("source_response") or {}).get("customer") or {}
        code = customer.get("Code", customer.get("MCode"))
        summary = {"connect_http": connected.status_code, "customer_http": created.status_code,
                   "customer_status": customer_result.get("status"), "customer_code_present": code is not None}
        if created.status_code != 200 or customer_result.get("status") != "verified" or code is None:
            (ROOT / ".local" / "live-write-check.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False))
            return
        code = int(code)
        amount = 1
        for direction in ("deposit", "payout"):
            op = {"operation_id": str(uuid.uuid4()), "kind": "cash_payment",
                  "payload": {"customer_code": code, "direction": direction, "amount_toman": amount,
                              "date": date, "description": "کنسول آزمایشی؛ بررسی قرارداد نقدی"}}
            response = await local.post("/api/operations", headers={"Origin": "http://testserver"}, json=op)
            item = response.json()
            records.append(item)
            if response.status_code != 200 or item.get("status") != "verified":
                summary[f"cash_{direction}"] = item.get("status", "error")
                break
            summary[f"cash_{direction}"] = "verified"
        try:
            balance = await local.get(f"/api/customers/{code}/balance?refresh=true")
            data = balance.json()
            summary["final_balance_http"] = balance.status_code
            summary["final_money_rial_is_zero"] = data.get("money_rial") == "0"
        except Exception as exc:
            summary["final_balance_error_type"] = type(exc).__name__
        summary["test_customer_code"] = code
        (ROOT / ".local" / "live-write-check.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
