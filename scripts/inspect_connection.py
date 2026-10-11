"""Read the existing local test configuration without printing credentials."""
import asyncio
import json
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend" / "main-backend-opencode"

async def main():
    config = {str(k).lower(): v for k, v in dotenv_values(BACKEND / ".env").items()}
    host = str(config.get("tahesab_base_url") or "https://api-test.tahesab.ir").rstrip("/")
    token = config.get("tahesab_token") or ""
    database = config.get("tahesab_dbname") or "TahesabDB"
    print(json.dumps({"configured_host_is_test": host == "https://api-test.tahesab.ir", "token_present": bool(token), "database": database}))
    if host != "https://api-test.tahesab.ir" or not token:
        return
    output = ROOT / ".local"
    output.mkdir(exist_ok=True)
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        headers = {"Authorization": "Bearer " + token, "DBname": database}
        for path, key in [("DoListNameARZ", "DoListNameARZ"), ("DoListNameSekeh", "DoListNameSekeh"), ("DoListNameShemsh", "DoListNameShemsh"), ("DoListGorooh", "DoListGorooh"), ("DoListHesabBanki", "DoListHesabBanki")]:
            try:
                response = await client.post(host + "/" + path, headers=headers, json={key: []})
                body = response.json()
                summary = {"operation": path, "http": response.status_code, "shape": type(body).__name__, "keys": list(body)[:12] if isinstance(body, dict) else None, "length": len(body) if isinstance(body, (dict, list)) else None, "business_error": bool(isinstance(body, dict) and body.get("ERROR"))}
                print(json.dumps(summary, ensure_ascii=False))
                if response.status_code == 200 and not summary["business_error"]:
                    (output / (path + ".json")).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                print(json.dumps({"operation": path, "failure_type": type(exc).__name__}))
        try:
            response = await client.post("http://127.0.0.1:8100/admin/auth/login", json={"username": config.get("admin_username") or "admin", "password": config.get("admin_password") or "admin1234"})
            admin_token = response.json().get("access_token")
            if not admin_token:
                print(json.dumps({"kariz_login_http": response.status_code, "authenticated": False}))
            else:
                catalogue = await client.get("http://127.0.0.1:8100/admin/products", headers={"Authorization": "Bearer " + admin_token})
                print(json.dumps({"kariz_catalogue_http": catalogue.status_code, "items": len(catalogue.json()) if catalogue.status_code == 200 else None}))
                if catalogue.status_code == 200:
                    (output / "kariz-products.json").write_text(json.dumps(catalogue.json(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print(json.dumps({"kariz_endpoint_failure_type": type(exc).__name__}))

if __name__ == "__main__":
    asyncio.run(main())
