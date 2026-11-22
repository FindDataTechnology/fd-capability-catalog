"""Three readers over the catalog, each cut differently (task 4 + 5).

The catalog holds the whole index; who may SEE what depends on the reader:

- ``assembly_view`` — a governor/builder sees everything in their governed range.
  This is the only reader that gets "the full catalog".
- ``resident_view`` — an agent's runtime context. It holds ONLY the capabilities
  the current pack actually uses, intersected with the agent's effective set.
  Never the whole catalog.
- ``retrieval_view`` — an agent's ad-hoc lookup. Paginated, filtered to the
  effective set. Returns nothing outside it.

All three take the SAME ``Resolution`` the resolver produced, and an id outside
that resolution yields NO row — not a placeholder, not a count difference. That
is "unperceivable" (task 5): a capability the subject lacks is indistinguishable
from one that does not exist.

Change: establish-capability-plane (tasks 4, 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .resolution import Resolution


@dataclass(frozen=True)
class CatalogRow:
    """A catalog entry as it leaves a reader. Minimal, JSON-friendly."""

    federated_id: str
    kind: str
    name: str
    status: str
    served_by: tuple[str, ...]

    def to_public(self) -> dict:
        return {
            "id": self.federated_id,
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "served_by": list(self.served_by),
        }


def _row(unit) -> CatalogRow:
    attrs = unit.attrs or {}
    return CatalogRow(
        federated_id=unit.external_key,
        kind=unit.kind,
        name=unit.body,
        status=str(attrs.get("status", "")),
        served_by=tuple(attrs.get("served_by") or ()),
    )


def assembly_view(
    units: Iterable,
    *,
    governed_kinds: Sequence[str] | None = None,
    governor_rules=None,
    serving: Mapping[str, Sequence[tuple[str, str]]] | None = None,
) -> list[CatalogRow]:
    """Governed view: what a GOVERNOR may assemble — the capabilities their own
    authority covers, optionally narrowed to kinds.

    Two modes:
    - no ``governor_rules`` -> full catalog (the caller has established
      governance authority out of band; used by admin tooling).
    - ``governor_rules`` given -> only capabilities whose serving (server, tool)
      is granted to the governor. This is the 4.1 range: a governor sees the
      capabilities their registry ``server_access`` (Logto role→scope) actually
      governs, bridged to capability level via the serving map — the SAME
      mechanism the agent resolution uses, just without the pack narrowing.
    """
    rows: list[CatalogRow] = []
    for u in units:
        if governed_kinds is not None and u.kind not in set(governed_kinds):
            continue
        if governor_rules is not None:
            from .access import capability_allowed

            if not capability_allowed(u.external_key, governor_rules, serving or {}):
                continue
        rows.append(_row(u))
    return sorted(rows, key=lambda r: (r.kind, r.federated_id))


def resident_view(units: Iterable, resolution: Resolution, *, pack_used: Sequence[str]) -> list[CatalogRow]:
    """An agent's resident context: only pack-used ∩ effective capabilities.

    ``pack_used`` is the set of capability ids the current pack actually
    references. A capability in the catalog but not in ``pack_used`` never enters
    the agent's context, even if the agent is allowed it — this is the
    intelligence/context saving.
    """
    used = set(pack_used)
    out: list[CatalogRow] = []
    for u in units:
        if u.external_key not in used:
            continue
        if not resolution.allows(u.external_key, kind=u.kind):
            continue
        out.append(_row(u))
    return sorted(out, key=lambda r: (r.kind, r.federated_id))


def retrieval_view(
    units: Iterable,
    resolution: Resolution,
    *,
    query: str = "",
    kind: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """An agent's ad-hoc lookup: paginated, effective-only.

    Filters to the effective set FIRST, so an unauthorized id is absent from the
    candidate pool before paging — no count, ordering or timing trace of it.
    """
    pool = [
        u for u in units
        if resolution.allows(u.external_key, kind=u.kind)
        and (kind is None or u.kind == kind)
        and (not query or query.lower() in (u.body or "").lower() or query.lower() in u.external_key.lower())
    ]
    pool.sort(key=lambda u: (u.kind, u.external_key))
    total = len(pool)
    page = pool[max(0, offset): max(0, offset) + max(1, limit)]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [_row(u).to_public() for u in page],
    }