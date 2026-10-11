"""Password authentication checks; all source reads are mocked."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from backend import main as service


class PasswordLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        service.sessions.clear()
        service.login_failures.clear()
        self.verify = AsyncMock(return_value=[])
        self.patches = [patch.object(service, 'CONSOLE_PASSWORD', 'test-password'),
                        patch.object(service, 'TAHESAB_TOKEN', 'server-only-token'),
                        patch.object(service.adapter, 'verify_database', self.verify)]
        for p in self.patches:
            p.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app),
            base_url='http://127.0.0.1:47183', headers={'Origin': 'http://127.0.0.1:47183'})

    async def asyncTearDown(self):
        await self.client.aclose()
        service.sessions.clear()
        service.login_failures.clear()
        for p in reversed(self.patches):
            p.stop()

    async def test_wrong_password_does_not_contact_source(self):
        response = await self.client.post('/api/connect', json={'password': 'wrong'})
        self.assertEqual(response.status_code, 401)
        self.verify.assert_not_awaited()
        self.assertFalse(service.sessions)

    async def test_password_uses_configured_token_and_private_cookie(self):
        response = await self.client.post('/api/connect', json={'password': 'test-password'})
        self.assertEqual(response.status_code, 200)
        self.verify.assert_awaited_once_with('server-only-token', service.DATABASE)
        self.assertNotIn('server-only-token', response.text)
        self.assertNotIn('test-password', response.text)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])
        self.assertTrue((await self.client.get('/api/session')).json()['connected'])
        await self.client.post('/api/disconnect')
        self.assertFalse((await self.client.get('/api/session')).json()['connected'])

    async def test_token_entry_no_longer_authenticates(self):
        response = await self.client.post('/api/connect', json={'token': 'server-only-token'})
        self.assertEqual(response.status_code, 422)
        self.verify.assert_not_awaited()

    async def test_failed_login_attempts_are_bounded(self):
        for _ in range(6):
            response = await self.client.post('/api/connect', json={'password': 'wrong'})
            self.assertEqual(response.status_code, 401)
        response = await self.client.post('/api/connect', json={'password': 'wrong'})
        self.assertEqual(response.status_code, 429)
        self.verify.assert_not_awaited()

    async def test_missing_server_configuration_is_clear(self):
        with patch.object(service, 'TAHESAB_TOKEN', ''):
            response = await self.client.post('/api/connect', json={'password': 'test-password'})
        self.assertEqual(response.status_code, 503)
        self.verify.assert_not_awaited()
