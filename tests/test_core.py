"""Konventionen, Fehlerübersetzung, Cache und Rate-Limit."""

import time

import httpx
import pytest
import respx

from pvgis_mcp.conventions import (
    azimuth_label,
    compass_to_v53_aspect,
    parse_azimuth,
    v6_horizon,
    v6_module_model,
    v53_aspect_to_compass,
    v53_horizon,
)
from pvgis_mcp.errors import PvgisInputError, PvgisRateLimitError, PvgisUnavailableError, describe_error_body
from pvgis_mcp.http import encode_params
from pvgis_mcp.storage import Storage

from conftest import V6, V53


@pytest.mark.parametrize("value,expected", [
    (180, 180), ("S", 180), ("Süd", 180), ("sued", 180), ("SO", 135), ("SE", 135), ("Südost", 135),
    ("Ost", 90), ("E", 90), ("West", 270), ("Südwest", 225), ("Nordwest", 315), ("NNO", 22.5),
    ("Westsüdwest", 247.5), ("Ostsüdost", 112.5), ("southwest", 225), ("-90", 270), ("172,5", 172.5), (None, 180),
])
def test_parse_azimuth(value, expected):
    assert parse_azimuth(value) == pytest.approx(expected)


def test_parse_azimuth_invalid():
    with pytest.raises(PvgisInputError):
        parse_azimuth("Südsüdsüd")


@pytest.mark.parametrize("compass,aspect", [(180, 0), (90, -90), (270, 90), (135, -45), (0, 180)])
def test_azimuth_conversion(compass, aspect):
    assert compass_to_v53_aspect(compass) == aspect
    assert v53_aspect_to_compass(aspect) == compass % 360


def test_labels_and_modules():
    assert azimuth_label(180) == "S" and azimuth_label(268) == "W" and azimuth_label(359) == "N"
    assert v6_module_model("crystSi2025", "free") == "cSi:Free standing 2025"
    assert v6_module_model("CdTe", "building") == "CdTe:Integrated"


def test_horizon():
    assert v6_horizon("auto") == "PVGIS"
    assert v6_horizon("none") == "0,0,0,0"
    assert v6_horizon([5, 10, 15, 20]) == "5,10,15,20"
    assert v53_horizon("none") == {"usehorizon": 0}
    assert v53_horizon("1;2;3;4")["userhorizon"] == "1,2,3,4"
    with pytest.raises(PvgisInputError):
        v6_horizon([1, 2])


def test_encode_params():
    q = encode_params({"a": True, "b": None, "c": [1.5, 2], "d": 0.860000}, "word")
    assert q == [("a", "true"), ("c", "1.5"), ("c", "2"), ("d", "0.86")]
    assert encode_params({"a": True}, "int") == [("a", "1")]


def test_error_bodies():
    msg, _, _ = describe_error_body(b'{"detail":[{"type":"less_than_equal","loc":["query","latitude"],'
                                    b'"msg":"Input should be less than or equal to 90","input":"953"}]}')
    assert "latitude" in msg and "953" in msg
    msg, _, _ = describe_error_body(b'{"message":"lat: Incorrect value.","status":400}')
    assert msg == "lat: Incorrect value."
    _, _, rid = describe_error_body(b'{"detail":"Internal Server Error","request_id":"abc"}')
    assert rid == "abc"


def test_rate_limiter_windows(tmp_path):
    st = Storage(tmp_path / "s.sqlite")
    limits = ((3, 1.0), (5, 60.0))
    now = time.time()
    assert [st.try_acquire("x", limits, now + i * 0.01) for i in range(3)] == [0, 0, 0]
    wait = st.try_acquire("x", limits, now + 0.05)
    assert 0.9 < wait <= 1.01
    assert st.try_acquire("x", limits, now + 1.2) == 0
    assert st.try_acquire("x", limits, now + 1.3) == 0
    wait = st.try_acquire("x", limits, now + 2.5)  # Minutenfenster voll (5)
    assert wait > 50
    assert st.remaining("x", limits) == 0


async def test_cache_and_retry(services):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{V6}/topography/elevation").mock(side_effect=[
            httpx.Response(503, json={"detail": "busy"}),
            httpx.Response(200, json={"elevation": 34}),
        ])
        assert (await services.v6.elevation(53.25, 9.5))["elevation"] == 34
        assert (await services.v6.elevation(53.25, 9.5))["elevation"] == 34  # Cache
        assert route.call_count == 2
    assert services.v6.http.stats["cache_treffer"] == 1
    assert services.v6.http.stats["wiederholungen"] == 1


async def test_input_error_not_retried(services):
    with respx.mock() as mock:
        route = mock.get(f"{V53}/PVcalc").mock(return_value=httpx.Response(
            400, json={"message": "lat: Incorrect value. Please, enter a float between -90 and 90.", "status": 400}))
        with pytest.raises(PvgisInputError, match="Incorrect value"):
            await services.v53.call("PVcalc", {"lat": 93})
        assert route.call_count == 1


async def test_server_error_with_request_id(services):
    with respx.mock() as mock:
        mock.get(f"{V6}/topography/elevation").mock(return_value=httpx.Response(
            500, json={"detail": "Internal Server Error", "request_id": "43b4"}))
        with pytest.raises(PvgisUnavailableError, match="43b4"):
            await services.v6.elevation(1, 1)


async def test_rate_limit_error(services, monkeypatch):
    lim = services.v6.http.limiter
    lim.limits = ((1, 3600.0),)
    lim.max_wait_s = 0.5
    with respx.mock() as mock:
        mock.get(f"{V6}/topography/elevation").mock(return_value=httpx.Response(200, json={"elevation": 1}))
        await services.v6.elevation(1, 1)
        with pytest.raises(PvgisRateLimitError, match="200 Aufrufe pro Stunde"):
            await services.v6.elevation(2, 2)
        # Cache-Treffer brauchen kein Kontingent
        assert (await services.v6.elevation(1, 1))["elevation"] == 1
