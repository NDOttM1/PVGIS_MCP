"""MCP-Server: Registrierung der Werkzeuge, Resources und Prompts."""

from __future__ import annotations

import json
import logging

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server

    SDK_MAJOR = 2
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]

    SDK_MAJOR = 1

from mcp.types import ToolAnnotations

from . import __version__
from .config import V6_FIRST_YEAR, V6_LAST_YEAR
from .errors import PvgisInputError
from .services import get_services
from .tools_v6 import V6_TOOLS
from .tools_v53 import V53_TOOLS

log = logging.getLogger("pvgis_mcp")

INSTRUCTIONS = f"""\
PVGIS-MCP: Solarstrahlung und PV-Ertrag weltweit (60°S–75°N) aus dem Photovoltaic Geographical
Information System der EU-Kommission (JRC).

Werkzeugwahl:
- Standort prüfen: pvgis_site_check (Datenquellen, Höhe, Horizontverschattung).
- Feste PV-Anlage, eine Fläche: pvgis_estimate_yield (PVGIS 6, Mittel {V6_FIRST_YEAR}–{V6_LAST_YEAR}).
- Mehrere Flächen/Ost-West/Varianten an einem Ort: pvgis_compare_surfaces.
- Optimale Neigung/Ausrichtung: pvgis_optimize_orientation.
- Tracker (Einachs N-S/O-W, zweiachsig) und Stromgestehungskosten: pvgis_tracking_yield (PVGIS 5.3).
- Viele Standorte/Varianten: pvgis_batch_yield.
- Zeitreihen: pvgis_power_timeseries (PVGIS 6, {V6_FIRST_YEAR}–{V6_LAST_YEAR}),
  pvgis_hourly_series_legacy (PVGIS 5.3, 2005–2023, auch Tracker).
- Klima/Strahlung: pvgis_tmy, pvgis_monthly_radiation, pvgis_daily_profile, pvgis_degree_days.
- Inselanlage mit Batterie: pvgis_offgrid. Horizont: pvgis_horizon_profile. Sonnenstand: pvgis_solar_position.

Konventionen: Azimut als Kompassrichtung (0=N, 90=O, 180=S, 270=W) oder Kürzel wie 'S', 'SW', 'Ost'.
Neigung 0=waagerecht. Verluste in % (Standard 14). Leistung in kWp (Inselanlage in Wp).
Rate-Limit PVGIS 6: 200 Aufrufe/Stunde – Ergebnisse werden zwischengespeichert, gleiche Anfragen
sind kostenlos. Große Zeitreihen landen als CSV im Ausgabeordner; im Ergebnis steht der Dateipfad.
PVGIS 5.3 und PVGIS 6 nutzen unterschiedliche Datensätze/Jahre: innerhalb eines Vergleichs nur eine
Quelle verwenden. Quellenangabe in Berichten: 'PVGIS © Europäische Union'.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

PARAMETER_HELP = f"""# PVGIS-MCP – Parameter und Konventionen

## Ausrichtung
- **Azimut** (Kompass): 0 = Nord, 90 = Ost, 180 = Süd, 270 = West. Alternativ Kürzel: N, NO, O, SO, S, SW, W, NW,
  NNO, ONO, OSO, SSO, SSW, WSW, WNW, NNW sowie 'Süd', 'Südwest', 'Ost', 'West' usw.
- Intern umgerechnet: PVGIS 6 nutzt dieselbe Konvention, PVGIS 5.3 südbasiert (0 = Süd, -90 = Ost, 90 = West).
- **Neigung**: 0° waagerecht, 90° senkrecht.

## Anlage
- **module_technology**: crystSi2025 (Standard, aktuelle Koeffizienten), crystSi, CIS, CdTe
- **mounting**: free (freistehend/aufgeständert), building (gebäudeintegriert)
- **system_loss_pct**: Systemverluste in %, Standard 14 (PVGIS 6 rechnet intern mit Wirkungsgrad 0,86)
- **bifacial**, **bifaciality_factor** (typ. 0,7), **module_height_m**, **albedo** (Gras 0,2; Schnee 0,6)
- **horizon**: 'auto' (Geländemodell), 'none' oder Liste von Höhen (Grad, ab Nord im Uhrzeigersinn)

## Datenbasis
| | PVGIS 6 | PVGIS 5.3 |
|---|---|---|
| Jahre | {V6_FIRST_YEAR}–{V6_LAST_YEAR} | 2005–2023 (SARAH3) |
| Strahlung | SARAH-3, ERA5 | PVGIS-SARAH3, PVGIS-ERA5 |
| Tracker | – | einachsig N-S/O-W/geneigt/vertikal, zweiachsig |
| Bifazial | ja | – |
| TMY, Inselanlage, LCOE | – | ja |

## Tracker-Systeme (pvgis_tracking_yield)
fixed, fixed_optimal, horizontal_ns (Standard-Freiflächentracker), inclined_axis, vertical_axis, two_axis,
horizontal_ew

## Limits
PVGIS 6: 10/s, 100/min, 200/h je Client. PVGIS 5.3: 30/s. Cache in SQLite, prozessübergreifend.
"""


def build_server() -> _Server:
    server = _Server(name="pvgis", instructions=INSTRUCTIONS)
    settings = get_services().settings
    tools = list(V6_TOOLS) + (list(V53_TOOLS) if settings.enable_v53 else [])
    for fn in tools:
        server.tool(annotations=READ_ONLY)(fn)

    # ------------------------------------------------------------------ Resources
    @server.resource("pvgis://hilfe/parameter", name="parameter-hilfe", mime_type="text/markdown",
                     description="Konventionen, zulässige Werte und Datenbasis")
    def parameter_help() -> str:
        return PARAMETER_HELP

    @server.resource("pvgis://referenz/{topic}", name="pvgis-referenz", mime_type="text/plain",
                     description="PVGIS-Referenzen: citation, publications, conventions-in-pvgis, license, features")
    async def reference(topic: str) -> str:
        allowed = {"citation", "publications", "conventions-in-pvgis", "license", "features"}
        if topic not in allowed:
            raise PvgisInputError(f"Unbekanntes Thema '{topic}'. Erlaubt: {', '.join(sorted(allowed))}")
        resp = await get_services().v6.reference(topic)
        return resp.text()

    @server.resource("pvgis://abdeckung/{layer}", name="pvgis-abdeckung", mime_type="application/geo+json",
                     description="Räumliche Abdeckung als GeoJSON: sarah3_coverage_layer, era5landplus_coverage_layer")
    async def coverage(layer: str) -> str:
        if layer not in {"sarah3_coverage_layer", "era5landplus_coverage_layer"}:
            raise PvgisInputError("Erlaubt: sarah3_coverage_layer, era5landplus_coverage_layer")
        return json.dumps(await get_services().v6.coverage_layer(layer))

    # -------------------------------------------------------------------- Prompts
    @server.prompt(name="standortbewertung_freiflaeche",
                   description="Strukturierte Ertragsbewertung eines Freiflächen-Standorts inkl. Tracker-Vergleich")
    def prompt_site(latitude: str, longitude: str, leistung_kwp: str = "10000") -> str:
        return (
            f"Bewerte den Standort {latitude}, {longitude} für eine PV-Freiflächenanlage mit {leistung_kwp} kWp.\n"
            "1. pvgis_site_check: Datenquelle, Höhe, Horizontverschattung.\n"
            "2. pvgis_optimize_orientation (optimize='both') für die feste Aufständerung.\n"
            "3. pvgis_estimate_yield mit optimaler und mit typischer Neigung (20°, Süd), bifazial und monofazial.\n"
            "4. pvgis_tracking_yield mit systems=['fixed_optimal','horizontal_ns'] für den Tracker-Mehrertrag.\n"
            "Fasse zusammen: spezifischer Ertrag (kWh/kWp), Jahresertrag, Jahr-zu-Jahr-Streuung, Mehrertrag "
            "Tracker, Verschattungsrisiko. Weise auf die Datenbasis (PVGIS 6 vs. 5.3) hin. Tabellarisch, sachlich."
        )

    @server.prompt(name="dach_variantenvergleich",
                   description="Vergleich mehrerer Dachflächen bzw. Belegungsvarianten an einem Gebäude")
    def prompt_roof(latitude: str, longitude: str, flaechen: str) -> str:
        return (
            f"Vergleiche die PV-Belegung an {latitude}, {longitude}. Flächen: {flaechen}.\n"
            "Nutze pvgis_compare_surfaces (eine Fläche je Dachseite, mounting='building' bei Indach). "
            "Gib Gesamtertrag, Ertrag und spezifischen Ertrag je Fläche sowie die Monatsverteilung an und "
            "empfehle die Priorisierung der Flächen."
        )

    @server.prompt(name="tracker_wirtschaftlichkeit",
                   description="LCOE-Vergleich feste Aufständerung vs. Tracker")
    def prompt_tracker(latitude: str, longitude: str, leistung_kwp: str, capex_fest_je_kwp: str,
                       capex_tracker_je_kwp: str, zins_prozent: str = "5", laufzeit_jahre: str = "30") -> str:
        return (
            f"Vergleiche für {latitude}, {longitude} und {leistung_kwp} kWp feste Aufständerung und "
            "Einachs-Tracker wirtschaftlich. Rufe pvgis_tracking_yield auf mit "
            "systems=['fixed_optimal','horizontal_ns','two_axis'], "
            f"capex_per_kwp={{'fixed_optimal': {capex_fest_je_kwp}, 'horizontal_ns': {capex_tracker_je_kwp}}}, "
            f"interest_pct={zins_prozent}, lifetime_years={laufzeit_jahre}. "
            "Stelle Ertrag, Mehrertrag und LCOE gegenüber und nenne die Annahmen und Grenzen "
            "(kein Backtracking, keine Reihenverschattung, O&M nach PVGIS-Methodik)."
        )

    return server
