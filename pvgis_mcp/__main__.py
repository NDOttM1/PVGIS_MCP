"""Kommandozeile: ``pvgis-mcp`` bzw. ``python -m pvgis_mcp``.

Beispiele:
    pvgis-mcp                                   # stdio (Claude Desktop, Claude Code, Cursor …)
    pvgis-mcp --transport http --port 8765      # Streamable HTTP unter http://127.0.0.1:8765/mcp
    pvgis-mcp --transport http --host 0.0.0.0 --token GEHEIM   # im Netzwerk, mit Bearer-Token
    pvgis-mcp --transport sse --port 8765       # ältere Clients (SSE unter /sse)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="pvgis-mcp", description="MCP-Server für PVGIS (JRC)")
    ap.add_argument("--transport", choices=["stdio", "http", "sse"],
                    default=os.environ.get("PVGIS_MCP_TRANSPORT", "stdio"))
    ap.add_argument("--host", default=os.environ.get("PVGIS_MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PVGIS_MCP_PORT", "8765")))
    ap.add_argument("--path", default=os.environ.get("PVGIS_MCP_PATH", "/mcp"),
                    help="Pfad des Streamable-HTTP-Endpunkts (Standard /mcp)")
    ap.add_argument("--token", default=os.environ.get("PVGIS_MCP_TOKEN"),
                    help="Bearer-Token für HTTP/SSE (empfohlen, sobald nicht nur localhost)")
    ap.add_argument("--allowed-hosts", default=os.environ.get("PVGIS_MCP_ALLOWED_HOSTS", ""),
                    help="Kommagetrennte Host-Header für DNS-Rebinding-Schutz, z. B. 'pc01:8765,192.168.1.20:8765'")
    ap.add_argument("--stateless", action="store_true", default=os.environ.get("PVGIS_MCP_STATELESS") == "1",
                    help="Zustandsloses HTTP (für Clients ohne Sitzungsverwaltung)")
    ap.add_argument("--json-response", action="store_true",
                    default=os.environ.get("PVGIS_MCP_JSON_RESPONSE") == "1",
                    help="JSON statt SSE-Stream als HTTP-Antwort")
    ap.add_argument("--log-level", default=os.environ.get("PVGIS_LOG_LEVEL", "WARNING"))
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse(argv)
    # stdio: Logs ausschließlich nach stderr, stdout gehört dem Protokoll
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.log_level.upper() != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
    from .server import build_server

    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
        return
    from .transport import run_http

    run_http(server, args)


if __name__ == "__main__":
    main()
