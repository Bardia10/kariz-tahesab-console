"""Compare source balance envelopes for the controlled test customer and an existing account."""
import asyncio
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend" / "main-backend-opencode"

async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(BACKEND / ".env").items()}
    host = str(cfg.get("tahesab_base_url") or "https://api-test.tahesab.ir").rstrip("/")
    headers = {"Authorization": "Bearer " + (cfg.get("tahesab_token") or ""), "DBname": cfg.get("tahesab_dbname") or "TahesabDB"}
    result = {}
    async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
        for label, code in (("controlled_test_account", 1891), ("existing_sample_account", 1)):
            response = await client.post(host + "/GetMandeHesabByCode", headers=headers,
                                         json={"getmandehesabbycode": [str(code)]})
            try: body = response.json()
            except ValueError: body = None
            inner = body.get("MandeHesab", body) if isinstance(body, dict) else body
            rows = inner if isinstance(inner, list) else list(inner.values()) if isinstance(inner, dict) and inner and all(isinstance(v, dict) for v in inner.values()) else [inner]
            row = next((x for x in rows if isinstance(x, dict)), {})
            (ROOT / ".local" / f"balance-{label}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            result[label] = {"http": response.status_code, "keys": list(row)[:20],
                             "money_field_present": row.get("MandeyeMali") not in (None, ""),
                             "error_field": bool(isinstance(body, dict) and body.get("ERROR"))}
    print(json.dumps(result, ensure_ascii=True))

if __name__ == "__main__": asyncio.run(main())
