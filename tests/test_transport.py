"""Ende-zu-Ende: Server als eigener Prozess über stdio, Streamable HTTP (mit Token) und SSE."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _env(tmp_path):
    env = dict(os.environ)
    env.update({"PVGIS_OUTPUT_DIR": str(tmp_path / "out"), "PVGIS_CACHE_PATH": str(tmp_path / "c.sqlite"),
                "PYTHONPATH": str(ROOT)})
    return env


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port: int, timeout: float = 20) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError("Server nicht gestartet")


async def test_stdio_roundtrip(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "pvgis_mcp"], env=_env(tmp_path))
    async with stdio_client(params) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {"pvgis_estimate_yield", "pvgis_tracking_yield", "pvgis_tmy"} <= names
            res = await session.call_tool("pvgis_api_status", {})
            text = res.content[0].text
            assert "kontingent_pvgis6" in text


def _rpc(client, url, headers, method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "method": method}
    if rid is not None:
        body["id"] = rid
    if params is not None:
        body["params"] = params
    return client.post(url, json=body, headers=headers)


@pytest.mark.parametrize("stateless", [False, True])
def test_http_with_token(tmp_path, stateless):
    port = _free_port()
    args = [sys.executable, "-m", "pvgis_mcp", "--transport", "http", "--port", str(port), "--token", "geheim",
            "--json-response"] + (["--stateless"] if stateless else [])
    proc = subprocess.Popen(args, env=_env(tmp_path), stderr=subprocess.PIPE)
    try:
        _wait_health(port)
        url = f"http://127.0.0.1:{port}/mcp"
        base = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        with httpx.Client(timeout=10) as c:
            init = {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"}}
            assert _rpc(c, url, base, "initialize", init).status_code == 401
            assert _rpc(c, url, {**base, "Authorization": "Bearer falsch"}, "initialize", init).status_code == 401
            auth = {**base, "Authorization": "Bearer geheim"}
            r = _rpc(c, url, auth, "initialize", init)
            assert r.status_code == 200, r.text
            assert r.json()["result"]["serverInfo"]["name"] == "pvgis"
            sid = r.headers.get("mcp-session-id")
            hdr = {**auth, "mcp-protocol-version": r.json()["result"]["protocolVersion"]}
            if sid:
                hdr["mcp-session-id"] = sid
            _rpc(c, url, hdr, "notifications/initialized", rid=None)
            r = _rpc(c, url, hdr, "tools/list", {}, rid=2)
            assert r.status_code == 200, r.text
            names = [t["name"] for t in r.json()["result"]["tools"]]
            assert len(names) == 16
            r = _rpc(c, url, hdr, "tools/call", {"name": "pvgis_api_status", "arguments": {}}, rid=3)
            assert r.status_code == 200 and "kontingent_pvgis6" in json.dumps(r.json())
    finally:
        proc.terminate()
        proc.wait(10)


def test_sse(tmp_path):
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "pvgis_mcp", "--transport", "sse", "--port", str(port)],
                            env=_env(tmp_path), stderr=subprocess.PIPE)
    try:
        _wait_health(port)
        with httpx.Client(timeout=5) as c:
            with c.stream("GET", f"http://127.0.0.1:{port}/sse") as r:
                assert r.status_code == 200
                assert r.headers["content-type"].startswith("text/event-stream")
                for line in r.iter_lines():
                    if line.startswith("data:"):
                        assert "/messages/" in line
                        break
    finally:
        proc.terminate()
        proc.wait(10)
