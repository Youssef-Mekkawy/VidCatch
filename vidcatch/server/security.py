"""Why this file exists: the server can read the user's browser cookies (sign-in) and write files, so
a random web page must never be able to drive it. Three layers, all enforced here:

1. Host check      - only 127.0.0.1 / localhost / ::1 on our port. Stops DNS-rebinding attacks.
2. Origin check    - a request that carries an Origin header must come from our own page.
3. Session cookie  - /api/* needs the secret token. The launcher opens the app at /?t=<token>,
                     the server swaps that for an HttpOnly, SameSite=Strict cookie and redirects.

It is a plain ASGI middleware (not Starlette's BaseHTTPMiddleware) so Server-Sent Events stream
without buffering.
"""

from __future__ import annotations

import hmac
import json
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

COOKIE_NAME = "ytdl_session"
PUBLIC_API_PATHS = {"/api/ping"}  # used by a second launch to find the running instance

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "style-src 'self'; "
    "font-src 'self'; "
    "script-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)

SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]


def allowed_hosts_for(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


class SecurityMiddleware:
    def __init__(self, app, token: str, port: int) -> None:
        self.app = app
        self.token = token
        self.hosts = allowed_hosts_for(port)
        self.origins = {f"http://{host}" for host in self.hosts}

    # ── helpers ─────────────────────────────────────────────────────────────
    def _has_valid_cookie(self, headers: dict[bytes, bytes]) -> bool:
        raw = headers.get(b"cookie")
        if not raw:
            return False
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw.decode("latin-1"))
        except Exception:
            return False
        morsel = jar.get(COOKIE_NAME)
        return bool(morsel) and hmac.compare_digest(morsel.value, self.token)

    @staticmethod
    async def _respond(send, status: int, body: bytes, content_type: bytes, extra=()) -> None:
        headers = [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
            *SECURITY_HEADERS,
            *extra,
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _json_error(self, send, status: int, message: str) -> None:
        body = json.dumps({"detail": message}).encode()
        await self._respond(send, status, body, b"application/json")

    # ── ASGI ────────────────────────────────────────────────────────────────
    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope["headers"]}
        host = headers.get(b"host", b"").decode("latin-1").lower()
        if host not in self.hosts:
            await self._json_error(send, 400, "Invalid host.")
            return
        origin = headers.get(b"origin")
        if origin is not None and origin.decode("latin-1").lower() not in self.origins:
            await self._json_error(send, 403, "Cross-origin requests are not allowed.")
            return

        path = scope["path"]

        # Launcher hand-off: /?t=<token> -> set the session cookie and go to the clean URL.
        if path == "/" and scope["query_string"]:
            candidate = (parse_qs(scope["query_string"].decode("latin-1")).get("t") or [""])[0]
            if candidate and hmac.compare_digest(candidate, self.token):
                cookie = f"{COOKIE_NAME}={self.token}; Path=/; HttpOnly; SameSite=Strict"
                await self._respond(
                    send, 303, b"", b"text/plain",
                    extra=[(b"location", b"/"), (b"set-cookie", cookie.encode())],
                )
                return

        if (
            path.startswith("/api/")
            and path not in PUBLIC_API_PATHS
            and not self._has_valid_cookie(headers)
        ):
            await self._json_error(send, 401, "Open the app from its launcher.")
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                extra = [h for h in SECURITY_HEADERS if h[0] not in existing]
                if path.startswith("/api/") and b"cache-control" not in existing:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
