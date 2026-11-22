"""Capability-catalog projector: pull each semantic system's list, write the index.

The catalog never copies data. A **source** is one semantic system exposing a
``list_capabilities(scope_id) -> list[CatalogEntry]`` method; the projector
iterates the registered sources, maps each entry to a kernel ``write`` (which
enforces the closed ``kind`` vocabulary and records an append-only event), and
that is the whole write path.

S0 ships a source Protocol and a fake source for tests; the real adapters (one
per semantic system) plug in the same shape. Wiring a live source needs the
system's DB/list endpoint, which is a deployment concern (the local .env still
points at the retired LAN Postgres), so it is not run here.

Change: establish-capability-plane (tasks 1.2 / 2.x).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from fd_ontology_kernel.kernel import Kernel


@dataclass(frozen=True)
class CatalogEntry:
    """One capability as a source reports it, before it becomes a catalog row."""

    federated_id: str          # semantic key: the entry's stable identity
    kind: str                  # concept | indicator | graph | dataset
    name: str                  # display name
    served_by: str             # which system this entry comes from
    status: str = "registered"  # verified | registered | unverified
    coverage: Mapping[str, object] = field(default_factory=dict)
    extra: Mapping[str, object] = field(default_factory=dict)
    #: The (server, tool) pairs that expose this capability. The bridge between
    #: this source's semantic id and registry's tool-level authorization. When
    #: empty, the capability is not covered by any tool grant (thus not
    #: resolvable) until a serving edge is supplied.
    served_by_tools: Sequence[tuple[str, str]] = ()


class CapabilitySource(Protocol):
    """One semantic system's read-only list surface.

    ``system`` names the source (recorded in each entry's ``served_by``/attrs);
    ``list_capabilities`` returns that system's entries for a scope.
    """

    system: str

    def list_capabilities(self, *, scope_id: str) -> Sequence[CatalogEntry]: ...


@dataclass
class ProjectionReport:
    """What one projection pass did — the observable result of a sync."""

    scope_id: str
    sources: int
    written: int
    by_kind: dict[str, int]
    failures: list[tuple[str, str]]  # (system, reason) — source unreachable etc.
    marked_stale: int = 0            # rows of failed sources marked "暂不可核"


STALE_MARKER = "暂不可核"


def project(
    kernel: Kernel,
    sources: Sequence[CapabilitySource],
    *,
    scope_id: str,
    actor: str = "capability-projector",
) -> ProjectionReport:
    """Pull every source and upsert its entries into the catalog.

    Idempotent: an unchanged entry re-writes to the same row (kernel folds it to
    an update event, not a duplicate). A source that raises is recorded as a
    failure and **its existing rows are marked "暂不可核" — never deleted** (the
    spec's "系统下线不误删目录条"): a transient outage keeps the entries visible
    but flagged, and the other sources are unaffected.
    """
    written = 0
    by_kind: dict[str, int] = {}
    failures: list[tuple[str, str]] = []
    marked_stale = 0

    # Accumulate per identity across ALL sources first, so the same capability
    # reported by two systems merges into ONE row with a union of served_by and
    # served_by_tools. Cross-system alignment is therefore "same full identity →
    # merge", never a name-similarity guess (spec capability-catalog).
    merged: dict[str, dict] = {}
    for source in sources:
        try:
            entries = source.list_capabilities(scope_id=scope_id)
        except Exception as exc:  # noqa: BLE001 - one source failing must not stop the pass
            failures.append((source.system, f"{type(exc).__name__}: {exc}"))
            marked_stale += _mark_source_stale(kernel, scope_id, source.system, actor)
            continue
        for entry in entries:
            slot = merged.get(entry.federated_id)
            if slot is None:
                slot = merged[entry.federated_id] = {
                    "kind": entry.kind, "body": entry.name, "status": entry.status,
                    "coverage": dict(entry.coverage), "extra": dict(entry.extra),
                    "served_by": [], "served_by_tools": [],
                }
            if entry.served_by not in slot["served_by"]:
                slot["served_by"].append(entry.served_by)
            for t in entry.served_by_tools:
                if list(t) not in slot["served_by_tools"]:
                    slot["served_by_tools"].append(list(t))

    for ident, s in merged.items():
        kernel.write(
            scope_id=scope_id,
            kind=s["kind"],
            external_key=ident,
            body=s["body"],
            attrs={
                "served_by": s["served_by"],
                "status": s["status"],
                "coverage": s["coverage"],
                "served_by_tools": s["served_by_tools"],
                **s["extra"],
            },
            actor=actor,
            via="project:" + ",".join(s["served_by"]),
        )
        written += 1
        by_kind[s["kind"]] = by_kind.get(s["kind"], 0) + 1

    return ProjectionReport(
        scope_id=scope_id, sources=len(sources), written=written,
        by_kind=by_kind, failures=failures, marked_stale=marked_stale,
    )


def _mark_source_stale(kernel: Kernel, scope_id: str, system: str, actor: str) -> int:
    """Flag a failed source's rows as "暂不可核" (visibility unchanged, no delete)."""
    n = 0
    for unit in kernel.live_units(scope_id=scope_id):
        served_by = list((unit.attrs or {}).get("served_by") or [])
        if system not in served_by:
            continue
        if (unit.attrs or {}).get("reachable") == STALE_MARKER:
            continue
        new_attrs = dict(unit.attrs)
        new_attrs["reachable"] = STALE_MARKER
        kernel.write(
            scope_id=scope_id, kind=unit.kind, external_key=unit.external_key,
            body=unit.body, attrs=new_attrs, actor=actor, via=f"stale:{system}",
        )
        n += 1
    return n


def serving_map_from_catalog(units: Iterable) -> dict[str, list[tuple[str, str]]]:
    """Build a capability→[(server,tool)] map from projected catalog units.

    This is how model C gets its bridging data at runtime: the projector stored
    each entry's ``served_by_tools``; this reverses it into the ServingMap the
    resolver consumes. A unit with no served_by_tools contributes no edges (its
    capability is un-resolvable until a source supplies them).
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for u in units:
        pairs = (u.attrs or {}).get("served_by_tools") or []
        edges = [(str(s), str(t)) for s, t in (p for p in pairs)]
        if edges:
            out[u.external_key] = edges
    return out


class StaticSource:
    """A source backed by a fixed list — the test double, and a stand-in until a
    live adapter is wired."""

    def __init__(self, system: str, entries: Sequence[CatalogEntry]) -> None:
        self.system = system
        self._entries = list(entries)

    def list_capabilities(self, *, scope_id: str) -> Sequence[CatalogEntry]:
        return list(self._entries)