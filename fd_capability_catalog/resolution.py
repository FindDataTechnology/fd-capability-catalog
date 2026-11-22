"""Capability resolution: the three-layer intersection (agent ∧ owner ∧ pack).

A subject's *effective* capability set is the intersection of three layers, each
of which may only narrow:

    effective = agent.scope  ∩  owner.scope  ∩  pack.needs

- ``agent.scope`` — what this agent, as an independent principal, is granted.
  Comes from principal registration; never from the request. If an agent has no
  registered scope, the resolution is FAIL-CLOSED (empty), not owner-only.
- ``owner.scope`` — what the human the agent acts for is allowed; the agent can
  never exceed it.
- ``pack.needs`` — what the agent's pack declares it needs. This is a *claim*,
  not a grant: it only narrows. Installing a pack grants nothing on its own.

The same resolved set drives all three readers (assembly / resident / retrieval);
whoever reads must consume THIS function, never the raw layers, or the two faces
drift and a "visible in catalog, denied by resolver" seam appears.

Change: establish-capability-plane (task 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

CAPABILITY_KIND_ANY = "*"


@dataclass(frozen=True)
class Scope:
    """A granted capability set.

    Entries are ``"<kind>:<id>"`` strings, or a bare ``"<id>"`` (kind-agnostic),
    or the wildcard ``"*"``. A wildcard matches everything on its side; it is
    still narrowed by the other layers.
    """

    entries: frozenset[str]

    @classmethod
    def of(cls, items: Optional[Iterable[str]]) -> "Scope":
        return cls(frozenset(x for x in (items or ()) if x))

    def grants(self, capability_id: str, kind: Optional[str] = None) -> bool:
        if "*" in self.entries or capability_id in self.entries:
            return True
        if kind is not None and f"{kind}:{capability_id}" in self.entries:
            return True
        return False


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving one agent's effective capability set."""

    agent_id: str
    effective: frozenset[str]
    fail_closed_reason: Optional[str] = None  # set when a layer was unusable

    def allows(self, capability_id: str, kind: Optional[str] = None) -> bool:
        if kind is not None and f"{kind}:{capability_id}" in self.effective:
            return True
        return capability_id in self.effective


def resolve(
    *,
    agent_id: str,
    agent_scope: Scope,
    owner_scope: Scope,
    pack_needs: Scope,
    catalog_ids: Iterable[str] | None = None,
) -> Resolution:
    """Intersect the three layers, taking the strictest.

    The result is expressed over *concrete capability ids*. Because a layer may
    use wildcards, the intersection is computed against a concrete universe: the
    union of all non-wildcard entries in the three scopes, optionally narrowed
    to ``catalog_ids`` (the live catalog) when provided.

    Fail-closed: an agent with no registered scope (``agent_scope`` has no
    entries) resolves to EMPTY — never owner-only — so an unregistered agent
    cannot silently borrow its owner's full权限.
    """
    if not agent_scope.entries:
        return Resolution(
            agent_id=agent_id,
            effective=frozenset(),
            fail_closed_reason="agent has no registered scope (fail-closed)",
        )

    # The universe is the set of CONCRETE capability ids we may return. It must
    # NOT include the scopes' own entries when they are coarse permission names
    # (e.g. "read"), or a permission name would masquerade as a capability in the
    # effective set. So: when catalog_ids is given, the universe IS the catalog;
    # scope entries only act as grant predicates. When catalog_ids is absent
    # (pure-logic / unit mode), fall back to the scope entries as the universe.
    if catalog_ids is not None:
        universe: set[str] = set(catalog_ids)
    else:
        universe = set()
        for scope in (agent_scope, owner_scope, pack_needs):
            for e in scope.entries:
                if e == "*":
                    continue
                universe.add(e)
                if ":" in e:
                    universe.add(e.split(":", 1)[1])

    def _matches(scope: Scope, cid: str) -> bool:
        # A universe entry may be "<kind>:<id>" or bare "<id>". A layer grants it
        # if it grants the exact form, the bare id, the kind-wildcard, or "*".
        if scope.grants(cid):
            return True
        if ":" in cid:
            kind, bare = cid.split(":", 1)
            if scope.grants(bare):
                return True
            if f"{kind}:*" in scope.entries:
                return True
        return False

    effective = {
        cid for cid in universe
        if _matches(agent_scope, cid) and _matches(owner_scope, cid) and _matches(pack_needs, cid)
    }
    return Resolution(agent_id=agent_id, effective=frozenset(effective))