"""Live source adapter: fd-open-data-mcp's concept/indicator list.

``list_concepts_with_family`` is already the union of the native ``concepts``
table and the verified unified-indicator-registry rows, so ONE source covers two
catalog kinds: rows with an ``entity_type`` are ``concept``; registry-only rows
(carrying ``native_code``/``source_db`` and no entity_type) are ``indicator``.

Import is deferred: this module only touches fd_open_data_mcp when you actually
build the source, so the catalog package has no hard import-time dependency.
Wiring it live needs that system's DB, a deployment concern.
"""

from __future__ import annotations

from typing import Sequence

from .projector import CatalogEntry

SYSTEM = "fd-open-data-mcp"

#: The tools on this server that expose a concept (both accept concept_id; see
#: fd_open_data_mcp/server.py read()/read_series() signatures). This is the real
#: serving edge that bridges this source's semantic ids to registry tool grants.
SERVING_TOOLS: tuple[tuple[str, str], ...] = (
    (SYSTEM, "read"),
    (SYSTEM, "read_series"),
)


class OpenDataConceptsSource:
    """fd-open-data-mcp concept/indicator list as a capability source."""

    system = SYSTEM

    def __init__(self, session_factory) -> None:
        # session_factory() -> a SQLAlchemy Session the caller owns/closes.
        self._session_factory = session_factory

    def list_capabilities(self, *, scope_id: str) -> Sequence[CatalogEntry]:
        from fd_open_data_mcp.semantic.concepts import (
            LIST_MAX_LIMIT,
            list_concepts_with_family,
        )

        session = self._session_factory()
        try:
            # The list is paginated (default 500, max 1000). Page until a page
            # returns fewer than a full page — a single call would silently
            # truncate the catalog to 500 rows.
            rows: list[dict] = []
            offset = 0
            page = LIST_MAX_LIMIT
            while True:
                chunk = list_concepts_with_family(session, limit=page, offset=offset)
                rows.extend(chunk)
                if len(chunk) < page:
                    break
                offset += page
            entries: list[CatalogEntry] = []
            for r in rows:
                is_registry = bool(r.get("native_code") or r.get("source_db")) and not r.get("entity_type")
                kind = "indicator" if is_registry else "concept"
                code = r.get("code") or r.get("semantic_code") or r.get("native_code")
                if not code:
                    continue
                # Concept identity is the FULL tuple (code, entity_type, measure,
                # unit, frequency) — a bare code collides across entity/unit (the
                # table's unique key is that tuple). Using only `code` as the id
                # would make distinct concepts overwrite each other in the catalog.
                identity = "|".join(str(r.get(k) or "") for k in
                                    ("code", "entity_type", "measure", "unit", "frequency"))
                if is_registry:
                    # registry-only rows carry a semantic_code, not the full tuple
                    identity = str(code)
                # Concept has verified/deprecated booleans, not a `status` string.
                if r.get("deprecated"):
                    status = "unverified"
                elif r.get("verified", True):
                    status = "verified"
                else:
                    status = "registered"
                entries.append(
                    CatalogEntry(
                        federated_id=identity,
                        kind=kind,
                        name=str(r.get("name_zh") or r.get("name_en") or r.get("label")
                                 or r.get("semantic_code") or code),
                        served_by=SYSTEM,
                        status=status,
                        coverage={
                            "code": code,
                            "entity_type": r.get("entity_type"),
                            "frequency": r.get("frequency"),
                            "unit": r.get("unit"),
                            "measure": r.get("measure"),
                        },
                        # read(concept_id,...) and read_series(concept_id,...) on
                        # this server expose EVERY concept — that is the real
                        # serving edge (verified against server.py signatures).
                        served_by_tools=tuple(SERVING_TOOLS),
                    )
                )
            return entries
        finally:
            session.close()