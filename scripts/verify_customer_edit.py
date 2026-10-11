"""Edit and restore the designated Tahesab edit-contract customer 1896."""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import dotenv_values
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import tahesab


VISIBLE_FIELDS = ("Name", "GoroupName", "CodeMelli", "Mobile", "Address", "BDate", "City", "Rasteh")


async def read_customer(token, database, code):
    rows = await tahesab.customer_by_code(token, database, code)
    return next((row for row in rows if tahesab.customer_code(row) == code), None)


async def edit_customer(token, database, code, current, name):
    params = tahesab.edit_customer_params(name, str(current.get("GoroupName") or ""),
        str(current.get("Mobile") or ""), str(current.get("Address") or ""),
        str(current.get("CodeMelli") or ""), code, current)
    return await tahesab.write_call(token, database, "customer_edit", params)


async def main():
    config = {str(k).lower(): v for k, v in dotenv_values(".env").items()}
    token = config.get("tahesab_token", "")
    database = config.get("tahesab_database", "TahesabDB")
    code = 1896
    before = await read_customer(token, database, code)
    if not before or not str(before.get("Name", "")).casefold().startswith("codex edit contract"):
        raise SystemExit("Designated test customer 1896 did not match; no edit attempted.")
    changed_name = str(before.get("Name")) + " Edit Check"
    edit_response = await edit_customer(token, database, code, before, changed_name)
    changed = await read_customer(token, database, code)
    basic_fields_match = bool(changed and changed.get("Name", "").casefold() == changed_name.casefold()
        and all(changed.get(key) == before.get(key) for key in VISIBLE_FIELDS if key != "Name"))
    if not basic_fields_match:
        print(json.dumps({"customer_code": code, "status": "uncertain",
            "readback_matches": False, "restore_attempted": False}, ensure_ascii=True))
        return
    restore_response = await edit_customer(token, database, code, changed, str(before["Name"]))
    restored = await read_customer(token, database, code)
    restored_matches = bool(restored and all(restored.get(key) == before.get(key) for key in VISIBLE_FIELDS))
    print(json.dumps({"customer_code": code,
        "status": "verified" if basic_fields_match and restored_matches else "uncertain",
        "readback_matches": basic_fields_match, "restore_matches": restored_matches,
        "birthdate_present": before.get("BDate") not in (None, ""),
        "source_city_and_rasteh_preserved": bool(changed and all(changed.get(k) == before.get(k) for k in ("City", "Rasteh"))),
        "referral_fields_returned_by_source": any(k in before for k in ("Moaref", "Code_Moaref")),
        "edit_response": edit_response, "restore_response": restore_response}, ensure_ascii=True, default=str))


if __name__ == "__main__":
    asyncio.run(main())
