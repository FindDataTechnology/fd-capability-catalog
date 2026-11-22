"""Second live source: fd-find-data-business-mcp's concept/indicator list.

business-mcp depends on ``fd-open-data-mcp`` and reads the SAME ``Concept`` model
from the SAME database (``from fd_open_data_mcp.models import Concept`` via
``get_database()``). So its concepts share one identity space with the first
source — a concept carried by both is the SAME capability, and cross-system
alignment is "same full identity merges served_by", not fuzzy matching.

The difference between the two sources is the SERVING EDGES: business-mcp exposes
``read`` / ``read_series`` / ``read_range`` (a range reader the public face does
not), so a concept served by it gets those tool edges.

Change: establish-capability-plane (tasks 2.2 / 2.3).
"""

from __future__ import annotations

from typing import Sequence

from .projector import CatalogEntry

SYSTEM = "fd-find-data-business-mcp"

#: Tools on business-mcp that expose a concept (read/read_series/read_range all
#: accept a concept/indicator id; read_range is the range reader).
SERVING_TOOLS: tuple[tuple[str, str], ...] = (
    (SYSTEM, "read"),
    (SYSTEM, "read_series"),
    (SYSTEM, "read_range"),
)


def _identity(r: dict, is_registry: bool) -> str | None:
    code = r.get("code") or r.get("semantic_code") or r.get("native_code")
    if not code:
        return None
    if is_registry:
        return str(code)
    return "|".join(str(r.get(k) or "") for k in
                    ("code", "entity_type", "measure", "unit", "frequency"))


def _status(r: dict) -> str:
    if r.get("deprecated"):
        return "unverified"
    return "verified" if r.get("verified", True) else "registered"


class BusinessMcpSource:
    """business-mcp concept/indicator list as a capability source."""

    system = SYSTEM

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    def list_capabilities(self, *, scope_id: str) -> Sequence[CatalogEntry]:
        from fd_open_data_mcp.semantic.concepts import (
            LIST_MAX_LIMIT,
            list_concepts_with_family,
        )

        session = self._session_factory()
        try:
            rows: list[dict] = []
            offset = 0
            while True:
                chunk = list_concepts_with_family(session, limit=LIST_MAX_LIMIT, offset=offset)
                rows.extend(chunk)
                if len(chunk) < LIST_MAX_LIMIT:
                    break
                offset += LIST_MAX_LIMIT

            entries: list[CatalogEntry] = []
            for r in rows:
                is_registry = bool(r.get("native_code") or r.get("source_db")) and not r.get("entity_type")
                ident = _identity(r, is_registry)
                if not ident:
                    continue
                entries.append(
                    CatalogEntry(
                        federated_id=ident,
                        kind="indicator" if is_registry else "concept",
                        name=str(r.get("name_zh") or r.get("name_en")
                                 or r.get("semantic_code") or r.get("code")),
                        served_by=SYSTEM,
                        status=_status(r),
                        coverage={
                            "code": r.get("code"),
                            "entity_type": r.get("entity_type"),
                            "frequency": r.get("frequency"),
                            "unit": r.get("unit"),
                            "measure": r.get("measure"),
                        },
                        served_by_tools=tuple(SERVING_TOOLS),
                    )
                )
            return entries
        finally:
            session.close()