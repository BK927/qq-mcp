"""Documented QQ OpenAPI and NapCat OneBot v11 adapters."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from .config import Config, QQError
from .transport import Transport

QQ_ORIGIN = "https://api.bot.qq.com"


def text_content(message: Any) -> str:
    """Return plain text only; media/CQ payloads are never fetched or executed."""
    if isinstance(message, str):
        return message[:8000]
    if not isinstance(message, list):
        return ""
    parts: list[str] = []
    for segment in message[:100]:
        if isinstance(segment, dict):
            if segment.get("type") == "text" and isinstance(segment.get("data"), dict):
                parts.append(str(segment["data"].get("text", ""))[:8000])
            else:
                parts.append("[non-text message]")
    return "".join(parts)[:8000]


class Official:
    def __init__(self, config: Config, http: Transport):
        self.config, self.http = config, http
        self._token = ""
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            result = await self.http.json("POST", f"{QQ_ORIGIN}/app/getAppAccessToken", body={
                "appId": self.config.app_id, "clientSecret": self.config.app_secret,
            })
            if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
                raise QQError("Official token exchange failed")
            try:
                expiry = min(7200, max(1, int(result["expires_in"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise QQError("Official token response has invalid expiry") from exc
            self._token = result["access_token"]
            self._expires = time.monotonic() + max(1, expiry - 60)
            return self._token

    async def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        token = await self.token()
        try:
            result = await self.http.json(method, QQ_ORIGIN + path,
                headers={"Authorization": f"QQBot {token}"}, body=body)
        except QQError:
            # Refresh on a later explicit operation, never automatically replay a send.
            self._token, self._expires = "", 0
            raise
        if isinstance(result, dict) and result.get("code") not in {None, 0}:
            raise QQError("Official API rejected the operation; check bot permissions and reply window")
        return result

    async def status(self) -> dict[str, Any]:
        profile = await self.request("GET", "/users/@me")
        if not isinstance(profile, dict):
            raise QQError("Invalid official bot profile response")
        return {"authenticated": True, "app_id": self.config.app_id,
                "account_id": str(profile.get("id", ""))[:128],
                "username": str(profile.get("username", ""))[:200]}

    async def send(self, kind: str, target: str, content: str, reply_to: str, sequence: int) -> str:
        collection = "groups" if kind == "group" else "users"
        result = await self.request("POST", f"/v2/{collection}/{target}/messages", {
            "msg_type": 0, "content": content, "msg_id": reply_to, "msg_seq": sequence,
        })
        if not isinstance(result, dict) or not result.get("id"):
            raise QQError("Official send had no message ID; delivery is unknown")
        return str(result["id"])[:128]


class Personal:
    def __init__(self, config: Config, http: Transport):
        self.config, self.http = config, http

    async def request(self, action: str, params: dict[str, Any] | None = None) -> Any:
        result = await self.http.json("POST", f"{self.config.napcat_url}/{action}",
            headers={"Authorization": f"Bearer {self.config.napcat_token}"}, body=params or {})
        if not isinstance(result, dict) or result.get("status") != "ok" or result.get("retcode") != 0:
            raise QQError("OneBot rejected the operation; check login, permission and extension support")
        return result.get("data")

    async def status(self) -> dict[str, Any]:
        login = await self.request("get_login_info")
        state = await self.request("get_status")
        if not isinstance(login, dict) or not isinstance(state, dict):
            raise QQError("Invalid OneBot status response")
        return {"account_id": str(login.get("user_id", ""))[:128],
                "nickname": str(login.get("nickname", ""))[:200],
                "online": state.get("online") is True, "good": state.get("good") is True}

    async def targets(self, kind: str) -> list[dict[str, Any]]:
        action = "get_group_list" if kind == "group" else "get_friend_list"
        id_key = "group_id" if kind == "group" else "user_id"
        name_key = "group_name" if kind == "group" else "nickname"
        allowed = getattr(self.config, f"personal_{kind}s")
        result = await self.request(action)
        if not isinstance(result, list):
            raise QQError("Invalid OneBot target list")
        return [{"id": str(row[id_key]), "name": str(row.get(name_key, ""))[:200]}
                for row in result if isinstance(row, dict) and str(row.get(id_key)) in allowed][:1000]

    async def history(self, kind: str, target: str, limit: int, cursor: str,
                      reverse_order: bool = False) -> list[dict[str, Any]]:
        action = "get_group_msg_history" if kind == "group" else "get_friend_msg_history"
        key = "group_id" if kind == "group" else "user_id"
        params: dict[str, Any] = {key: target, "count": limit, "reverse_order": reverse_order,
                                  "disable_get_url": True, "parse_mult_msg": False}
        if cursor:
            if not re.fullmatch(r"-?[0-9]{1,30}", cursor):
                raise QQError("History cursor must be a numeric NapCat message ID")
            params["message_seq"] = cursor
        data = await self.request(action, params)
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise QQError("NapCat history extension returned an invalid response")
        rows = []
        for row in data["messages"][:limit]:
            if not isinstance(row, dict):
                continue
            # Reject cross-target group results rather than trusting an upstream bug.
            if kind == "group" and str(row.get("group_id")) != target:
                continue
            sender = row.get("sender") if isinstance(row.get("sender"), dict) else {}
            rows.append({"message_id": str(row.get("message_id", ""))[:128],
                         "message_seq": str(row.get("message_seq", ""))[:128],
                         "author": str(sender.get("user_id", row.get("user_id", "")))[:128],
                         "timestamp": str(row.get("time", ""))[:64],
                         "content": text_content(row.get("message"))})
        return rows

    async def send(self, kind: str, target: str, content: str) -> str:
        action = "send_group_msg" if kind == "group" else "send_private_msg"
        key = "group_id" if kind == "group" else "user_id"
        result = await self.request(action, {key: target, "message": [
            {"type": "text", "data": {"text": content}},
        ]})
        if not isinstance(result, dict) or "message_id" not in result:
            raise QQError("OneBot send had no message ID; delivery is unknown")
        return str(result["message_id"])[:128]
