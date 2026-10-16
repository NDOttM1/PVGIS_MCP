# PVGIS-MCP

MCP-Server für das **Photovoltaic Geographical Information System (PVGIS)** der Europäischen Kommission (Joint Research Centre).
Er stellt Solarstrahlung, PV-Ertrag, Tracker-Vergleiche, Horizontverschattung, TMY-Klimadaten und weitere Auswertungen als
Werkzeuge für KI-Agenten bereit – über **stdio** (Claude Desktop, Claude Code, Cursor …) und **HTTP** (Langflow, eigene Agenten,
andere MCP-Clients im Netzwerk).

Zwei Datenquellen werden kombiniert:

| | PVGIS 6 Web API (Standard) | PVGIS 5.3 (Ergänzung) |
|---|---|---|
| Jahre | 2014–2024 | 2005–2023 |
| Feste Anlagen, Mehrflächen, Bifazial, Optimierung | ✔ | (✔) |
| Tracker (einachsig N-S, O-W, geneigt, vertikal; zweiachsig) | – | ✔ |
| Stromgestehungskosten (LCOE) | – | ✔ |
| TMY (CSV/EPW), Inselanlage, Monats-/Tagesstrahlung | – | ✔ |
| Gradtage, Sonnenstand, Geländehöhe | ✔ | – |

## Werkzeuge

| Werkzeug | Zweck | Basis |
|---|---|---|
| `pvgis_site_check` | Standortvorprüfung: Datenquellen, Höhe, Horizontverschattung | v6 |
| `pvgis_estimate_yield` | Jahres-/Monatsertrag einer festen Fläche, PR, Verluste, Jahr-zu-Jahr-Streuung | v6 |
| `pvgis_compare_surfaces` | Mehrere Flächen in einem Aufruf (Ost/West, Varianten) | v6 |
| `pvgis_optimize_orientation` | Optimale Neigung und/oder Azimut | v6 |
| `pvgis_power_timeseries` | Leistungszeitreihe als CSV + Kennzahlen | v6 |
| `pvgis_horizon_profile` | Horizontprofil aus Geländemodell, eigenen Werten oder Datei | v6 |
| `pvgis_solar_position` | Sonnenstand, Einfallswinkel, Horizontverschattung | v6 |
| `pvgis_degree_days` | Heiz-/Kühlgradtage | v6 |
| `pvgis_batch_yield` | Viele Standorte/Varianten mit Limit-Steuerung, CSV | v6 |
| `pvgis_api_status` | Kontingente, Cache, Konfiguration | – |
| `pvgis_tracking_yield` | Fest vs. Tracker, Mehrertrag, LCOE | v5.3 |
| `pvgis_tmy` | Typisches meteorologisches Jahr (CSV/EPW) | v5.3 |
| `pvgis_offgrid` | Inselanlage mit Batterie | v5.3 |
| `pvgis_monthly_radiation` | Monatsstrahlung und Temperatur je Jahr | v5.3 |
| `pvgis_daily_profile` | Mittlerer Tagesgang der Einstrahlung | v5.3 |
| `pvgis_hourly_series_legacy` | Stundenreihe 2005–2023, auch Tracker | v5.3 |

Zusätzlich: Resources `pvgis://hilfe/parameter`, `pvgis://referenz/{topic}`, `pvgis://abdeckung/{layer}` und Prompts
`standortbewertung_freiflaeche`, `dach_variantenvergleich`, `tracker_wirtschaftlichkeit`.

## Konventionen

- **Azimut als Kompassrichtung**: 0 = Nord, 90 = Ost, 180 = Süd, 270 = West – oder Kürzel/Wörter: `S`, `SO`, `SW`, `Ost`,
  `Südwest` … Der Server rechnet für PVGIS 5.3 (0 = Süd) automatisch um.
- **Neigung** 0° = waagerecht. **Verluste** in % (Standard 14). **Leistung** in kWp (Inselanlage in Wp).
- **Horizont**: `auto` (Geländemodell), `none` oder Liste von Höhen in Grad ab Nord im Uhrzeigersinn.
- PVGIS 6 und 5.3 verwenden unterschiedliche Datensätze und Jahre. Innerhalb eines Vergleichs nur eine Quelle nutzen.

## Installation (Windows)

Voraussetzung: Python ≥ 3.10.

```powershell
cd D:\Python-Projekte\PVGIS-MCP
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -e .
```

Alternativ mit [uv](https://docs.astral.sh/uv/): `uv sync` bzw. `uv pip install -e .`

Funktionstest ohne Internet: `.venv\Scripts\pip install -e .[dev]` und `.venv\Scripts\python -m pytest`.
Live-Test gegen PVGIS: `set PVGIS_LIVE=1` und erneut `pytest -m live`.

## Einbindung in Claude Desktop (stdio)

`%APPDATA%\Claude\claude_desktop_config.json` – Abschnitt `mcpServers` ergänzen (siehe `claude_desktop_config.example.json`):

```json
{
  "mcpServers": {
    "pvgis": {
      "command": "D:\\Python-Projekte\\PVGIS-MCP\\.venv\\Scripts\\pvgis-mcp.exe",
      "env": {
        "PVGIS_OUTPUT_DIR": "D:\\Python-Projekte\\PVGIS-MCP\\output"
      }
    }
  }
}
```

Claude Desktop danach neu starten. Für Claude Code: `claude mcp add pvgis -- D:\Python-Projekte\PVGIS-MCP\.venv\Scripts\pvgis-mcp.exe`.

## Betrieb als HTTP-Server (für andere Agenten)

```powershell
# nur lokal (http://127.0.0.1:8765/mcp)
.venv\Scripts\pvgis-mcp --transport http --port 8765

# im Netzwerk mit Token
.venv\Scripts\pvgis-mcp --transport http --host 0.0.0.0 --port 8765 --token MEIN_GEHEIMES_TOKEN
```

Oder `start_http.cmd` ausführen. Endpunkte:

| Pfad | Zweck |
|---|---|
| `/mcp` | Streamable HTTP (aktueller MCP-Standard) |
| `/sse` | nur mit `--transport sse` – für ältere Clients |
| `/health` | Statusprüfung ohne Token |

Clients senden das Token als Header `Authorization: Bearer <token>`. Beispiel Langflow („MCP Tools“-Komponente):
URL `http://<rechner>:8765/mcp`, Header `Authorization: Bearer <token>`. Für Clients ohne Sitzungsverwaltung
`--stateless` und ggf. `--json-response` setzen. Bei festen Hostnamen kann `--allowed-hosts "pc01:8765,192.168.1.20:8765"`
den DNS-Rebinding-Schutz aktivieren.

stdio- und HTTP-Instanz können gleichzeitig laufen: Cache und Rate-Limit-Zähler liegen in derselben SQLite-Datei und gelten
damit prozessübergreifend.

## Konfiguration (Umgebungsvariablen)

| Variable | Standard | Bedeutung |
|---|---|---|
| `PVGIS_OUTPUT_DIR` | `<Projekt>\output` | Ausgabeordner für CSV/EPW |
| `PVGIS_CSV_STYLE` | `de` | `de` = Semikolon/Dezimalkomma (Excel DE), `intl` = Komma/Punkt |
| `PVGIS_CACHE_PATH` | `<Projekt>\.cache\pvgis_cache.sqlite` | Cache und Limit-Zähler |
| `PVGIS_CACHE_TTL_DAYS` | `180` | Gültigkeit von Cache-Einträgen |
| `PVGIS_CACHE_ENABLED` | `true` | Cache an/aus |
| `PVGIS_TIMEOUT_S` | `180` | Zeitlimit je Anfrage |
| `PVGIS_MAX_WAIT_S` | `90` | Maximale Wartezeit auf freies Kontingent, danach verständliche Fehlermeldung |
| `PVGIS_V6_LIMITS` | `10/1,100/60,200/3600` | Limits PVGIS 6 (Anzahl/Sekunden) |
| `PVGIS_V53_LIMITS` | `25/1` | Limits PVGIS 5.3 |
| `PVGIS_ENABLE_V53` | `true` | PVGIS-5.3-Werkzeuge an/aus |
| `PVGIS_BATCH_MAX_CASES` | `150` | Höchstzahl Fälle je Batch |
| `PVGIS_V6_BASE_URL`, `PVGIS_V53_BASE_URL` | offizielle URLs | z. B. für einen künftigen Versionswechsel |
| `PVGIS_MCP_TRANSPORT`, `PVGIS_MCP_HOST`, `PVGIS_MCP_PORT`, `PVGIS_MCP_TOKEN` | – | Alternativ zu den Kommandozeilenoptionen |

## Rate-Limits und Cache

PVGIS 6 erlaubt je Client 10 Aufrufe/s, 100/min und **200/h** (zusätzlich 10.000/Tag anwendungsweit). Der Server hält diese
Grenzen selbst ein, wartet kurze Engpässe ab und meldet längere verständlich („Nächster Aufruf in ca. 12 min“). Alle
erfolgreichen Antworten werden zwischengespeichert; identische Anfragen kosten kein Kontingent. `pvgis_batch_yield`
stellt Fälle bei erschöpftem Kontingent zurück – ein späterer identischer Aufruf rechnet nur die fehlenden nach.

## Projektstruktur

```
pvgis_mcp/
  server.py       Registrierung von Werkzeugen, Resources, Prompts
  tools_v6.py     Werkzeuge PVGIS 6
  tools_v53.py    Werkzeuge PVGIS 5.3 (Tracker, TMY, Inselanlage, Strahlung)
  clients.py      API-Clients
  http.py         Wiederholungen, Fehlerübersetzung, Cache-Anbindung
  storage.py      SQLite-Cache und prozessübergreifendes Rate-Limit
  conventions.py  Azimut-, Verlust-, Horizont- und Technologie-Umrechnungen
  export.py       CSV/EPW-Ausgabe
  transport.py    HTTP/SSE, Bearer-Token, /health
tests/            Offline-Tests mit nachgebildeten PVGIS-Antworten, Live- und Drift-Tests
```

## Quellen und Lizenz

Daten: PVGIS © Europäische Union, 2001–2026. PVGIS 6 Web API unter EUPL-1.2.
Bei Veröffentlichung von Ergebnissen PVGIS als Quelle angeben (Zitierhinweis über Resource `pvgis://referenz/citation`).

- PVGIS 6 Web API: https://photovoltaic-geographic-information-system.ec.europa.eu/api/
- OpenAPI: https://photovoltaic-geographic-information-system.ec.europa.eu/api/v6/openapi.json
- Dokumentation: https://pvgis.pages.code.europa.eu/pvgis/web_api/
- PVGIS 5.3 API: https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/using-pvgis-5/api-non-interactive-service_en

Hinweis: Die JRC entwickelt die PVGIS-6-API aktiv weiter. `pytest -m live` enthält einen Drift-Test, der Änderungen an
Endpunkten und Parametern meldet.
