"""Customer edit contracts: positional optional fields, verification, and recovery."""
import json
import unittest
from unittest.mock import AsyncMock, patch
import httpx

from backend import main as service
from backend import tahesab


class CustomerEditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        service.sessions.clear()
        service.cache.clear()
        service.sessions["edit-session"] = ("server-token", 10**20)
        self.row = {"operation_id": "f9467f8f-c51e-40e9-bb13-b2a2dcfe4a5e", "kind": "customer_edit",
                    "status": "submitting", "payload": "", "source_response": None,
                    "factor_code": None, "message": None, "created_at": "now", "updated_at": "now"}
        self.stored = {}
        def reserve(op_id, kind, payload, factor):
            self.row.update(operation_id=op_id, kind=kind, status="submitting",
                            payload=json.dumps(payload, ensure_ascii=False), source_response=None)
            return dict(self.row), True
        def update(op_id, status, message, response=None):
            self.row.update(status=status, message=message,
                            source_response=json.dumps(response, ensure_ascii=False) if response is not None else None)
            return dict(self.row)
        def get(op_id):
            return dict(self.row) if self.row["operation_id"] == op_id else None
        self.customer = {"Code": 42, "Name": "Old", "GoroupName": "صندوق", "Mobile": "0912",
                         "Address": "Old address", "CodeMelli": "001", "BDate": "1370/01/02",
                         "City": "Tehran", "Rasteh": "Retail", "Moaref": "Existing referrer",
                         "Code_Moaref": "17"}
        self.read = AsyncMock(side_effect=lambda token, database, code: [dict(self.customer)])
        self.groups = AsyncMock(return_value={"groups": [{"Name": "صندوق"}]})
        self.write = AsyncMock(return_value={"OK": "42"})
        self.patches = [patch.object(service.adapter, "customer_by_code", self.read),
                        patch.object(service.adapter, "reference_catalogues", self.groups),
                        patch.object(service.adapter, "write_call", self.write),
                        patch.object(service.journal, "reserve", reserve),
                        patch.object(service.journal, "update", update),
                        patch.object(service.journal, "get", get)]
        for item in self.patches:
            item.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),
                                        base_url="http://127.0.0.1:47183",
                                        headers={"Origin": "http://127.0.0.1:47183"},
                                        cookies={service.COOKIE_NAME: "edit-session"})

    async def asyncTearDown(self):
        await self.client.aclose()
        service.sessions.clear()
        service.cache.clear()
        for item in reversed(self.patches):
            item.stop()

    def test_edit_params_keep_source_owned_optional_fields(self):
        params = tahesab.edit_customer_params("New", "صندوق", "0912", "Address", "001", 42, self.customer)
        self.assertEqual(params, ["New", "صندوق", "0912", "Address", "001",
                                  "1370/01/02", "Existing referrer", "17", 42])
        fallback = tahesab.edit_customer_params("New", "صندوق", "0912", "Address", "001", 42,
            {"Code": 42, "BDate": None})
        self.assertEqual(fallback[5:8], [None, "-1", "-1"])

    async def test_edit_uses_fresh_values_and_verifies_source_readback(self):
        async def write(token, database, operation, params):
            self.assertEqual(operation, "customer_edit")
            self.assertEqual(params[5:8], ["1370/01/02", "Existing referrer", "17"])
            self.customer["Name"] = params[0]
            return {"OK": "42"}
        self.write.side_effect = write
        response = await self.client.post("/api/operations", json={
            "operation_id": self.row["operation_id"], "kind": "customer_edit",
            "payload": {"customer_code": 42, "full_name": "New"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "verified")
        self.assertEqual(response.json()["source_response"]["customer"]["Name"], "New")
        self.write.assert_awaited_once()

    async def test_recovery_reads_only_and_never_resends(self):
        self.row.update(status="uncertain", payload=json.dumps({"customer_code": 42, "full_name": "New"}))
        self.row["source_response"] = json.dumps({"pre_write": {"expected": {"full_name": "New"},
             "preserved": {key: self.customer[key] for key in ("BDate", "City", "Rasteh")}}})
        self.customer["Name"] = "New"
        response = await self.client.post(f"/api/operations/{self.row['operation_id']}/verify", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "verified")
        self.write.assert_not_awaited()

    async def test_timeout_keeps_context_and_recovery_does_not_resend(self):
        async def write(token, database, operation, params):
            self.customer["Name"] = params[0]
            raise tahesab.TahesabWriteUncertain("timeout")
        self.write.side_effect = write
        response = await self.client.post('/api/operations', json={
            'operation_id': self.row['operation_id'], 'kind': 'customer_edit',
            'payload': {'customer_code': 42, 'full_name': 'New'}})
        self.assertEqual(response.json()['status'], 'uncertain')
        self.assertIn('pre_write', response.json()['source_response'])
        response = await self.client.post(f"/api/operations/{self.row['operation_id']}/verify", json={})
        self.assertEqual(response.json()['status'], 'verified')
        self.assertEqual(self.write.await_count, 1)

    async def test_changed_preserved_field_cannot_be_verified(self):
        async def write(token, database, operation, params):
            self.customer['Name'] = params[0]
            self.customer['City'] = ''
            return {'Edited': 42}
        self.write.side_effect = write
        response = await self.client.post('/api/operations', json={
            'operation_id': self.row['operation_id'], 'kind': 'customer_edit',
            'payload': {'customer_code': 42, 'full_name': 'New'}})
        self.assertEqual(response.json()['status'], 'uncertain')
        response = await self.client.post(f"/api/operations/{self.row['operation_id']}/verify", json={})
        self.assertEqual(response.json()['status'], 'uncertain')
        self.assertEqual(self.write.await_count, 1)


if __name__ == "__main__":
    unittest.main()
