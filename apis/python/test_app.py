"""Protocol/crypto checks without PostgreSQL; shared suite checks real persistence."""
import base64
import os
import unittest
import uuid
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
import app


class FakePool:
    def __init__(self):
        self.rows = {}

    def acquire(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, identifier, nonce, ciphertext, tag):
        self.rows[identifier] = dict(nonce=nonce, ciphertext=ciphertext, tag=tag)

    async def fetchrow(self, sql, identifier):
        return self.rows.get(identifier)


class API(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.dict(os.environ, ENCRYPTION_KEY_BASE64=base64.b64encode(os.urandom(32)).decode()):
            application = app.create_app()
        application.cleanup_ctx.clear()
        self.pool = FakePool()
        application[app.POOL] = self.pool
        self.client = TestClient(TestServer(application))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_roundtrip_limits_and_authentication(self):
        for message in ['', 'Olá 🌊 " \\ \n', 'a' * 16384, '😀' * 4096]:
            response = await self.client.post('/messages', json={'message': message, 'extra': True})
            self.assertEqual(response.status, 201)
            identifier = (await response.json())['id']
            response = await self.client.get('/messages/' + identifier)
            self.assertEqual(await response.json(), dict(id=identifier, message=message))
            row = self.pool.rows[uuid.UUID(identifier)]
            self.assertEqual(len(row['nonce']), 12)
            self.assertEqual(len(row['tag']), 16)
            row['tag'] = bytes([row['tag'][0] ^ 1]) + row['tag'][1:]
            response = await self.client.get('/messages/' + identifier)
            self.assertEqual(response.status, 500)
        response = await self.client.post('/messages', json={'message': '😀' * 4097})
        self.assertEqual(response.status, 413)
        response = await self.client.post('/messages', data=b'x' * 131073)
        self.assertEqual(response.status, 413)

    async def test_invalid_input(self):
        for value in ['{', '{}', '{"message":null}', '{"message":1}', '[]', '{"message":"\\ud800"}', '{"message":"a","x":NaN}']:
            response = await self.client.post('/messages', data=value)
            self.assertEqual(response.status, 400, value)
        response = await self.client.get('/messages/bad')
        self.assertEqual(response.status, 400)
        response = await self.client.get('/messages/' + str(uuid.uuid4()))
        self.assertEqual(response.status, 404)

    def test_key_rejected(self):
        for value in ['', '!!!', 'YWJj']:
            with patch.dict(os.environ, ENCRYPTION_KEY_BASE64=value):
                with self.assertRaises(ValueError):
                    app.load_key()


if __name__ == '__main__':
    unittest.main()
