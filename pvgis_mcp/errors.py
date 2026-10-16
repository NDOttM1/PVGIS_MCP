"""Fehlerklassen mit verständlichen Meldungen für Agenten und Nutzer."""

from __future__ import annotations

import json
from typing import Any

# Fehler müssen von der ToolError-Klasse des SDK erben: Nur dann reicht MCP die Meldung an das
# Sprachmodell weiter (SDK 2.x zeigt bei sonstigen Ausnahmen nur "Error executing tool").
try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver.exceptions import ToolError as _ToolError
except ImportError:  # pragma: no cover - SDK 1.x
    try:
        from mcp.server.fastmcp.exceptions import ToolError as _ToolError  # type: ignore[no-redef]
    except ImportError:
        _ToolError = Exception  # type: ignore[misc,assignment]


class PvgisError(_ToolError):
    """Allgemeiner Fehler bei einem PVGIS-Aufruf."""

    def __init__(self, message: str, *, status: int | None = None, request_id: str | None = None,
                 api: str | None = None, details: Any = None):
        self.status = status
        self.request_id = request_id
        self.api = api
        self.details = details
        parts = [message]
        if status is not None:
            parts.append(f"(HTTP {status}{', ' + api if api else ''})")
        if request_id:
            parts.append(f"Request-ID für Support-Tickets: {request_id}")
        super().__init__(" ".join(parts))


class PvgisInputError(PvgisError):
    """Die Eingaben wurden von PVGIS abgelehnt (400/422) oder lokal als ungültig erkannt."""


class PvgisRateLimitError(PvgisError):
    """Ein Rate-Limit ist erschöpft und die Wartezeit wäre zu lang."""

    def __init__(self, message: str, *, wait_s: float, api: str | None = None):
        self.wait_s = wait_s
        super().__init__(message, status=429, api=api)


class PvgisUnavailableError(PvgisError):
    """Dienst/Daten vorübergehend nicht erreichbar (450, 460, 5xx, 529, Netzwerk)."""


STATUS_TEXT = {
    400: "Ungültige Anfrage",
    404: "Dienst bzw. Ressource nicht verfügbar",
    405: "Methode nicht erlaubt",
    422: "Eingaben ungültig",
    429: "Zu viele Anfragen (Rate-Limit)",
    450: "Daten nicht erreichbar",
    460: "PVGIS-Dienste nicht erreichbar",
    500: "Serverfehler bei PVGIS",
    502: "Bad Gateway bei PVGIS",
    503: "PVGIS vorübergehend nicht verfügbar",
    504: "Zeitüberschreitung bei PVGIS",
    529: "PVGIS überlastet",
    550: "Fehler beim Lesen der PVGIS-Daten",
}


def describe_error_body(body: bytes | str) -> tuple[str, Any, str | None]:
    """Extrahiert eine lesbare Meldung aus einer PVGIS-Fehlerantwort.

    Gibt (meldung, rohdetails, request_id) zurück. Unterstützt
    PVGIS 6 (FastAPI: ``detail`` als Text oder Liste) und PVGIS 5.3 (``message``).
    """
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return (text.strip()[:500] or "keine Details"), None, None
    if not isinstance(data, dict):
        return str(data)[:500], data, None
    request_id = data.get("request_id")
    if "message" in data:  # PVGIS 5.3
        return str(data["message"]), data, request_id
    detail = data.get("detail", data.get("error", data))
    if isinstance(detail, list):  # FastAPI-Validierungsfehler
        msgs = []
        seen = set()
        for item in detail:
            if not isinstance(item, dict):
                msgs.append(str(item))
                continue
            loc = [str(x) for x in item.get("loc", []) if x not in ("query", "body", "path")]
            msg = item.get("msg", "")
            line = f"{'.'.join(loc) or 'Eingabe'}: {msg}"
            if "input" in item and item["input"] not in (None, ""):
                line += f" (übergeben: {item['input']})"
            if line not in seen:
                seen.add(line)
                msgs.append(line)
        return "; ".join(msgs[:8]) or "Validierungsfehler", detail, request_id
    return str(detail)[:800], data, request_id
