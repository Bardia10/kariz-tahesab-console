"""Read-only check of the test source's cross-customer document selector.

Credentials are loaded locally and never printed. No writes are performed.
"""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import dotenv_values
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import tahesab as adapter


def document_customer(row):
    try:
        return int(adapter.normalize(row.get("MCode")))
    except (ValueError, TypeError):
        return None


async def main():
    config = {str(key).lower(): value for key, value in dotenv_values(
        ROOT.parent / "backend" / "main-backend-opencode" / ".env"
    ).items()}
    token = config.get("tahesab_token") or ""
    host = str(config.get("tahesab_base_url") or adapter.BASE_URL).rstrip("/")
    database = config.get("tahesab_dbname") or "TahesabDB"
    if host != adapter.BASE_URL or not token:
        print(json.dumps({"connection_available": False}))
        return
    if "--ui-readback" in sys.argv:
        await verify_client(token, save_readback=True)
        return
    snapshots = {}
    for count in (25, 50, 0):
        try:
            payload = await adapter._call(token, database, "documents", [count, "", "", "", "", 0])
            rows = adapter.records(payload)
            codes = {document_customer(row) for row in rows}
            codes.discard(None)
            snapshots[count] = rows
            print(json.dumps({"requested_count": count, "selector": "",
                              "documents": len(rows), "distinct_customers": len(codes),
                              "all_rows_have_customer": all(document_customer(row) is not None for row in rows)}))
        except adapter.TahesabFailure:
            print(json.dumps({"requested_count": count, "selector": "", "read_failed": True}))
            return
    samples = {}
    for row in snapshots.get(25, []):
        code = document_customer(row)
        if code is not None:
            samples.setdefault(code, row)
        if len(samples) >= 2:
            break
    matched = 0
    complete_histories_matched = 0
    for code, sample in samples.items():
        rows = await adapter.documents_for_customer(token, database, code)
        if any(str(row.get("ID")) == str(sample.get("ID")) and
               str(row.get("Radif")) == str(sample.get("Radif")) and
               document_customer(row) == code for row in rows):
            matched += 1
        global_ids = {(str(row.get("ID")), str(row.get("Radif"))) for row in snapshots[0]
                      if str(row.get("MCode")) == str(code)}
        customer_ids = {(str(row.get("ID")), str(row.get("Radif"))) for row in rows}
        if global_ids == customer_ids:
            complete_histories_matched += 1
    print(json.dumps({"sample_customers": len(samples), "customer_context_readbacks_matched": matched,
                      "complete_sample_histories_matched": complete_histories_matched}))

    await verify_client(token)


async def verify_client(token: str, save_readback: bool = False):
    from backend import main as service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),
                                 base_url="http://127.0.0.1:47183",
                                 headers={"Origin": "http://127.0.0.1:47183"}) as client:
        connected = await client.post("/api/connect", json={"token": token})
        if connected.status_code != 200:
            print(json.dumps({"client_connection_http": connected.status_code}))
            return
        response = await client.get("/api/documents")
        data = response.json()
        print(json.dumps({"client_documents_http": response.status_code,
                          "source_total": data.get("source_total"), "page_rows": len(data.get("items", [])),
                          "visible_customer_names": sum(bool(row.get("customer_name")) for row in data.get("items", []))}))
        next_page = await client.get("/api/documents?page=2")
        print(json.dumps({"client_next_page_http": next_page.status_code,
                          "page": next_page.json().get("page"), "page_rows": len(next_page.json().get("items", []))}))
        payments = await client.get("/api/documents?kind=payment")
        print(json.dumps({"client_payment_filter_http": payments.status_code, "payment_count": payments.json().get("total")}))
        if save_readback and all(response.status_code == 200 for response in (response, next_page, payments)):
            (ROOT / ".local" / "document-browser-readback.json").write_text(json.dumps(
                {"page1": data, "page2": next_page.json(), "payments": payments.json()},
                ensure_ascii=False), encoding="utf-8")
        await client.post("/api/disconnect")


if __name__ == "__main__":
    asyncio.run(main())
