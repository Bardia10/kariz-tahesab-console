"""Read-only audit of the anomalous gold-trade probe; never writes."""
import asyncio
import json
import sys
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import journal, tahesab

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"


async def main():
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    op = next((r for r in journal.list_recent() if r["kind"] == "contract_trade_gold_customer_buys"), None)
    if not op:
        print(json.dumps({"found": False}))
        return
    factor = op["factor_code"]
    payload = json.loads(op["payload"])
    docs = await tahesab.documents_for_customer(token, database, 1891)
    document = next((r for r in docs if str(r.get("Factor_Code")) == factor), None)
    inquiry = await tahesab.inquire_factor(token, database, factor)
    balance_payload = await tahesab._call(token, database, "balance", ["1891"])
    print(json.dumps({"operation_status": op["status"], "factor": factor,
                      "test_request": payload, "source_document": document,
                      "factor_inquiry": inquiry, "raw_balance": balance_payload},
                     ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
