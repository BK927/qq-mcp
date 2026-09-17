"""Environment configuration; empty allowlists deny access."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class QQError(Exception):
    """A deliberate, credential-free error safe to return through MCP."""


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_:.\-]{1,128}", value):
        raise QQError("Invalid target or message identifier")
    return value


def _flag(env: dict[str, str], key: str, default: bool = False) -> bool:
    value = env.get(key, str(default)).lower().strip()
    if value not in {"true", "false", "1", "0"}:
        raise QQError(f"{key} must be true or false")
    return value in {"true", "1"}


def _number(env: dict[str, str], key: str, default: int, low: int, high: int) -> int:
    try:
        number = int(env.get(key, str(default)))
    except ValueError as exc:
        raise QQError(f"{key} must be an integer") from exc
    if not low <= number <= high:
        raise QQError(f"{key} must be between {low} and {high}")
    return number


@dataclass(frozen=True)
class Config:
    app_id: str = ""
    app_secret: str = field(default="", repr=False)
    napcat_url: str = "http://127.0.0.1:3000"
    napcat_token: str = field(default="", repr=False)
    enable_official: bool = False
    enable_personal: bool = False
    enable_send: bool = False
    allow_remote_napcat: bool = False
    official_groups: frozenset[str] = frozenset()
    official_users: frozenset[str] = frozenset()
    personal_groups: frozenset[str] = frozenset()
    personal_users: frozenset[str] = frozenset()
    state_path: Path = Path.home() / ".local" / "share" / "qq-mcp" / "state.sqlite3"
    retention_seconds: int = 86400
    max_messages: int = 2000
    request_timeout: int = 15
    webhook_port: int = 8787

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ) if env is None else env

        def scope(key: str) -> frozenset[str]:
            result = frozenset(identifier(x.strip()) for x in env.get(key, "").split(",") if x.strip())
            if len(result) > 1000:
                raise QQError(f"{key} supports at most 1000 IDs")
            return result

        config = cls(
            app_id=env.get("QQ_APP_ID", "").strip(),
            app_secret=env.get("QQ_APP_SECRET", ""),
            napcat_url=env.get("QQ_NAPCAT_URL", "http://127.0.0.1:3000").rstrip("/"),
            napcat_token=env.get("QQ_NAPCAT_TOKEN", ""),
            enable_official=_flag(env, "QQ_ENABLE_OFFICIAL"),
            enable_personal=_flag(env, "QQ_ENABLE_PERSONAL"),
            enable_send=_flag(env, "QQ_ENABLE_SEND"),
            allow_remote_napcat=_flag(env, "QQ_ALLOW_REMOTE_NAPCAT"),
            official_groups=scope("QQ_OFFICIAL_GROUPS"),
            official_users=scope("QQ_OFFICIAL_USERS"),
            personal_groups=scope("QQ_PERSONAL_GROUPS"),
            personal_users=scope("QQ_PERSONAL_USERS"),
            state_path=Path(env.get("QQ_STATE_PATH", str(cls.state_path))).expanduser(),
            retention_seconds=_number(env, "QQ_RETENTION_SECONDS", 86400, 60, 604800),
            max_messages=_number(env, "QQ_MAX_MESSAGES", 2000, 1, 10000),
            request_timeout=_number(env, "QQ_REQUEST_TIMEOUT", 15, 1, 60),
            webhook_port=_number(env, "QQ_WEBHOOK_PORT", 8787, 1024, 65535),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.enable_official and (not self.app_id or not self.app_secret):
            raise QQError("Official provider requires QQ_APP_ID and QQ_APP_SECRET")
        if self.enable_personal:
            if not self.napcat_token:
                raise QQError("Personal provider requires QQ_NAPCAT_TOKEN")
            parsed = urlsplit(self.napcat_url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in {"", "/"}):
                raise QQError("QQ_NAPCAT_URL must be a plain HTTP(S) origin")
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                # Literal IP avoids DNS rebinding and localhost resolution ambiguity.
                loopback = False
            if not loopback and not self.allow_remote_napcat:
                raise QQError("NapCat must use a literal loopback IP unless remote access is enabled")
            if not loopback and parsed.scheme != "https":
                raise QQError("Remote NapCat requires HTTPS")
            for target in self.personal_groups | self.personal_users:
                if not target.isascii() or not target.isdigit() or int(target) < 1:
                    raise QQError("Personal account scopes must be positive numeric QQ IDs")

    def require_provider(self, provider: str) -> None:
        if provider not in {"official", "personal"}:
            raise QQError("Provider must be official or personal")
        if not getattr(self, f"enable_{provider}"):
            raise QQError(f"{provider} provider is disabled")

    def require_target(self, provider: str, kind: str, target: str) -> None:
        self.require_provider(provider)
        identifier(target)
        if kind not in {"group", "user"}:
            raise QQError("Target kind must be group or user")
        if target not in getattr(self, f"{provider}_{kind}s"):
            raise QQError("Target is not in the configured allowlist")
