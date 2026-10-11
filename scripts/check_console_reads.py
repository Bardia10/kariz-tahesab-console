"""Sanitized end-to-end read check through the console API."""
import asyncio
import json
import sys
import traceback
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BACKEND = ROOT.parent / "backend" / "main-backend-opencode"

async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(BACKEND / ".env").items()}
    token = cfg.get("tahesab_token") or ""
    if not token:
        print(json.dumps({"token_present": False}))
        return
    from backend.main import app
    from backend import tahesab
    try:
        await tahesab.balance_for_customer(token, cfg.get("tahesab_dbname") or "TahesabDB", 1)
        direct = {"ok": True}
    except Exception as exc:
        direct = {"ok": False, "failure_type": type(exc).__name__,
                  "frames": [[f.filename.rsplit("\\", 1)[-1], f.lineno, f.name] for f in traceback.extract_tb(exc.__traceback__)[-5:]]}
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        connected = await client.post("/api/connect", headers={"Origin": "http://testserver"}, json={"token": token})
        if connected.status_code != 200:
            print(json.dumps({"connect_http": connected.status_code}))
            return
        mapping = (await client.get("/api/catalogue")).json()
        good = await client.get("/api/customers/1/balance")
        test_balance = await client.get("/api/customers/1891/balance?refresh=true")
        capabilities = await client.get("/api/capabilities")
        operations = await client.get("/api/operations")
        good_body = good.json() if good.status_code == 200 else {}
        test_body = test_balance.json() if test_balance.status_code == 200 else {}
        cap_items = {item["operation"]: item for item in capabilities.json().get("items", [])}
        print(json.dumps({"connect_http": connected.status_code,
                          "mapped_products": len(mapping.get("mapping", {}).get("products", [])),
                          "verified_mappings": sum(bool(x.get("verified")) for x in mapping.get("mapping", {}).get("products", [])),
                          "existing_account_balance_http": good.status_code,
                          "source_product_positions": len(good_body.get("product_balances", [])),
                          "translated_product_positions": sum(bool(x.get("mapping_verified")) for x in good_body.get("product_balances", [])),
                          "test_account_balance_http": test_balance.status_code,
                          "test_account_has_money": test_body.get("money_rial") is not None,
                          "test_account_has_noncoin_gold": test_body.get("noncoin_gold_grams") is not None,
                          "enabled_coin_bar_and_bank": all(cap_items.get(key, {}).get("enabled") for key in (
                              "bank_payment", "physical_coin_receipt_delivery", "physical_bar_receipt_delivery",
                              "coin_accounting_trade", "bar_accounting_trade")),
                          "abshode_gold_trade_enabled": cap_items.get("gold_accounting_trade", {}).get("enabled") is True,
                          "misc_gold_trade_disabled_with_reason": not cap_items.get("gold_misc_accounting_trade", {}).get("enabled") and bool(cap_items.get("gold_misc_accounting_trade", {}).get("reason")),
                          "visible_client_operations": len(operations.json().get("items", [])),
                          "internal_contract_probes_hidden": not any(x["kind"].startswith(("contract_", "seed_")) for x in operations.json().get("items", [])),
                          "direct_balance_parser": direct,
                          "error_responses_exclude_token": token not in good.text + test_balance.text}))

if __name__ == "__main__": asyncio.run(main())
