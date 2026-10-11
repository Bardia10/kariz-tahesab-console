"""Read source inquiry, customer history, and balance for one journaled factor."""
import asyncio
import json
import sys
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import journal, tahesab

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"


async def main(operation_id):
    op = journal.get(operation_id)
    if not op:
        raise SystemExit("No local operation with that ID.")
    payload = json.loads(op["payload"])
    cfg = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = cfg.get("tahesab_token") or ""
    database = cfg.get("tahesab_dbname") or cfg.get("tahesab_database") or "TahesabDB"
    code = int(payload["customer_code"])
    factor = op.get("factor_code")
    inquiry = await tahesab.inquire_factor(token, database, factor)
    docs = await tahesab.documents_for_customer(token, database, code)
    matches = [row for row in docs if str(row.get("Factor_Code")) == str(factor)]
    raw_balance = await tahesab._call(token, database, "balance", [str(code)])
    print(json.dumps({"operation_id": operation_id, "kind": op["kind"], "status": op["status"],
                      "factor": factor, "inquiry": inquiry, "matching_documents": matches,
                      "raw_balance": raw_balance}, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
