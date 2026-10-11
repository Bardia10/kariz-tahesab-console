"""Read-only inspection of the designated Tahesab console test customer."""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import tahesab

CONFIG = ROOT.parent / "backend" / "main-backend-opencode" / ".env"


async def main():
    config = {str(k).lower(): v for k, v in dotenv_values(CONFIG).items()}
    token = config.get("tahesab_token") or ""
    database = config.get("tahesab_dbname") or config.get("tahesab_database") or "TahesabDB"
    customer = 1891
    docs = await tahesab.documents_for_customer(token, database, customer)
    balance_payload = await tahesab._call(token, database, "balance", [str(customer)])
    balance_rows = tahesab.records(balance_payload)
    bank_rows = tahesab.records(await tahesab._call(token, database, "bank_accounts", []))
    print(json.dumps({
        "test_database_access": bool(token),
        "customer_code": customer,
        "customer_document_count": len(docs),
        "document_fields": sorted({key for row in docs for key in row}),
        "document_samples": [{key: row.get(key) for key in (
            "ID", "Radif", "NO", "Factor_Code", "Vazn", "Mali", "Count",
            "Name_Seke", "Name_Shemsh", "Seke", "Shemsh", "Reygiri",
            "Mazeneh", "Ayar", "IsTemporary", "IsTemp", "Currency") if key in row}
            for row in docs[:4]],
        "balance_fields": sorted({key for row in balance_rows for key in row}),
        "money_field_present": any(row.get("MandeyeMali") not in (None, "") for row in balance_rows),
        "bank_account_count": len(bank_rows),
        "bank_account_fields": sorted({key for row in bank_rows for key in row}),
    }, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
