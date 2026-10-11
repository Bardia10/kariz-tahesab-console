"""Document browsing regressions; source calls are mocked and no records are written."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend import main as service
from backend import tahesab


def source_rows():
    rows = [{"ID": number, "Radif": 1, "MCode": 1 if number % 2 else 2,
             "Tarikh": "1405/07/18", "Tarikh_Time": "10:00:00",
             "NO": "فروش سکه" if number % 2 else "واریز وجه",
             "Mali": str(number * 100000000000), "Arz": "IRR",
             "Sharh1": f"شماره {number}", "TasirBarMande": 1}
            for number in range(1, 61)]
    rows.append({"ID": 9999, "Radif": 2, "MCode": None, "_source_key": "777",
                 "Tarikh": "1405/07/18", "Tarikh_Time": "12:00:00", "NO": "نوع ناشناخته"})
    return rows


class DocumentBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        service.sessions.clear()
        service.cache.clear()
        service.document_snapshot_locks.clear()
        self.source = AsyncMock(return_value=source_rows())
        self.customer = AsyncMock(side_effect=lambda token, database, code: [{"Code": code, "Name": f"مشتری {code}"}])
        self.patches = [patch.object(tahesab, "documents_all", self.source),
                        patch.object(tahesab, "customer_by_code", self.customer),
                        patch.object(tahesab, "verify_database", AsyncMock(return_value=[{"Name": "ریال"}]))]
        for source_patch in self.patches:
            source_patch.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),
                                       base_url="http://127.0.0.1:47183",
                                       headers={"Origin": "http://127.0.0.1:47183"})
        response = await self.client.post("/api/connect", json={"token": "not-a-real-token"})
        self.assertEqual(response.status_code, 200)

    async def asyncTearDown(self):
        await self.client.post("/api/disconnect")
        await self.client.aclose()
        for source_patch in reversed(self.patches):
            source_patch.stop()

    async def test_opens_without_customer_and_preserves_source_identity(self):
        response = await self.client.get("/api/documents")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source_total"], 61)
        self.assertEqual(len(body["items"]), 25)
        self.assertEqual(body["items"][0]["id"], 9999)
        self.assertIsNone(body["items"][0]["customer_code"])
        self.assertEqual(body["items"][0]["radif"], 2)
        self.assertEqual(body["items"][1]["id"], 60)
        self.assertEqual(body["items"][1]["customer_name"], "مشتری 2")
        self.assertEqual(body["items"][1]["amount"], "6000000000000")
        unknown_customer = await self.client.get("/api/documents?customer=777")
        self.assertEqual(unknown_customer.json()["total"], 0)

    async def test_pages_and_filters_reuse_global_snapshot(self):
        first = (await self.client.get("/api/documents")).json()
        second = (await self.client.get("/api/documents?page=2")).json()
        first_ids = {(row["customer_code"], row["id"], row["radif"]) for row in first["items"]}
        second_ids = {(row["customer_code"], row["id"], row["radif"]) for row in second["items"]}
        self.assertFalse(first_ids & second_ids)
        payment = (await self.client.get("/api/documents?kind=payment")).json()
        self.assertEqual(payment["total"], 30)
        self.assertTrue(all(row["category"] == "payment" for row in payment["items"]))
        self.assertEqual(self.source.await_count, 1)
        await self.client.get("/api/documents?refresh=true")
        self.assertEqual(self.source.await_count, 2)

    async def test_persian_search_and_optional_customer_filter(self):
        found = (await self.client.get("/api/documents", params={"search": "شماره ۱۲"})).json()
        self.assertEqual([row["id"] for row in found["items"]], [12])
        customer = (await self.client.get("/api/documents?customer=1&page_size=50")).json()
        self.assertEqual(customer["total"], 30)
        self.assertTrue(all(row["customer_code"] == 1 for row in customer["items"]))

    async def test_unpadded_dates_sort_chronologically(self):
        rows = source_rows()
        self.source.return_value = [{**rows[0], "ID": 999, "Tarikh": "1405/7/20"},
                                    {**rows[1], "ID": 1, "Tarikh": "1405/10/1"}]
        response = await self.client.get("/api/documents")
        self.assertEqual([row["id"] for row in response.json()["items"]], [1, 999])

    async def test_literal_source_nulls_are_missing_without_changing_raw_data(self):
        self.source.return_value = [{**source_rows()[0], "NO": "null", "Mali": "null", "Arz": "null",
                                     "No_Kar": "null", "Name_az": "null"}]
        response = await self.client.get("/api/documents")
        item = response.json()["items"][0]
        self.assertIsNone(item["amount"])
        self.assertIsNone(item["label"])
        self.assertIsNone(item["currency"])
        self.assertEqual(item["product"], "")
        self.assertEqual(item["source"]["Mali"], "null")

    async def test_source_failure_is_an_error_and_empty_source_is_empty(self):
        self.source.side_effect = tahesab.TahesabFailure("upstream_unavailable")
        failed = await self.client.get("/api/documents")
        self.assertEqual(failed.status_code, 502)
        self.source.side_effect = None
        self.source.return_value = []
        empty = await self.client.get("/api/documents")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["total"], 0)

    async def test_name_failure_keeps_documents_visible(self):
        self.customer.side_effect = tahesab.TahesabFailure("upstream_unavailable")
        response = await self.client.get("/api/documents")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 25)
        self.assertIsNone(response.json()["items"][1]["customer_name"])

    async def test_concurrent_queries_share_one_source_read(self):
        async def delayed_source(*args):
            await asyncio.sleep(0.02)
            return source_rows()
        self.source.side_effect = delayed_source
        results = await asyncio.gather(self.client.get("/api/documents"), self.client.get("/api/documents?page=2"))
        self.assertTrue(all(response.status_code == 200 for response in results))
        self.assertEqual(self.source.await_count, 1)

    async def test_session_required(self):
        await self.client.post("/api/disconnect")
        response = await self.client.get("/api/documents")
        self.assertEqual(response.status_code, 401)


class DocumentAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_selector_uses_empty_customer_and_zero_count(self):
        with patch.object(tahesab, "_call", AsyncMock(return_value=source_rows())) as source:
            rows = await tahesab.documents_all("not-a-real-token", "test-database")
            source.assert_awaited_once_with("not-a-real-token", "test-database", "documents", [0, "", "", "", "", 0])
            self.assertEqual(len(rows), 61)


if __name__ == "__main__":
    unittest.main()
