"""Gemeinsame Laufzeitobjekte (Einstellungen, Speicher, API-Clients)."""

from __future__ import annotations

from dataclasses import dataclass

from .clients import PvgisV6, PvgisV53
from .config import Settings
from .storage import Storage


@dataclass
class Services:
    settings: Settings
    storage: Storage
    v6: PvgisV6
    v53: PvgisV53

    @classmethod
    def create(cls, settings: Settings | None = None) -> "Services":
        settings = settings or Settings.from_env()
        storage = Storage(settings.cache_path)
        return cls(settings=settings, storage=storage,
                   v6=PvgisV6(settings, storage), v53=PvgisV53(settings, storage))

    async def aclose(self) -> None:
        await self.v6.http.aclose()
        await self.v53.http.aclose()


_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = Services.create()
    return _services


def set_services(services: Services | None) -> None:
    """Für Tests: eigene Services injizieren."""
    global _services
    _services = services
