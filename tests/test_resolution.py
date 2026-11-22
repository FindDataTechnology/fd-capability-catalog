"""Resolution tests (establish-capability-plane, task 3).

The three-layer intersection, each layer narrowing only; pack grants nothing on
its own; agent never exceeds owner; unregistered agent fails closed; two agents
under one owner differ.
"""

from __future__ import annotations

from fd_capability_catalog.resolution import Scope, resolve


def _r(agent, owner, pack, catalog=None):
    return resolve(
        agent_id="a", agent_scope=Scope.of(agent),
        owner_scope=Scope.of(owner), pack_needs=Scope.of(pack),
        catalog_ids=catalog,
    )


def test_three_layer_strictest():
    # agent A/B/C, owner A/B, pack A/B/C/D  -> A/B
    r = _r(["A", "B", "C"], ["A", "B"], ["A", "B", "C", "D"])
    assert r.effective == frozenset({"A", "B"})


def test_pack_grants_nothing_alone():
    # pack wants X, but neither agent nor owner has it
    r = _r(["A"], ["A"], ["A", "X"])
    assert "X" not in r.effective
    assert r.effective == frozenset({"A"})


def test_agent_not_beyond_owner():
    r = _r(["A", "Y"], ["A"], ["A", "Y"])
    assert "Y" not in r.effective
    assert r.effective == frozenset({"A"})


def test_unregistered_agent_fails_closed():
    r = _r([], ["A", "B"], ["A", "B"])
    assert r.effective == frozenset()
    assert r.fail_closed_reason is not None


def test_two_agents_same_owner_differ():
    r1 = resolve(agent_id="a1", agent_scope=Scope.of(["A"]),
                 owner_scope=Scope.of(["A", "B"]), pack_needs=Scope.of(["A", "B"]))
    r2 = resolve(agent_id="a2", agent_scope=Scope.of(["B"]),
                 owner_scope=Scope.of(["A", "B"]), pack_needs=Scope.of(["A", "B"]))
    assert r1.effective == frozenset({"A"})
    assert r2.effective == frozenset({"B"})


def test_kind_scoped_entries():
    r = _r(["concept:price.close"], ["price.close"], ["price.close"])
    assert r.allows("price.close", kind="concept")
    assert not r.allows("price.close", kind="graph")


def test_wildcard_narrowed_by_other_layers():
    # agent wildcard, owner only A -> A
    r = _r(["*"], ["A"], ["A", "B"])
    assert r.effective == frozenset({"A"})