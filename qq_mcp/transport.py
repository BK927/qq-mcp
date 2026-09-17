"""HTTP with finite time/size limits, no redirects, no ambient proxies or retries."""

from __future__ import annotations

from typing import Any

import httpx

from .config import QQError


class Transport:
    def __init__(self, timeout: int = 15, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(timeout=timeout, follow_redirects=False,
                                        trust_env=False, transport=transport)

    async def json(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                   body: dict[str, Any] | None = None) -> Any:
        try:
            async with self.client.stream(method, url, headers=headers, json=body) as response:
                if not 200 <= response.status_code < 300:
                    # Never echo provider bodies, request URLs or exception details: may contain secrets.
                    raise QQError(f"Provider HTTP {response.status_code}; no automatic retry")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 1_048_576:
                        raise QQError("Provider response exceeded 1 MiB")
                try:
                    import json
                    return json.loads(content)
                except (ValueError, UnicodeError) as exc:
                    raise QQError("Provider returned invalid JSON") from exc
        except httpx.HTTPError as exc:
            raise QQError("Provider transport failed; delivery may be unknown, no automatic retry") from exc

    async def close(self) -> None:
        await self.client.aclose()
