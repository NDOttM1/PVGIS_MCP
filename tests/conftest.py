import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pvgis_mcp.config import Settings  # noqa: E402
from pvgis_mcp.services import Services, set_services  # noqa: E402

V6 = "https://pvgis6.test/api/v6"
V53 = "https://pvgis53.test/api/v5_3"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(v6_base_url=V6, v53_base_url=V53, output_dir=tmp_path / "output",
                    cache_path=tmp_path / "cache.sqlite", max_wait_s=2.0, max_retries=2, csv_style="intl")


@pytest.fixture
async def services(settings):
    svc = Services.create(settings)
    set_services(svc)
    yield svc
    await svc.aclose()
    set_services(None)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("PVGIS_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="Live-Tests nur mit PVGIS_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
