# Security and privacy

This initial release has mock-based tests, not a full security audit or live-account certification.

- The MCP interface is local stdio. Do not wrap it in an unauthenticated network gateway.
- Both providers and sending are opt-in. Empty allowlists deny all targets. There is no wildcard or tool to change scopes.
- QQ official events require Ed25519 signature verification over the original bytes and a timestamp within five minutes. URL-validation challenges are the official protocol's unsigned exception: they are app-bound, size/freshness/rate limited and cannot store events or send messages. Challenge tokens must use only ASCII letters, digits, underscore or hyphen; JSON and control characters are rejected so the challenge cannot sign an attacker-created event body.
- The callback listener binds loopback only. The operator must provide an HTTPS reverse proxy with rate/body limits for public QQ delivery. Keep AppSecret private.
- NapCat requires a bearer token even locally. Non-loopback hosts require explicit configuration and HTTPS. TLS validation, response bounds and timeouts are mandatory. Redirects and environment proxies are disabled.
- We return only selected identity/message fields. Credentials, raw HTTP errors, event extension fields and auth tokens are not exposed. User-generated text and names remain untrusted even when an authenticated provider delivered them.
- All sends require a target and a durable idempotency key. Unknown delivery is never automatically retried. Deduplication lasts seven days only, applies to one shared SQLite database, and is not an upstream exactly-once guarantee.
- Official received messages are retained in a bounded SQLite database. Restrict filesystem ACLs and backups. Logical deletion cannot guarantee secure erasure from WAL files or snapshots. Personal history is not persisted by this server. Your MCP host may separately retain results.
- No incoming chat message can authorize sending or alter scopes. MCP clients must obtain the user's actual intent before using the send tool.
- Personal-account connectivity depends on unofficial NapCat/QQ client behavior, including account restriction and compatibility risks. Keep NapCat updated separately.

To report a vulnerability, use the repository's private GitHub vulnerability reporting feature if enabled. Do not post credentials, session databases, message contents or active attack details in a public issue. If private reporting is unavailable, open an issue requesting a private contact channel without sensitive details. Rotate affected credentials immediately after a suspected disclosure.
