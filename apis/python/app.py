"""Minimal asynchronous HTTP/1.1 benchmark API."""
import asyncio
import base64
import binascii
import json
import logging
import os
import re
import uuid

import asyncpg
from aiohttp import web
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BODY_LIMIT = 128 * 1024
MESSAGE_LIMIT = 16 * 1024
UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
POOL = web.AppKey("pool", asyncpg.Pool)
CIPHER = web.AppKey("cipher", AESGCM)


def load_key():
    try:
        key = base64.b64decode(os.environ["ENCRYPTION_KEY_BASE64"], validate=True)
        if len(key) != 32:
            raise ValueError()
        return key
    except (KeyError, ValueError, binascii.Error):
        raise ValueError("ENCRYPTION_KEY_BASE64 must encode exactly 32 bytes") from None


def error(status, code):
    return web.json_response({"error": code}, status=status)


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except web.HTTPException as exc:
        return error(exc.status, "not_found" if exc.status == 404 else "invalid_request")
    except Exception:
        logging.error("Request failed (database, cryptography or internal error)")
        return error(500, "internal_error")


def invalid_constant(value):
    raise ValueError("Invalid JSON constant")


async def create_message(request):
    if request.content_length is not None and request.content_length > BODY_LIMIT:
        response = error(413, "payload_too_large")
        response.force_close()
        return response
    body = bytearray()
    async with asyncio.timeout(5):
        async for chunk in request.content.iter_chunked(16384):
            body.extend(chunk)
            if len(body) > BODY_LIMIT:
                response = error(413, "payload_too_large")
                response.force_close()
                return response
    try:
        data = json.loads(body.decode("utf-8"), parse_constant=invalid_constant)
        if not isinstance(data, dict) or not isinstance(data.get("message"), str):
            return error(400, "invalid_request")
        plaintext = data["message"].encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        return error(400, "invalid_request")
    if len(plaintext) > MESSAGE_LIMIT:
        return error(413, "payload_too_large")
    identifier = uuid.uuid4()
    nonce = os.urandom(12)
    encrypted = request.app[CIPHER].encrypt(nonce, plaintext, None)
    async with asyncio.timeout(5):
        async with request.app[POOL].acquire(timeout=5) as connection:
            await connection.execute(
                "INSERT INTO messages (id, nonce, ciphertext, tag) VALUES ($1, $2, $3, $4)",
                identifier, nonce, encrypted[:-16], encrypted[-16:],
            )
    return web.json_response({"id": str(identifier)}, status=201)


async def get_message(request):
    raw_id = request.match_info["id"]
    if not UUID_PATTERN.fullmatch(raw_id):
        return error(400, "invalid_id")
    identifier = uuid.UUID(raw_id)
    async with asyncio.timeout(5):
        async with request.app[POOL].acquire(timeout=5) as connection:
            row = await connection.fetchrow(
                "SELECT nonce, ciphertext, tag FROM messages WHERE id = $1", identifier
            )
    if row is None:
        return error(404, "not_found")
    plaintext = request.app[CIPHER].decrypt(
        row["nonce"], row["ciphertext"] + row["tag"], None
    ).decode("utf-8")
    return web.json_response({"id": str(identifier), "message": plaintext})


async def database(app):
    maximum = int(os.environ.get("DB_POOL_MAX", "10"))
    if not 1 <= maximum <= 10:
        raise ValueError("DB_POOL_MAX must be between 1 and 10")
    app[POOL] = await asyncpg.create_pool(
        os.environ["DATABASE_URL"], min_size=1, max_size=maximum,
        timeout=5, command_timeout=5, statement_cache_size=0,
        server_settings={"statement_timeout": "5000", "synchronous_commit": "on"},
    )
    try:
        yield
    finally:
        await app[POOL].close()


def create_app():
    app = web.Application(middlewares=[errors], client_max_size=BODY_LIMIT)
    app[CIPHER] = AESGCM(load_key())
    app.cleanup_ctx.append(database)
    app.router.add_post("/messages", create_message)
    app.router.add_get("/messages/{id}", get_message, allow_head=False)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    try:
        app = create_app()
        web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
                    access_log=None, print=None, keepalive_timeout=60,
                    auto_decompress=False, shutdown_timeout=10)
    except (ValueError, KeyError):
        logging.error("Invalid startup configuration")
        raise SystemExit(1)
