"""SQLite-Speicher für Antwort-Cache und Rate-Limit-Protokoll.

Eine Datei wird von allen Server-Prozessen geteilt (z. B. Claude Desktop über stdio
und ein HTTP-Server für andere Agenten). Dadurch zählt das stündliche PVGIS-Limit
prozessübergreifend korrekt, und berechnete Ergebnisse werden wiederverwendet.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    created REAL NOT NULL,
    status INTEGER NOT NULL,
    content_type TEXT,
    body BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS request_log (
    api TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_request_log ON request_log(api, ts);
"""


class Storage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            yield con
        finally:
            con.close()

    # ------------------------------------------------------------------ Cache
    def cache_get(self, key: str, ttl_s: float) -> tuple[int, str | None, bytes] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT created, status, content_type, body FROM cache WHERE key=?", (key,)
            ).fetchone()
        if not row:
            return None
        created, status, ctype, body = row
        if ttl_s > 0 and time.time() - created > ttl_s:
            return None
        return status, ctype, body

    def cache_put(self, key: str, status: int, content_type: str | None, body: bytes) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO cache(key, created, status, content_type, body) VALUES (?,?,?,?,?)",
                (key, time.time(), status, content_type, body),
            )

    def cache_stats(self) -> dict:
        with self._connect() as con:
            n, size = con.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(body)),0) FROM cache").fetchone()
        return {"eintraege": n, "groesse_mb": round(size / 1_048_576, 2), "datei": str(self.path)}

    def cache_clear(self) -> int:
        with self._connect() as con:
            n = con.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            con.execute("DELETE FROM cache")
        return n

    # ----------------------------------------------------------- Rate-Limit-Log
    def try_acquire(self, api: str, limits: tuple[tuple[int, float], ...], now: float | None = None) -> float:
        """Versucht, einen Aufruf zu reservieren.

        Gibt 0 zurück, wenn der Aufruf protokolliert wurde, sonst die nötige Wartezeit in Sekunden.
        Atomar über Prozesse hinweg (BEGIN IMMEDIATE).
        """
        now = time.time() if now is None else now
        longest = max(w for _, w in limits)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute("DELETE FROM request_log WHERE api=? AND ts < ?", (api, now - longest - 5))
                wait = 0.0
                for limit, window in limits:
                    rows = con.execute(
                        "SELECT ts FROM request_log WHERE api=? AND ts > ? ORDER BY ts ASC",
                        (api, now - window),
                    ).fetchall()
                    if len(rows) >= limit:
                        # Frei, sobald der älteste relevante Eintrag aus dem Fenster fällt
                        oldest = rows[len(rows) - limit][0]
                        wait = max(wait, oldest + window - now + 0.01)
                if wait > 0:
                    con.execute("COMMIT")
                    return wait
                con.execute("INSERT INTO request_log(api, ts) VALUES (?, ?)", (api, now))
                con.execute("COMMIT")
                return 0.0
            except Exception:
                con.execute("ROLLBACK")
                raise

    def usage(self, api: str, limits: tuple[tuple[int, float], ...]) -> list[dict]:
        now = time.time()
        out = []
        with self._connect() as con:
            for limit, window in limits:
                n = con.execute(
                    "SELECT COUNT(*) FROM request_log WHERE api=? AND ts > ?", (api, now - window)
                ).fetchone()[0]
                out.append({"fenster_s": window, "limit": limit, "genutzt": n, "frei": max(0, limit - n)})
        return out

    def remaining(self, api: str, limits: tuple[tuple[int, float], ...]) -> int:
        """Freie Aufrufe im längsten (engsten) Fenster."""
        limit, window = max(limits, key=lambda lw: lw[1])
        return next(u["frei"] for u in self.usage(api, ((limit, window),)))


class RateLimiter:
    """Gleitende Fenster über mehrere Zeitskalen, prozessübergreifend über SQLite."""

    def __init__(self, storage: Storage, api: str, limits: tuple[tuple[int, float], ...], max_wait_s: float):
        self.storage = storage
        self.api = api
        self.limits = limits
        self.max_wait_s = max_wait_s
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        from .errors import PvgisRateLimitError

        waited = 0.0
        async with self._lock:
            while True:
                wait = await asyncio.to_thread(self.storage.try_acquire, self.api, self.limits)
                if wait <= 0:
                    return
                if waited + wait > self.max_wait_s:
                    minutes = wait / 60
                    raise PvgisRateLimitError(
                        f"Rate-Limit für {self.api} erschöpft. Nächster Aufruf in ca. "
                        f"{minutes:.1f} min möglich (PVGIS 6 erlaubt 200 Aufrufe pro Stunde). "
                        "Bereits berechnete Ergebnisse kommen weiterhin sofort aus dem Cache.",
                        wait_s=wait,
                        api=self.api,
                    )
                await asyncio.sleep(wait)
                waited += wait

    def usage(self) -> list[dict]:
        return self.storage.usage(self.api, self.limits)

    def remaining(self) -> int:
        return self.storage.remaining(self.api, self.limits)
