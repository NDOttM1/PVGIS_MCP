"""Konfiguration über Umgebungsvariablen.

Alle Werte haben sinnvolle Voreinstellungen; überschreiben per Umgebungsvariable,
z. B. in der Claude-Desktop-Konfiguration (Feld ``env``) oder in einer ``.env``-Datei.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

V6_DEFAULT_URL = "https://photovoltaic-geographic-information-system.ec.europa.eu/api/v6"
V53_DEFAULT_URL = "https://re.jrc.ec.europa.eu/api/v5_3"

# Dokumentierte Limits der PVGIS-6-Web-API pro Client: 10/s, 100/min, 200/h.
V6_LIMITS: tuple[tuple[int, float], ...] = ((10, 1.0), (100, 60.0), (200, 3600.0))
# PVGIS 5.3: 30 Aufrufe/s pro IP – mit Sicherheitsabstand.
V53_LIMITS: tuple[tuple[int, float], ...] = ((25, 1.0),)

# Zeitliche Abdeckung der PVGIS-6-Web-API.
V6_FIRST_YEAR = 2014
V6_LAST_YEAR = 2024


def _project_root() -> Path:
    """Projektordner: Elternordner des Pakets, wenn dort eine pyproject.toml liegt."""
    pkg_parent = Path(__file__).resolve().parent.parent
    if (pkg_parent / "pyproject.toml").exists():
        return pkg_parent
    return Path.home() / "PVGIS-MCP"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "ja", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_limits(name: str, default: tuple[tuple[int, float], ...]) -> tuple[tuple[int, float], ...]:
    """Format: ``10/1,100/60,200/3600`` (Anzahl/Sekunden)."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        out = []
        for part in raw.split(","):
            n, w = part.strip().split("/")
            out.append((int(n), float(w)))
        return tuple(out)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    v6_base_url: str = V6_DEFAULT_URL
    v53_base_url: str = V53_DEFAULT_URL
    output_dir: Path = field(default_factory=lambda: _project_root() / "output")
    cache_path: Path = field(default_factory=lambda: _project_root() / ".cache" / "pvgis_cache.sqlite")
    cache_enabled: bool = True
    cache_ttl_days: float = 180.0
    timeout_s: float = 180.0
    # Längste Wartezeit auf ein freies Rate-Limit-Fenster, bevor ein Aufruf mit Hinweis abbricht.
    max_wait_s: float = 90.0
    max_retries: int = 3
    enable_v53: bool = True
    v6_limits: tuple[tuple[int, float], ...] = V6_LIMITS
    v53_limits: tuple[tuple[int, float], ...] = V53_LIMITS
    batch_max_cases: int = 150
    # CSV-Stil: "de" = Semikolon + Dezimalkomma (Excel DE), "intl" = Komma + Dezimalpunkt
    csv_style: str = "de"
    user_agent: str = "pvgis-mcp/0.1 (+https://github.com/modelcontextprotocol)"

    @classmethod
    def from_env(cls) -> "Settings":
        root = _project_root()
        out_dir = Path(os.environ.get("PVGIS_OUTPUT_DIR") or (root / "output")).expanduser()
        cache = Path(os.environ.get("PVGIS_CACHE_PATH") or (root / ".cache" / "pvgis_cache.sqlite")).expanduser()
        style = (os.environ.get("PVGIS_CSV_STYLE") or "de").strip().lower()
        return cls(
            v6_base_url=(os.environ.get("PVGIS_V6_BASE_URL") or V6_DEFAULT_URL).rstrip("/"),
            v53_base_url=(os.environ.get("PVGIS_V53_BASE_URL") or V53_DEFAULT_URL).rstrip("/"),
            output_dir=out_dir,
            cache_path=cache,
            cache_enabled=_env_bool("PVGIS_CACHE_ENABLED", True),
            cache_ttl_days=_env_float("PVGIS_CACHE_TTL_DAYS", 180.0),
            timeout_s=_env_float("PVGIS_TIMEOUT_S", 180.0),
            max_wait_s=_env_float("PVGIS_MAX_WAIT_S", 90.0),
            max_retries=int(_env_float("PVGIS_MAX_RETRIES", 3)),
            enable_v53=_env_bool("PVGIS_ENABLE_V53", True),
            v6_limits=_env_limits("PVGIS_V6_LIMITS", V6_LIMITS),
            v53_limits=_env_limits("PVGIS_V53_LIMITS", V53_LIMITS),
            batch_max_cases=int(_env_float("PVGIS_BATCH_MAX_CASES", 150)),
            csv_style=style if style in {"de", "intl"} else "de",
        )
