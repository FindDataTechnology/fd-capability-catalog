"""Capability Catalog — MCP tool surface.

The catalog is a **service**, consumed the way every other capability in the
system is consumed: over MCP. A pack-install checker does not read our SQLite
file or spawn our CLI; it calls these tools through the gateway like it calls
fd-open-data-mcp.

Tools:
- ``list_capabilities`` — governed view (optionally narrowed by kind and by the
  caller's server_access), one JSON row per capability.
- ``serving_edges``     — capability -> [[server, tool], ...] (the bridge a
  reference check needs: is this (server, tool) real?).
- ``resolve_for``       — model-C effective set for a principal's server_access.

The catalog owns its db / build / refresh. Callers only pass a db path via env
(``CAPABILITY_CATALOG_DB``) and an optional governor ``server_access``.

Change: wire-capability-catalog-into-pack-assembly (D3: MCP shape).
"""

from __future__ import annotations

import json
import os
from typing import Any

from .access import resolution_from_tool_grants, rules_from_server_access
from .adapter import build_kernel
from .projector import serving_map_from_catalog
from .readers import assembly_view

DEFAULT_SCOPE = os.getenv("CAPABILITY_CATALOG_SCOPE", "org:finddata")


def _db_path() -> str:
    p = os.getenv("CAPABILITY_CATALOG_DB", "").strip()
    if not p:
        raise RuntimeError("CAPABILITY_CATALOG_DB is not set")
    return p


def _units(scope_id: str):
    return build_kernel(_db_path()).live_units(scope_id=scope_id)


def _rules(server_access_json: str | None):
    if not server_access_json:
        return None
    try:
        parsed = json.loads(server_access_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"server_access is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError("server_access must be a JSON array of {server, tools}")
    return rules_from_server_access(parsed)


def list_capabilities(
    kind: str | None = None,
    server_access: str | None = None,
    scope_id: str = DEFAULT_SCOPE,
) -> list[dict[str, Any]]:
    """Governed list of capabilities.

    Args:
        kind: narrow to one capability kind (concept/indicator/graph/dataset).
        server_access: the caller's registry server_access as a JSON array
            (e.g. ``[{"server":"fd-open-data-mcp","tools":["read"]}]``). When
            given, only capabilities that server_access governs are returned;
            omitted = admin/full view.
        scope_id: ontology scope (default from CAPABILITY_CATALOG_SCOPE).
    """
    units = _units(scope_id)
    serving = serving_map_from_catalog(units)
    rows = assembly_view(
        units,
        governed_kinds=[kind] if kind else None,
        governor_rules=_rules(server_access),
        serving=serving,
    )
    return [r.to_public() for r in rows]


def serving_edges(scope_id: str = DEFAULT_SCOPE) -> dict[str, list[list[str]]]:
    """capability id -> [[server, tool], ...] serving edges.

    What a reference check uses to answer "is this (server, tool) real?".
    """
    units = _units(scope_id)
    return {cid: [list(t) for t in edges] for cid, edges in serving_map_from_catalog(units).items()}


def resolve_for(
    server_access: str,
    needs: list[str] | None = None,
    owner_server_access: str | None = None,
    agent_id: str = "agent",
    scope_id: str = DEFAULT_SCOPE,
) -> dict[str, Any]:
    """Model-C effective capability set for a principal.

    Args:
        server_access: the agent's registry server_access (JSON array string).
        needs: optional pack.needs capability ids (narrows; default = whole catalog).
        owner_server_access: the owner's server_access (JSON array string);
            defaults to the agent's own.
        agent_id: label for the resolved principal.
    """
    units = _units(scope_id)
    catalog_ids = {u.external_key for u in units}
    serving = serving_map_from_catalog(units)
    agent_rules = _rules(server_access) or []
    owner_rules = _rules(owner_server_access) if owner_server_access else agent_rules
    res = resolution_from_tool_grants(
        agent_id=agent_id,
        agent_rules=agent_rules,
        owner_rules=owner_rules,
        pack_needs=needs or catalog_ids,
        serving=serving,
        catalog_ids=catalog_ids,
    )
    return {
        "agent_id": res.agent_id,
        "effective": sorted(res.effective),
        "fail_closed_reason": res.fail_closed_reason,
    }


def build_mcp():
    """Construct the FastMCP server (imported lazily so the core lib avoids the dep)."""
    from fastmcp import FastMCP

    mcp = FastMCP(
        name="capability-catalog",
        instructions=(
            "Federated capability catalog. Call serving_edges to check whether a "
            "(server, tool) reference is real; resolve_for for a principal's "
            "effective capability set; list_capabilities for a governed browse."
        ),
    )
    mcp.tool(list_capabilities)
    mcp.tool(serving_edges)
    mcp.tool(resolve_for)
    return mcp


def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8320) -> None:
    """Run the catalog MCP server (stdio default; http serves at /mcp)."""
    mcp = build_mcp()
    if transport == "stdio":
        mcp.run()
        return
    import uvicorn

    uvicorn.run(mcp.http_app(), host=host, port=port)


def serve_cli(argv: list[str] | None = None) -> None:
    """Console entry (``capability-catalog-mcp``): parse flags, then run.

    The parsing used to live under ``if __name__ == "__main__"``, which the
    console script never triggers — so ``capability-catalog-mcp --transport http``
    silently started **stdio** (measured 2026-11-23). A deployed server must not
    be able to ignore its own transport flag.
    """
    import argparse

    p = argparse.ArgumentParser(description="Capability catalog MCP server.")
    p.add_argument("--transport", default=os.getenv("MCP_TRANSPORT", "stdio"), choices=["stdio", "http"])
    p.add_argument("--host", default=os.getenv("MCP_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8320")))
    a = p.parse_args(argv)
    main(transport=a.transport, host=a.host, port=a.port)


if __name__ == "__main__":  # pragma: no cover
    serve_cli()