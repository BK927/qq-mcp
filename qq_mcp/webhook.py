"""QQ's Ed25519-signed event callback and unsigned URL-validation challenge."""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import QQError
from .service import Service


def signing_key(secret: str) -> Ed25519PrivateKey:
    if not secret:
        raise QQError("Webhook requires a nonempty app secret")
    seed = secret.encode("utf-8")
    while len(seed) < 32:
        seed += seed
    return Ed25519PrivateKey.from_private_bytes(seed[:32])


def verify_signature(secret: str, timestamp: str, signature: str, body: bytes) -> bool:
    try:
        if len(timestamp) > 16 or abs(time.time() - int(timestamp)) > 300:
            return False
        if len(signature) != 128:
            return False
        signing_key(secret).public_key().verify(bytes.fromhex(signature), timestamp.encode() + body)
        return True
    except (ValueError, InvalidSignature):
        return False


def create_webhook(service: Service) -> Starlette:
    config = service.config
    config.require_provider("official")
    challenge_times: list[float] = []

    async def receive(request: Request) -> JSONResponse:
        # Official validation callbacks are unsigned, but must identify this application.
        if request.headers.get("x-bot-appid") != config.app_id:
            return JSONResponse({"error": "invalid application"}, status_code=401)
        raw = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        return JSONResponse({"error": "body too large"}, status_code=413)
        except TimeoutError:
            return JSONResponse({"error": "body timeout"}, status_code=408)
        body = bytes(raw)
        try:
            event = json.loads(body)
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(event, dict):
            return JSONResponse({"error": "invalid event"}, status_code=400)
        if event.get("op") == 13:
            data = event.get("d")
            if not isinstance(data, dict):
                return JSONResponse({"error": "invalid challenge"}, status_code=400)
            plain, timestamp = data.get("plain_token"), data.get("event_ts")
            try:
                # The unsigned challenge must never become a signing oracle for JSON events.
                # QQ documents an opaque alphanumeric token; allow URL-safe token characters,
                # excluding JSON syntax, whitespace and control characters.
                valid = (isinstance(plain, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", plain) is not None
                         and isinstance(timestamp, str) and len(timestamp) <= 16
                         and abs(time.time() - int(timestamp)) <= 300)
            except ValueError:
                valid = False
            if not valid:
                return JSONResponse({"error": "invalid challenge"}, status_code=400)
            now = time.time()
            challenge_times[:] = [x for x in challenge_times if now - x < 60]
            if len(challenge_times) >= 20:
                return JSONResponse({"error": "challenge rate exceeded"}, status_code=429)
            challenge_times.append(now)
            signature = signing_key(config.app_secret).sign((timestamp + plain).encode()).hex()
            return JSONResponse({"plain_token": plain, "signature": signature})
        if not verify_signature(config.app_secret, request.headers.get("x-signature-timestamp", ""),
                                request.headers.get("x-signature-ed25519", ""), body):
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        service.accept_official_event(event)
        return JSONResponse({"op": 12})

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async def prune_loop():
            while True:
                await asyncio.sleep(30)
                service.store.prune()
        task = asyncio.create_task(prune_loop())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await service.close()

    return Starlette(routes=[Route("/qq/webhook", receive, methods=["POST"])], lifespan=lifespan)
