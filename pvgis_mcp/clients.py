"""Dünne Clients für die PVGIS-6-Web-API und die PVGIS-5.3-API."""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from .config import Settings
from .errors import PvgisError
from .http import ApiResponse, Params, PvgisHttp, encode_params
from .storage import Storage


class PvgisV6:
    """PVGIS 6 Web API (FastAPI). Basis: .../api/v6"""

    API = "PVGIS 6"

    def __init__(self, settings: Settings, storage: Storage, client: httpx.AsyncClient | None = None):
        self.http = PvgisHttp(api=self.API, base_url=settings.v6_base_url, settings=settings,
                              storage=storage, limits=settings.v6_limits, client=client)

    @staticmethod
    def params(p: Mapping[str, Any]) -> Params:
        return encode_params(p, bool_style="word")

    async def get_json(self, path: str, p: Mapping[str, Any], *, use_cache: bool = True) -> Any:
        resp = await self.http.request(path, self.params(p), use_cache=use_cache)
        return _json(resp, self.API)

    async def get_csv(self, path: str, p: Mapping[str, Any], filename: str = "pvgis.csv") -> str:
        query = dict(p)
        query["csv"] = filename
        resp = await self.http.request(path, self.params(query), accept="text/csv")
        return resp.text()

    async def post_file_json(self, path: str, p: Mapping[str, Any], field: str, filename: str,
                             content: bytes, content_type: str) -> Any:
        resp = await self.http.request(path, self.params(p), method="POST",
                                       files={field: (filename, content, content_type)})
        return _json(resp, self.API)

    def is_cached(self, path: str, p: Mapping[str, Any]) -> bool:
        return self.http.is_cached(path, self.params(p))

    # Endpunkte ----------------------------------------------------------------
    async def performance(self, p: Mapping[str, Any]) -> Any:
        return await self.get_json("performance/broadband", p)

    async def performance_multi(self, p: Mapping[str, Any]) -> Any:
        return await self.get_json("performance/broadband-multiple-surfaces", p)

    async def optimise(self, p: Mapping[str, Any]) -> Any:
        return await self.get_json("power/surface/optimisation", p)

    async def elevation(self, latitude: float, longitude: float) -> Any:
        return await self.get_json("topography/elevation", {"latitude": latitude, "longitude": longitude,
                                                            "angle_output_units": "Degrees"})

    async def horizon(self, latitude: float, longitude: float, horizon_profile: str = "PVGIS") -> Any:
        return await self.get_json("topography/horizon-profile", {
            "latitude": latitude, "longitude": longitude, "horizon_profile": horizon_profile,
            "angle_output_units": "Degrees"})

    async def available_sources(self, latitude: float, longitude: float) -> Any:
        return await self.get_json("coverage/available-sources-in-selected-location",
                                   {"latitude": latitude, "longitude": longitude})

    async def within_source(self, latitude: float, longitude: float, source: str) -> Any:
        return await self.get_json("coverage/location-within-selected-source",
                                   {"latitude": latitude, "longitude": longitude, "irradiance_source": source})

    async def degree_days(self, p: Mapping[str, Any]) -> Any:
        return await self.get_json("meteorological/degree-days", p)

    async def reference(self, topic: str) -> ApiResponse:
        path = "features" if topic == "features" else f"reference/{topic}"
        return await self.http.request(path, [], accept="*/*")

    async def coverage_layer(self, name: str) -> Any:
        return await self.get_json(f"coverage/layers/{name}", {})


class PvgisV53:
    """PVGIS 5.3 (re.jrc.ec.europa.eu/api/v5_3) – Tracker, TMY, Inselanlagen, Strahlungsstatistik."""

    API = "PVGIS 5.3"

    def __init__(self, settings: Settings, storage: Storage, client: httpx.AsyncClient | None = None):
        self.http = PvgisHttp(api=self.API, base_url=settings.v53_base_url, settings=settings,
                              storage=storage, limits=settings.v53_limits, client=client)

    @staticmethod
    def params(p: Mapping[str, Any], outputformat: str = "json") -> Params:
        q = dict(p)
        q["outputformat"] = outputformat
        return encode_params(q, bool_style="int")

    async def call(self, tool: str, p: Mapping[str, Any]) -> Any:
        resp = await self.http.request(tool, self.params(p))
        return _json(resp, self.API)

    async def call_text(self, tool: str, p: Mapping[str, Any], outputformat: str) -> str:
        resp = await self.http.request(tool, self.params(p, outputformat), accept="*/*")
        return resp.text()


def _json(resp: ApiResponse, api: str) -> Any:
    try:
        return resp.json()
    except ValueError as exc:
        raise PvgisError(f"Unerwartete Antwort von {api} (kein JSON): {resp.text()[:300]}", api=api) from exc
