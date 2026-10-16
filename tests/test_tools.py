"""Werkzeuge mit nachgebildeten PVGIS-Antworten, aufgerufen über den MCP-Server."""

import csv
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

import fixtures as fx
from conftest import V6, V53
from pvgis_mcp.server import build_server


async def call(server, name, args):
    """Ruft ein Werkzeug über die MCP-Schicht auf und gibt das strukturierte Ergebnis zurück."""
    res = await server.call_tool(name, args)
    # SDK 2.x: CallToolResult; SDK 1.x: (content, structured) oder Liste
    if hasattr(res, "structured_content") and res.structured_content is not None:
        data = res.structured_content
    elif isinstance(res, tuple):
        data = res[1]
    else:
        content = res.content if hasattr(res, "content") else res
        data = json.loads(content[0].text)
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data


def q(request) -> dict:
    return {k: v if len(v) > 1 else v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


@pytest.fixture
async def server(services):
    return build_server()


async def test_estimate_yield(server):
    with respx.mock() as mock:
        route = mock.get(f"{V6}/performance/broadband").mock(return_value=httpx.Response(200, json=fx.PERFORMANCE))
        r = await call(server, "pvgis_estimate_yield",
                       {"latitude": 53.25, "longitude": 9.5, "peak_power_kwp": 1, "tilt_deg": 35, "azimuth": "S",
                        "system_loss_pct": 14})
        sent = q(route.calls[0].request)
    assert sent["surface_orientation"] == "180"
    assert sent["system_efficiency"] == "0.86"
    assert sent["peak-power"] == "1"
    assert sent["photovoltaic_module"] == "cSi:Free standing 2025"
    assert sent["start_time"] == "2014-01-01T00:00:00" and sent["end_time"].startswith("2024-12-31")
    assert r["ergebnis"]["spezifischer_ertrag_kwh_kwp"] == pytest.approx(1023.9, abs=0.1)
    assert r["ergebnis"]["performance_ratio"] == pytest.approx(0.821, abs=0.001)
    assert len(r["monatswerte"]) == 12 and r["monatswerte"][5]["monat"] == "Jun"
    assert r["verluste_prozent"]["system"] == -14.0
    assert r["anlage"]["ausrichtung"] == "S"


async def test_year_range_validation(server):
    with pytest.raises(Exception, match="Startjahr liegt nach dem Endjahr"):
        await server.call_tool("pvgis_estimate_yield", {"latitude": 53, "longitude": 9, "start_year": 2016,
                                                        "end_year": 2015})
    with pytest.raises(Exception, match="2014"):  # Schema-Grenze
        await server.call_tool("pvgis_estimate_yield", {"latitude": 53, "longitude": 9, "start_year": 2010})


async def test_compare_surfaces(server):
    with respx.mock() as mock:
        route = mock.get(f"{V6}/performance/broadband-multiple-surfaces").mock(
            return_value=httpx.Response(200, json=fx.MULTI))
        r = await call(server, "pvgis_compare_surfaces", {
            "latitude": 53.25, "longitude": 9.5,
            "surfaces": [{"label": "Ost", "tilt_deg": 15, "azimuth": "O", "peak_power_kwp": 50},
                         {"label": "West", "tilt_deg": 15, "azimuth": 270, "peak_power_kwp": 50}]})
        params = parse_qs(urlparse(str(route.calls[0].request.url)).query)
    assert params["surface_orientation"] == ["90", "270"]
    assert params["peak-power"] == ["50", "50"]
    assert r["gesamt"]["jahresertrag_kwh"] == 87867
    assert r["flaechen"][0]["spezifischer_ertrag_kwh_kwp"] == pytest.approx(889.2, abs=0.1)
    assert sum(f["anteil_prozent"] for f in r["flaechen"]) == pytest.approx(100, abs=0.2)


async def test_optimize(server):
    with respx.mock() as mock:
        opt = mock.get(f"{V6}/power/surface/optimisation").mock(return_value=httpx.Response(200, json=fx.OPTIMUM))
        mock.get(f"{V6}/performance/broadband").mock(return_value=httpx.Response(200, json=fx.PERFORMANCE))
        r = await call(server, "pvgis_optimize_orientation", {"latitude": 53.25, "longitude": 9.5})
        sent = q(opt.calls[0].request)
    assert sent["surface_position_optimisation_mode"] == "Orientation & Tilt"
    assert "system_efficiency" not in sent
    assert r["optimum"] == {"neigung_grad": 38.4, "azimut_grad": 173.1, "ausrichtung": "S"}
    assert r["ertrag_bei_optimum"]["jahresertrag_kwh"] == 1024


async def test_site_check(server):
    with respx.mock() as mock:
        mock.get(f"{V6}/coverage/available-sources-in-selected-location").mock(
            return_value=httpx.Response(200, json=fx.SOURCES))
        mock.get(f"{V6}/coverage/location-within-selected-source").mock(return_value=httpx.Response(200, json=fx.WITHIN))
        mock.get(f"{V6}/topography/elevation").mock(return_value=httpx.Response(200, json=fx.ELEVATION))
        mock.get(f"{V6}/topography/horizon-profile").mock(return_value=httpx.Response(200, json=fx.HORIZON))
        r = await call(server, "pvgis_site_check", {"latitude": 53.25, "longitude": 9.5})
    assert r["empfohlene_quelle"] == "SARAH-3"
    assert r["standort"]["hoehe_m"] == 34
    assert r["horizont"]["bewertung"] == "vernachlässigbar"
    assert r["horizont"]["richtung_max"].endswith("(SSO)") or "S" in r["horizont"]["richtung_max"]


async def test_power_timeseries(server, settings):
    with respx.mock() as mock:
        route = mock.get(f"{V6}/power/broadband").mock(
            return_value=httpx.Response(200, text=fx.POWER_CSV, headers={"content-type": "text/csv"}))
        r = await call(server, "pvgis_power_timeseries", {
            "latitude": 53.25, "longitude": 9.5, "start": "2020-06-21T10:00", "end": "2020-06-21T12:59"})
        sent = q(route.calls[0].request)
    assert sent["csv"] == "power.csv" and sent["verbose"] == "1"
    assert r["kennzahlen"]["energie_kwh"] == pytest.approx(2.34, abs=0.01)
    assert r["kennzahlen"]["zeitpunkt_max"].startswith("2020-06-21T11")
    assert r["metadaten"]["Module Type"] == "Mono-Facial"
    with open(r["datei"], encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["Time", "Power"] and len(rows) == 4


async def test_power_timeseries_outside_range(server):
    with pytest.raises(Exception, match="2014"):
        await server.call_tool("pvgis_power_timeseries", {"latitude": 53, "longitude": 9, "start": "2010-01-01",
                                                          "end": "2010-01-02"})


async def test_horizon_upload(server, tmp_path):
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"horizon_height": [5, 10, 15, 20, 10, 5, 0, 0]}))
    with respx.mock() as mock:
        route = mock.post(f"{V6}/topography/horizon-profile").mock(return_value=httpx.Response(200, json=fx.HORIZON))
        r = await call(server, "pvgis_horizon_profile", {"latitude": 53.25, "longitude": 9.5,
                                                         "file_path": str(f), "export_csv": True})
        body = route.calls[0].request.content
    assert b'name="horizon_profile"' in body and b"horizon_height" in body
    assert r["herkunft"] == "Datei h.json" and len(r["profil"]) == 48 and r["datei"].endswith(".csv")


async def test_solar_position(server):
    with respx.mock() as mock:
        def handler(request):
            if "csv" in q(request):
                return httpx.Response(200, text=fx.SOLAR_POSITION_CSV, headers={"content-type": "text/csv"})
            return httpx.Response(200, json=fx.SOLAR_POSITION)
        mock.get(f"{V6}/solar-position/overview").mock(side_effect=handler)
        r = await call(server, "pvgis_solar_position", {"latitude": 53.25, "longitude": 9.5,
                                                        "start": "2020-06-21T10:00", "end": "2020-06-21T11:59"})
    assert r["kennzahlen"]["max_sonnenhoehe_grad"] == pytest.approx(59.89, abs=0.01)
    assert len(r["werte"]) == 2 and r["datei"].endswith(".csv")


async def test_degree_days(server):
    with respx.mock() as mock:
        mock.get(f"{V6}/meteorological/degree-days").mock(return_value=httpx.Response(200, json=fx.DEGREE_DAYS))
        r = await call(server, "pvgis_degree_days", {"latitude": 53.25, "longitude": 9.5, "start_year": 2020,
                                                     "end_year": 2020})
    assert r["werte"][0] == {"periode": "2020-01", "heizgradtage": 396.3, "kuehlgradtage": 0,
                             "mitteltemperatur_c": 5.22}


async def test_batch_with_cache_and_rate_limit(server, services):
    services.v6.http.limiter.limits = ((2, 3600.0),)
    services.v6.http.limiter.max_wait_s = 0.2
    cases = [{"id": f"F{i}", "latitude": 53 + i / 100, "longitude": 9.5} for i in range(3)]
    with respx.mock() as mock:
        route = mock.get(f"{V6}/performance/broadband").mock(return_value=httpx.Response(200, json=fx.PERFORMANCE))
        r = await call(server, "pvgis_batch_yield", {"cases": cases})
        assert r["berechnet"] == 2 and r["zurueckgestellt"][0]["id"] == "F2"
        # erneuter Aufruf: F0/F1 aus dem Cache, F2 weiterhin zurückgestellt
        r2 = await call(server, "pvgis_batch_yield", {"cases": cases})
        assert r2["berechnet"] == 2 and route.call_count == 2
    with open(r["datei"], encoding="utf-8") as fh:
        assert len(fh.read().splitlines()) == 3


async def test_tracking_yield_grouping_and_lcoe(server):
    calls = []

    def handler(request):
        p = q(request)
        calls.append(p)
        slots = {}
        if p.get("fixed") == "1":
            slots["fixed"] = (977.37, 1223.5, 8031.5, 39 if p.get("optimalangles") else 20,
                              bool(p.get("optimalangles")), 0)
        if p.get("inclined_axis") == "1":
            slots["inclined_axis"] = (1150.0, 1450.0, 6826.1, 0, False, None)
        if p.get("twoaxis") == "1":
            slots["two_axis"] = (1301.88, 1605.8, 6030.2, None, False, None)
        return httpx.Response(200, json=fx.pvcalc(slots))

    with respx.mock() as mock:
        mock.get(f"{V53}/PVcalc").mock(side_effect=handler)
        mock.get(f"{V53}/seriescalc").mock(return_value=httpx.Response(200, json=fx.seriescalc((2014, 2015))))
        r = await call(server, "pvgis_tracking_yield", {
            "latitude": 53.25, "longitude": 9.5, "peak_power_kwp": 100,
            "systems": ["fixed_optimal", "horizontal_ns", "two_axis", "horizontal_ew"],
            "capex_per_kwp": {"fixed_optimal": 550, "horizontal_ns": 640, "two_axis": 900},
            "ew_start_year": 2014, "ew_end_year": 2015})
    assert len(calls) == 1  # alle PVcalc-Systeme in einem Aufruf
    p = calls[0]
    assert p["optimalangles"] == "1" and p["inclinedaxisangle"] == "0" and p["twoaxis"] == "1"
    assert p["pvprice"] == "1" and p["systemcost"] == "100000000" and p["peakpower"] == "1"
    assert p["outputformat"] == "json"
    by = {s["system"]: s for s in r["systeme"]}
    assert by["horizontal_ns"]["mehrertrag_vs_fixed_optimal_prozent"] == pytest.approx(17.7, abs=0.1)
    assert by["fixed_optimal"]["lcoe_je_kwh"] == pytest.approx(8031.5 * 550 / 1e8, abs=1e-5)
    assert by["fixed_optimal"]["jahresertrag_kwh"] == pytest.approx(97737, abs=1)  # 100 kWp skaliert
    assert by["fixed_optimal"]["investition"] == 55000
    assert by["horizontal_ew"]["jahresertrag_kwh"] == pytest.approx(1200.0, abs=0.1)  # 120 h * 100 W * 100 kWp
    assert "_lcoe_ref" not in by["two_axis"]


async def test_tracking_needs_two_calls_for_conflicting_slots(server):
    calls = []

    def handler(request):
        p = q(request)
        calls.append(p)
        slots = {}
        if p.get("fixed") == "1":
            slots["fixed"] = (977.0, 1200.0, None, 20, False, 0)
        if p.get("inclined_axis") == "1":
            slots["inclined_axis"] = (1100.0, 1400.0, None, 30, True, None)
        return httpx.Response(200, json=fx.pvcalc(slots))

    with respx.mock() as mock:
        mock.get(f"{V53}/PVcalc").mock(side_effect=handler)
        r = await call(server, "pvgis_tracking_yield", {"latitude": 53.25, "longitude": 9.5,
                                                        "systems": ["fixed", "inclined_axis", "horizontal_ns"],
                                                        "fixed_azimuth": "SW"})
    assert len(calls) == 2
    assert calls[0]["aspect"] == "45" and calls[0]["inclined_optimum"] == "1"
    assert calls[1]["fixed"] == "0" and calls[1]["inclinedaxisangle"] == "0"
    assert [s["system"] for s in r["systeme"]] == ["fixed", "inclined_axis", "horizontal_ns"]


async def test_tmy(server, settings):
    with respx.mock() as mock:
        def handler(request):
            if q(request)["outputformat"] == "epw":
                return httpx.Response(200, text="LOCATION,Test\n")
            return httpx.Response(200, json=fx.tmy())
        mock.get(f"{V53}/tmy").mock(side_effect=handler)
        r = await call(server, "pvgis_tmy", {"latitude": 53.25, "longitude": 9.5, "file_format": "both"})
    assert r["monate"][0]["quelljahr"] == 2011 and r["monate"][0]["ghi_kwh_m2"] == 0.6
    assert r["dateien"]["epw"].endswith(".epw") and r["dateien"]["csv"].endswith(".csv")


async def test_offgrid(server):
    with respx.mock() as mock:
        route = mock.get(f"{V53}/SHScalc").mock(return_value=httpx.Response(200, json=fx.SHS))
        r = await call(server, "pvgis_offgrid", {"latitude": 53.25, "longitude": 9.5, "peak_power_w": 500,
                                                 "battery_capacity_wh": 2000, "consumption_wh_per_day": 1500,
                                                 "hourly_profile": [1] * 24})
        p = q(route.calls[0].request)
    assert p["hourconsumption"][0] == "0.041667" and len(p["hourconsumption"]) == 24
    assert r["ergebnis"]["tage_batterie_leer_prozent"] == 71.15


async def test_monthly_radiation(server):
    with respx.mock() as mock:
        mock.get(f"{V53}/MRcalc").mock(return_value=httpx.Response(200, json=fx.MRCALC))
        r = await call(server, "pvgis_monthly_radiation", {"latitude": 53.25, "longitude": 9.5})
    assert r["jahressummen"]["H(h)_jahr"]["mittel"] == pytest.approx(18.07 * 78, abs=0.1)
    assert r["monatsmittel"][0]["monat"] == "Jan"


async def test_daily_profile_and_legacy_series(server):
    with respx.mock() as mock:
        mock.get(f"{V53}/DRcalc").mock(return_value=httpx.Response(200, json=fx.DRCALC))
        route = mock.get(f"{V53}/seriescalc").mock(return_value=httpx.Response(200, json=fx.seriescalc((2020,))))
        r = await call(server, "pvgis_daily_profile", {"latitude": 53.25, "longitude": 9.5})
        assert len(r["tagesgang"]) == 24
        r = await call(server, "pvgis_hourly_series_legacy", {"latitude": 53.25, "longitude": 9.5,
                                                              "start_year": 2020, "end_year": 2020,
                                                              "tracking": "horizontal_ns", "peak_power_kwp": 2})
        p = q(route.calls[0].request)
    assert p["trackingtype"] == "1" and "aspect" not in p
    assert r["jahressummen"]["2020"]["energie_kwh"] == 12.0 and r["spezifischer_ertrag_kwh_kwp"] == 6.0


async def test_api_status(server):
    r = await call(server, "pvgis_api_status", {})
    assert r["kontingent_pvgis6"][2]["limit"] == 200


async def test_resources_and_prompts(server):
    res = await server.read_resource("pvgis://hilfe/parameter")
    first = res[0] if isinstance(res, (list, tuple)) else res
    text = getattr(first, "content", None) or getattr(first, "text", None) or str(first)
    assert "Azimut" in text
    prompt = await server.get_prompt("tracker_wirtschaftlichkeit", {
        "latitude": "53.25", "longitude": "9.5", "leistung_kwp": "10000", "capex_fest_je_kwp": "550",
        "capex_tracker_je_kwp": "640"})
    assert "horizontal_ns" in str(prompt)
