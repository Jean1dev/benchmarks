import http from 'node:http';
import { createCipheriv, createDecipheriv, randomBytes, randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import pg from 'pg';

const MAX_BODY = 128 * 1024;
const MAX_MESSAGE = 16 * 1024;
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function loadKey(encoded) {
  if (typeof encoded !== 'string' || !/^[A-Za-z0-9+/]{43}=$/.test(encoded)) throw new Error('invalid encryption key configuration');
  const key = Buffer.from(encoded, 'base64');
  if (key.length !== 32 || key.toString('base64') !== encoded) throw new Error('invalid encryption key configuration');
  return key;
}
export function encrypt(key, message) {
  const nonce = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, nonce, { authTagLength: 16 });
  const ciphertext = Buffer.concat([cipher.update(message, 'utf8'), cipher.final()]);
  return { nonce, ciphertext, tag: cipher.getAuthTag() };
}
export function decrypt(key, row) {
  if (row.nonce.length !== 12 || row.tag.length !== 16) throw new Error('invalid encrypted record');
  const cipher = createDecipheriv('aes-256-gcm', key, row.nonce, { authTagLength: 16 });
  cipher.setAuthTag(row.tag);
  return decoder.decode(Buffer.concat([cipher.update(row.ciphertext), cipher.final()]));
}
function send(res, status, data) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
}
function fail(res, status, error) { send(res, status, { error }); }
function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    let finished = false;
    req.on('data', chunk => {
      if (finished) return;
      size += chunk.length;
      if (size > MAX_BODY) { finished = true; chunks.length = 0; reject(Object.assign(new Error('body too large'), { status: 413 })); return; }
      chunks.push(chunk);
    });
    req.on('end', () => { if (!finished) { finished = true; resolve(Buffer.concat(chunks)); } });
    req.on('error', () => { if (!finished) { finished = true; reject(Object.assign(new Error('body read failed'), { status: 400 })); } });
  });
}
export function createServer(pool, key) {
  const server = http.createServer({ requestTimeout: 5000, headersTimeout: 5000, connectionsCheckingInterval: 1000, keepAliveTimeout: 60000 }, async (req, res) => {
    res.setTimeout(10000, () => res.destroy());
    try {
      const path = req.url.split('?')[0];
      if (req.method === 'POST' && path === '/messages') {
        let body;
        try { body = JSON.parse(decoder.decode(await readBody(req))); }
        catch (error) { fail(res, error.status === 413 ? 413 : 400, error.status === 413 ? 'payload_too_large' : 'invalid_request'); return; }
        if (body === null || Array.isArray(body) || typeof body !== 'object' || typeof body.message !== 'string') { fail(res, 400, 'invalid_request'); return; }
        if (Buffer.byteLength(body.message, 'utf8') > MAX_MESSAGE) { fail(res, 413, 'payload_too_large'); return; }
        const id = randomUUID();
        const { nonce, ciphertext, tag } = encrypt(key, body.message);
        await pool.query('INSERT INTO messages (id, nonce, ciphertext, tag) VALUES ($1,$2,$3,$4)', [id, nonce, ciphertext, tag]);
        send(res, 201, { id });
      } else if (req.method === 'GET' && path.startsWith('/messages/')) {
        const rawID = path.slice('/messages/'.length);
        if (!uuidPattern.test(rawID)) { fail(res, 400, 'invalid_id'); return; }
        const id = rawID.toLowerCase();
        const result = await pool.query('SELECT nonce, ciphertext, tag FROM messages WHERE id=$1', [id]);
        if (result.rows.length === 0) { fail(res, 404, 'not_found'); return; }
        send(res, 200, { id, message: decrypt(key, result.rows[0]) });
      } else { fail(res, 404, 'not_found'); }
    } catch { console.error('request operation failed'); fail(res, 500, 'internal_error'); }
  });
  return server;
}

async function main() {
  const key = loadKey(process.env.ENCRYPTION_KEY_BASE64);
  if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is required');
  const max = Number(process.env.DB_POOL_MAX ?? '10');
  if (!Number.isInteger(max) || max < 1 || max > 10) throw new Error('DB_POOL_MAX must be between 1 and 10');
  const port = Number(process.env.PORT ?? '8080');
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('invalid PORT');
  const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL, max, connectionTimeoutMillis: 5000, query_timeout: 5000, statement_timeout: 5000, idleTimeoutMillis: 30000 });
  pool.on('error', () => console.error('idle database connection failed'));
  try { await pool.query('SELECT 1'); } catch { await pool.end(); throw new Error('database startup connection failed'); }
  const database = {
    async query(text, values) {
      const started = performance.now();
      const client = await pool.connect();
      let failed;
      try {
        const remaining = Math.floor(5000 - (performance.now() - started));
        if (remaining <= 0) throw new Error('database deadline exceeded');
        return await client.query({ text, values, query_timeout: remaining });
      } catch (error) { failed = error; throw error; }
      finally { client.release(failed); }
    }
  };
  const server = createServer(database, key);
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, '0.0.0.0', resolve); });
  console.error('API listening');
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(() => { console.error('API initialization failed'); process.exit(1); });
}
