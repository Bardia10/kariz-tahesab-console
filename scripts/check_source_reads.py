"""Sanitized read-contract check against the already configured Tahesab test server."""
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
    token, db = cfg.get("tahesab_token") or "", cfg.get("tahesab_dbname") or "TahesabDB"
    if host != "https://api-test.tahesab.ir" or not token:
        print(json.dumps({"configured_test_server": host == "https://api-test.tahesab.ir", "token_present": bool(token)}))
        return
    headers = {"Authorization": "Bearer " + token, "DBname": db, "Content-Type": "application/json"}
    out = ROOT / ".local"
    async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
        customers = await client.post(host + "/DoListMoshtari-ByCode", headers=headers, json={"Dolistmoshtari": [1, 250]})
        body = customers.json()
        (out / "customer-range-check.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        rows = body if isinstance(body, list) else body.get("value", body.get("data", body)) if isinstance(body, dict) else []
        if isinstance(rows, dict): rows = list(rows.values())
        rows = [x for x in rows if isinstance(x, dict)]
        codes = [x.get("Code", x.get("MCode")) for x in rows if x.get("Code", x.get("MCode")) not in (None, "")]
        summary = {"customer_range_http": customers.status_code, "customer_rows": len(rows), "codes_available": len(codes)}
        if codes:
            code = int(codes[0])
            d0 = await client.post(host + "/DoListAsnad", headers=headers, json={"DoListAsnad": [0, code, "", "", "", 0]})
            d1 = await client.post(host + "/DoListAsnad", headers=headers, json={"DoListAsnad": [1, code, "", "", "", 0]})
            b0, b1 = d0.json(), d1.json()
            (out / "documents-count-zero-check.json").write_text(json.dumps(b0, ensure_ascii=False), encoding="utf-8")
            (out / "documents-count-one-check.json").write_text(json.dumps(b1, ensure_ascii=False), encoding="utf-8")
            def shape(x):
                vals = list(x.values()) if isinstance(x, dict) and x and all(isinstance(v, dict) for v in x.values()) else x if isinstance(x, list) else []
                m_codes = {str(r.get("MCode", r.get("Code", ""))) for r in vals if isinstance(r, dict)}
                return {"kind": type(x).__name__, "rows": len(x) if isinstance(x, (list, dict)) else None,
                        "keys": list(x)[:10] if isinstance(x, dict) else None,
                        "customer_rows_match_requested_code": bool(m_codes) and m_codes == {str(code)}}
            summary.update({"docs_zero_http": d0.status_code, "docs_zero": shape(b0), "docs_one_http": d1.status_code, "docs_one": shape(b1)})
        else:
            summary["documents_check"] = "skipped: no customer in bounded range"
        try:
            assay = await client.post(host + "/DoListNameReygiri", headers=headers, json={"DoListNameReygiri": []})
            assay_body = assay.json()
            (out / "DoListNameReygiri.json").write_text(json.dumps(assay_body, ensure_ascii=False), encoding="utf-8")
            summary["assay_list"] = {"http": assay.status_code, "rows": len(assay_body) if isinstance(assay_body, (list, dict)) else None}
        except Exception as exc:
            summary["assay_list"] = {"failure_type": type(exc).__name__}
        print(json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
