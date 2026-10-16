"""Werkzeuge auf Basis der PVGIS-6-Web-API (Datenbasis SARAH-3/ERA5, 2014–2024)."""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from .config import V6_FIRST_YEAR, V6_LAST_YEAR
from .conventions import (
    AzimuthInput,
    HorizonInput,
    ModuleTechnology,
    Mounting,
    azimuth_label,
    check_coordinates,
    check_v6_years,
    loss_to_efficiency,
    parse_azimuth,
    v6_horizon,
    v6_module_model,
)
from .errors import PvgisError, PvgisInputError, PvgisRateLimitError
from .export import build_name, parse_csv, split_constant_columns, write_table
from .services import get_services

SOURCE_V6 = "PVGIS 6 Web API, Joint Research Centre (JRC), Europäische Kommission – Lizenz EUPL-1.2"
MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

# --------------------------------------------------------------------------- Typen
Lat = Annotated[float, Field(description="Breitengrad in Dezimalgrad (Süd negativ), z. B. 53.25", ge=-60, le=75)]
Lon = Annotated[float, Field(description="Längengrad in Dezimalgrad (West negativ), z. B. 9.5", ge=-180, le=180)]
Kwp = Annotated[float, Field(description="Nennleistung (Peak) in kWp", gt=0)]
Tilt = Annotated[float, Field(description="Modulneigung in Grad (0 = waagerecht, 90 = senkrecht)", ge=0, le=90)]
Azimuth = Annotated[
    Union[float, str],
    Field(description="Ausrichtung als Kompass-Azimut in Grad (0=N, 90=O, 180=S, 270=W) "
                      "oder Himmelsrichtung wie 'S', 'SO', 'Südwest', 'Ost'"),
]
Loss = Annotated[float, Field(description="Systemverluste in % (Kabel, Wechselrichter, Verschmutzung, Alterung). "
                                          "PVGIS-Standard 14", ge=0, lt=100)]
Tech = Annotated[ModuleTechnology, Field(description="Modultechnologie: crystSi2025 (kristallines Si, "
                                                     "aktuelle Koeffizienten, Standard), crystSi, CIS, CdTe")]
Mount = Annotated[Mounting, Field(description="Montage: 'free' = freistehend/aufgeständert, "
                                              "'building' = gebäudeintegriert (schlechter hinterlüftet)")]
StartYear = Annotated[int, Field(description=f"Erstes Jahr der Auswertung ({V6_FIRST_YEAR}–{V6_LAST_YEAR})",
                                 ge=V6_FIRST_YEAR, le=V6_LAST_YEAR)]
EndYear = Annotated[int, Field(description=f"Letztes Jahr der Auswertung ({V6_FIRST_YEAR}–{V6_LAST_YEAR})",
                               ge=V6_FIRST_YEAR, le=V6_LAST_YEAR)]
Horizon = Annotated[
    Union[str, list[float], None],
    Field(description="Horizont/Geländeverschattung: 'auto' (PVGIS-Geländemodell, Standard), 'none' "
                      "(ohne) oder Liste von Horizonthöhen in Grad, gleichmäßig verteilt ab Nord im Uhrzeigersinn"),
]
Source = Annotated[Literal["SARAH-3", "ERA5"], Field(description="Strahlungsdaten: SARAH-3 (Satellit, Standard "
                                                                 "für Europa/Afrika/Asien) oder ERA5 (Reanalyse)")]


class SurfaceSpec(BaseModel):
    label: Optional[str] = Field(None, description="Bezeichnung, z. B. 'Dach Ost'")
    tilt_deg: float = Field(..., ge=0, le=90, description="Neigung in Grad")
    azimuth: Union[float, str] = Field(..., description="Kompass-Azimut (180=S) oder Himmelsrichtung")
    peak_power_kwp: float = Field(..., gt=0, description="Leistung dieser Fläche in kWp")


class YieldCase(BaseModel):
    id: str = Field(..., description="Eindeutige Kennung, z. B. Flurstück oder Variante")
    latitude: float = Field(..., ge=-60, le=75)
    longitude: float = Field(..., ge=-180, le=180)
    peak_power_kwp: float = Field(1.0, gt=0)
    tilt_deg: float = Field(30.0, ge=0, le=90)
    azimuth: Union[float, str] = Field(180.0)
    system_loss_pct: Optional[float] = Field(None, ge=0, lt=100)
    module_technology: Optional[ModuleTechnology] = None
    mounting: Optional[Mounting] = None
    bifacial: Optional[bool] = None


# --------------------------------------------------------------------------- Hilfen
def _val(entry: Any, to_kwh: bool = False) -> Any:
    """Liest {"value": x, "unit(s)": u}; rechnet Wh -> kWh, wenn gewünscht."""
    if isinstance(entry, dict):
        value = entry.get("value")
        unit = entry.get("units") or entry.get("unit") or ""
        if to_kwh and isinstance(unit, str) and unit.strip().startswith("Wh") and value is not None:
            if isinstance(value, list):
                return [v / 1000 for v in value]
            return value / 1000
        return value
    return entry


def _r(x: Any, nd: int = 1) -> Any:
    if isinstance(x, float):
        return round(x, nd) if math.isfinite(x) else None
    if isinstance(x, list):
        return [_r(v, nd) for v in x]
    return x


def _period(start_year: int, end_year: int) -> dict:
    check_v6_years(start_year, end_year)
    return {"start_time": f"{start_year}-01-01T00:00:00", "end_time": f"{end_year}-12-31T23:59:59",
            "frequency": "Hourly"}


def _system_params(*, latitude: float, longitude: float, tilt_deg: float, azimuth: AzimuthInput,
                   peak_power_kwp: float, system_loss_pct: float, module_technology: str, mounting: str,
                   bifacial: bool, bifaciality_factor: float, module_height_m: float, albedo: float,
                   irradiance_source: str, horizon: HorizonInput, installation_height_m: float) -> dict:
    check_coordinates(latitude, longitude)
    return {
        "latitude": latitude,
        "longitude": longitude,
        "installation_height": installation_height_m,
        "surface_tilt": tilt_deg,
        "surface_orientation": parse_azimuth(azimuth),
        "peak-power": peak_power_kwp,
        "system_efficiency": loss_to_efficiency(system_loss_pct),
        "photovoltaic_module": v6_module_model(module_technology, mounting),
        "photovoltaic_module_type": "Bi-Facial" if bifacial else "Mono-Facial",
        "bifacialy_factor": bifaciality_factor,
        "module_height": module_height_m,
        "albedo": albedo,
        "irradiance_source": irradiance_source,
        "horizon_profile": v6_horizon(horizon),
        "angle_output_units": "Degrees",
    }


def _monthly_table(stats: dict, peak_power_kwp: float) -> list[dict]:
    energy = _val(stats.get("monthly_energy_summary"), to_kwh=True) or []
    sd = _val(stats.get("monthly_energy_standard_deviation_due_to_year_to_year_variability"), to_kwh=True) or []
    irr = _val(stats.get("monthly_global_inclined_irradiance_before_reflectivity_summary")) or []
    temp = _val(stats.get("average_temperature_per_month")) or []
    rows = []
    for i, e in enumerate(energy):
        row = {"monat": MONTHS[i] if len(energy) == 12 else i + 1,
               "energie_kwh": _r(e, 1),
               "spez_ertrag_kwh_kwp": _r(e / peak_power_kwp, 1) if peak_power_kwp else None}
        if i < len(sd):
            row["stdabw_kwh"] = _r(sd[i], 1)
        if i < len(irr):
            row["einstrahlung_kwh_m2"] = _r(irr[i], 1)
        if i < len(temp):
            row["temperatur_c"] = _r(temp[i], 1)
        rows.append(row)
    return rows


def _summary(perf: dict, stats: dict, peak_power_kwp: float) -> dict:
    annual = _val(stats.get("yearly_average_energy"), to_kwh=True)
    irradiation = _val(stats.get("yearly_average_global_inclined_irradiance_before_reflectivity"))
    out = {
        "jahresertrag_kwh": _r(annual, 0),
        "spezifischer_ertrag_kwh_kwp": _r(annual / peak_power_kwp, 1) if annual and peak_power_kwp else None,
        "jahresschwankung_stdabw_kwh": _r(_val(stats.get("year_to_year_energy_variability"), to_kwh=True), 0),
        "einstrahlung_modulebene_kwh_m2": _r(irradiation, 0),
        "performance_ratio": _r(annual / peak_power_kwp / irradiation, 3) if annual and irradiation else None,
        "mitteltemperatur_c": _r(_val(stats.get("average_temperature")), 1),
    }
    if out["jahresschwankung_stdabw_kwh"] == 0:
        out["jahresschwankung_stdabw_kwh"] = None  # bei nur einem Jahr nicht aussagekräftig
    return out


def _losses(perf: dict) -> dict | None:
    keys = {
        "spektral": "spectral_effect_percentage",
        "reflexion": "reflectivity_effect_percentage",
        "temperatur_und_schwachlicht": "temperature_and_low_irradiance_effect_percentage",
        "system": "system_efficiency_effect_percentage",
        "gesamt": "effect_percentage",
    }
    out = {k: _r(perf.get(v), 2) for k, v in keys.items() if perf.get(v) is not None}
    return out or None


def _location(perf: dict) -> dict:
    return {"breite": _val(perf.get("latitude")), "laenge": _val(perf.get("longitude")),
            "hoehe_m": _val(perf.get("elevation"))}


def _out_dir() -> Path:
    return get_services().settings.output_dir


def _csv_style() -> str:
    return get_services().settings.csv_style


# =========================================================================== Tools
async def pvgis_site_check(latitude: Lat, longitude: Lon) -> dict:
    """Vorprüfung eines Standorts: verfügbare Strahlungsdaten, Geländehöhe und Horizontverschattung.

    Sinnvoll als erster Schritt vor jeder Ertragsberechnung. Kostet 3–4 PVGIS-Aufrufe (danach im Cache).
    """
    check_coordinates(latitude, longitude)
    v6 = get_services().v6
    sources, within, elev, hz = await asyncio.gather(
        v6.available_sources(latitude, longitude),
        v6.within_source(latitude, longitude, "SARAH-3"),
        v6.elevation(latitude, longitude),
        v6.horizon(latitude, longitude),
    )
    horizon = _horizon_summary(hz)
    src = sources.get("irradiance_sources", []) if isinstance(sources, dict) else sources
    in_sarah = bool(within.get("result")) if isinstance(within, dict) else None
    return {
        "standort": {"breite": latitude, "laenge": longitude, "hoehe_m": elev.get("elevation")},
        "strahlungsdaten_verfuegbar": src,
        "in_sarah3_abdeckung": in_sarah,
        "empfohlene_quelle": "SARAH-3" if in_sarah else "ERA5",
        "horizont": horizon,
        "zeitraum_pvgis6": f"{V6_FIRST_YEAR}–{V6_LAST_YEAR}",
        "quelle": SOURCE_V6,
    }


def _horizon_summary(hz: dict) -> dict:
    prof = hz.get("horizon_profile", {}) if isinstance(hz, dict) else {}
    az = prof.get("azimuth") or []
    h = prof.get("horizon_height") or []
    if not az or not h:
        return {"hinweis": "kein Horizontprofil geliefert"}
    pairs = list(zip(az, h))
    imax = max(range(len(h)), key=lambda i: h[i])
    sectors: dict[str, list[float]] = {}
    for a, hh in pairs:
        sectors.setdefault(azimuth_label(round(a / 45) * 45 % 360), []).append(hh)
    south = [hh for a, hh in pairs if 90 <= a <= 270]
    return {
        "max_hoehe_grad": _r(h[imax], 1),
        "richtung_max": f"{_r(az[imax], 0)}° ({azimuth_label(az[imax])})",
        "mittel_grad": _r(sum(h) / len(h), 1),
        "mittel_suedhalbkreis_grad": _r(sum(south) / len(south), 1) if south else None,
        "sektoren_mittel_grad": {k: _r(sum(v) / len(v), 1) for k, v in sectors.items()},
        "bewertung": ("vernachlässigbar" if max(h) < 3 else "gering" if max(h) < 8
                      else "deutlich" if max(h) < 15 else "stark"),
        "punkte": len(h),
    }


async def pvgis_estimate_yield(
    latitude: Lat,
    longitude: Lon,
    peak_power_kwp: Kwp = 1.0,
    tilt_deg: Tilt = 30.0,
    azimuth: Azimuth = 180.0,
    system_loss_pct: Loss = 14.0,
    module_technology: Tech = "crystSi2025",
    mounting: Mount = "free",
    bifacial: Annotated[bool, Field(description="Bifaziale Module (Rückseitenertrag)")] = False,
    bifaciality_factor: Annotated[float, Field(description="Bifazialitätsfaktor 0–1 (typ. 0,7)", ge=0, le=1)] = 0.7,
    module_height_m: Annotated[float, Field(description="Modulunterkante über Grund in m (für Bifazial)", ge=0)] = 1.0,
    albedo: Annotated[float, Field(description="Bodenreflexion 0–1 (Gras ≈0,2, Schnee ≈0,6)", ge=0, le=1)] = 0.2,
    start_year: StartYear = V6_FIRST_YEAR,
    end_year: EndYear = V6_LAST_YEAR,
    irradiance_source: Source = "SARAH-3",
    horizon: Horizon = "auto",
    installation_height_m: Annotated[float, Field(description="Montagehöhe über Gelände in m", ge=0, le=1000)] = 0.0,
    include_raw: Annotated[bool, Field(description="Rohantwort von PVGIS zusätzlich zurückgeben")] = False,
) -> dict:
    """Jahres- und Monatsertrag einer fest montierten PV-Anlage (eine Fläche) nach PVGIS 6.

    Liefert Jahresertrag, spezifischen Ertrag (kWh/kWp), Performance Ratio, Monatswerte mit
    Jahr-zu-Jahr-Streuung und Verlustaufschlüsselung. Standard: Mittel über 2014–2024.
    Für Nachführsysteme (Tracker) pvgis_tracking_yield verwenden.
    """
    params = _system_params(
        latitude=latitude, longitude=longitude, tilt_deg=tilt_deg, azimuth=azimuth,
        peak_power_kwp=peak_power_kwp, system_loss_pct=system_loss_pct, module_technology=module_technology,
        mounting=mounting, bifacial=bifacial, bifaciality_factor=bifaciality_factor,
        module_height_m=module_height_m, albedo=albedo, irradiance_source=irradiance_source,
        horizon=horizon, installation_height_m=installation_height_m)
    params.update(_period(start_year, end_year))
    params.update({"analysis": "Simple", "statistics": True})
    data = await get_services().v6.performance(params)
    perf = data.get("photovoltaic_performance", {})
    stats = data.get("statistics", {})
    az = params["surface_orientation"]
    result = {
        "standort": _location(perf),
        "anlage": {
            "leistung_kwp": peak_power_kwp, "neigung_grad": tilt_deg, "azimut_grad": az,
            "ausrichtung": azimuth_label(az), "verluste_prozent": system_loss_pct,
            "modul": params["photovoltaic_module"], "modultyp": params["photovoltaic_module_type"],
            "strahlungsdaten": irradiance_source, "horizont": "PVGIS-Geländemodell"
            if params["horizon_profile"] == "PVGIS" else params["horizon_profile"],
        },
        "zeitraum": f"{start_year}–{end_year} ({end_year - start_year + 1} Jahre, Jahresmittel)",
        "ergebnis": _summary(perf, stats, peak_power_kwp),
        "verluste_prozent": _losses(perf),
        "monatswerte": _monthly_table(stats, peak_power_kwp),
        "quelle": SOURCE_V6,
    }
    if include_raw:
        result["rohdaten"] = data
    return result


async def pvgis_compare_surfaces(
    latitude: Lat,
    longitude: Lon,
    surfaces: Annotated[list[SurfaceSpec], Field(description="Liste der Flächen (2–20), z. B. Ost- und Westdach",
                                                 min_length=1, max_length=20)],
    system_loss_pct: Loss = 14.0,
    module_technology: Tech = "crystSi2025",
    mounting: Mount = "free",
    bifacial: bool = False,
    bifaciality_factor: Annotated[float, Field(ge=0, le=1)] = 0.7,
    albedo: Annotated[float, Field(ge=0, le=1)] = 0.2,
    start_year: StartYear = V6_FIRST_YEAR,
    end_year: EndYear = V6_LAST_YEAR,
    irradiance_source: Source = "SARAH-3",
    horizon: Horizon = "auto",
) -> dict:
    """Ertrag mehrerer Teilflächen an einem Standort in einem Aufruf (z. B. Ost/West-Dach, Varianten).

    Gibt Gesamtertrag, Ertrag je Fläche und Anteil zurück. Ein einziger PVGIS-Aufruf.
    """
    specs = [s if isinstance(s, SurfaceSpec) else SurfaceSpec(**s) for s in surfaces]
    total_kwp = sum(s.peak_power_kwp for s in specs)
    params = _system_params(
        latitude=latitude, longitude=longitude, tilt_deg=0, azimuth=180, peak_power_kwp=1,
        system_loss_pct=system_loss_pct, module_technology=module_technology, mounting=mounting,
        bifacial=bifacial, bifaciality_factor=bifaciality_factor, module_height_m=1.0, albedo=albedo,
        irradiance_source=irradiance_source, horizon=horizon, installation_height_m=0)
    azimuths = [parse_azimuth(s.azimuth) for s in specs]
    params.update({
        "surface_tilt": [s.tilt_deg for s in specs],
        "surface_orientation": azimuths,
        "peak-power": [s.peak_power_kwp for s in specs],
        "analysis": "Simple", "statistics": True, "show_individual_surfaces": True,
    })
    params.update(_period(start_year, end_year))
    data = await get_services().v6.performance_multi(params)
    perf = data.get("photovoltaic_performance", {})
    stats = data.get("statistics", {})
    rows = []
    for i, spec in enumerate(specs):
        s_stats = data.get(f"statistics_for_surface_{i}", {})
        s_perf = data.get(f"performance_for_surface_{i}", {})
        annual = _val(s_stats.get("yearly_average_energy"), to_kwh=True)
        if annual is None:
            energy = _val(s_perf.get("energy"))
            years = end_year - start_year + 1
            annual = energy / years if energy is not None else None
        rows.append({
            "flaeche": spec.label or f"Fläche {i + 1}",
            "leistung_kwp": spec.peak_power_kwp,
            "neigung_grad": spec.tilt_deg,
            "azimut_grad": azimuths[i],
            "ausrichtung": azimuth_label(azimuths[i]),
            "jahresertrag_kwh": _r(annual, 0),
            "spezifischer_ertrag_kwh_kwp": _r(annual / spec.peak_power_kwp, 1) if annual else None,
            "einstrahlung_kwh_m2": _r(_val(s_stats.get(
                "yearly_average_global_inclined_irradiance_before_reflectivity")), 0),
        })
    total = _val(stats.get("yearly_average_energy"), to_kwh=True) or sum(r["jahresertrag_kwh"] or 0 for r in rows)
    for r in rows:
        r["anteil_prozent"] = _r(100 * (r["jahresertrag_kwh"] or 0) / total, 1) if total else None
    return {
        "standort": _location(perf),
        "zeitraum": f"{start_year}–{end_year} (Jahresmittel)",
        "gesamt": {"leistung_kwp": total_kwp, "jahresertrag_kwh": _r(total, 0),
                   "spezifischer_ertrag_kwh_kwp": _r(total / total_kwp, 1) if total else None},
        "flaechen": rows,
        "monatswerte_gesamt": _monthly_table(stats, total_kwp),
        "quelle": SOURCE_V6,
    }


async def pvgis_optimize_orientation(
    latitude: Lat,
    longitude: Lon,
    optimize: Annotated[Literal["both", "tilt", "azimuth"],
                        Field(description="'both' = Neigung und Azimut, 'tilt' = nur Neigung "
                                          "(Azimut fest), 'azimuth' = nur Azimut (Neigung fest)")] = "both",
    tilt_deg: Annotated[float, Field(description="Feste Neigung, falls nur der Azimut optimiert wird",
                                     ge=0, le=90)] = 30.0,
    azimuth: Annotated[Union[float, str], Field(description="Fester Azimut, falls nur die Neigung "
                                                            "optimiert wird (180 = Süd)")] = 180.0,
    peak_power_kwp: Kwp = 1.0,
    system_loss_pct: Loss = 14.0,
    module_technology: Tech = "crystSi2025",
    mounting: Mount = "free",
    bifacial: bool = False,
    start_year: StartYear = V6_FIRST_YEAR,
    end_year: EndYear = V6_LAST_YEAR,
    irradiance_source: Source = "SARAH-3",
    horizon: Horizon = "auto",
    method: Annotated[Literal["L-BFGS-B", "SHGO", "Brute", "Powell", "Nelder-Mead", "DIRECT"],
                      Field(description="Optimierungsverfahren (SciPy). L-BFGS-B schnell, "
                                        "SHGO/Brute gründlicher")] = "L-BFGS-B",
    with_yield: Annotated[bool, Field(description="Ertrag bei optimaler Ausrichtung zusätzlich berechnen "
                                                  "(+1 Aufruf)")] = True,
) -> dict:
    """Ermittelt die ertragsoptimale Ausrichtung (Neigung und/oder Azimut) einer festen PV-Fläche."""
    mode = {"both": "Orientation & Tilt", "tilt": "Tilt", "azimuth": "Orientation"}[optimize]
    params = _system_params(
        latitude=latitude, longitude=longitude, tilt_deg=tilt_deg, azimuth=azimuth,
        peak_power_kwp=peak_power_kwp, system_loss_pct=system_loss_pct, module_technology=module_technology,
        mounting=mounting, bifacial=bifacial, bifaciality_factor=0.7, module_height_m=1.0, albedo=0.2,
        irradiance_source=irradiance_source, horizon=horizon, installation_height_m=0)
    for key in ("system_efficiency", "albedo", "peak-power"):
        params.pop(key, None)  # vom Optimierungs-Endpunkt nicht unterstützt
    params.update(_period(start_year, end_year))
    params.update({"surface_position_optimisation_mode": mode, "surface_position_optimisation_method": method})
    data = await get_services().v6.optimise(params)
    opt = data.get("optimal_surface_position", data)
    best_az = opt.get("surface_orientation", params["surface_orientation"])
    best_tilt = opt.get("surface_tilt", tilt_deg)
    result: dict[str, Any] = {
        "optimum": {"neigung_grad": _r(float(best_tilt), 1), "azimut_grad": _r(float(best_az), 1),
                    "ausrichtung": azimuth_label(float(best_az))},
        "optimiert": optimize,
        "verfahren": method,
        "zeitraum": f"{start_year}–{end_year}",
    }
    if with_yield:
        y = await pvgis_estimate_yield(
            latitude=latitude, longitude=longitude, peak_power_kwp=peak_power_kwp,
            tilt_deg=round(float(best_tilt), 1), azimuth=round(float(best_az), 1),
            system_loss_pct=system_loss_pct, module_technology=module_technology, mounting=mounting,
            bifacial=bifacial, start_year=start_year, end_year=end_year,
            irradiance_source=irradiance_source, horizon=horizon)
        result["ertrag_bei_optimum"] = y["ergebnis"]
    result["quelle"] = SOURCE_V6
    return result


_DETAIL_VERBOSE = {"power": 1, "irradiance": 5, "full": 9}


async def pvgis_power_timeseries(
    latitude: Lat,
    longitude: Lon,
    start: Annotated[str, Field(description="Beginn, ISO-Format, z. B. '2023-06-01' oder '2023-06-01T00:00'")],
    end: Annotated[str, Field(description="Ende, ISO-Format, z. B. '2023-06-30T23:59'")],
    peak_power_kwp: Kwp = 1.0,
    tilt_deg: Tilt = 30.0,
    azimuth: Azimuth = 180.0,
    system_loss_pct: Loss = 14.0,
    module_technology: Tech = "crystSi2025",
    mounting: Mount = "free",
    bifacial: bool = False,
    frequency: Annotated[Literal["Hourly", "Minutely", "Daily"],
                         Field(description="Zeitschritt. Hourly ist die Auflösung der Eingangsdaten")] = "Hourly",
    timezone: Annotated[str, Field(description="IANA-Zeitzone für Ein- und Ausgabe, z. B. 'UTC' "
                                               "oder 'Europe/Berlin'")] = "UTC",
    detail: Annotated[Literal["power", "irradiance", "full"],
                      Field(description="'power' = nur Leistung, 'irradiance' = plus Einstrahlungskomponenten, "
                                        "'full' = alle Zwischengrößen inkl. Temperatur, Wind, Sonnenstand")] = "power",
    irradiance_source: Source = "SARAH-3",
    horizon: Horizon = "auto",
) -> dict:
    """PV-Leistungszeitreihe als CSV-Export plus Kennzahlen (Energie, Maximum, Volllaststunden).

    Die vollständige Zeitreihe wird in den Ausgabeordner geschrieben, im Ergebnis stehen nur
    Kennzahlen und der Dateipfad. Daten 2014–2024.
    """
    t0, t1 = _iso(start, end_of_day=False), _iso(end, end_of_day=True)
    if not (str(V6_FIRST_YEAR) <= t0[:4] <= str(V6_LAST_YEAR)) or not (str(V6_FIRST_YEAR) <= t1[:4] <= str(V6_LAST_YEAR)):
        raise PvgisInputError(f"PVGIS 6 deckt nur {V6_FIRST_YEAR}–{V6_LAST_YEAR} ab; für ältere Jahre "
                              "pvgis_hourly_series_legacy (PVGIS 5.3) verwenden.")
    params = _system_params(
        latitude=latitude, longitude=longitude, tilt_deg=tilt_deg, azimuth=azimuth,
        peak_power_kwp=peak_power_kwp, system_loss_pct=system_loss_pct, module_technology=module_technology,
        mounting=mounting, bifacial=bifacial, bifaciality_factor=0.7, module_height_m=1.0, albedo=0.2,
        irradiance_source=irradiance_source, horizon=horizon, installation_height_m=0)
    params.update({"start_time": t0, "end_time": t1, "frequency": frequency, "timezone": timezone,
                   "verbose": _DETAIL_VERBOSE[detail]})
    text = await get_services().v6.get_csv("power/broadband", params, "power.csv")
    header, rows = parse_csv(text)
    header, rows, constants = split_constant_columns(header, rows, keep=("Time", "Power"))
    if "Power" not in header:
        raise PvgisError("Antwort enthält keine Spalte 'Power'.", api="PVGIS 6")
    ip, it = header.index("Power"), header.index("Time") if "Time" in header else None
    # Längen-/Breitengrad sind konstant -> Metadaten
    path = write_table(_out_dir(), build_name("leistung", latitude, longitude, label=f"{t0[:10]}_{t1[:10]}"),
                       header, rows, _csv_style())
    powers = [r[ip] for r in rows if isinstance(r[ip], (int, float))]
    dt_h = {"Hourly": 1.0, "Minutely": 1 / 60}.get(frequency)
    summary: dict[str, Any] = {"werte": len(rows), "max_leistung_w": _r(max(powers), 1) if powers else None}
    if powers and it is not None:
        imax = max(range(len(rows)), key=lambda i: rows[i][ip] if isinstance(rows[i][ip], (int, float)) else -1)
        summary["zeitpunkt_max"] = rows[imax][it]
    if dt_h and powers:
        energy = sum(powers) * dt_h / 1000
        summary["energie_kwh"] = _r(energy, 2)
        summary["volllaststunden_h"] = _r(energy / peak_power_kwp, 1)
    elif frequency == "Daily":
        summary["hinweis"] = "Bei täglicher Frequenz ist jeder Wert eine Momentanleistung, keine Tagesenergie."
    return {
        "datei": str(path),
        "spalten": header,
        "kennzahlen": summary,
        "metadaten": constants,
        "einheiten": {"Power": "W (gesamte Anlage)", "Zeit": timezone},
        "quelle": SOURCE_V6,
    }


def _iso(value: str, end_of_day: bool) -> str:
    v = value.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError as exc:
        raise PvgisInputError(f"Ungültiges Datum '{value}'. Format z. B. 2023-06-01 oder 2023-06-01T12:00.") from exc
    if len(v) <= 10 and end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


async def pvgis_horizon_profile(
    latitude: Lat,
    longitude: Lon,
    user_heights_deg: Annotated[Optional[list[float]],
                                Field(description="Optional eigene Horizonthöhen in Grad, gleichmäßig ab Nord "
                                                  "im Uhrzeigersinn (mind. 4 Werte)")] = None,
    file_path: Annotated[Optional[str],
                         Field(description="Optional Pfad zu einer Horizontdatei (JSON mit 'horizon_height' "
                                           "[, 'azimuth', 'units'] oder CSV mit Höhenwerten) auf dem Server")] = None,
    export_csv: Annotated[bool, Field(description="Profil als CSV in den Ausgabeordner schreiben")] = False,
) -> dict:
    """Horizontprofil (Geländeverschattung) eines Standorts aus dem PVGIS-Geländemodell
    (SRTM/GTOPO30) oder aus eigenen Messwerten bzw. einer Datei.

    Azimut im Ergebnis: Kompass, 0 = Nord, im Uhrzeigersinn.
    """
    check_coordinates(latitude, longitude)
    v6 = get_services().v6
    if file_path:
        p = Path(file_path).expanduser()
        if not p.is_file():
            raise PvgisInputError(f"Datei nicht gefunden: {p}")
        ctype = "application/json" if p.suffix.lower() == ".json" else "text/csv"
        data = await v6.post_file_json("topography/horizon-profile",
                                       {"latitude": latitude, "longitude": longitude,
                                        "angle_output_units": "Degrees"},
                                       "horizon_profile", p.name, p.read_bytes(), ctype)
        origin = f"Datei {p.name}"
    elif user_heights_deg:
        data = await v6.horizon(latitude, longitude, v6_horizon(user_heights_deg))
        origin = "eigene Werte"
    else:
        data = await v6.horizon(latitude, longitude)
        origin = "PVGIS-Geländemodell (SRTM/GTOPO30)"
    prof = data.get("horizon_profile", {})
    az = prof.get("azimuth") or []
    h = prof.get("horizon_height") or []
    result: dict[str, Any] = {
        "standort": {"breite": latitude, "laenge": longitude},
        "herkunft": origin,
        "zusammenfassung": _horizon_summary(data),
        "profil": [{"azimut_grad": _r(a, 1), "hoehe_grad": _r(x, 2)} for a, x in zip(az, h)],
    }
    if export_csv and az:
        path = write_table(_out_dir(), build_name("horizont", latitude, longitude),
                           ["azimut_grad_nord", "hoehe_grad"], list(zip(az, h)), _csv_style())
        result["datei"] = str(path)
    result["quelle"] = SOURCE_V6
    return result


async def pvgis_solar_position(
    latitude: Lat,
    longitude: Lon,
    start: Annotated[str, Field(description="Beginn ISO, z. B. '2024-06-21'")],
    end: Annotated[str, Field(description="Ende ISO, z. B. '2024-06-21T23:59'")],
    tilt_deg: Tilt = 30.0,
    azimuth: Azimuth = 180.0,
    frequency: Literal["Hourly", "Minutely", "Daily"] = "Hourly",
    timezone: Annotated[str, Field(description="IANA-Zeitzone, z. B. 'Europe/Berlin'")] = "UTC",
    horizon: Horizon = "auto",
    export_csv: Annotated[bool, Field(description="Vollständige Zeitreihe als CSV schreiben")] = True,
) -> dict:
    """Sonnenstand (Höhe, Azimut, Einfallswinkel auf die Modulfläche, Verschattung durch den Horizont)
    als Zeitreihe nach NOAA. Nützlich für Blendgutachten-Vorprüfungen und Verschattungsfragen."""
    check_coordinates(latitude, longitude)
    t0, t1 = _iso(start, end_of_day=False), _iso(end, end_of_day=True)
    params = {
        "latitude": latitude, "longitude": longitude, "surface_tilt": tilt_deg,
        "surface_orientation": parse_azimuth(azimuth), "start_time": t0, "end_time": t1,
        "frequency": frequency, "timezone": timezone, "horizon_profile": v6_horizon(horizon),
        "angle_output_units": "Degrees",
    }
    data = await get_services().v6.get_json("solar-position/overview", params)
    res = data.get("results", data)
    model = next(iter(res.values())) if isinstance(res, dict) and res else {}
    alt = model.get("altitude") or []
    inc = model.get("incidence") or []
    visible = model.get("visible") or []
    summary = {
        "werte": len(alt),
        "max_sonnenhoehe_grad": _r(max(alt), 2) if alt else None,
        "min_einfallswinkel_grad": _r(min(inc), 2) if inc else None,
        "stunden_ueber_horizont": sum(1 for v in visible if v) if frequency == "Hourly" else None,
        "algorithmus": model.get("position_algorithm"),
        "einfallswinkel_definition": model.get("Incidence angle") or model.get("incidence_algorithm"),
    }
    result: dict[str, Any] = {"kennzahlen": summary}
    if export_csv:
        try:
            text = await get_services().v6.get_csv("solar-position/overview", params, "solar_position.csv")
            header, rows = parse_csv(text)
            header, rows, constants = split_constant_columns(header, rows)
            path = write_table(_out_dir(), build_name("sonnenstand", latitude, longitude,
                                                      label=f"{t0[:10]}_{t1[:10]}"), header, rows, _csv_style())
            result["datei"] = str(path)
            result["metadaten"] = constants
        except PvgisError as exc:
            result["hinweis"] = f"CSV-Export nicht möglich: {exc}"
    if len(alt) <= 48:
        keys = ["altitude", "azimuth", "zenith", "incidence", "visible"]
        result["werte"] = [{k: _r(model.get(k)[i], 2) if isinstance(model.get(k), list) else None for k in keys}
                           for i in range(len(alt))]
    result["quelle"] = SOURCE_V6
    return result


async def pvgis_degree_days(
    latitude: Lat,
    longitude: Lon,
    start_year: StartYear = V6_FIRST_YEAR,
    end_year: EndYear = V6_LAST_YEAR,
    period: Annotated[Literal["daily", "weekly", "monthly", "yearly"],
                      Field(description="Aggregation der Gradtage")] = "monthly",
    mode: Literal["all", "heating", "cooling"] = "all",
    heating_threshold_c: Annotated[float, Field(description="Heizgrenztemperatur in °C")] = 15.0,
    heating_base_c: Annotated[float, Field(description="Raum-Solltemperatur Heizen in °C")] = 18.0,
    cooling_threshold_c: Annotated[float, Field(description="Kühlgrenztemperatur in °C")] = 24.0,
    cooling_base_c: Annotated[float, Field(description="Raum-Solltemperatur Kühlen in °C")] = 21.0,
    export_csv: bool = False,
) -> dict:
    """Heiz- und Kühlgradtage aus ERA5-Temperaturen (2014–2024), z. B. für Wärme-/Kältebedarf
    von Rechenzentren oder Gebäuden."""
    check_coordinates(latitude, longitude)
    params = {"latitude": latitude, "longitude": longitude, "mode": mode, "period": period,
              "temperature_output_units": "Celsius", "heating_threshold": heating_threshold_c,
              "heating_cutoff": heating_base_c, "cooling_threshold": cooling_threshold_c,
              "cooling_cutoff": cooling_base_c}
    params.update(_period(start_year, end_year))
    data = await get_services().v6.degree_days(params)
    heat = data.get("heating_days") or {}
    cool = data.get("cooling_days") or {}
    temp = data.get("average_temperature") or {}
    keys = sorted(set(heat) | set(cool) | set(temp))
    table = [{"periode": k, "heizgradtage": _r(heat.get(k), 1), "kuehlgradtage": _r(cool.get(k), 1),
              "mitteltemperatur_c": _r(temp.get(k), 2)} for k in keys]
    result: dict[str, Any] = {
        "zeitraum": f"{start_year}–{end_year}",
        "mittel": {"heizgradtage_je_periode": _r(data.get("average_heating_days"), 1),
                   "kuehlgradtage_je_periode": _r(data.get("average_cooling_days"), 1),
                   "mitteltemperatur_c": _r(data.get("mean_temperature"), 2)},
        "schwellen": {"heizgrenze_c": heating_threshold_c, "heiz_soll_c": heating_base_c,
                      "kuehlgrenze_c": cooling_threshold_c, "kuehl_soll_c": cooling_base_c},
    }
    if export_csv or len(table) > 60:
        path = write_table(_out_dir(), build_name("gradtage", latitude, longitude, label=period),
                           list(table[0].keys()) if table else ["periode"],
                           [list(r.values()) for r in table], _csv_style())
        result["datei"] = str(path)
    if len(table) <= 60:
        result["werte"] = table
    else:
        result["hinweis"] = f"{len(table)} Perioden – vollständige Tabelle in der CSV-Datei."
    result["quelle"] = SOURCE_V6
    return result


async def pvgis_batch_yield(
    cases: Annotated[list[YieldCase], Field(description="Liste von Standorten/Varianten. Pro Fall: id, latitude, "
                                                        "longitude, optional peak_power_kwp, tilt_deg, azimuth, "
                                                        "system_loss_pct, module_technology, mounting, bifacial",
                                            min_length=1)],
    system_loss_pct: Loss = 14.0,
    module_technology: Tech = "crystSi2025",
    mounting: Mount = "free",
    bifacial: bool = False,
    start_year: StartYear = V6_FIRST_YEAR,
    end_year: EndYear = V6_LAST_YEAR,
    irradiance_source: Source = "SARAH-3",
) -> dict:
    """Ertragsberechnung für viele Standorte oder Varianten mit Rate-Limit-Steuerung und CSV-Export.

    Bereits berechnete Fälle kommen aus dem Cache und zählen nicht gegen das Limit. Reicht das
    Stundenkontingent nicht, werden die übrigen Fälle als 'zurückgestellt' gemeldet und können später
    mit demselben Aufruf nachgeholt werden.
    """
    svc = get_services()
    specs = [c if isinstance(c, YieldCase) else YieldCase(**c) for c in cases]
    if len(specs) > svc.settings.batch_max_cases:
        raise PvgisInputError(f"Maximal {svc.settings.batch_max_cases} Fälle pro Aufruf (PVGIS_BATCH_MAX_CASES).")
    rows, deferred, failed = [], [], []
    for case in specs:
        kwargs = dict(
            latitude=case.latitude, longitude=case.longitude, peak_power_kwp=case.peak_power_kwp,
            tilt_deg=case.tilt_deg, azimuth=case.azimuth,
            system_loss_pct=case.system_loss_pct if case.system_loss_pct is not None else system_loss_pct,
            module_technology=case.module_technology or module_technology,
            mounting=case.mounting or mounting,
            bifacial=case.bifacial if case.bifacial is not None else bifacial,
            start_year=start_year, end_year=end_year, irradiance_source=irradiance_source)
        try:
            res = await pvgis_estimate_yield(**kwargs)
        except PvgisRateLimitError as exc:
            deferred.append({"id": case.id, "grund": f"Rate-Limit, frei in ca. {exc.wait_s / 60:.0f} min"})
            continue
        except PvgisError as exc:
            failed.append({"id": case.id, "fehler": str(exc)})
            continue
        e = res["ergebnis"]
        rows.append({
            "id": case.id, "breite": case.latitude, "laenge": case.longitude,
            "leistung_kwp": case.peak_power_kwp, "neigung_grad": case.tilt_deg,
            "azimut_grad": res["anlage"]["azimut_grad"], "hoehe_m": res["standort"].get("hoehe_m"),
            "jahresertrag_kwh": e["jahresertrag_kwh"], "spezifischer_ertrag_kwh_kwp": e["spezifischer_ertrag_kwh_kwp"],
            "einstrahlung_kwh_m2": e["einstrahlung_modulebene_kwh_m2"], "performance_ratio": e["performance_ratio"],
            "jahresschwankung_kwh": e["jahresschwankung_stdabw_kwh"],
        })
    result: dict[str, Any] = {"berechnet": len(rows), "zurueckgestellt": deferred, "fehlgeschlagen": failed}
    if rows:
        path = write_table(svc.settings.output_dir, build_name("batch_ertrag", label=f"{len(rows)}_faelle"),
                           list(rows[0].keys()), [list(r.values()) for r in rows], svc.settings.csv_style)
        result["datei"] = str(path)
        result["ergebnisse"] = rows if len(rows) <= 50 else rows[:50]
        if len(rows) > 50:
            result["hinweis"] = "Nur die ersten 50 Ergebnisse angezeigt – vollständige Liste in der CSV-Datei."
    result["zeitraum"] = f"{start_year}–{end_year}"
    result["quelle"] = SOURCE_V6
    return result


async def pvgis_api_status(
    clear_cache: Annotated[bool, Field(description="Cache vollständig leeren")] = False,
) -> dict:
    """Status des Servers: Rate-Limit-Kontingente, Cache, Ausgabeordner und Konfiguration."""
    svc = get_services()
    cleared = await asyncio.to_thread(svc.storage.cache_clear) if clear_cache else None
    from . import __version__

    return {
        "version": __version__,
        "kontingent_pvgis6": svc.v6.http.limiter.usage(),
        "kontingent_pvgis53": svc.v53.http.limiter.usage(),
        "cache": svc.storage.cache_stats() | ({"geloescht": cleared} if cleared is not None else {}),
        "sitzung": {"pvgis6": svc.v6.http.stats, "pvgis53": svc.v53.http.stats},
        "ausgabeordner": str(svc.settings.output_dir),
        "csv_stil": svc.settings.csv_style,
        "pvgis6_url": svc.settings.v6_base_url,
        "pvgis53_url": svc.settings.v53_base_url if svc.settings.enable_v53 else "deaktiviert",
        "pvgis6_zeitraum": f"{V6_FIRST_YEAR}–{V6_LAST_YEAR}",
    }


V6_TOOLS = [
    pvgis_site_check,
    pvgis_estimate_yield,
    pvgis_compare_surfaces,
    pvgis_optimize_orientation,
    pvgis_power_timeseries,
    pvgis_horizon_profile,
    pvgis_solar_position,
    pvgis_degree_days,
    pvgis_batch_yield,
    pvgis_api_status,
]
