"""Live-Tests gegen die echten PVGIS-Server. Ausführen mit: PVGIS_LIVE=1 pytest -m live

Verbrauchen ca. 10 Aufrufe des PVGIS-6-Kontingents (200/h).
"""

import httpx
import pytest

from pvgis_mcp.config import V6_DEFAULT_URL, V53_DEFAULT_URL, Settings
from pvgis_mcp.services import Services, set_services

pytestmark = pytest.mark.live

# Endpunkte und Parameter, auf die sich dieser Server stützt (Stand Oktober 2026).
EXPECTED = {
    "/performance/broadband": {"latitude", "longitude", "surface_orientation", "surface_tilt", "start_time",
                               "end_time", "frequency", "irradiance_source", "horizon_profile", "photovoltaic_module",
                               "photovoltaic_module_type", "bifacialy_factor", "module_height", "albedo",
                               "system_efficiency", "peak-power", "angle_output_units", "analysis", "statistics",
                               "installation_height"},
    "/performance/broadband-multiple-surfaces": {"surface_orientation", "surface_tilt", "peak-power",
                                                 "show_individual_surfaces", "analysis", "statistics"},
    "/power/broadband": {"csv", "verbose", "timezone", "frequency", "start_time", "end_time"},
    "/power/surface/optimisation": {"surface_position_optimisation_mode", "surface_position_optimisation_method"},
    "/solar-position/overview": {"surface_orientation", "surface_tilt", "horizon_profile", "csv"},
    "/topography/horizon-profile": {"latitude", "longitude", "horizon_profile", "angle_output_units"},
    "/topography/elevation": {"latitude", "longitude"},
    "/meteorological/degree-days": {"mode", "period", "heating_threshold", "cooling_threshold", "heating_cutoff",
                                    "cooling_cutoff", "temperature_output_units"},
    "/coverage/available-sources-in-selected-location": {"latitude", "longitude"},
    "/coverage/location-within-selected-source": {"latitude", "longitude", "irradiance_source"},
}


def test_openapi_drift():
    spec = httpx.get(f"{V6_DEFAULT_URL}/openapi.json", timeout=60).json()
    problems = []
    for path, params in EXPECTED.items():
        op = spec["paths"].get(path, {}).get("get")
        if not op:
            problems.append(f"Endpunkt fehlt: {path}")
            continue
        names = {p["name"] for p in op.get("parameters", [])}
        missing = params - names
        if missing:
            problems.append(f"{path}: Parameter fehlen {sorted(missing)}")
    assert not problems, "PVGIS-6-API hat sich geändert:\n" + "\n".join(problems)


@pytest.fixture
async def live(tmp_path):
    svc = Services.create(Settings(output_dir=tmp_path / "out", cache_path=tmp_path / "c.sqlite"))
    set_services(svc)
    yield svc
    await svc.aclose()
    set_services(None)


async def test_live_yield_northern_germany(live):
    from pvgis_mcp.tools_v6 import pvgis_estimate_yield

    r = await pvgis_estimate_yield(latitude=53.25, longitude=9.5, peak_power_kwp=1, tilt_deg=35, azimuth="S")
    assert 900 < r["ergebnis"]["spezifischer_ertrag_kwh_kwp"] < 1200
    assert len(r["monatswerte"]) == 12


async def test_live_tracker(live):
    from pvgis_mcp.tools_v53 import pvgis_tracking_yield

    r = await pvgis_tracking_yield(latitude=53.25, longitude=9.5, systems=["fixed_optimal", "horizontal_ns"],
                                   capex_per_kwp=600)
    by = {s["system"]: s for s in r["systeme"]}
    assert by["horizontal_ns"]["jahresertrag_kwh"] > by["fixed_optimal"]["jahresertrag_kwh"]
    assert 0.01 < by["fixed_optimal"]["lcoe_je_kwh"] < 0.2


def test_live_v53_reachable():
    r = httpx.get(f"{V53_DEFAULT_URL}/printhorizon", params={"lat": 53.25, "lon": 9.5, "outputformat": "json"},
                  timeout=60)
    assert r.status_code == 200
