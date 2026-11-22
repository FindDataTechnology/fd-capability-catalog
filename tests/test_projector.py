"""Projector tests (establish-capability-plane, tasks 1.2 / 2.x).

StaticSource stands in for a live semantic system: the projector maps entries to
catalog rows, an unchanged re-run is idempotent, and one failing source does not
block the others.
"""

from __future__ import annotations

import os

from fd_capability_catalog.adapter import build_kernel
from fd_capability_catalog.projector import CatalogEntry, StaticSource, project

SCOPE = "org:finddata"


def _kernel(tmp_path):
    return build_kernel(os.path.join(str(tmp_path), "cap.db"))


def _entries(system, n, kind="concept"):
    return [
        CatalogEntry(federated_id=f"{kind}.{system}.{i}", kind=kind, name=f"{system} {i}",
                     served_by=system, status="verified")
        for i in range(n)
    ]


def test_projection_writes_all_entries(tmp_path):
    k = _kernel(tmp_path)
    src = StaticSource("fd-open-data-mcp", _entries("od", 5))
    rep = project(k, [src], scope_id=SCOPE)
    assert rep.written == 5
    assert rep.by_kind == {"concept": 5}
    assert len(k.live_units(scope_id=SCOPE)) == 5


def test_projection_idempotent(tmp_path):
    k = _kernel(tmp_path)
    src = StaticSource("od", _entries("od", 3))
    project(k, [src], scope_id=SCOPE)
    n_events_1 = len(k.events.fold(scope_id=SCOPE))
    project(k, [src], scope_id=SCOPE)
    # rows unchanged; a second pass adds update events, not new rows
    assert len(k.live_units(scope_id=SCOPE)) == 3
    assert len(k.events.fold(scope_id=SCOPE)) >= n_events_1


def test_one_failing_source_does_not_block_others(tmp_path):
    k = _kernel(tmp_path)

    class Boom:
        system = "broken"

        def list_capabilities(self, *, scope_id):
            raise RuntimeError("unreachable")

    good = StaticSource("od", _entries("od", 2))
    rep = project(k, [Boom(), good], scope_id=SCOPE)
    assert rep.written == 2
    assert rep.failures and rep.failures[0][0] == "broken"
    assert len(k.live_units(scope_id=SCOPE)) == 2


def test_two_kinds_from_two_sources(tmp_path):
    k = _kernel(tmp_path)
    a = StaticSource("od", _entries("od", 2, "concept"))
    b = StaticSource("biz", _entries("biz", 3, "indicator"))
    rep = project(k, [a, b], scope_id=SCOPE)
    assert rep.by_kind == {"concept": 2, "indicator": 3}

def test_failed_source_marks_rows_stale_not_deleted(tmp_path):
    """System down → its rows flagged "暂不可核", visible, not removed; others untouched."""
    k = _kernel(tmp_path)
    od = StaticSource("od", _entries("od", 3))
    project(k, [od], scope_id=SCOPE)  # first pass: 3 rows from od

    class Boom:
        system = "od"

        def list_capabilities(self, *, scope_id):
            raise RuntimeError("down")

    rep = project(k, [Boom()], scope_id=SCOPE)
    assert rep.written == 0
    assert rep.marked_stale == 3
    units = k.live_units(scope_id=SCOPE)
    assert len(units) == 3  # not deleted
    assert all(u.attrs.get("reachable") == "暂不可核" for u in units)
