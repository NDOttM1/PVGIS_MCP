"""HTTP-Transporte (Streamable HTTP und SSE) mit optionalem Bearer-Token und Health-Endpunkt."""

from __future__ import annotations

import hmac
import inspect
import json
import logging
from typing import Any

log = logging.getLogger("pvgis_mcp.transport")

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class BearerAuthMiddleware:
    """Reine ASGI-Middleware: verlangt ``Authorization: Bearer <token>`` (außer für /health)."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "") in ("/health", "/healthz"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        supplied = auth[7:].strip().encode() if auth.lower().startswith("bearer ") else b""
        if supplied and hmac.compare_digest(supplied, self.token):
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized", "detail": "Bearer-Token fehlt oder ist falsch"}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
        await send({"type": "http.response.body", "body": body})


def _security(args) -> Any:
    from mcp.server.transport_security import TransportSecuritySettings

    hosts = [h.strip() for h in (args.allowed_hosts or "").split(",") if h.strip()]
    if args.host in _LOOPBACK and not hosts:
        hosts = [f"127.0.0.1:{args.port}", f"localhost:{args.port}", "127.0.0.1", "localhost"]
        return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                         allowed_origins=[f"http://{h}" for h in hosts])
    if hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                         allowed_origins=[f"http://{h}" for h in hosts] +
                                         [f"https://{h}" for h in hosts])
    # Netzwerkbetrieb ohne feste Hostliste: Schutz über Bearer-Token
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _call(fn, **kwargs):
    """Ruft fn nur mit den Parametern auf, die die installierte SDK-Version kennt."""
    params = inspect.signature(fn).parameters
    return fn(**{k: v for k, v in kwargs.items() if k in params})


def build_http_app(server, args):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from . import __version__

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request):
        return JSONResponse({"status": "ok", "server": "pvgis-mcp", "version": __version__})

    from .server import SDK_MAJOR

    security = _security(args)
    v1 = SDK_MAJOR == 1
    if args.transport == "sse":
        if v1:  # SDK 1.x
            server.settings.transport_security = security
        app = _call(server.sse_app, transport_security=security, host=args.host)
    else:
        if v1:  # SDK 1.x: Konfiguration über settings
            server.settings.streamable_http_path = args.path
            server.settings.stateless_http = args.stateless
            server.settings.json_response = args.json_response
            server.settings.transport_security = security
        app = _call(server.streamable_http_app, streamable_http_path=args.path, stateless_http=args.stateless,
                    json_response=args.json_response, transport_security=security, host=args.host)
    if args.token:
        app = BearerAuthMiddleware(app, args.token)
    elif args.host not in _LOOPBACK:
        log.warning("HTTP-Server ist im Netzwerk erreichbar, aber ohne Token (--token / PVGIS_MCP_TOKEN).")
    return app


def run_http(server, args) -> None:
    import uvicorn

    app = build_http_app(server, args)
    endpoint = "/sse" if args.transport == "sse" else args.path
    log.warning("pvgis-mcp %s auf http://%s:%s%s", args.transport, args.host, args.port, endpoint)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
