"""Capability-catalog adapter tests (establish-capability-plane, task 1).

Covers the kernel-domain contract the adapter must honour: write → append-only
event, idempotent rewrite = update (not a second row), and the vocabulary gate
refusing an out-of-set kind. Runs against an in-memory SQLite catalog.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from fd_capability_catalog.adapter import CAPABILITY_KINDS, build_kernel
from fd_ontology_kernel.errors import VocabOutOfSet

SCOPE = "org:finddata"


def _kernel(tmp_path):
    return build_kernel(os.path.join(str(tmp_path), "cap.db"))


def test_write_creates_unit_and_event(tmp_path):
    k = _kernel(tmp_path)
    k.write(
        scope_id=SCOPE, kind="concept", external_key="price.close", body="收盘价",
        attrs={"served_by": ["fd-open-data-mcp"], "status": "verified"},
        actor="projector", via="federation-sync",
    )
    units = k.live_units(scope_id=SCOPE)
    assert [(u.kind, u.external_key) for u in units] == [("concept", "price.close")]
    events = k.events.fold(scope_id=SCOPE)
    assert [(e.op, e.target_key) for e in events] == [("create", "price.close")]


def test_rewrite_is_update_not_duplicate(tmp_path):
    k = _kernel(tmp_path)
    for body in ("v1", "v2"):
        k.write(scope_id=SCOPE, kind="concept", external_key="price.close",
                body=body, actor="p", via="s")
    assert len(k.live_units(scope_id=SCOPE)) == 1
    # two events: create then update — append-only trail preserved
    assert [e.op for e in k.events.fold(scope_id=SCOPE)] == ["create", "update"]


def test_vocab_gate_refuses_out_of_set_kind(tmp_path):
    k = _kernel(tmp_path)
    with pytest.raises(VocabOutOfSet):
        k.write(scope_id=SCOPE, kind="weapon", external_key="x.y", actor="p", via="s")
    assert k.live_units(scope_id=SCOPE) == []


def test_all_four_kinds_accepted(tmp_path):
    k = _kernel(tmp_path)
    for kind in CAPABILITY_KINDS:
        k.write(scope_id=SCOPE, kind=kind, external_key=f"{kind}.demo",
                body="", actor="p", via="s")
    assert {u.kind for u in k.live_units(scope_id=SCOPE)} == set(CAPABILITY_KINDS)
