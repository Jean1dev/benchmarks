import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, loadKey, encrypt, decrypt } from './server.js';
const key = Buffer.alloc(32, 7);

test('key validation and authenticated encryption', () => {
  for (const invalid of ['', 'invalid', Buffer.alloc(31).toString('base64')]) assert.throws(() => loadKey(invalid));
  assert.deepEqual(loadKey(key.toString('base64')), key);
  for (const message of ['', 'olá 😀\n"', 'a'.repeat(16384)]) {
    const row = encrypt(key, message);
    assert.equal(decrypt(key, row), message);
    row.tag[0] ^= 1;
    assert.throws(() => decrypt(key, row));
  }
});

test('HTTP contract using isolated in-memory database substitute', async () => {
  const rows = new Map();
  const db = { async query(sql, args) {
    if (sql.startsWith('INSERT')) { rows.set(args[0], { nonce: args[1], ciphertext: args[2], tag: args[3] }); return {}; }
    return { rows: rows.has(args[0]) ? [rows.get(args[0])] : [] };
  } };
  const server = createServer(db, key);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = body => fetch(`${base}/messages`, { method: 'POST', body });
  try {
    for (const message of ['', 'á😀\n"', 'a'.repeat(16384)]) {
      const response = await post(JSON.stringify({ message, ignored: true }));
      assert.equal(response.status, 201);
      const { id } = await response.json();
      const get = await fetch(`${base}/messages/${id.toUpperCase()}`);
      assert.equal(get.headers.get('content-type'), 'application/json');
      assert.deepEqual(await get.json(), { id, message });
      rows.get(id).tag[0] ^= 1;
      assert.equal((await fetch(`${base}/messages/${id}`)).status, 500);
    }
    for (const body of ['{}', 'null', '[]', '{"message":null}', '{"message":1}', '{', '{}{}', Buffer.from([0xff])]) assert.equal((await post(body)).status, 400);
    assert.equal((await post(JSON.stringify({ message: 'á'.repeat(8193) }))).status, 413);
    assert.equal((await post(' '.repeat(131073))).status, 413);
    assert.equal((await fetch(`${base}/messages/invalid`)).status, 400);
    assert.equal((await fetch(`${base}/messages/00000000-0000-0000-0000-000000000000`)).status, 404);
    assert.equal((await fetch(`${base}/unknown`)).status, 404);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
});
