"""Complete-catalog customer search regressions; mocked source reads only."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from backend import main as service
from backend import tahesab


def source_rows():
    rows = [{"Code": code, "Name": f"مشتری {code}", "Mobile": f"0912000{code:04d}"}
            for code in range(300, 390)]
    rows.extend([
        {"Code": 500, "Name": "كريم ياسر", "Mobile": "٠٩١٢٣٤٥٦٧٨٩"},
        {"Code": 1896, "Name": "فروشنده دوردست", "Mobile": "09129999999"},
        {"Code": 901, "Name": "مشتری با کد پراکنده", "Mobile": "09127770000"},
    ])
    return rows


class CustomerSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        service.sessions.clear()
        service.cache.clear()
        service.customer_snapshot_locks.clear()
        service.sessions["search-session"] = ("test-source-token", 10**20)
        self.source = AsyncMock(return_value=source_rows())
        self.source_patch = patch.object(service.adapter, "all_customers", self.source)
        self.source_patch.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service.app),
            base_url="http://127.0.0.1:47183",
            cookies={service.COOKIE_NAME: "search-session"},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.source_patch.stop()

    async def test_complete_snapshot_paginates_beyond_first_eighty_and_250(self):
        first = await self.client.get("/api/customer-search")
        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertEqual(body["total"], 93)
        self.assertEqual(len(body["items"]), 25)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 25)
        self.assertTrue(body["complete_snapshot"])
        self.assertTrue(body["fetched_at"])

        late = await self.client.get("/api/customer-search", params={"q": "۱۸۹۶"})
        self.assertEqual(late.json()["total"], 1)
        self.assertEqual(late.json()["items"][0]["Code"], 1896)
        self.assertEqual(self.source.await_count, 1)

    async def test_name_and_mobile_search_normalize_arabic_persian_and_digits(self):
        by_name = await self.client.get("/api/customer-search", params={"q": "کریم یاسر"})
        self.assertEqual([item["Code"] for item in by_name.json()["items"]], [500])
        by_mobile = await self.client.get("/api/customer-search", params={"q": "۰۹۱۲۳۴۵۶۷۸۹"})
        self.assertEqual([item["Code"] for item in by_mobile.json()["items"]], [500])
        self.assertEqual(self.source.await_count, 1)

    async def test_more_than_eighty_search_results_are_paginated(self):
        first = await self.client.get("/api/customer-search", params={"q": "مشتری", "page_size": 50})
        second = await self.client.get("/api/customer-search", params={"q": "مشتری", "page": 2, "page_size": 50})
        self.assertEqual(first.json()["total"], 91)
        self.assertEqual(len(first.json()["items"]), 50)
        self.assertEqual(len(second.json()["items"]), 41)
        self.assertFalse(set(row["Code"] for row in first.json()["items"]) &
                         set(row["Code"] for row in second.json()["items"]))

    async def test_parallel_searches_share_one_full_source_read(self):
        async def delayed(*args):
            await asyncio.sleep(0.02)
            return source_rows()
        self.source.side_effect = delayed
        responses = await asyncio.gather(
            self.client.get("/api/customer-search", params={"q": "مشتری"}),
            self.client.get("/api/customer-search", params={"q": "۱۸۹۶"}),
        )
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(self.source.await_count, 1)

    async def test_zero_customer_code_is_searchable(self):
        self.source.return_value = [{"Code": 0, "Name": "zero code"}]
        response = await self.client.get("/api/customer-search", params={"q": "0"})
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["Code"], 0)

    async def test_session_and_pagination_validation(self):
        response = await self.client.get("/api/customer-search?page_size=30")
        self.assertEqual(response.status_code, 422)
        self.client.cookies.clear()
        response = await self.client.get("/api/customer-search")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.source.await_count, 0)

    async def test_adapter_uses_verified_empty_name_filter(self):
        self.source_patch.stop()
        try:
            with patch.object(tahesab, "_call", AsyncMock(return_value={
                    "1896": {"Code": 1896, "Name": "test"}})) as source:
                rows = await tahesab.all_customers("test-token", "test-db")
        finally:
            self.source_patch.start()
        source.assert_awaited_once_with("test-token", "test-db", "customer_search", [""])
        self.assertEqual(rows[0]["Code"], 1896)


if __name__ == "__main__":
    unittest.main()
