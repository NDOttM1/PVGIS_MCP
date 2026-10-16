"""Werkzeuge auf Basis von PVGIS 5.3 – ergänzt PVGIS 6 um Tracker, TMY, Inselanlagen,
Stromgestehungskosten (LCOE) und Strahlungsstatistiken (Daten 2005–2023)."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import Field

from .conventions import (
    azimuth_label,
    check_coordinates,
    compass_to_v53_aspect,
    parse_azimuth,
    v53_horizon,
    v53_technology,
)
from .errors import PvgisInputError
from .export import build_name, write_table, write_text
from .services import get_services
from .tools_v6 import MONTHS, Azimuth, Horizon, Kwp, Lat, Lon, Loss, Mount, Tech, _r

SOURCE_V53 = "PVGIS 5.3, Joint Research Centre (JRC), Europäische Kommission"
RadDb = Annotated[
    Optional[Literal["PVGIS-SARAH3", "PVGIS-ERA5"]],
    Field(description="Strahlungsdatenbank; leer = PVGIS-Standard für den Standort (Europa: SARAH3)"),
]
Year53 = Annotated[Optional[int], Field(description="Jahr (2005–2023, je nach Datenbank); leer = gesamter Zeitraum",
                                        ge=2005, le=2030)]

TrackingSystem = Literal["fixed", "fixed_optimal", "horizontal_ns", "inclined_axis", "vertical_axis",
                         "two_axis", "horizontal_ew"]
SYSTEM_NAMES = {
    "fixed": "Fest (vorgegebene Ausrichtung)",
    "fixed_optimal": "Fest (optimale Ausrichtung)",
    "horizontal_ns": "Einachsig, horizontale Achse Nord-Süd (Standard-Tracker Freifläche)",
    "inclined_axis": "Einachsig, geneigte Achse Nord-Süd",
    "vertical_axis": "Einachsig, vertikale Achse (Azimut-Nachführung)",
    "two_axis": "Zweiachsig",
    "horizontal_ew": "Einachsig, horizontale Achse Ost-West",
}
_SLOT = {"fixed": "fixed", "fixed_optimal": "fixed", "horizontal_ns": "inclined_axis",
         "inclined_axis": "inclined_axis", "vertical_axis": "vertical_axis", "two_axis": "two_axis"}
# PVGIS gibt LCOE nur mit 2 Nachkommastellen aus und erlaubt Systemkosten bis 1e8. Gerechnet wird deshalb
# je 1 kWp mit Referenzkosten 1e8 und anschließend linear skaliert (Ertrag ∝ kWp, LCOE ∝ Kosten – live geprüft).
_LCOE_REF_COST = 100_000_000.0


def _v53_base(latitude: float, longitude: float, raddatabase: Optional[str], horizon: Any) -> dict:
    check_coordinates(latitude, longitude)
    if not get_services().settings.enable_v53:
        raise PvgisInputError("PVGIS-5.3-Werkzeuge sind deaktiviert (PVGIS_ENABLE_V53=false).")
    p: dict[str, Any] = {"lat": latitude, "lon": longitude, "raddatabase": raddatabase}
    p.update(v53_horizon(horizon))
    return p


def _f(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


async def pvgis_tracking_yield(
    latitude: Lat,
    longitude: Lon,
    peak_power_kwp: Kwp = 1.0,
    systems: Annotated[list[TrackingSystem], Field(
        description="Zu vergleichende Systeme: fixed, fixed_optimal, horizontal_ns (horizontaler "
                    "Einachs-Tracker N-S, Standard bei Freiflächen), inclined_axis, vertical_axis, two_axis, "
                    "horizontal_ew", min_length=1)] = ["fixed_optimal", "horizontal_ns", "two_axis"],
    fixed_tilt_deg: Annotated[float, Field(description="Neigung für 'fixed'", ge=0, le=90)] = 20.0,
    fixed_azimuth: Annotated[Union[float, str], Field(description="Azimut für 'fixed' (Kompass, 180=S)")] = 180.0,
    inclined_axis_tilt_deg: Annotated[Optional[float], Field(
        description="Achsneigung für 'inclined_axis'; leer = optimal", ge=0, le=90)] = None,
    vertical_axis_tilt_deg: Annotated[Optional[float], Field(
        description="Modulneigung für 'vertical_axis'; leer = optimal", ge=0, le=90)] = None,
    system_loss_pct: Loss = 14.0,
    module_technology: Tech = "crystSi2025",
    mounting: Mount = "free",
    raddatabase: RadDb = None,
    horizon: Horizon = "auto",
    capex_per_kwp: Annotated[Optional[Union[float, dict[str, float]]], Field(
        description="Investitionskosten je kWp (beliebige Währung) für die Stromgestehungskosten. Eine Zahl für "
                    "alle Systeme oder je System, z. B. {'fixed_optimal': 550, 'horizontal_ns': 640}")] = None,
    interest_pct: Annotated[float, Field(description="Kalkulationszins % p. a. für LCOE", ge=0, le=30)] = 5.0,
    lifetime_years: Annotated[int, Field(description="Nutzungsdauer in Jahren für LCOE", ge=1, le=60)] = 30,
    ew_start_year: Annotated[int, Field(description="Startjahr für 'horizontal_ew' (Stundenreihe)",
                                        ge=2005, le=2023)] = 2014,
    ew_end_year: Annotated[int, Field(description="Endjahr für 'horizontal_ew'", ge=2005, le=2023)] = 2023,
) -> dict:
    """Ertragsvergleich fest montierter und nachgeführter PV-Systeme (Tracker) mit Mehrertrag und
    optional Stromgestehungskosten (LCOE) – Basis PVGIS 5.3 (Langzeitmittel 2005–2023).

    Typische Frage: Lohnt ein Einachs-Tracker (horizontal_ns) gegenüber fester Aufständerung?
    """
    base = _v53_base(latitude, longitude, raddatabase, horizon)
    base.update({"peakpower": 1, "loss": system_loss_pct,
                 "pvtechchoice": v53_technology(module_technology),
                 "mountingplace": "free" if mounting == "free" else "building"})
    use_lcoe = capex_per_kwp is not None
    if use_lcoe:
        base.update({"pvprice": 1, "systemcost": _LCOE_REF_COST, "interest": interest_pct,
                     "lifetime": lifetime_years})

    wanted = list(dict.fromkeys(systems))
    calls: list[dict[str, str]] = []  # Slot -> System
    for s in wanted:
        if s == "horizontal_ew":
            continue
        slot = _SLOT[s]
        for c in calls:
            if slot not in c:
                c[slot] = s
                break
        else:
            calls.append({slot: s})

    v53 = get_services().v53
    results: dict[str, dict] = {}
    meteo: dict = {}
    for c in calls:
        p = dict(base)
        p["fixed"] = 1 if "fixed" in c else 0
        if c.get("fixed") == "fixed":
            p.update({"angle": fixed_tilt_deg, "aspect": compass_to_v53_aspect(parse_azimuth(fixed_azimuth))})
        elif c.get("fixed") == "fixed_optimal":
            p["optimalangles"] = 1
        if c.get("inclined_axis") == "horizontal_ns":
            p.update({"inclined_axis": 1, "inclinedaxisangle": 0})
        elif c.get("inclined_axis") == "inclined_axis":
            p["inclined_axis"] = 1
            if inclined_axis_tilt_deg is None:
                p["inclined_optimum"] = 1
            else:
                p["inclinedaxisangle"] = inclined_axis_tilt_deg
        if "vertical_axis" in c:
            p["vertical_axis"] = 1
            if vertical_axis_tilt_deg is None:
                p["vertical_optimum"] = 1
            else:
                p["verticalaxisangle"] = vertical_axis_tilt_deg
        if "two_axis" in c:
            p["twoaxis"] = 1
        data = await v53.call("PVcalc", p)
        meteo = data.get("inputs", {}).get("meteo_data", meteo)
        mount_in = data.get("inputs", {}).get("mounting_system", {})
        totals = data.get("outputs", {}).get("totals", {})
        monthly = data.get("outputs", {}).get("monthly", {})
        for slot, system in c.items():
            t = totals.get(slot, {})
            m = monthly.get(slot, [])
            angles = mount_in.get(slot, {})
            results[system] = _tracking_entry(system, t, m, angles, peak_power_kwp)  # Werte je kWp skaliert

    if "horizontal_ew" in wanted:
        results["horizontal_ew"] = await _horizontal_ew(base, peak_power_kwp, ew_start_year, ew_end_year)

    baseline = next((b for b in ("fixed", "fixed_optimal") if b in results), None)
    base_e = results[baseline]["jahresertrag_kwh"] if baseline else None
    rows = []
    for s in wanted:
        r = results[s]
        if base_e and r.get("jahresertrag_kwh") is not None:
            r["mehrertrag_vs_" + baseline + "_prozent"] = _r(100 * (r["jahresertrag_kwh"] / base_e - 1), 1)
        if use_lcoe:
            capex = capex_per_kwp.get(s) if isinstance(capex_per_kwp, dict) else capex_per_kwp
            ref = r.pop("_lcoe_ref", None)
            if capex is not None and ref is not None:
                r["investition"] = _r(float(capex * peak_power_kwp), 0)
                r["lcoe_je_kwh"] = round(ref * capex / _LCOE_REF_COST, 5)
            elif s == "horizontal_ew":
                r["lcoe_je_kwh"] = None
        else:
            r.pop("_lcoe_ref", None)
        rows.append(r)

    out: dict[str, Any] = {
        "standort": {"breite": latitude, "laenge": longitude},
        "leistung_kwp": peak_power_kwp,
        "datenbasis": {"strahlung": meteo.get("radiation_db"), "meteo": meteo.get("meteo_db"),
                       "jahre": f"{meteo.get('year_min')}–{meteo.get('year_max')}",
                       "horizont": meteo.get("horizon_db")},
        "systeme": rows,
        "referenz_fuer_mehrertrag": baseline,
    }
    if use_lcoe:
        out["lcoe_annahmen"] = {"zins_prozent": interest_pct, "nutzungsdauer_jahre": lifetime_years,
                                "methode": "Berechnung durch PVGIS (pvprice), auf die angegebenen Kosten skaliert"}
    out["hinweise"] = [
        "PVGIS 5.3 nutzt andere Datensätze und Jahre als PVGIS 6; Werte sind untereinander, "
        "aber nicht exakt mit pvgis_estimate_yield vergleichbar.",
        "Tracker-Modelle ohne Rückwärtsnachführung (Backtracking) und ohne Reihenverschattung.",
    ]
    out["quelle"] = SOURCE_V53
    return out


def _tracking_entry(system: str, t: dict, monthly: list, angles: dict, kwp: float) -> dict:
    """t/monthly gelten für 1 kWp und werden auf kwp skaliert."""
    e1 = _f(t.get("E_y"))
    e_y = e1 * kwp if e1 is not None else None
    sd = _f(t.get("SD_y"))
    slope = angles.get("slope", {}) if isinstance(angles, dict) else {}
    az = angles.get("azimuth", {}) if isinstance(angles, dict) else {}
    entry: dict[str, Any] = {
        "system": system,
        "beschreibung": SYSTEM_NAMES[system],
        "jahresertrag_kwh": _r(e_y, 0),
        "spezifischer_ertrag_kwh_kwp": _r(e1, 1),
        "einstrahlung_kwh_m2": _r(_f(t.get("H(i)_y")), 0),
        "jahresschwankung_stdabw_kwh": _r(sd * kwp, 0) if sd is not None else None,
        "verluste_prozent": {"einfallswinkel": _f(t.get("l_aoi")), "spektral": _f(t.get("l_spec")),
                             "temperatur_schwachlicht": _f(t.get("l_tg")), "gesamt": _f(t.get("l_total"))},
        "monatsertrag_kwh": [_r((_f(m.get("E_m")) or 0) * kwp, 1) for m in monthly],
        "_lcoe_ref": _f(t.get("LCOE_pv")),
    }
    if slope.get("value") not in (None, "-"):
        entry["neigung_grad"] = slope.get("value")
        if slope.get("optimal"):
            entry["neigung_optimal"] = True
    if az.get("value") not in (None, "-"):
        comp = (float(az["value"]) + 180) % 360
        entry["azimut_grad"] = comp
        entry["ausrichtung"] = azimuth_label(comp)
    return entry


async def _horizontal_ew(base: dict, kwp: float, y0: int, y1: int) -> dict:
    p = {k: v for k, v in base.items() if k not in {"pvprice", "systemcost", "interest", "lifetime"}}
    p.update({"pvcalculation": 1, "trackingtype": 4, "startyear": y0, "endyear": y1})
    data = await get_services().v53.call("seriescalc", p)
    hourly = data.get("outputs", {}).get("hourly", [])
    years = max(1, y1 - y0 + 1)
    by_month = defaultdict(float)
    for row in hourly:
        t = str(row.get("time", ""))
        if len(t) >= 6:
            by_month[int(t[4:6])] += (row.get("P") or 0) / 1000
    annual = sum(by_month.values()) / years * kwp  # Abfrage je 1 kWp
    return {
        "system": "horizontal_ew",
        "beschreibung": SYSTEM_NAMES["horizontal_ew"],
        "jahresertrag_kwh": _r(annual, 0),
        "spezifischer_ertrag_kwh_kwp": _r(annual / kwp, 1),
        "monatsertrag_kwh": [_r(by_month[m] / years * kwp, 1) for m in range(1, 13)],
        "hinweis": f"aus Stundenreihe {y0}–{y1} aggregiert (keine Verlustaufschlüsselung/LCOE)",
        "_lcoe_ref": None,
    }


async def pvgis_tmy(
    latitude: Lat,
    longitude: Lon,
    start_year: Annotated[Optional[int], Field(description="Erstes Jahr (Zeitraum mind. 10 Jahre); leer = alle",
                                               ge=2005, le=2023)] = None,
    end_year: Annotated[Optional[int], Field(description="Letztes Jahr; leer = alle", ge=2005, le=2023)] = None,
    file_format: Annotated[Literal["csv", "epw", "both"],
                           Field(description="Exportformat: csv (Stundenwerte), epw (EnergyPlus) oder beides")] = "csv",
    horizon: Horizon = "auto",
) -> dict:
    """Typisches meteorologisches Jahr (TMY, 8760 Stundenwerte) für Simulationen (z. B. PVsyst-Import,
    EnergyPlus, Kühlbedarf von Rechenzentren). Liefert Monatsübersicht und Exportdatei(en)."""
    p = _v53_base(latitude, longitude, None, horizon)
    p.pop("raddatabase", None)
    if start_year and end_year and end_year - start_year + 1 < 10:
        raise PvgisInputError("Der TMY-Zeitraum muss mindestens 10 Jahre umfassen.")
    p.update({"startyear": start_year, "endyear": end_year})
    v53 = get_services().v53
    data = await v53.call("tmy", p)
    out_dir = get_services().settings.output_dir
    hourly = data.get("outputs", {}).get("tmy_hourly", [])
    months_sel = data.get("outputs", {}).get("months_selected", [])
    agg: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in hourly:
        t = str(row.get("time(UTC)", ""))
        if len(t) < 6:
            continue
        m = int(t[4:6])
        a = agg[m]
        a["n"] += 1
        a["T2m"] += row.get("T2m") or 0
        a["RH"] += row.get("RH") or 0
        a["WS10m"] += row.get("WS10m") or 0
        a["Gh"] += (row.get("G(h)") or 0) / 1000
        a["Gbn"] += (row.get("Gb(n)") or 0) / 1000
        a["Gdh"] += (row.get("Gd(h)") or 0) / 1000
    sel = {int(x["month"]): x.get("year") for x in months_sel if "month" in x}
    table = [{
        "monat": MONTHS[m - 1], "quelljahr": sel.get(m),
        "temperatur_c": _r(a["T2m"] / a["n"], 1), "rel_feuchte_prozent": _r(a["RH"] / a["n"], 0),
        "wind_m_s": _r(a["WS10m"] / a["n"], 1), "ghi_kwh_m2": _r(a["Gh"], 1),
        "dni_kwh_m2": _r(a["Gbn"], 1), "dhi_kwh_m2": _r(a["Gdh"], 1),
    } for m, a in sorted(agg.items()) if a["n"]]
    result: dict[str, Any] = {
        "standort": data.get("inputs", {}).get("location"),
        "datenbasis": data.get("inputs", {}).get("meteo_data"),
        "jahressumme_ghi_kwh_m2": _r(sum(r["ghi_kwh_m2"] for r in table), 0),
        "jahresmittel_temperatur_c": _r(sum(r["temperatur_c"] * 1 for r in table) / len(table), 1) if table else None,
        "monate": table,
        "dateien": {},
    }
    style = get_services().settings.csv_style
    if file_format in ("csv", "both") and hourly:
        cols = list(hourly[0].keys())
        path = write_table(out_dir, build_name("tmy", latitude, longitude), cols,
                           [[r.get(c) for c in cols] for r in hourly], style)
        result["dateien"]["csv"] = str(path)
    if file_format in ("epw", "both"):
        epw = await v53.call_text("tmy", p, "epw")
        path = write_text(out_dir, build_name("tmy", latitude, longitude, ext="epw"), epw)
        result["dateien"]["epw"] = str(path)
    result["einheiten"] = {"T2m": "°C", "RH": "%", "G(h)/Gb(n)/Gd(h)": "W/m²", "IR(h)": "W/m²",
                           "WS10m": "m/s", "WD10m": "Grad (0=N, 90=O)", "SP": "Pa", "Zeit": "UTC"}
    result["quelle"] = SOURCE_V53
    return result


async def pvgis_offgrid(
    latitude: Lat,
    longitude: Lon,
    peak_power_w: Annotated[float, Field(description="PV-Leistung in Watt (Wp)", gt=0)],
    battery_capacity_wh: Annotated[float, Field(description="Batteriekapazität in Wh", gt=0)],
    consumption_wh_per_day: Annotated[float, Field(description="Tagesverbrauch in Wh", gt=0)],
    discharge_cutoff_pct: Annotated[float, Field(description="Entladegrenze in % (Batterie wird nicht unter "
                                                             "diesen Ladezustand entladen)", ge=0, lt=100)] = 40.0,
    tilt_deg: Annotated[float, Field(ge=0, le=90)] = 45.0,
    azimuth: Azimuth = 180.0,
    hourly_profile: Annotated[Optional[list[float]], Field(
        description="Optional 24 Anteile des Tagesverbrauchs je Stunde (Summe 1, Normalzeit)")] = None,
    raddatabase: RadDb = None,
    horizon: Horizon = "auto",
) -> dict:
    """Inselanlage (PV + Batterie, ohne Netz): Deckungsgrad, Tage mit voller bzw. leerer Batterie,
    ungenutzte und fehlende Energie – z. B. für Sensorik, Baustellen, Weidezäune, Messmasten."""
    p = _v53_base(latitude, longitude, raddatabase, horizon)
    if hourly_profile is not None:
        if len(hourly_profile) != 24:
            raise PvgisInputError("Das Stundenprofil braucht genau 24 Werte.")
        s = sum(hourly_profile)
        if s <= 0:
            raise PvgisInputError("Das Stundenprofil muss positive Werte enthalten.")
        hourly_profile = [round(v / s, 6) for v in hourly_profile]
    p.update({"peakpower": peak_power_w, "batterysize": battery_capacity_wh, "cutoff": discharge_cutoff_pct,
              "consumptionday": consumption_wh_per_day, "angle": tilt_deg,
              "aspect": compass_to_v53_aspect(parse_azimuth(azimuth)), "hourconsumption": hourly_profile})
    data = await get_services().v53.call("SHScalc", p)
    o = data.get("outputs", {})
    tot = o.get("totals", {})
    monthly = [{"monat": MONTHS[int(m["month"]) - 1], "energie_wh_tag": m.get("E_d"),
                "ungenutzt_wh_tag": m.get("E_lost_d"), "tage_batterie_voll_prozent": m.get("f_f"),
                "tage_batterie_leer_prozent": m.get("f_e")} for m in o.get("monthly", [])]
    return {
        "anlage": {"pv_wp": peak_power_w, "batterie_wh": battery_capacity_wh, "entladegrenze_prozent":
                   discharge_cutoff_pct, "verbrauch_wh_tag": consumption_wh_per_day, "neigung_grad": tilt_deg,
                   "azimut_grad": parse_azimuth(azimuth)},
        "ergebnis": {"tage_ausgewertet": tot.get("d_total"),
                     "tage_batterie_voll_prozent": tot.get("f_f"),
                     "tage_batterie_leer_prozent": tot.get("f_e"),
                     "ungenutzte_energie_wh_tag": tot.get("E_lost"),
                     "fehlende_energie_wh_tag": tot.get("E_miss")},
        "monatswerte": monthly,
        "ladezustand_histogramm": [{"von_prozent": h.get("CS_min"), "bis_prozent": h.get("CS_max"),
                                    "anteil_prozent": h.get("f_CS")} for h in o.get("histogram", [])],
        "datenbasis": data.get("inputs", {}).get("meteo_data"),
        "quelle": SOURCE_V53,
    }


async def pvgis_monthly_radiation(
    latitude: Lat,
    longitude: Lon,
    start_year: Year53 = None,
    end_year: Year53 = None,
    horizontal: Annotated[bool, Field(description="Globalstrahlung horizontal")] = True,
    optimal_plane: Annotated[bool, Field(description="Strahlung auf optimal geneigter Fläche")] = True,
    selected_tilt_deg: Annotated[Optional[float], Field(description="Zusätzlich Strahlung bei dieser Neigung "
                                                                    "(Südausrichtung)", ge=0, le=90)] = None,
    direct_normal: Annotated[bool, Field(description="Direktnormalstrahlung (DNI)")] = True,
    diffuse_ratio: Annotated[bool, Field(description="Diffusanteil")] = False,
    temperature: Annotated[bool, Field(description="Monatsmitteltemperatur")] = True,
    raddatabase: RadDb = None,
    horizon: Horizon = "auto",
    export_csv: bool = True,
) -> dict:
    """Monatliche Einstrahlungs- und Temperaturwerte je Jahr (PVGIS 5.3, 2005–2023) plus
    Langzeit-Monatsmittel – z. B. für Gutachten, Jahresvergleiche oder Bankunterlagen."""
    p = _v53_base(latitude, longitude, raddatabase, horizon)
    p.update({"startyear": start_year, "endyear": end_year, "horirrad": horizontal, "optrad": optimal_plane,
              "mr_dni": direct_normal, "d2g": diffuse_ratio, "avtemp": temperature})
    if selected_tilt_deg is not None:
        p.update({"selectrad": 1, "angle": selected_tilt_deg})
    data = await get_services().v53.call("MRcalc", p)
    monthly = data.get("outputs", {}).get("monthly", [])
    if not monthly:
        raise PvgisInputError("Keine Werte – mindestens eine Ausgabegröße aktivieren.")
    fields = [k for k in monthly[0].keys() if k not in ("year", "month")]
    means = []
    for m in range(1, 13):
        vals = [r for r in monthly if r.get("month") == m]
        if vals:
            means.append({"monat": MONTHS[m - 1], **{f: _r(sum(_f(v.get(f)) or 0 for v in vals) / len(vals), 2)
                                                     for f in fields}})
    annual: dict[str, Any] = {}
    for f in fields:
        if f.endswith("_m"):
            per_year = defaultdict(float)
            for r in monthly:
                per_year[r["year"]] += _f(r.get(f)) or 0
            annual[f.replace("_m", "_jahr")] = {"mittel": _r(sum(per_year.values()) / len(per_year), 1),
                                               "min": _r(min(per_year.values()), 1),
                                               "max": _r(max(per_year.values()), 1),
                                               "je_jahr": {y: _r(v, 1) for y, v in sorted(per_year.items())}}
    result: dict[str, Any] = {
        "datenbasis": data.get("inputs", {}).get("meteo_data"),
        "ebenen": data.get("inputs", {}).get("plane"),
        "jahressummen": annual,
        "monatsmittel": means,
        "einheiten": {"H(h)_m": "kWh/m² horizontal", "H(i_opt)_m": "kWh/m² optimal geneigt",
                      "H(i)_m": "kWh/m² gewählte Neigung", "Hb(n)_m": "kWh/m² direkt-normal",
                      "Kd": "Diffusanteil", "T2m": "°C"},
    }
    if export_csv:
        cols = ["year", "month"] + fields
        result["datei"] = str(write_table(get_services().settings.output_dir,
                                          build_name("monatsstrahlung", latitude, longitude), cols,
                                          [[r.get(c) for c in cols] for r in monthly],
                                          get_services().settings.csv_style))
    result["quelle"] = SOURCE_V53
    return result


async def pvgis_daily_profile(
    latitude: Lat,
    longitude: Lon,
    month: Annotated[int, Field(description="Monat 1–12; 0 = alle Monate", ge=0, le=12)] = 6,
    tilt_deg: Annotated[float, Field(ge=0, le=90)] = 0.0,
    azimuth: Azimuth = 180.0,
    global_irradiance: Annotated[bool, Field(description="Global-, Direkt- und Diffusstrahlung auf der Fläche")] = True,
    two_axis: Annotated[bool, Field(description="Strahlung bei zweiachsiger Nachführung")] = False,
    clear_sky: Annotated[bool, Field(description="Klarhimmel-Strahlung")] = True,
    temperatures: Annotated[bool, Field(description="Mittlerer Tagesgang der Temperatur")] = False,
    local_time: Annotated[bool, Field(description="Ortszeit (ohne Sommerzeit) statt UTC")] = True,
    raddatabase: RadDb = None,
    horizon: Horizon = "auto",
) -> dict:
    """Mittlerer Tagesgang der Einstrahlung (W/m²) je Stunde für einen Monat oder alle Monate –
    z. B. für Lastprofilabgleich oder Eigenverbrauchsabschätzungen."""
    p = _v53_base(latitude, longitude, raddatabase, horizon)
    p.update({"month": month, "angle": tilt_deg, "aspect": compass_to_v53_aspect(parse_azimuth(azimuth)),
              "global": global_irradiance, "glob_2axis": two_axis, "clearsky": clear_sky,
              "clearsky_2axis": two_axis and clear_sky, "showtemperatures": temperatures,
              "localtime": local_time})
    data = await get_services().v53.call("DRcalc", p)
    prof = data.get("outputs", {}).get("daily_profile", [])
    result: dict[str, Any] = {"zeitbasis": "Ortszeit (Normalzeit)" if local_time else "UTC",
                              "einheiten": "W/m² (G = global, Gb = direkt, Gd = diffus, Gcs = Klarhimmel)"}
    if len(prof) <= 48:
        result["tagesgang"] = prof
    else:
        cols = list(prof[0].keys())
        result["datei"] = str(write_table(get_services().settings.output_dir,
                                          build_name("tagesgang", latitude, longitude, label="alle_monate"), cols,
                                          [[r.get(c) for c in cols] for r in prof],
                                          get_services().settings.csv_style))
        peak = defaultdict(float)
        for r in prof:
            peak[r.get("month")] = max(peak[r.get("month")], _f(r.get("G(i)")) or 0)
        result["max_global_je_monat_w_m2"] = {MONTHS[int(m) - 1]: v for m, v in sorted(peak.items())}
    result["quelle"] = SOURCE_V53
    return result


_TRACKING_TYPES = {"fixed": 0, "horizontal_ns": 1, "two_axis": 2, "vertical_axis": 3, "horizontal_ew": 4,
                   "inclined_ns": 5}


async def pvgis_hourly_series_legacy(
    latitude: Lat,
    longitude: Lon,
    start_year: Annotated[int, Field(description="Erstes Jahr (2005–2023)", ge=2005, le=2023)],
    end_year: Annotated[int, Field(description="Letztes Jahr (2005–2023)", ge=2005, le=2023)],
    pv_calculation: Annotated[bool, Field(description="PV-Leistung berechnen (sonst nur Strahlung)")] = True,
    peak_power_kwp: Kwp = 1.0,
    system_loss_pct: Loss = 14.0,
    tracking: Annotated[Literal["fixed", "horizontal_ns", "two_axis", "vertical_axis", "horizontal_ew",
                                "inclined_ns"], Field(description="Montage-/Nachführungsart")] = "fixed",
    tilt_deg: Annotated[float, Field(ge=0, le=90)] = 30.0,
    azimuth: Azimuth = 180.0,
    optimal_angles: Annotated[bool, Field(description="Optimale Neigung und Ausrichtung (nur fest)")] = False,
    components: Annotated[bool, Field(description="Direkt-, Diffus- und Reflexanteil ausgeben")] = False,
    module_technology: Tech = "crystSi2025",
    mounting: Mount = "free",
    raddatabase: RadDb = None,
    horizon: Horizon = "auto",
) -> dict:
    """Stündliche Strahlungs- und PV-Zeitreihe aus PVGIS 5.3 (2005–2023), auch für Tracker.

    Ergänzt pvgis_power_timeseries (PVGIS 6, nur 2014–2024) um ältere Jahre und Nachführsysteme.
    Die Zeitreihe wird als CSV geschrieben; im Ergebnis stehen Jahressummen.
    """
    if start_year > end_year:
        raise PvgisInputError("Startjahr liegt nach dem Endjahr.")
    p = _v53_base(latitude, longitude, raddatabase, horizon)
    p.update({"startyear": start_year, "endyear": end_year, "pvcalculation": pv_calculation,
              "trackingtype": _TRACKING_TYPES[tracking], "components": components})
    if pv_calculation:
        p.update({"peakpower": peak_power_kwp, "loss": system_loss_pct,
                  "pvtechchoice": v53_technology(module_technology),
                  "mountingplace": "free" if mounting == "free" else "building"})
    if tracking in ("fixed", "vertical_axis", "inclined_ns"):
        if optimal_angles and tracking == "fixed":
            p["optimalangles"] = 1
        else:
            p["angle"] = tilt_deg
    if tracking == "fixed" and not optimal_angles:
        p["aspect"] = compass_to_v53_aspect(parse_azimuth(azimuth))
    data = await get_services().v53.call("seriescalc", p)
    hourly = data.get("outputs", {}).get("hourly", [])
    if not hourly:
        raise PvgisInputError("PVGIS lieferte keine Stundenwerte.")
    cols = list(hourly[0].keys())
    path = write_table(get_services().settings.output_dir,
                       build_name("stundenreihe_v53", latitude, longitude, label=f"{tracking}_{start_year}-{end_year}"),
                       cols, [[r.get(c) for c in cols] for r in hourly], get_services().settings.csv_style)
    per_year: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in hourly:
        y = str(r.get("time", ""))[:4]
        if "P" in r:
            per_year[y]["energie_kwh"] += (r.get("P") or 0) / 1000
        gi = r.get("G(i)")
        if gi is None and "Gb(i)" in r:
            gi = (r.get("Gb(i)") or 0) + (r.get("Gd(i)") or 0) + (r.get("Gr(i)") or 0)
        per_year[y]["einstrahlung_kwh_m2"] += (gi or 0) / 1000
    years = {y: {k: _r(v, 1) for k, v in d.items()} for y, d in sorted(per_year.items())}
    result: dict[str, Any] = {
        "datei": str(path),
        "spalten": cols,
        "werte": len(hourly),
        "jahressummen": years,
        "montage": data.get("inputs", {}).get("mounting_system"),
        "datenbasis": data.get("inputs", {}).get("meteo_data"),
        "einheiten": {"P": "W", "G(i)": "W/m²", "H_sun": "Grad", "T2m": "°C", "WS10m": "m/s",
                      "time": "UTC, Format JJJJMMTT:hhmm"},
    }
    if pv_calculation and years:
        mean = sum(v.get("energie_kwh", 0) for v in years.values()) / len(years)
        result["mittlerer_jahresertrag_kwh"] = _r(mean, 0)
        result["spezifischer_ertrag_kwh_kwp"] = _r(mean / peak_power_kwp, 1)
    result["quelle"] = SOURCE_V53
    return result


V53_TOOLS = [
    pvgis_tracking_yield,
    pvgis_tmy,
    pvgis_offgrid,
    pvgis_monthly_radiation,
    pvgis_daily_profile,
    pvgis_hourly_series_legacy,
]
