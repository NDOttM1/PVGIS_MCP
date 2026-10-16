"""HTTP-Schicht: Rate-Limit, Cache, Wiederholungen und Fehlerübersetzung."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import httpx

from .config import Settings
from .errors import (
    STATUS_TEXT,
    PvgisError,
    PvgisInputError,
    PvgisUnavailableError,
    describe_error_body,
)
from .storage import RateLimiter, Storage

log = logging.getLogger("pvgis_mcp.http")

RETRY_STATUS = {429, 450, 460, 500, 502, 503, 504, 529}
Params = list[tuple[str, str]]


@dataclass
class ApiResponse:
    status: int
    content_type: str
    body: bytes
    from_cache: bool = False
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))

    def text(self) -> str:
        return self.body.decode("utf-8-sig", "replace")


def _fmt(value: Any, bool_style: str) -> str:
    if isinstance(value, bool):
        if bool_style == "int":
            return "1" if value else "0"
        return "true" if value else "false"
    if isinstance(value, float):
        txt = f"{value:.6f}".rstrip("0").rstrip(".")
        return txt if txt not in ("", "-0") else "0"
    return str(value)


def encode_params(params: Mapping[str, Any], bool_style: str = "word") -> Params:
    """Wandelt ein Dict in Query-Parameter um.

    ``None`` wird weggelassen, Listen werden als wiederholte Parameter übergeben
    (FastAPI-Konvention), Booleans je nach API als ``true/false`` oder ``1/0``.
    """
    out: Params = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            out.extend((key, _fmt(v, bool_style)) for v in value)
        else:
            out.append((key, _fmt(value, bool_style)))
    return out


def cache_key(api: str, method: str, path: str, params: Iterable[tuple[str, str]], body_hash: str = "") -> str:
    canon = "&".join(f"{k}={v}" for k, v in sorted(params))
    raw = f"{api}|{method}|{path}|{canon}|{body_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()


class PvgisHttp:
    """Gemeinsamer HTTP-Client für eine PVGIS-API-Version."""

    def __init__(self, *, api: str, base_url: str, settings: Settings, storage: Storage,
                 limits: tuple[tuple[int, float], ...], client: httpx.AsyncClient | None = None):
        self.api = api
        self.base_url = base_url.rstrip("/")
        self.settings = settings
        self.storage = storage
        self.limiter = RateLimiter(storage, api, limits, settings.max_wait_s)
        self._client = client
        self.stats = {"aufrufe": 0, "cache_treffer": 0, "wiederholungen": 0}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.timeout_s, connect=20.0),
                headers={"User-Agent": self.settings.user_agent},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def is_cached(self, path: str, params: Params, method: str = "GET", accept: str = "application/json") -> bool:
        if not self.settings.cache_enabled:
            return False
        key = cache_key(self.api, method, path, params + [("_accept", accept)])
        return self.storage.cache_get(key, self.settings.cache_ttl_days * 86400) is not None

    async def request(self, path: str, params: Params, *, method: str = "GET",
                      files: dict[str, tuple[str, bytes, str]] | None = None,
                      accept: str = "application/json", use_cache: bool = True) -> ApiResponse:
        body_hash = ""
        if files:
            h = hashlib.sha256()
            for name, (fname, content, ctype) in sorted(files.items()):
                h.update(name.encode() + fname.encode() + ctype.encode() + content)
            body_hash = h.hexdigest()
        key = cache_key(self.api, method, path, params + [("_accept", accept)], body_hash)
        ttl = self.settings.cache_ttl_days * 86400
        if use_cache and self.settings.cache_enabled:
            hit = await asyncio.to_thread(self.storage.cache_get, key, ttl)
            if hit:
                status, ctype, body = hit
                self.stats["cache_treffer"] += 1
                return ApiResponse(status, ctype or "", body, from_cache=True)

        url = f"{self.base_url}/{path.lstrip('/')}"
        attempt = 0
        while True:
            await self.limiter.acquire()
            self.stats["aufrufe"] += 1
            t0 = time.monotonic()
            try:
                resp = await self.client.request(
                    method, url, params=params, files=files, headers={"Accept": accept}
                )
            except httpx.TimeoutException as exc:
                if attempt < self.settings.max_retries:
                    attempt += 1
                    self.stats["wiederholungen"] += 1
                    await asyncio.sleep(min(30, 3 * attempt))
                    continue
                raise PvgisUnavailableError(
                    f"Zeitüberschreitung nach {self.settings.timeout_s:.0f}s bei {self.api}. "
                    "Bei langen Zeiträumen oder hoher Detailstufe den Zeitraum verkürzen.",
                    api=self.api,
                ) from exc
            except httpx.HTTPError as exc:
                if attempt < self.settings.max_retries:
                    attempt += 1
                    self.stats["wiederholungen"] += 1
                    await asyncio.sleep(min(30, 3 * attempt))
                    continue
                raise PvgisUnavailableError(
                    f"Netzwerkfehler beim Zugriff auf {self.api}: {exc}", api=self.api
                ) from exc

            log.debug("%s %s -> %s (%.2fs)", method, resp.url, resp.status_code, time.monotonic() - t0)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200:
                if use_cache and self.settings.cache_enabled:
                    await asyncio.to_thread(self.storage.cache_put, key, 200, ctype, resp.content)
                return ApiResponse(200, ctype, resp.content, headers=dict(resp.headers))

            if resp.status_code in RETRY_STATUS and attempt < self.settings.max_retries:
                attempt += 1
                self.stats["wiederholungen"] += 1
                await asyncio.sleep(self._retry_delay(resp, attempt))
                continue
            raise self._to_error(resp)

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        if resp.status_code == 429:
            for header in ("retry-after", "x-ratelimit-reset"):
                raw = resp.headers.get(header)
                if not raw:
                    continue
                try:
                    val = float(raw)
                except ValueError:
                    continue
                delay = val - time.time() if val > 1e9 else val  # Epoch oder Sekunden
                return max(1.0, min(delay + 0.5, self.settings.max_wait_s))
            return min(60.0, 5.0 * attempt)
        return min(30.0, 2.0 * (2 ** (attempt - 1)))

    def _to_error(self, resp: httpx.Response) -> PvgisError:
        msg, details, req_id = describe_error_body(resp.content)
        req_id = req_id or resp.headers.get("x-request-id")
        status = resp.status_code
        head = STATUS_TEXT.get(status, "Fehler")
        if status in (400, 422):
            return PvgisInputError(f"{head}: {msg}", status=status, request_id=req_id, api=self.api, details=details)
        if status == 429:
            return PvgisUnavailableError(
                f"{head}. PVGIS hat die Anfrage trotz Wartezeit abgelehnt. Später erneut versuchen.",
                status=status, request_id=req_id, api=self.api,
            )
        if status in RETRY_STATUS or status == 550:
            return PvgisUnavailableError(f"{head}: {msg}", status=status, request_id=req_id, api=self.api, details=details)
        return PvgisError(f"{head}: {msg}", status=status, request_id=req_id, api=self.api, details=details)
