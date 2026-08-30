"""Shared fixtures. Offline tests replay recorded bahn.de responses; no network."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """Route `_request` to fixtures by path; record every call for assertions."""
    import db_fahrplan_mcp.server as s

    calls: dict[str, list[dict[str, Any]]] = {}
    s._locations.cache_clear()

    def fake(method: str, path: str, **kw: Any) -> Any:
        calls.setdefault(path, []).append(kw)
        name = path.replace("/", "_")
        if not (FIXTURES / f"{name}.json").exists():
            raise AssertionError(f"no fixture for {path}")
        return load(name)

    monkeypatch.setattr(s, "_request", fake)
    monkeypatch.setattr(s._bucket, "take", lambda: None)
    return calls


@pytest.fixture
def mcp_session():
    """In-memory client session against our server; works on mcp 1.x and 2.x."""
    from contextlib import asynccontextmanager

    import anyio
    from mcp.client.session import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams

    @asynccontextmanager
    async def _connect(server: Any):
        low = getattr(server, "_mcp_server", None) or server._lowlevel_server
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            cr, cw = client_streams
            sr, sw = server_streams
            async with anyio.create_task_group() as tg:
                tg.start_soon(low.run, sr, sw, low.create_initialization_options())
                async with ClientSession(cr, cw) as session:
                    await session.initialize()
                    yield session
                tg.cancel_scope.cancel()

    return _connect
