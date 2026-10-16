"""Beispielantworten, nachgebildet aus echten PVGIS-Antworten (Standort 53,25° N / 9,5° O, Oktober 2026)."""

MONTHLY_E = [27.106333, 50.521954, 88.06374, 122.44922, 134.0606, 134.66998, 129.30388, 122.46327, 98.9098,
             62.401672, 32.785786, 21.1393]
MONTHLY_SD = [3.39, 11.21, 17.87, 16.39, 16.16, 10.10, 11.69, 9.54, 10.42, 11.72, 5.94, 5.45]
MONTHLY_H = [31.797358, 59.438324, 104.62593, 147.66257, 164.17769, 167.12358, 160.25235, 151.92506, 121.149956,
             75.10438, 38.93336, 25.029186]
MONTHLY_T = [2.79, 3.87, 5.80, 9.09, 13.50, 17.52, 18.56, 18.79, 15.69, 11.65, 6.66, 4.43]


def stats(scale: float = 1.0, sd_unit: str = "kWh") -> dict:
    return {
        "yearly_average_energy": {"value": 1023.8754375 * scale, "units": "kWh", "comment": "Using peak power"},
        "monthly_energy_summary": {"value": [v * scale for v in MONTHLY_E], "units": "kWh"},
        "monthly_energy_standard_deviation_due_to_year_to_year_variability": {
            "value": [v * scale for v in MONTHLY_SD], "units": sd_unit},
        "year_to_year_energy_variability": {"value": 58.36 * scale, "units": "kWh"},
        "yearly_average_global_inclined_irradiance_before_reflectivity": {"value": 1247.219875, "units": "kWh/m²"},
        "monthly_global_inclined_irradiance_before_reflectivity_summary": {"value": MONTHLY_H, "units": "kWh/m²"},
        "average_temperature": {"value": 10.4, "units": "C"},
        "average_temperature_per_month": {"value": MONTHLY_T, "units": "C"},
        "count": 96432,
    }


def perf(kwp: float = 1.0, tilt: float = 35, az: float = 180) -> dict:
    return {
        "latitude": {"value": 53.25, "unit": "degrees"},
        "longitude": {"value": 9.5, "unit": "degrees"},
        "elevation": {"value": 34, "unit": "meters"},
        "surface_orientation": {"value": az, "unit": "degrees"},
        "surface_tilt": {"value": tilt, "unit": "degrees"},
        "start_time": "2014-01-01 00:11", "end_time": "2024-12-31 23:11", "frequency": "h",
        "energy": {"value": 11262.63 * kwp, "unit": "kWh"},
        "spectral_effect_percentage": 1.777, "reflectivity_effect_percentage": -1.818,
        "temperature_and_low_irradiance_effect_percentage": -4.474,
        "system_efficiency_effect_percentage": -14.0, "effect_percentage": -17.907,
        "technology": "cSi", "peak_power": kwp, "mount_type": "Free standing 2025",
    }


PERFORMANCE = {"photovoltaic_performance": perf(), "statistics": stats()}

MULTI = {
    "photovoltaic_performance": perf(100),
    "performance_for_surface_0": perf(50, 15, 90),
    "performance_for_surface_1": perf(50, 15, 270),
    "statistics": {**stats(87.87), "yearly_average_energy": {"value": 87866.92, "units": "kWh"}},
    "statistics_for_surface_0": {**stats(44.46), "yearly_average_energy": {"value": 44457.73, "units": "kWh"},
                                 "yearly_average_global_inclined_irradiance_before_reflectivity":
                                     {"value": 1090.16, "units": "kWh/m²"}},
    "statistics_for_surface_1": {**stats(43.41), "yearly_average_energy": {"value": 43409.19, "units": "kWh"},
                                 "yearly_average_global_inclined_irradiance_before_reflectivity":
                                     {"value": 1068.27, "units": "kWh/m²"}},
}

SOURCES = {"irradiance_sources": ["SARAH-3", "ERA5", "CLEAR-SKY [Hofierka et al. (2002)]"]}
WITHIN = {"result": True, "detail": "The requested coordinates are within the extend of SARAH-3 source."}
ELEVATION = {"latitude": 53.25, "longitude": 9.5, "elevation": 34}
HORIZON = {
    "latitude": 53.25, "longitude": 9.5,
    "horizon_profile": {"azimuth": [i * 7.5 for i in range(48)],
                        "horizon_height": [0.38 + (1.5 if 150 <= i * 7.5 <= 210 else 0) for i in range(48)],
                        "unit": "degrees", "reference": "Azimuth starts at North and increases clockwise"},
}
OPTIMUM = {"optimal_surface_position": {"surface_orientation": 173.09186669805334, "surface_tilt": 38.43753773108714}}

POWER_CSV = (
    "Time,Longitude,Latitude,Power,Module Type,Technology,Peak Power,Peak Power Unit\n"
    "2020-06-21T10:11:04.912801,9.5,53.25,765.3648,Mono-Facial,cSi:Free standing 2025,1.0,kWp\n"
    "2020-06-21T11:11:04.912801,9.5,53.25,803.1229,Mono-Facial,cSi:Free standing 2025,1.0,kWp\n"
    "2020-06-21T12:11:04.912801,9.5,53.25,776.29504,Mono-Facial,cSi:Free standing 2025,1.0,kWp\n"
)

DEGREE_DAYS = {
    "heating_days": {"2020-01": 396.284, "2020-02": 354.96, "2020-03": 384.33},
    "cooling_days": {"2020-01": 0, "2020-02": 0, "2020-03": 0},
    "average_temperature": {"2020-01": 5.217, "2020-02": 5.76, "2020-03": 5.602},
    "average_heating_days": 378.525, "average_cooling_days": 0, "mean_temperature": 5.526,
    "temperature_output_units": "Celsius",
}

SOLAR_POSITION = {"results": {"noaa": {
    "position_algorithm": "NOAA", "altitude": [56.298782, 59.885048], "azimuth": [143.99794, 169.39964],
    "zenith": [33.701214, 30.114954], "incidence": [25.085112, 16.18052], "visible": [True, True],
    "Incidence angle": "Sun-Vector-to-Surface-Normal"}}}
SOLAR_POSITION_CSV = "Time,Altitude,Azimuth,Incidence\n2020-06-21T10:00:00,56.3,144.0,25.1\n2020-06-21T11:00:00,59.9,169.4,16.2\n"


def _sys(e_y, h_y, lcoe, slope=None, opt=False, az=None):
    return ({"E_d": e_y / 365, "E_m": e_y / 12, "E_y": e_y, "H(i)_d": h_y / 365, "H(i)_m": h_y / 12, "H(i)_y": h_y,
             "SD_m": 4.4, "SD_y": 52.7, "l_aoi": -3.14, "l_spec": "1.80", "l_tg": -5.8, "l_total": -20.12,
             "LCOE_pv": lcoe},
            {"slope": {"value": slope if slope is not None else "-", "optimal": opt if slope is not None else "-"},
             "azimuth": {"value": az if az is not None else "-", "optimal": False if az is not None else "-"}})


def pvcalc(slots: dict[str, tuple]) -> dict:
    """slots: name -> (E_y, H_y, LCOE_ref, slope, optimal, aspect)."""
    totals, mounting, monthly = {}, {}, {}
    for name, (e, h, l, s, o, a) in slots.items():
        totals[name], mounting[name] = _sys(e, h, l, s, o, a)
        monthly[name] = [{"month": m, "E_m": e / 12} for m in range(1, 13)]
    return {"inputs": {"location": {"latitude": 53.25, "longitude": 9.5, "elevation": 35},
                       "meteo_data": {"radiation_db": "PVGIS-SARAH3", "meteo_db": "ERA5", "year_min": 2005,
                                      "year_max": 2023, "use_horizon": True, "horizon_db": "DEM-calculated"},
                       "mounting_system": mounting},
            "outputs": {"monthly": monthly, "totals": totals}}


def seriescalc(years=(2014, 2015), p_w=100.0) -> dict:
    hourly = []
    for y in years:
        for m in range(1, 13):
            for h in range(10):
                hourly.append({"time": f"{y}{m:02d}15:{h:02d}11", "P": p_w, "G(i)": 120.0, "H_sun": 20,
                               "T2m": 10, "WS10m": 3, "Int": 0})
    return {"inputs": {"meteo_data": {"radiation_db": "PVGIS-SARAH3"},
                       "mounting_system": {"horizontal_axis_E-W": {}}},
            "outputs": {"hourly": hourly}}


def tmy() -> dict:
    rows = []
    for m in range(1, 13):
        for d in range(2):
            rows.append({"time(UTC)": f"2022{m:02d}0{d + 1}:1200", "T2m": 5.0 + m, "RH": 80.0, "G(h)": 300.0,
                         "Gb(n)": 200.0, "Gd(h)": 100.0, "IR(h)": 300.0, "WS10m": 3.0, "WD10m": 250, "SP": 101500})
    return {"inputs": {"location": {"latitude": 53.25, "longitude": 9.5, "elevation": 35},
                       "meteo_data": {"radiation_db": "PVGIS-SARAH3", "year_min": 2005, "year_max": 2023}},
            "outputs": {"months_selected": [{"month": m, "year": 2010 + m} for m in range(1, 13)],
                        "tmy_hourly": rows}}


SHS = {"inputs": {"meteo_data": {"radiation_db": "PVGIS-SARAH3"}},
       "outputs": {"monthly": [{"month": m, "E_d": 367.59, "E_lost_d": 0.02, "f_f": 0.17, "f_e": 100}
                               for m in range(1, 13)],
                   "totals": {"d_total": 6939, "f_f": 29.9, "f_e": 71.15, "E_lost": 616.42, "E_miss": 772.44},
                   "histogram": [{"CS_min": 40, "CS_max": 46, "f_CS": 47}]}}

MRCALC = {"inputs": {"meteo_data": {"radiation_db": "PVGIS-SARAH3"}, "plane": {}},
          "outputs": {"monthly": [{"year": y, "month": m, "H(h)_m": 18.07 * m, "H(i_opt)_m": 31.49 * m,
                                   "Hb(n)_m": 22.15, "T2m": 5.4} for y in (2020, 2021) for m in range(1, 13)]}}

DRCALC = {"inputs": {}, "outputs": {"daily_profile": [
    {"month": 6, "time": f"{h:02d}:00", "G(i)": max(0, 800 - abs(12 - h) * 100), "Gb(i)": 0, "Gd(i)": 0,
     "Gcs(i)": 900} for h in range(24)]}}
