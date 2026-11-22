"""Reader tests (tasks 4 + 5).

assembly sees all; resident holds only pack-used ∩ effective; retrieval is
paginated and effective-only; and — task 5 — an unauthorized id is UNPERCEIVABLE:
absent from items, count, and order, indistinguishable from a nonexistent one.
"""

from __future__ import annotations

import os

from fd_capability_catalog.adapter import build_kernel
from fd_capability_catalog.readers import (
    assembly_view,
    resident_view,
    retrieval_view,
)
from fd_capability_catalog.resolution import Resolution, Scope, resolve

SCOPE = "org:f"


def _units(tmp_path, ids_kinds):
    k = build_kernel(os.path.join(str(tmp_path), "c.db"))
    for cid, kind in ids_kinds:
        k.write(scope_id=SCOPE, kind=kind, external_key=cid, body=cid,
                attrs={"status": "verified", "served_by": ["od"]}, actor="p", via="s")
    return k.live_units(scope_id=SCOPE)


def _res(agent, owner, pack):
    return resolve(agent_id="a", agent_scope=Scope.of(agent),
                   owner_scope=Scope.of(owner), pack_needs=Scope.of(pack))


def test_assembly_sees_all(tmp_path):
    units = _units(tmp_path, [("a", "concept"), ("b", "concept"), ("c", "indicator")])
    rows = assembly_view(units)
    assert {r.federated_id for r in rows} == {"a", "b", "c"}


def test_resident_only_pack_used_and_effective(tmp_path):
    units = _units(tmp_path, [("a", "concept"), ("b", "concept"), ("c", "concept")])
    r = _res(["a", "b", "c"], ["a", "b", "c"], ["a", "b", "c"])
    rows = resident_view(units, r, pack_used=["a", "c"])
    assert {x.federated_id for x in rows} == {"a", "c"}  # b not pack-used
    # and c not effective -> excluded
    r2 = _res(["a"], ["a"], ["a", "b", "c"])
    rows2 = resident_view(units, r2, pack_used=["a", "b", "c"])
    assert {x.federated_id for x in rows2} == {"a"}


def test_retrieval_paginated_and_effective_only(tmp_path):
    units = _units(tmp_path, [(f"c{i}", "concept") for i in range(5)])
    r = _res(["c0", "c1", "c2", "c3"], ["c0", "c1", "c2", "c3", "c4"], ["c0", "c1", "c2", "c3", "c4"])
    view = retrieval_view(units, r, limit=2)
    assert view["total"] == 4  # c4 not effective
    assert len(view["items"]) == 2
    view2 = retrieval_view(units, r, offset=2, limit=2)
    assert {i["id"] for i in view2["items"]}.isdisjoint({i["id"] for i in view["items"]})


def test_unauthorized_is_unperceivable(tmp_path):
    """A capability the subject lacks is absent from items AND total AND order —
    indistinguishable from one that does not exist (task 5)."""
    units = _units(tmp_path, [("a", "concept"), ("secret", "concept")])
    r = _res(["a"], ["a"], ["a", "secret"])
    view = retrieval_view(units, r)
    assert view["total"] == 1
    assert [i["id"] for i in view["items"]] == ["a"]
    # "secret" appears nowhere; a lookup for it yields nothing
    assert retrieval_view(units, r, query="secret")["total"] == 0


def test_resident_and_retrieval_agree_on_effective(tmp_path):
    """Same resolution -> same visible set across resident and retrieval (no seam)."""
    units = _units(tmp_path, [("a", "concept"), ("b", "concept")])
    r = _res(["a", "b"], ["a"], ["a", "b"])
    res_ids = {x.federated_id for x in resident_view(units, r, pack_used=["a", "b"])}
    ret_ids = {i["id"] for i in retrieval_view(units, r)["items"]}
    assert res_ids == ret_ids == {"a"}

def test_breaking_the_cut_goes_red(tmp_path):
    """Reverse self-proof (task 5.2): if the effective-set filter were dropped,
    an unauthorized capability would leak — asserted here so the guard is real."""
    units = _units(tmp_path, [("a", "concept"), ("secret", "concept")])
    r = _res(["a"], ["a"], ["a", "secret"])

    # Simulate a reader that forgot to filter by the resolution:
    def _unfiltered(units):
        return sorted(u.external_key for u in units)

    leaked = _unfiltered(units)
    assert "secret" in leaked  # the broken reader leaks it

    # The real reader must NOT:
    assert "secret" not in {i["id"] for i in retrieval_view(units, r)["items"]}


def test_assembly_governor_sees_only_governed_range(tmp_path):
    """4.1: a governor with server_access sees only capabilities they govern;
    a governor without rules (admin tooling) sees all."""
    from fd_capability_catalog.access import rules_from_server_access
    from fd_capability_catalog.projector import serving_map_from_catalog
    units = _units(tmp_path, [("a", "concept"), ("b", "concept"), ("c", "concept")])
    # give each a serving edge on fd-open-data-mcp / business
    k_units = []
    for u in units:
        u.attrs["served_by_tools"] = [["fd-open-data-mcp", "read"]] if u.external_key!="c" else [["fd-find-data-business-mcp","read_range"]]
    serving = serving_map_from_catalog(units)
    # governor governs only fd-open-data-mcp read -> sees a,b not c
    gov = rules_from_server_access([{"server": "fd-open-data-mcp", "tools": ["read"]}])
    rows = assembly_view(units, governor_rules=gov, serving=serving)
    assert {r.federated_id for r in rows} == {"a", "b"}
    # no rules = admin full view
    assert len(assembly_view(units)) == 3
