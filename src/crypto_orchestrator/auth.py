from __future__ import annotations

import json
from collections.abc import Iterable

from .account_context import account_scope
from .accounts import AccountManager


class AccountMiddleware:
    """Resolve every HTTP/MCP request to an authenticated account."""

    _PUBLIC_PATHS = frozenset({
        "/health",
        "/docs",
        "/docs/oauth2-redirect",
        "/openapi.json",
        "/redoc",
        "/",
        "/app.js",
        "/styles.css",
    })

    def __init__(self, app, account_manager: AccountManager):
        self.app = app
        self.account_manager = account_manager

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "").rstrip("/") or "/"
        if path in self._PUBLIC_PATHS or (
            path == "/api/v1/accounts" and scope.get("method") == "POST"
        ):
            await self.app(scope, receive, send)
            return

        access_token = self._bearer_token(scope.get("headers", ()))
        account = self.account_manager.authenticate(access_token)
        if account is None:
            await self._unauthorized(send)
            return

        with account_scope(account.account_id):
            await self.app(scope, receive, send)

    @staticmethod
    def _bearer_token(headers: Iterable[tuple[bytes, bytes]]) -> str | None:
        authorization: str | None = None
        for name, value in headers:
            if name.lower() == b"authorization":
                authorization = value.decode("latin-1")
                break
        if not authorization:
            return None
        scheme, separator, credentials = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer":
            return None
        credentials = credentials.strip()
        return credentials or None

    @staticmethod
    async def _unauthorized(send) -> None:
        body = json.dumps({"detail": "authentication required"}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b"Bearer"),
        ]
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})
