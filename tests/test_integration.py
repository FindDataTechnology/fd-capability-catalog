"""Integration test: the whole chain, real modules, model C end to end.

Units prove each piece; this proves they COMPOSE. It runs:

    sources ──project()──► catalog(SQLite kernel) ──live_units──►
    registry server_access ──rules_from_server_access──► AllowRule
    serving map (capability -> (server,tool)) ──resolution_from_tool_grants──►
    Resolution ──► readers (assembly / resident / retrieval)

Model C: assembly sees semantic capabilities; authorization stays on the tools
registry already governs, bridged by the serving map. This is the test that
catches "single piece green, chain broken" — and it already did (see
test_scope_names_vs_capability_ids_documented historical note removed).
"""

from __future__ import annotations

import os

from fd_capability_catalog.access import (
    AllowRule,
    resolution_from_tool_grants,
    rules_from_server_access,
)
from fd_capability_catalog.adapter import build_kernel
from fd_capability_catalog.projector import CatalogEntry, StaticSource, project
from fd_capability_catalog.readers import assembly_view, resident_view, retrieval_view

SCOPE = "org:finddata"

# A serving map: which (server, tool) exposes each capability. In production this
# comes from each source (e.g. fd-open-data-mcp read(concept=...) serves the
# concept; business-mcp registry tools serve the indicator). Here it is explicit.
SERVING = {
    "price.close": [("fd-open-data-mcp", "read")],
    "price.open": [("fd-open-data-mcp", "read")],
    "gdp.cn": [("fd-open-data-mcp", "read"), ("fd-find-data-business-mcp", "read_range")],
    "rev.bank": [("fd-find-data-business-mcp", "read_range")],
}


def _catalog(tmp_path):
    k = build_kernel(os.path.join(str(tmp_path), "cap.db"))
    od = StaticSource("fd-open-data-mcp", [
        CatalogEntry("price.close", "concept", "收盘价", "fd-open-data-mcp", "verified"),
        CatalogEntry("price.open", "concept", "开盘价", "fd-open-data-mcp", "verified"),
        CatalogEntry("gdp.cn", "indicator", "中国GDP", "fd-open-data-mcp", "registered"),
    ])
    biz = StaticSource("fd-find-data-business-mcp", [
        CatalogEntry("rev.bank", "indicator", "银行营收", "fd-find-data-business-mcp", "verified"),
    ])
    project(k, [od, biz], scope_id=SCOPE)
    return k


def test_model_c_full_chain(tmp_path):
    """Project → tool-grant resolution (model C) → three readers consistent."""
    k = _catalog(tmp_path)
    units = k.live_units(scope_id=SCOPE)
    catalog_ids = {u.external_key for u in units}
    assert catalog_ids == {"price.close", "price.open", "gdp.cn", "rev.bank"}

    # owner's registry scope: full access to both data servers
    owner_access = [
        {"server": "fd-open-data-mcp", "tools": ["all"]},
        {"server": "fd-find-data-business-mcp", "tools": ["all"]},
    ]
    owner_rules = rules_from_server_access(owner_access)

    # agent granted only fd-open-data-mcp read (NOT business-mcp) -> rev.bank excluded
    agent_access = [{"server": "fd-open-data-mcp", "tools": ["read"]}]
    agent_rules = rules_from_server_access(agent_access)

    res = resolution_from_tool_grants(
        agent_id="agent:1", agent_rules=agent_rules, owner_rules=owner_rules,
        pack_needs=catalog_ids, serving=SERVING, catalog_ids=catalog_ids,
    )
    # effective: grep the data-mcp read serves + owner allows -> price.close/open, gdp.cn
    assert res.effective == frozenset({"price.close", "price.open", "gdp.cn"})
    assert "rev.bank" not in res.effective  # agent not granted business-mcp

    # assembly sees all four (semantic view)
    assert len(assembly_view(units)) == 4
    # resident + retrieval agree on the effective subset
    res_ids = {r.federated_id for r in resident_view(units, res, pack_used=list(catalog_ids))}
    ret_ids = {i["id"] for i in retrieval_view(units, res, limit=100)["items"]}
    assert res_ids == ret_ids == set(res.effective)


def test_owner_narrows_agent(tmp_path):
    """Agent granted a server the owner lacks -> that capability excluded."""
    k = _catalog(tmp_path)
    units = k.live_units(scope_id=SCOPE)
    cids = {u.external_key for u in units}
    agent_rules = rules_from_server_access([{"server": "fd-find-data-business-mcp", "tools": ["read_range"]}])
    owner_rules = rules_from_server_access([{"server": "fd-open-data-mcp", "tools": ["read"]}])
    res = resolution_from_tool_grants(
        agent_id="a", agent_rules=agent_rules, owner_rules=owner_rules,
        pack_needs=cids, serving=SERVING, catalog_ids=cids,
    )
    assert "rev.bank" not in res.effective  # owner lacks business-mcp


def test_pack_needs_only_narrows(tmp_path):
    k = _catalog(tmp_path)
    units = k.live_units(scope_id=SCOPE)
    cids = {u.external_key for u in units}
    full = [{"server": "*", "tools": ["all"]}]
    rules = rules_from_server_access(full)
    # pack asks for only price.close
    res = resolution_from_tool_grants(
        agent_id="a", agent_rules=rules, owner_rules=rules,
        pack_needs=["price.close"], serving=SERVING, catalog_ids=cids,
    )
    assert res.effective == frozenset({"price.close"})


def test_no_grant_fails_closed(tmp_path):
    k = _catalog(tmp_path)
    cids = {u.external_key for u in k.live_units(scope_id=SCOPE)}
    res = resolution_from_tool_grants(
        agent_id="a", agent_rules=[], owner_rules=rules_from_server_access([{"server": "*", "tools": ["all"]}]),
        pack_needs=cids, serving=SERVING, catalog_ids=cids,
    )
    assert res.effective == frozenset()
    assert res.fail_closed_reason


def test_unauthorized_capability_unperceivable_through_chain(tmp_path):
    k = _catalog(tmp_path)
    units = k.live_units(scope_id=SCOPE)
    cids = {u.external_key for u in units}
    only_od = rules_from_server_access([{"server": "fd-open-data-mcp", "tools": ["read"]}])
    res = resolution_from_tool_grants(
        agent_id="a", agent_rules=only_od, owner_rules=only_od,
        pack_needs=cids, serving=SERVING, catalog_ids=cids,
    )
    view = retrieval_view(units, res, query="rev")  # rev.bank not granted
    assert view["total"] == 0
    assert all("rev.bank" != i["id"] for i in retrieval_view(units, res, limit=100)["items"])


def test_wildcard_server_and_all_tools(tmp_path):
    k = _catalog(tmp_path)
    cids = {u.external_key for u in k.live_units(scope_id=SCOPE)}
    r = rules_from_server_access([{"server": "*", "tools": ["all"]}])
    res = resolution_from_tool_grants(
        agent_id="a", agent_rules=r, owner_rules=r,
        pack_needs=cids, serving=SERVING, catalog_ids=cids,
    )
    assert res.effective == cids

def test_serving_map_roundtrips_through_catalog(tmp_path):
    """served_by_tools survives project()→units→serving_map_from_catalog, and
    model-C resolution works off the CATALOG-built serving map (not a hand map).
    This is the real integration: the serving edges come from the source, land in
    the catalog, and drive resolution."""
    from fd_capability_catalog.projector import serving_map_from_catalog

    k = build_kernel(os.path.join(str(tmp_path), "c.db"))
    od = StaticSource("fd-open-data-mcp", [
        CatalogEntry("price.close", "concept", "收盘价", "fd-open-data-mcp", "verified",
                     served_by_tools=(("fd-open-data-mcp", "read"), ("fd-open-data-mcp", "read_series"))),
        CatalogEntry("gdp.cn", "indicator", "中国GDP", "fd-open-data-mcp", "verified",
                     served_by_tools=(("fd-open-data-mcp", "read"),)),
    ])
    project(k, [od], scope_id=SCOPE)
    units = k.live_units(scope_id=SCOPE)
    serving = serving_map_from_catalog(units)
    assert serving["price.close"] == [("fd-open-data-mcp", "read"), ("fd-open-data-mcp", "read_series")]

    rules = rules_from_server_access([{"server": "fd-open-data-mcp", "tools": ["read"]}])
    res = resolution_from_tool_grants(
        agent_id="a", agent_rules=rules, owner_rules=rules,
        pack_needs={"price.close", "gdp.cn"}, serving=serving,
        catalog_ids={u.external_key for u in units},
    )
    # both served by (fd-open-data-mcp, read) which is granted
    assert res.effective == frozenset({"price.close", "gdp.cn"})

    # now deny read_series only: price.close still allowed via `read`
    rules2 = rules_from_server_access([{"server": "fd-open-data-mcp", "tools": ["read_series"]}])
    res2 = resolution_from_tool_grants(
        agent_id="a", agent_rules=rules2, owner_rules=rules2,
        pack_needs={"price.close", "gdp.cn"}, serving=serving,
        catalog_ids={u.external_key for u in units},
    )
    assert res2.effective == frozenset({"price.close"})  # gdp.cn only had `read` -> excluded


def test_capability_without_serving_edge_is_unresolvable(tmp_path):
    """A catalog row with no served_by_tools is never resolvable (fail-closed)."""
    from fd_capability_catalog.projector import serving_map_from_catalog
    k = build_kernel(os.path.join(str(tmp_path), "c.db"))
    od = StaticSource("od", [CatalogEntry("orphan", "concept", "无服务", "od", "verified")])
    project(k, [od], scope_id=SCOPE)
    units = k.live_units(scope_id=SCOPE)
    serving = serving_map_from_catalog(units)
    assert "orphan" not in serving
    rules = rules_from_server_access([{"server": "*", "tools": ["all"]}])
    res = resolution_from_tool_grants(
        agent_id="a", agent_rules=rules, owner_rules=rules,
        pack_needs={"orphan"}, serving=serving, catalog_ids={"orphan"},
    )
    assert res.effective == frozenset()


def test_cross_system_same_identity_merges_served_by(tmp_path):
    """2.3: the same capability reported by two systems is ONE row with union
    served_by + union serving tools; distinct identities stay separate."""
    from fd_capability_catalog.projector import serving_map_from_catalog
    k = build_kernel(os.path.join(str(tmp_path), "c.db"))
    ident = "price.close|stock||CNY|daily"  # the shared full-identity tuple
    od = StaticSource("fd-open-data-mcp", [
        CatalogEntry(ident, "concept", "收盘价", "fd-open-data-mcp", "verified",
                     served_by_tools=(("fd-open-data-mcp", "read"),)),
        CatalogEntry("only.od|stock||CNY|daily", "concept", "独占", "fd-open-data-mcp", "verified",
                     served_by_tools=(("fd-open-data-mcp", "read"),)),
    ])
    biz = StaticSource("fd-find-data-business-mcp", [
        CatalogEntry(ident, "concept", "收盘价", "fd-find-data-business-mcp", "verified",
                     served_by_tools=(("fd-find-data-business-mcp", "read_range"),)),
        CatalogEntry("only.biz|stock||CNY|daily", "concept", "另一个", "fd-find-data-business-mcp", "verified",
                     served_by_tools=(("fd-find-data-business-mcp", "read_range"),)),
    ])
    project(k, [od, biz], scope_id=SCOPE)
    units = {u.external_key: u for u in k.live_units(scope_id=SCOPE)}
    assert set(units) == {ident, "only.od|stock||CNY|daily", "only.biz|stock||CNY|daily"}
    shared = units[ident].attrs
    assert sorted(shared["served_by"]) == ["fd-find-data-business-mcp", "fd-open-data-mcp"]
    edges = {tuple(e) for e in shared["served_by_tools"]}
    assert ("fd-open-data-mcp", "read") in edges
    assert ("fd-find-data-business-mcp", "read_range") in edges
    # serving map has both edges for the shared capability
    serving = serving_map_from_catalog(units.values())
    assert set(serving[ident]) == {("fd-open-data-mcp", "read"), ("fd-find-data-business-mcp", "read_range")}


def test_granting_either_system_serves_the_shared_capability(tmp_path):
    """A shared capability is usable if EITHER serving system is granted."""
    from fd_capability_catalog.projector import serving_map_from_catalog
    k = build_kernel(os.path.join(str(tmp_path), "c.db"))
    ident = "gdp.cn|country||USD|annual"
    od = StaticSource("od", [CatalogEntry(ident, "indicator", "GDP", "od", "verified",
                                          served_by_tools=(("fd-open-data-mcp", "read"),))])
    biz = StaticSource("biz", [CatalogEntry(ident, "indicator", "GDP", "biz", "verified",
                                            served_by_tools=(("fd-find-data-business-mcp", "read_range"),))])
    project(k, [od, biz], scope_id=SCOPE)
    units = k.live_units(scope_id=SCOPE)
    serving = serving_map_from_catalog(units)
    cids = {u.external_key for u in units}
    # grant ONLY business-mcp read_range
    rules = rules_from_server_access([{"server": "fd-find-data-business-mcp", "tools": ["read_range"]}])
    res = resolution_from_tool_grants(agent_id="a", agent_rules=rules, owner_rules=rules,
                                      pack_needs=cids, serving=serving, catalog_ids=cids)
    assert res.effective == frozenset({ident})


def test_graph_kind_from_real_textcore_verticals(tmp_path):
    """6.1: graph kind sourced from text-core's REAL verticals, full chain.
    Skips if the sibling repo is not checked out here."""
    import pathlib
    from fd_capability_catalog.projector import serving_map_from_catalog
    from fd_capability_catalog.source_graph import TextCoreGraphSource

    vdir = pathlib.Path(__file__).resolve().parents[2] / "fd-text-core" / "fd_text_core" / "kg_lane" / "verticals"
    if not vdir.is_dir():
        import pytest
        pytest.skip("fd-text-core verticals not present")
    k = build_kernel(os.path.join(str(tmp_path), "c.db"))
    project(k, [TextCoreGraphSource(vdir)], scope_id=SCOPE)
    units = k.live_units(scope_id=SCOPE)
    graphs = [u for u in units if u.kind == "graph"]
    assert len(graphs) >= 2  # legal + gov
    assert {"graph:legal_v2", "graph:gov_v3"} <= {u.external_key for u in units}
    serving = serving_map_from_catalog(units)
    # a grant on text-core kg tools serves the graph capabilities
    rules = rules_from_server_access([{"server": "fd-text-core", "tools": ["kg_search_facts"]}])
    res = resolution_from_tool_grants(
        agent_id="a", agent_rules=rules, owner_rules=rules,
        pack_needs={u.external_key for u in units}, serving=serving,
        catalog_ids={u.external_key for u in units},
    )
    assert {"graph:legal_v2", "graph:gov_v3"} <= res.effective
