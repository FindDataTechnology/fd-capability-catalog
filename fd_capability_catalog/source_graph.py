"""Graph-kind source: text-core's KG verticals as ``graph`` capabilities.

text-core's temporal knowledge-graph lane ships one config per vertical
(``fd_text_core/kg_lane/verticals/<name>.kg.json``), each declaring a
``name`` and a FalkorDB ``group_id`` (the graph). That is the ground truth for a
graph capability: one vertical = one graph. The tools that read it are text-core's
KG tools (``kg_search_facts`` / ``kg_walk`` / ``kg_facts_as_of``), so those are the
serving edges.

Grounding (verified 2026-11-22):
- verticals/legal.kg.json -> group_id legal_v2
- verticals/gov.kg.json   -> group_id gov_v3
- FalkorDB GRAPH.LIST -> [legal_v2, gov_v3, default_db]
- text-core tools: kg_search_facts, kg_walk, kg_facts_as_of

Reading the configs (not the live DB) keeps this source offline-testable and
independent of FalkorDB reachability; the graph_id is recorded in coverage so a
consumer knows which graph backs the capability.

Change: establish-capability-plane (task 6.1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .projector import CatalogEntry

SYSTEM = "fd-text-core"

#: Tools on text-core that read a KG vertical (verified from tool annotations).
SERVING_TOOLS: tuple[tuple[str, str], ...] = (
    (SYSTEM, "kg_search_facts"),
    (SYSTEM, "kg_walk"),
    (SYSTEM, "kg_facts_as_of"),
)


class TextCoreGraphSource:
    """text-core KG verticals as ``graph`` capabilities."""

    system = SYSTEM

    def __init__(self, verticals_dir: Path | str) -> None:
        self._dir = Path(verticals_dir)

    def list_capabilities(self, *, scope_id: str) -> Sequence[CatalogEntry]:
        entries: list[CatalogEntry] = []
        if not self._dir.is_dir():
            return entries
        for cfg in sorted(self._dir.glob("*.kg.json")):
            doc = json.loads(cfg.read_text(encoding="utf-8"))
            graph_id = doc.get("group_id")
            name = doc.get("name")
            if not graph_id or not name:
                continue
            entries.append(
                CatalogEntry(
                    federated_id=f"graph:{graph_id}",
                    kind="graph",
                    name=str(name),
                    served_by=SYSTEM,
                    status="verified",
                    coverage={
                        "graph_id": graph_id,
                        "language": doc.get("language"),
                        "entity_types": [e.get("name") for e in doc.get("entity_types", [])
                                         if isinstance(e, dict)],
                    },
                    served_by_tools=tuple(SERVING_TOOLS),
                )
            )
        return entries


#: The entity-relationship graph (NetworkX) served by ``graph_search``. Unlike the
#: text-core KG verticals, this is ONE graph (entities + entity_relationships
#: tables), exposed by both fd-open-data-mcp and fd-find-data-business-mcp under
#: the same tool name. It was missing from the first cut of this source; added
#: 2026-11-23.
ENTITY_GRAPH_SYSTEM = "fd-open-data-mcp"
ENTITY_GRAPH_SERVING: tuple[tuple[str, str], ...] = (
    ("fd-open-data-mcp", "graph_search"),
    ("fd-find-data-business-mcp", "graph_search"),
)


class EntityGraphSource:
    """The entity-relationship graph as one ``graph`` capability."""

    system = "entity-graph"

    def list_capabilities(self, *, scope_id: str) -> Sequence[CatalogEntry]:
        return [
            CatalogEntry(
                federated_id="graph:entity-graph",
                kind="graph",
                name="entity-relationship graph",
                served_by=ENTITY_GRAPH_SYSTEM,
                status="verified",
                coverage={
                    "backing": "entities + entity_relationships (NetworkX)",
                    "algorithms": ["bfs", "dfs", "neighbors", "shortest_path",
                                   "subgraph", "ego_graph", "statistics"],
                },
                served_by_tools=tuple(ENTITY_GRAPH_SERVING),
            )
        ]