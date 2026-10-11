"""Build display mappings from the authenticated Kariz export and Tahesab lists."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
products = json.loads((ROOT / ".local" / "kariz-products.json").read_text(encoding="utf-8"))
refs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / ".local").glob("DoListName*.json")}
balance_path = ROOT / ".local" / "balance-existing_sample_account.json"
balance_payload = json.loads(balance_path.read_text(encoding="utf-8")) if balance_path.exists() else {}

def find_balance_rows(value):
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        if any(key in value for key in ("MandeyeMali", "MandeMenhayeSeke", "Seke_emami")):
            return [value]
        for nested in value.values():
            rows = find_balance_rows(nested)
            if rows:
                return rows
    return []

balance_rows = find_balance_rows(balance_payload)
balance_keys = set(balance_rows[0]) if balance_rows else set()

def norm(value):
    value = str(value or "").translate(str.maketrans({"\u064a": "\u06cc", "\u0643": "\u06a9"}))
    return " ".join(value.split()).casefold()


def family(product):
    kind = product.get("kind")
    return {"gold": "gold", "coin": "coin", "silver_bar": "bar", "bar": "bar", "handmade": "handmade"}.get(kind, "other")

rows = []
for p in products:
    fam = family(p)
    source_file = {"coin": "DoListNameSekeh", "bar": "DoListNameShemsh", "handmade": "DoListNameKarsakhte"}.get(fam)
    source_rows = refs.get(source_file, []) if source_file else []
    configured_name = p.get("tahesab_name") or ""
    matched = next((r for r in source_rows if norm(r.get("Name")) == norm(configured_name)), None)
    candidates = [r for r in source_rows if p.get("unit_weight") and str(r.get("Vazn")) == str(p["unit_weight"]) and (not p.get("ayar") or str(r.get("Ayar")) == str(p["ayar"]))]
    catalogue_name = matched.get("Name") if matched else (candidates[0].get("Name") if len(candidates) == 1 else "")
    source_name = configured_name
    balance_field_match = bool(configured_name and f"Seke_{configured_name}" in balance_keys)
    verified = bool(matched or balance_field_match)
    evidence = ("Exact Tahesab catalogue name matched the current Kariz configuration." if matched else
                f"The exact configured Tahesab name is present as a source balance field; the live product catalogue returns {catalogue_name!r} as its label." if balance_field_match else
                "No source field confirms this configured name; the catalogue candidate is for display review only." if catalogue_name else
                "Kariz has no Tahesab source name configured for this product.")
    rows.append({
        "key": p["key"], "title_fa": p["name_fa"], "kariz_product_id": p["id"],
        "kohan_product_id": p.get("kohan_product_id"), "source_identifier": source_name or None,
        "source_name": source_name, "source_catalogue_name": catalogue_name or None,
        "source_family": fam, "unit": p["unit"],
        "configured_tahesab_name": configured_name or None,
        "source_weight": p.get("unit_weight"), "purity": p.get("ayar"),
        "mesghal_to_gram_factor": p.get("mesghal_to_gram_factor"),
        "metal": str(p.get("jens_felez", 0)), "display_category": p.get("kind", "other"),
        "verified": verified, "verification_evidence": evidence,
        "candidate_source_names": [r.get("Name") for r in candidates],
    })

payload = {"version": 1, "kariz_catalogue_fetched_at": datetime.now(timezone.utc).isoformat(), "products": rows}
(ROOT / "catalogue.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"products": len(rows), "verified": sum(x["verified"] for x in rows), "families": sorted({x["source_family"] for x in rows})}))
