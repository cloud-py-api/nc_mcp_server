"""Multi-user mode: one HTTP server, one Nextcloud login per request.

With ``NEXTCLOUD_MCP_MULTIUSER=true`` the server holds no account of its own. Every HTTP request
must carry ``Authorization: Basic <login:app-password>``. :class:`MultiUserAuthMiddleware` checks
the header before the MCP layer sees the request, resolves the login to its Nextcloud user ID
(one OCS call per new login, which also proves the credentials), and pins the matching client to
the request. Tools keep calling :func:`nc_mcp_server.state.get_client` and
:func:`nc_mcp_server.state.get_config`; in this mode those return the request's own client and a
config whose ``user`` is the request's user ID.

Rules:

* No header, a malformed header or credentials Nextcloud rejects -> HTTP 401, nothing runs.
* Exception: discovery without any ``Authorization`` header. A POST whose JSON-RPC messages are all
  in :data:`DISCOVERY_METHODS` (``initialize``, ``ping``, ``tools/list``, notifications) runs
  without a login. Some clients list tools at startup, before they know who will call them; the
  answer is the same static tool list for everyone and no Nextcloud call happens. Everything else
  (``tools/call`` above all) still needs a login. A request that sends a header that is wrong is
  refused, never downgraded to discovery. GET/DELETE without a login get 405 (no SSE stream).
* Clients are cached per login (LRU, idle TTL). The cache key is an HMAC of login and password
  with a per-process random key, so neither the password nor a plain hash of it is kept as a key.
* A request can lower, never raise, the server's permission level with the
  ``X-Nextcloud-MCP-Permissions`` header (e.g. ``read`` for a read-only service account).
* The identity comes from each request's header, never from the MCP session: a session ID is not
  a credential, and two requests in one session may run as different logins.
* On ASGI lifespan shutdown the pool closes every cached client.
"""

import asyncio
import base64
import binascii
import contextlib
import contextvars
import dataclasses
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Generator
from typing import Any, cast

import niquests
from mcp.server.lowlevel.server import request_ctx

from .client import NextcloudClient
from .config import Config
from .permissions import PermissionLevel, set_cap_provider

log = logging.getLogger(__name__)

PERMISSIONS_HEADER = "x-nextcloud-mcp-permissions"
# A replaced or evicted client may still serve a tool call that started before; close it later.
CLOSE_GRACE_SECONDS = 600.0
_MAX_HEADER_BYTES = 4096
# Upper bound for a discovery body read before the login check; real ones are a few hundred bytes.
_MAX_DISCOVERY_BODY_BYTES = 64 * 1024
# JSON-RPC methods that run without a login: protocol handshake and the static tool list.
DISCOVERY_METHODS = frozenset(
    {
        "initialize",
        "ping",
        "tools/list",
        "notifications/initialized",
        "notifications/cancelled",
    }
)
_AMBIGUOUS = "\x00ambiguous"


class AuthenticationRequiredError(Exception):
    """A tool ran without the login of a request (multi-user mode)."""


@dataclasses.dataclass(frozen=True)
class Credentials:
    """A Nextcloud login taken from one request. ``repr`` never shows the password."""

    login: str
    password: str = dataclasses.field(repr=False)


@dataclasses.dataclass
class Binding:
    """What a request runs as: its client, its config and its permission cap."""

    client: NextcloudClient
    config: Config
    permission_cap: PermissionLevel | None = None


def parse_basic_auth(value: str | None) -> Credentials | None:
    """Parse an ``Authorization`` header value. Returns None unless it is a usable Basic login."""
    if not value or len(value) > _MAX_HEADER_BYTES:
        return None
    scheme, _, token = value.strip().partition(" ")
    if scheme.lower() != "basic" or not token.strip():
        return None
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    login, sep, password = decoded.partition(":")
    if not sep or not login or not password:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in login + password):
        return None
    return Credentials(login=login, password=password)


def parse_permission_cap(value: str | None) -> PermissionLevel | None:
    """Parse ``X-Nextcloud-MCP-Permissions``. Raises ValueError for unknown values."""
    if value is None or not value.strip():
        return None
    return PermissionLevel(value.strip().lower())


def lowest(a: PermissionLevel, b: PermissionLevel | None) -> PermissionLevel:
    """The lower of two permission levels (``b`` None means no cap)."""
    if b is None:
        return a
    return b if a.includes(b) else a


# User-ID lookup for a login: (base config, credentials) -> user ID. Raises LoginRejectedError.
UserIdLookup = Callable[[Config, Credentials], Awaitable[str]]


class LoginRejectedError(Exception):
    """Nextcloud refused the login (HTTP 401/403)."""


class NextcloudUnavailableError(Exception):
    """Nextcloud could not be asked (network error, 5xx, unexpected answer)."""


async def lookup_user_id(base: Config, creds: Credentials) -> str:
    """Ask Nextcloud who the login belongs to (``GET /ocs/v2.php/cloud/user``)."""
    url = f"{base.nextcloud_url}/ocs/v2.php/cloud/user"
    try:
        async with niquests.AsyncSession(
            auth=(creds.login, creds.password),
            timeout=15,
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
        ) as session:
            response = await session.get(url, params={"format": "json"})
    except (OSError, niquests.RequestException) as exc:
        raise NextcloudUnavailableError(type(exc).__name__) from None
    code = response.status_code or 0
    if code in (401, 403):
        raise LoginRejectedError(str(code))
    if not response.ok:
        raise NextcloudUnavailableError(f"HTTP {code}")
    try:
        user_id = response.json()["ocs"]["data"]["id"]
    except (ValueError, KeyError, TypeError):
        raise NextcloudUnavailableError("unexpected answer") from None
    if not isinstance(user_id, str) or not user_id:
        raise NextcloudUnavailableError("unexpected answer")
    return user_id


@dataclasses.dataclass
class _Entry:
    client: NextcloudClient
    config: Config
    last_used: float


class ClientPool:
    """Clients per login: LRU with an idle TTL. Never shares a client between two logins."""

    def __init__(
        self,
        base: Config,
        *,
        lookup: UserIdLookup = lookup_user_id,
        clock: Callable[[], float] = time.monotonic,
        close_grace: float = CLOSE_GRACE_SECONDS,
    ) -> None:
        self._base = base
        self._lookup = lookup
        self._clock = clock
        self._close_grace = close_grace
        self._key = secrets.token_bytes(32)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._pending: dict[str, asyncio.Future[_Entry]] = {}
        # Retired clients waiting out the grace period, so close() can close them right away.
        self._closing: dict[asyncio.Task[None], NextcloudClient] = {}

    def _cache_key(self, creds: Credentials) -> str:
        message = creds.login.encode("utf-8") + b"\0" + creds.password.encode("utf-8")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def __len__(self) -> int:
        return len(self._entries)

    async def get(self, creds: Credentials) -> tuple[NextcloudClient, Config]:
        """The client and config for a login; the first use of a login asks Nextcloud for its user ID.

        Raises LoginRejectedError or NextcloudUnavailableError when the login cannot be used.
        """
        self._expire()
        key = self._cache_key(creds)
        entry = self._entries.get(key)
        if entry is not None:
            entry.last_used = self._clock()
            self._entries.move_to_end(key)
            return entry.client, entry.config
        # Concurrent first requests of one login share a single lookup.
        future = self._pending.get(key)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._pending[key] = future
            try:
                user_id = await self._lookup(self._base, creds)
                config = dataclasses.replace(
                    self._base,
                    user=user_id,
                    login=creds.login,
                    password=creds.password,
                    is_app_password=True,
                )
                entry = _Entry(client=NextcloudClient(config), config=config, last_used=self._clock())
            except BaseException as exc:
                future.set_exception(exc)
                # Retrieve it so an unawaited future does not log "exception was never retrieved".
                future.exception()
                raise
            finally:
                self._pending.pop(key, None)
            future.set_result(entry)
            self._entries[key] = entry
            self._evict_overflow()
            return entry.client, entry.config
        entry = await asyncio.shield(future)
        return entry.client, entry.config

    def _expire(self) -> None:
        cutoff = self._clock() - self._base.multiuser_ttl
        for key in [k for k, e in self._entries.items() if e.last_used <= cutoff]:
            self._retire(self._entries.pop(key))

    def _evict_overflow(self) -> None:
        while len(self._entries) > self._base.multiuser_cache_size:
            _, entry = self._entries.popitem(last=False)
            self._retire(entry)

    def _retire(self, entry: _Entry) -> None:
        async def close_later() -> None:
            await asyncio.sleep(self._close_grace)
            await entry.client.close()

        try:
            task = asyncio.get_running_loop().create_task(close_later())
        except RuntimeError:
            return
        self._closing[task] = entry.client
        task.add_done_callback(lambda t: self._closing.pop(t, None))

    async def close(self) -> None:
        """Close every client now, cached or retired (server shutdown)."""
        clients = list(self._closing.values())
        for task in list(self._closing):
            task.cancel()
        self._closing.clear()
        clients.extend(entry.client for entry in self._entries.values())
        self._entries = OrderedDict()
        for client in clients:
            try:
                await client.close()
            except Exception:
                log.warning("Closing a Nextcloud client failed", exc_info=True)


# The binding of the running request. Set by the middleware (or bind() in tests and scripts).
_binding: contextvars.ContextVar[Binding | None] = contextvars.ContextVar("nc_mcp_binding", default=None)
_SCOPE_KEY = "nc_mcp_binding"


def current_binding() -> Binding:
    """The binding of the running request. Raises AuthenticationRequiredError without one.

    Looks first at the MCP request context (the Starlette request the tool call came with), then at
    the context variable. The MCP layer may run tools in a task created outside the HTTP request, so
    the request's own scope is the reliable source.
    """
    binding = _binding_from_request_context()
    if binding is None:
        binding = _binding.get()
    if binding is None:
        raise AuthenticationRequiredError(
            "No Nextcloud login for this call. In multi-user mode every request needs its own "
            "'Authorization: Basic <login:app-password>' header."
        )
    return binding


def _binding_from_request_context() -> Binding | None:
    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    request = getattr(ctx, "request", None)
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        return None
    state = cast(dict[str, Any], scope).get("state")
    if not isinstance(state, dict):
        return None
    binding = cast(dict[str, Any], state).get(_SCOPE_KEY)
    return binding if isinstance(binding, Binding) else None


def current_cap(level: PermissionLevel) -> PermissionLevel:
    """``level`` lowered by the running request's permission cap, if a multi-user request is running."""
    binding = _binding_from_request_context() or _binding.get()
    return level if binding is None else lowest(level, binding.permission_cap)


set_cap_provider(current_cap)


@contextlib.contextmanager
def bind(binding: Binding) -> Generator[None]:
    """Run code as one login (tests, scripts). The middleware does the same per request."""
    token = _binding.set(binding)
    try:
        yield
    finally:
        _binding.reset(token)


ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]], Awaitable[None]]


class MultiUserAuthMiddleware:
    """ASGI middleware: no valid login, no MCP beyond discovery. Pins the login's client to the request."""

    def __init__(self, app: ASGIApp, pool: ClientPool, server_level: PermissionLevel) -> None:
        self.app = app
        self.pool = pool
        self.server_level = server_level

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope.get("type") != "http":
            if scope.get("type") == "lifespan":
                send = self._closing_pool_on_shutdown(send)
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        if "authorization" not in headers:
            await self._without_login(scope, receive, send)
            return
        creds = parse_basic_auth(headers.get("authorization"))
        if creds is None:
            await _reply(send, 401, "Authentication required: send 'Authorization: Basic <login:app-password>'.")
            return
        try:
            cap = parse_permission_cap(headers.get(PERMISSIONS_HEADER))
        except ValueError:
            await _reply(send, 400, "Invalid X-Nextcloud-MCP-Permissions: use read, write or destructive.")
            return
        try:
            client, config = await self.pool.get(creds)
        except LoginRejectedError:
            await _reply(send, 401, "Nextcloud rejected the login.")
            return
        except NextcloudUnavailableError as exc:
            log.warning("Login check failed: Nextcloud unavailable (%s)", exc)
            await _reply(send, 502, "Nextcloud is not reachable.")
            return
        binding = Binding(client=client, config=config, permission_cap=lowest(self.server_level, cap))
        state = scope.setdefault("state", {})
        state[_SCOPE_KEY] = binding
        token = _binding.set(binding)
        try:
            await self.app(scope, receive, send)
        finally:
            _binding.reset(token)

    def _closing_pool_on_shutdown(self, send: Callable[..., Any]) -> Callable[..., Awaitable[None]]:
        """A lifespan ``send`` that closes the pool's clients before the app reports shutdown complete."""

        async def wrapped(message: dict[str, Any]) -> None:
            if message.get("type") == "lifespan.shutdown.complete":
                await self.pool.close()
            await send(message)

        return wrapped

    async def _without_login(
        self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]
    ) -> None:
        """No Authorization header: only discovery runs (see module docstring), without a client."""
        method = scope.get("method")
        if method != "POST":
            await _reply(send, 405, "Method not allowed without a login.", allow=b"POST")
            return
        body = await _read_body(receive, _MAX_DISCOVERY_BODY_BYTES)
        if body is None or not is_discovery_body(body):
            await _reply(send, 401, "Authentication required: send 'Authorization: Basic <login:app-password>'.")
            return
        await self.app(scope, _replay(body, receive), send)


def is_discovery_body(body: bytes) -> bool:
    """True if ``body`` is a JSON-RPC message (or non-empty batch) whose methods are all discovery."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    messages = cast(list[Any], payload) if isinstance(payload, list) else [payload]
    if not messages:
        return False
    return all(isinstance(m, dict) and cast(dict[str, Any], m).get("method") in DISCOVERY_METHODS for m in messages)


async def _read_body(receive: Callable[..., Any], limit: int) -> bytes | None:
    """Read the whole request body; None if it is larger than ``limit`` or the client went away."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            return None
        chunk = cast(bytes, message.get("body", b""))
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay(body: bytes, receive: Callable[..., Any]) -> Callable[..., Awaitable[dict[str, Any]]]:
    """A ``receive`` that hands the already read body to the app once, then waits on the real one."""
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return cast(dict[str, Any], await receive())

    return replay


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    raw_headers = cast(list[tuple[bytes, bytes]], scope.get("headers") or [])
    for raw_name, raw_value in raw_headers:
        name = raw_name.decode("latin-1").lower()
        # A repeated Authorization or permissions header is ambiguous: refuse rather than pick one.
        if name in out and name in ("authorization", PERMISSIONS_HEADER):
            out[name] = _AMBIGUOUS
            continue
        out[name] = raw_value.decode("latin-1")
    return out


async def _reply(send: Callable[..., Any], status: int, message: str, allow: bytes | None = None) -> None:
    body = ('{"error": "' + message.replace('"', "'") + '"}').encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))]
    if status == 401:
        headers.append((b"www-authenticate", b'Basic realm="nc-mcp-server", charset="UTF-8"'))
    if allow is not None:
        headers.append((b"allow", allow))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
