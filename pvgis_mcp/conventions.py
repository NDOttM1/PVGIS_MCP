"""Einheitliche Konventionen und Umrechnungen zwischen PVGIS 6 und PVGIS 5.3.

Einheitliche Eingabe dieses Servers:
- Azimut als Kompassrichtung in Grad: 0 = Nord, 90 = Ost, 180 = Süd, 270 = West
  (entspricht PVGIS 6). Alternativ Himmelsrichtungen wie "S", "SO", "Südwest", "Ost".
- Neigung in Grad gegen die Horizontale (0 = flach, 90 = senkrecht).
- Systemverluste in Prozent (PVGIS-Standard 14 %).

PVGIS 5.3 erwartet den Azimut ("aspect") dagegen südbasiert: 0 = Süd, -90 = Ost, 90 = West.
"""

from __future__ import annotations

import re
from typing import Literal, Sequence, Union

from .errors import PvgisInputError

AzimuthInput = Union[float, int, str]

_DIRECTIONS = {
    "N": 0.0, "NNO": 22.5, "NO": 45.0, "ONO": 67.5, "O": 90.0, "OSO": 112.5, "SO": 135.0, "SSO": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5, "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}
_WORDS = [("NORD", "N"), ("NORTH", "N"), ("SUED", "S"), ("SÜD", "S"), ("SOUTH", "S"),
          ("OST", "O"), ("EAST", "O"), ("WEST", "W")]

_LABELS = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def parse_azimuth(value: AzimuthInput | None, default: float = 180.0) -> float:
    """Kompass-Azimut (0..360) aus Zahl oder Himmelsrichtung."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, (int, float)):
        az = float(value)
    else:
        raw = value.strip()
        try:
            az = float(raw.replace(",", "."))
        except ValueError:
            token = raw.upper().replace("-", "").replace(" ", "").replace("Ü", "Ü")
            for word, letter in _WORDS:
                token = token.replace(word, letter)
            token = token.replace("E", "O") if re.fullmatch(r"[NSEWO]+", token) else token
            if token not in _DIRECTIONS:
                raise PvgisInputError(
                    f"Unbekannte Himmelsrichtung '{value}'. Erlaubt: Grad (0=N, 90=O, 180=S, 270=W) "
                    f"oder {', '.join(_DIRECTIONS)}."
                )
            az = _DIRECTIONS[token]
    if not -360.0 <= az <= 360.0:
        raise PvgisInputError(f"Azimut {az} außerhalb von 0..360°.")
    return az % 360.0


def azimuth_label(compass: float) -> str:
    return _LABELS[int(((compass % 360) + 11.25) // 22.5) % 16]


def compass_to_v53_aspect(compass: float) -> float:
    """Kompass (S=180) -> PVGIS 5.3 aspect (S=0, O=-90, W=90)."""
    aspect = (compass % 360.0) - 180.0
    return 180.0 if aspect == -180.0 else aspect


def v53_aspect_to_compass(aspect: float) -> float:
    return (aspect + 180.0) % 360.0


def loss_to_efficiency(loss_pct: float) -> float:
    if not 0 <= loss_pct < 100:
        raise PvgisInputError("Systemverluste müssen zwischen 0 und 100 % liegen.")
    return round(1.0 - loss_pct / 100.0, 6)


ModuleTechnology = Literal["crystSi2025", "crystSi", "CIS", "CdTe"]
Mounting = Literal["free", "building"]

_V6_TECH = {"crystSi2025": ("cSi", " 2025"), "crystSi": ("cSi", ""), "CIS": ("CIS", ""), "CdTe": ("CdTe", "")}


def v6_module_model(technology: str, mounting: str) -> str:
    """Abbildung auf den PVGIS-6-Wert ``photovoltaic_module``."""
    if technology not in _V6_TECH:
        raise PvgisInputError(f"Unbekannte Modultechnologie '{technology}'. Erlaubt: {', '.join(_V6_TECH)}.")
    base, suffix = _V6_TECH[technology]
    mount = "Free standing" if mounting == "free" else "Integrated"
    return f"{base}:{mount}{suffix}"


def v53_technology(technology: str) -> str:
    return {"crystSi2025": "crystSi2025", "crystSi": "crystSi", "CIS": "CIS", "CdTe": "CdTe"}.get(technology, "crystSi")


HorizonInput = Union[str, Sequence[float], None]


def v6_horizon(horizon: HorizonInput) -> str:
    """'auto' -> PVGIS-Geländemodell, 'none' -> kein Horizont, Liste -> eigene Höhen ab Nord im Uhrzeigersinn."""
    if horizon is None or (isinstance(horizon, str) and horizon.lower() in {"auto", "pvgis", "dem", ""}):
        return "PVGIS"
    if isinstance(horizon, str) and horizon.lower() in {"none", "kein", "aus", "off"}:
        return "0,0,0,0"
    heights = _heights(horizon)
    return ",".join(f"{h:g}" for h in heights)


def v53_horizon(horizon: HorizonInput) -> dict:
    """Parameter für PVGIS 5.3 (usehorizon/userhorizon). Eigene Höhen beginnen dort ebenfalls bei Nord."""
    if horizon is None or (isinstance(horizon, str) and horizon.lower() in {"auto", "pvgis", "dem", ""}):
        return {"usehorizon": 1}
    if isinstance(horizon, str) and horizon.lower() in {"none", "kein", "aus", "off"}:
        return {"usehorizon": 0}
    return {"usehorizon": 1, "userhorizon": ",".join(f"{h:g}" for h in _heights(horizon))}


def _heights(horizon: HorizonInput) -> list[float]:
    if isinstance(horizon, str):
        parts = [p for p in re.split(r"[;,\s]+", horizon.strip()) if p]
        try:
            vals = [float(p) for p in parts]
        except ValueError as exc:
            raise PvgisInputError(
                "Horizont: 'auto', 'none' oder Liste von Höhen in Grad (ab Nord, im Uhrzeigersinn)."
            ) from exc
    else:
        vals = [float(v) for v in horizon]  # type: ignore[union-attr]
    if len(vals) < 4:
        raise PvgisInputError("Ein eigenes Horizontprofil braucht mindestens 4 Höhenwerte.")
    if any(v < 0 or v > 90 for v in vals):
        raise PvgisInputError("Horizonthöhen müssen zwischen 0 und 90° liegen.")
    return vals


def check_v6_years(start_year: int, end_year: int) -> None:
    from .config import V6_FIRST_YEAR, V6_LAST_YEAR

    if start_year > end_year:
        raise PvgisInputError("Startjahr liegt nach dem Endjahr.")
    if start_year < V6_FIRST_YEAR or end_year > V6_LAST_YEAR:
        raise PvgisInputError(
            f"PVGIS 6 deckt nur {V6_FIRST_YEAR}–{V6_LAST_YEAR} ab. "
            "Für ältere Jahre (ab 2005) die PVGIS-5.3-Werkzeuge verwenden."
        )


def check_coordinates(latitude: float, longitude: float) -> None:
    if not -90 <= latitude <= 90:
        raise PvgisInputError("Breitengrad muss zwischen -90 und 90 liegen.")
    if not -180 <= longitude <= 180:
        raise PvgisInputError("Längengrad muss zwischen -180 und 180 liegen.")
    if latitude > 75 or latitude < -60:
        raise PvgisInputError("PVGIS deckt nur 60°S bis 75°N ab.")
