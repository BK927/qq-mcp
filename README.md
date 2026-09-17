# QQ MCP

Independent, local stdio MCP server supporting **both official QQ bots and personal QQ accounts through NapCat / OneBot v11**. MIT licensed. This is an initial implementation with mocked protocol tests; live account acceptance is still required before production use.

## Capabilities

| Capability | Official bot | Personal account (NapCat) |
|---|---|---|
| Account/status probe | Access-token exchange and bot profile | Login identity and OneBot status |
| Targets | Explicit configured bot-scoped open IDs | Allowlisted subset of friend/group lists |
| Context | Signed webhook messages received since setup | Bounded on-demand NapCat history extension |
| Send | Plain-text reply to a received group/C2C message | Plain-text message to an allowlisted group/friend |
| Authentication | App ID + AppSecret; Ed25519 inbound verification | Bearer access token; loopback HTTP or explicit HTTPS remote |

The official bot is a separate identity: it cannot read your personal account's past conversations. Official group IDs and user IDs are **bot-scoped open IDs**, not ordinary QQ numbers. NapCat is an unofficial personal-account bridge that you install and operate separately. Its history is not guaranteed complete and depends on the QQ client/session.

## Install

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are required for the commands below.

```sh
git clone https://github.com/BK927/qq-mcp.git
cd qq-mcp
uv sync --frozen --extra dev
uv run qq-mcp --help
```

Use `uv sync --frozen --no-dev` for runtime installation (the `dev` extra is only included with `--extra dev`). The checked-in `uv.lock` pins the resolved dependency set. `pip install .` also works, but does not consume this lockfile.

Copy `.env.example` to `.env`, edit the values locally, and start with `uv run --env-file .env qq-mcp`. Environment files are never loaded implicitly; `--env-file` is a uv option. Never put credentials in tool arguments, prompts, Git, or screenshots.

Example MCP client configuration (replace the directory; set environment through your client or secure launcher):

```json
{
  "mcpServers": {
    "qq": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/qq-mcp", "--env-file", "/absolute/path/qq-mcp/.env", "qq-mcp"]
    }
  }
}
```

Windows directory values can use `D:/repo/qq-mcp`. Both providers can be enabled in the same instance. Keep the same absolute `QQ_STATE_PATH` for the MCP process and webhook process. Run one webhook worker; SQLite coordinates the separate MCP and webhook processes.

## Official bot setup

1. Register your own bot in [QQ Open Platform](https://q.qq.com/). Enable the required group/C2C capabilities and obtain AppID/AppSecret. This repository does not register bots or obtain permissions for you.
2. Set `QQ_ENABLE_OFFICIAL=true`, `QQ_APP_ID`, `QQ_APP_SECRET`, `QQ_OFFICIAL_GROUPS`, and/or `QQ_OFFICIAL_USERS`. Empty lists grant no conversation access. Obtain bot-scoped IDs from your trusted QQ developer integration; this server deliberately does not enumerate unsolicited events from all chats to discover IDs.
3. Run `uv run --env-file .env qq-mcp webhook`. It binds **127.0.0.1 only**, on `QQ_WEBHOOK_PORT` (8787 by default), and serves `/qq/webhook`.
4. Place an HTTPS reverse proxy in front of that endpoint and configure its public URL in QQ's callback settings. Forward the raw body and `X-Bot-Appid`, `X-Signature-Ed25519`, `X-Signature-Timestamp` unchanged. QQ currently permits public callback ports 80, 443, 8080, and 8443. Configure proxy body and rate limits too.
5. Subscribe to `C2C_MESSAGE_CREATE`, `GROUP_AT_MESSAGE_CREATE`, and/or `GROUP_MESSAGE_CREATE` as your bot's permission scope permits. The receiver handles the official unsigned `op:13` URL challenge separately, verifies signatures and timestamp freshness for all ordinary events, drops conversations outside the allowlist, and acknowledges with `op:12`.
6. Run the MCP process, call `qq_status` with `probe=true`, and inspect `qq_context` after a user messages the bot in an allowed conversation.

Official sends require `reply_to` identifying a message already received through the signed webhook for that target. The server advances `msg_seq` for multiple replies. QQ enforces permissions, message quotas and reply windows; their current documentation has conflicting C2C window values, so this project does not promise a fixed window. No proactive official messaging, media uploads, guild administration, or personal-account history is claimed by this version.

## Personal account setup

1. Install [NapCatQQ](https://github.com/NapNeko/NapCatQQ), sign in locally, and enable its OneBot v11 HTTP server. Set a strong access token in NapCat. This project does not install NapCat, manage QR login, or extract session credentials.
2. Set `QQ_ENABLE_PERSONAL=true`, `QQ_NAPCAT_URL=http://127.0.0.1:3000`, and `QQ_NAPCAT_TOKEN` to the same token. The port is whatever you configured in NapCat.
3. Add numeric QQ group IDs to `QQ_PERSONAL_GROUPS` and numeric friend QQ IDs to `QQ_PERSONAL_USERS`. Unlisted conversations are rejected. There is no wildcard or automatic monitoring of every chat.
4. Call `qq_status`, `qq_targets`, and `qq_context`. Context fetches at most 100 messages per call using NapCat's `get_group_msg_history` / `get_friend_msg_history` extension. This is an on-demand snapshot, not a realtime listener. Unsupported extensions fail explicitly.

Remote NapCat requires both `QQ_ALLOW_REMOTE_NAPCAT=true` and HTTPS with a valid certificate. Loopback must be a literal address (`127.0.0.1` or `[::1]`), preventing hostname rebinding. TLS verification cannot be disabled. Redirects, ambient proxy settings, raw arbitrary API calls, credential-fetching endpoints, moderation and account actions are not exposed.

## Tools and send safety

- `qq_status(probe=false)`: configuration and provider capability status; `probe=true` makes read-only authentication/status requests.
- `qq_targets(provider, kind)`: configured official targets or the allowlisted personal target list.
- `qq_context(provider, kind, target, limit=20, message_cursor="", reverse_order=false)`: bounded context, labelled as untrusted chat content. Personal history accepts a returned `message_id` as `message_cursor` (including negative IDs); direction and available history depend on NapCat. There is no promise of complete or gap-free pagination.
- `qq_send(provider, kind, target, text, idempotency_key, reply_to="")`: explicit plain-text send; requires `QQ_ENABLE_SEND=true`.
- `qq_forget(provider, kind, target)`: delete that target's locally retained webhook context; does not delete upstream messages or the send ledger.

Sending is disabled by default. Enabling it is an operator decision; the MCP host must also obtain the user's intent to send the exact text to the exact recipient. Incoming chat text is untrusted data and must never authorize another tool call. No send happens on startup or from webhook receipt.

Use a unique `idempotency_key` for each intentional message and reuse that exact key when retrying the same request. A SQLite reservation is committed **before** dispatch. Successful repeats return the saved message ID. Failed, timed-out or interrupted dispatches remain `unknown` and are not resent automatically. Check delivery manually before using a new key. Guarantees apply to this shared database for **seven days**; separate databases or keys do not deduplicate each other. The ledger stores a payload hash and bounded result, not the message text. It rejects new sends if its 10,000-entry limit is reached until entries expire. Official reply sequence tracking is retained for 24 hours.

## Data handling and operations

Official webhook context is stored locally for up to 24 hours and 2,000 messages by default (`QQ_RETENTION_SECONDS`, maximum seven days; `QQ_MAX_MESSAGES`, maximum 10,000). Messages are limited to 8,000 characters. Personal history is returned on demand and is not persisted by this server. The MCP host may keep its own conversation logs. Names and messages in every result are untrusted content.

The default SQLite path is `~/.local/share/qq-mcp/state.sqlite3`; prefer an explicit absolute path. Protect the containing directory with operating-system permissions. On POSIX, the database file is set to mode 0600; on Windows, restrict its directory's ACL to your user. SQLite WAL and filesystem snapshots can retain deleted bytes; forgetting is logical deletion, not guaranteed forensic erasure. Stop both processes and securely remove the database directory if you need to retire all state. Retention cleanup runs on reads/writes and periodically while either process runs.

All outbound requests have finite timeouts and 1 MiB response limits. Webhook bodies are limited to 256 KiB. HTTP redirects and automatic mutation retries are disabled. Errors do not return credentials, raw provider responses or request bodies. The server does not log chat text. Keep your machine clock synchronized for the five-minute webhook replay window.

## Development and validation

```sh
uv sync --frozen --extra dev
uv run pytest
uv run ruff check .
```

Tests use mock HTTP transports and signed synthetic events, not user accounts. They cover denied scopes, token reuse, send payloads and idempotency, timeout/unknown sends, webhook signatures/challenges, replay/tamper rejection, bounded retention, and MCP tool discovery. Real QQ developer approval, live callback delivery, platform quotas, and NapCat compatibility must be checked in your own staging accounts. No live message has been sent as part of repository validation.

## Primary references

Verified against these sources on 2026-09-17:

- [QQ token exchange and API authentication](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/api-use.html)
- [QQ event subscription, challenge and acknowledgement](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)
- [QQ Ed25519 signature verification](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html)
- [QQ group send API](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html) and [C2C send API](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_users_user_openid_messages.post.html)
- [QQ message overview and quotas](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)
- [Official Tencent bot profile API implementation](https://github.com/tencent-connect/botgo/blob/master/openapi/v1/me.go)
- [OneBot v11 API specification](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)
- [NapCat group history implementation](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/go-cqhttp/GetGroupMsgHistory.ts) and [friend history implementation](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/go-cqhttp/GetFriendMsgHistory.ts)

## 한국어 안내

**공식 QQ 봇과 개인 QQ 계정(NapCat)을 둘 다 지원합니다.** 하나의 MCP 인스턴스에서 함께 켤 수 있습니다. 공식 봇은 별도 봇 계정이며 개인 계정의 기존 대화를 읽을 수 없습니다. 개인 계정은 사용자가 설치하고 로그인한 NapCat에 연결합니다.

1. `uv sync --frozen --extra dev`로 설치하고 `.env.example`을 `.env`로 복사합니다.
2. 공식 봇은 `QQ_ENABLE_OFFICIAL=true`, AppID/AppSecret, 허용할 봇 전용 그룹/사용자 open ID를 설정합니다. `uv run --env-file .env qq-mcp webhook`을 별도로 실행하고 HTTPS 프록시를 통해 QQ 콜백을 연결합니다.
3. 개인 계정은 `QQ_ENABLE_PERSONAL=true`, NapCat 주소·토큰, 허용할 숫자 QQ 그룹/친구 ID를 설정합니다.
4. `uv run --env-file .env qq-mcp`를 MCP 클라이언트의 로컬 서버로 등록합니다. 웹훅과 MCP는 같은 `QQ_STATE_PATH`를 사용해야 합니다.
5. `qq_status`, `qq_targets`, `qq_context`로 상태와 허용된 대화를 확인합니다. 빈 허용 목록은 모든 대화를 차단합니다.

발송은 기본적으로 꺼져 있습니다. `QQ_ENABLE_SEND=true` 설정 후에도 대상·본문·중복 방지 키를 명시해야 합니다. 공식 봇은 서명 검증된 수신 메시지의 `reply_to`로만 답장합니다. 재시도 시 같은 `idempotency_key`를 사용하세요. 결과가 `unknown`이면 전달 여부를 먼저 확인하고 새 키를 발급해야 합니다. 웹훅 수신만으로 자동 발송하지 않습니다.

공식 봇 수신 기록은 기본 24시간/2,000개만 로컬에 보관합니다. 개인 계정 기록은 필요한 대상에 대해 최대 100개씩 조회하며 이 서버에 저장하지 않습니다. NapCat의 기록이 전체 과거 대화를 보장하지는 않습니다. 이 버전은 모의 API 및 서명 이벤트로 검증했으며, 실제 계정·권한·QQ 콜백·NapCat 버전별 실연동은 별도 확인이 필요합니다.
