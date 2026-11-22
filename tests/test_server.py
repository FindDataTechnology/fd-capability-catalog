"""MCP tool-surface tests (change: wire-capability-catalog-into-pack-assembly, D3).

The catalog is a service consumed over MCP. These tests exercise the tool
FUNCTIONS directly (FastMCP just wraps them) against a real SQLite catalog — the
same path the FastMCP server exposes.
"""

from __future__ import annotations

import os

import pytest

from fd_capability_catalog import server
from fd_capability_catalog.adapter import build_kernel
from fd_capability_catalog.projector import CatalogEntry, StaticSource, project

SCOPE = "org:finddata"


@pytest.fixture
def catalog_db(tmp_path, monkeypatch):
    db = str(tmp_path / "mcp.db")
    k = build_kernel(db)
    project(k, [StaticSource("fd-open-data-mcp", [
        CatalogEntry("price.close", "concept", "收盘价", "fd-open-data-mcp", "verified",
                     served_by_tools=(("fd-open-data-mcp", "read"),)),
        CatalogEntry("gdp.cn", "indicator", "GDP", "fd-open-data-mcp", "verified",
                     served_by_tools=(("fd-open-data-mcp", "read"),)),
        CatalogEntry("graph:entity-graph", "graph", "entity graph", "fd-open-data-mcp", "verified",
                     served_by_tools=(("fd-open-data-mcp", "graph_search"),)),
    ])], scope_id=SCOPE)
    monkeypatch.setenv("CAPABILITY_CATALOG_DB", db)
    return db


def test_serving_edges_tool(catalog_db):
    e = server.serving_edges()
    assert e["graph:entity-graph"] == [["fd-open-data-mcp", "graph_search"]]
    assert e["price.close"] == [["fd-open-data-mcp", "read"]]


def test_resolve_for_tool_effective(catalog_db):
    r = server.resolve_for('[{"server":"fd-open-data-mcp","tools":["read"]}]')
    assert set(r["effective"]) == {"price.close", "gdp.cn"}
    assert "graph:entity-graph" not in r["effective"]  # needs graph_search, not read


def test_resolve_for_tool_governor_degree(catalog_db):
    r = server.resolve_for('[{"server":"fd-open-data-mcp","tools":["graph_search"]}]')
    assert r["effective"] == ["graph:entity-graph"]


def test_list_capabilities_tool_kind(catalog_db):
    kinds = {r["kind"] for r in server.list_capabilities(kind="graph")}
    assert kinds == {"graph"}
    assert {r["id"] for r in server.list_capabilities(kind="graph")} == {"graph:entity-graph"}


def test_list_capabilities_governed(catalog_db):
    # governor without graph_search can't see the graph capability
    rows = server.list_capabilities(server_access='[{"server":"fd-open-data-mcp","tools":["read"]}]')
    ids = {r["id"] for r in rows}
    assert "graph:entity-graph" not in ids
    assert {"price.close", "gdp.cn"} <= ids


def test_missing_db_env_raises(monkeypatch):
    monkeypatch.delenv("CAPABILITY_CATALOG_DB", raising=False)
    with pytest.raises(RuntimeError):
        server.list_capabilities()


def test_bad_server_access_json_raises(catalog_db):
    with pytest.raises(ValueError):
        server.list_capabilities(server_access="not json")

def test_console_entry_parses_transport(monkeypatch):
    """The console script must honour --transport; it used to fall through to
    stdio because argparse lived under __main__ (measured 2026-11-23)."""
    import sys
    import types

    calls = []
    fake_uv = types.ModuleType("uvicorn")
    fake_uv.run = lambda app, host, port: calls.append((host, port))
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uv)
    monkeypatch.setattr(server, "build_mcp", lambda: types.SimpleNamespace(http_app=lambda: object()))
    monkeypatch.setenv("CAPABILITY_CATALOG_DB", "/tmp/unused.db")

    server.serve_cli(["--transport", "http", "--host", "0.0.0.0", "--port", "8321"])
    assert calls == [("0.0.0.0", 8321)]
