"""capability-catalog CLI — the runnable entry the library lacked.

Until now every capability lived only in tests: nothing could project a real
catalog or resolve a real principal from the command line. This makes the plane
callable:

    capability-catalog build  --db cat.db [--scope org:finddata] [--source open-data|business|graph]
    capability-catalog list   --db cat.db [--kind concept] [--governor TOOLS_JSON]
    capability-catalog resolve --db cat.db --agent TOOLS_JSON [--owner TOOLS_JSON] [--needs ids.txt]

``TOOLS_JSON`` is a registry ``server_access`` list, e.g.
``[{"server":"fd-open-data-mcp","tools":["read"]}]`` — the real shape registry
handed the gateway. ``resolve`` prints the effective capability set (model C);
``list`` prints the governed assembly view.

Sources are pluggable; on a machine that cannot reach a semantic system the
source is reported as a failure (never a silent empty catalog).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .access import resolution_from_tool_grants, rules_from_server_access
from .adapter import build_kernel
from .projector import project, serving_map_from_catalog
from .readers import assembly_view

DEFAULT_SCOPE = "org:finddata"


def _sources(selected: list[str], verticals_dir: str | None):
    """Build the requested sources. Each import is deferred so a missing dep on
    one source does not block the others (graceful degradation)."""
    out = []
    if "open-data" in selected or "all" in selected:
        try:
            from .source_open_data import OpenDataConceptsSource

            from fd_open_data_mcp.db import get_database

            out.append(OpenDataConceptsSource(lambda: get_database().get_session()))
        except Exception as exc:  # noqa: BLE001
            print(f"! open-data source unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
    if "business" in selected or "all" in selected:
        try:
            from .source_business import BusinessMcpSource

            from fd_open_data_mcp.db import get_database

            out.append(BusinessMcpSource(lambda: get_database().get_session()))
        except Exception as exc:  # noqa: BLE001
            print(f"! business source unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
    if "graph" in selected or "all" in selected:
        try:
            from .source_graph import TextCoreGraphSource

            vdir = verticals_dir or "fd-text-core/fd_text_core/kg_lane/verticals"
            out.append(TextCoreGraphSource(Path(vdir)))
        except Exception as exc:  # noqa: BLE001
            print(f"! graph source unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
    return out


def _load_rules(raw: str | None):
    if not raw:
        return None
    return rules_from_server_access(json.loads(raw))


def _cmd_build(args) -> int:
    kernel = build_kernel(args.db)
    sources = _sources(args.source, args.verticals_dir)
    if not sources:
        print("no sources available", file=sys.stderr)
        return 2
    rep = project(kernel, sources, scope_id=args.scope)
    print(
        json.dumps(
            {
                "scope": rep.scope_id,
                "sources": rep.sources,
                "written": rep.written,
                "by_kind": rep.by_kind,
                "failures": rep.failures,
                "marked_stale": rep.marked_stale,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not rep.failures else 1


def _cmd_serving(args) -> int:
    """Emit capability -> [[server, tool], ...] serving edges.

    This is what a downstream (e.g. paas pack assembly) needs to check whether a
    referenced (server, tool) is real: the ``list`` view carries provider names
    but not the tool edges. One JSON object, no decoration.
    """
    kernel = build_kernel(args.db)
    units = kernel.live_units(scope_id=args.scope)
    serving = serving_map_from_catalog(units)
    out = {cid: [list(t) for t in edges] for cid, edges in serving.items()}
    print(json.dumps(out, ensure_ascii=False))
    return 0


def _cmd_list(args) -> int:
    kernel = build_kernel(args.db)
    units = kernel.live_units(scope_id=args.scope)
    serving = serving_map_from_catalog(units)
    gov = _load_rules(args.governor)
    rows = assembly_view(
        units, governed_kinds=[args.kind] if args.kind else None,
        governor_rules=gov, serving=serving,
    )
    print(json.dumps([r.to_public() for r in rows], ensure_ascii=False, indent=2))
    return 0


def _cmd_resolve(args) -> int:
    kernel = build_kernel(args.db)
    units = kernel.live_units(scope_id=args.scope)
    catalog_ids = {u.external_key for u in units}
    serving = serving_map_from_catalog(units)
    agent = _load_rules(args.agent) or []
    owner = _load_rules(args.owner) or agent
    needs: list[str] = []
    if args.needs:
        needs = [ln.strip() for ln in Path(args.needs).read_text(encoding="utf-8").splitlines() if ln.strip()]
    res = resolution_from_tool_grants(
        agent_id=args.agent_id, agent_rules=agent, owner_rules=owner,
        pack_needs=needs or catalog_ids, serving=serving, catalog_ids=catalog_ids,
    )
    print(json.dumps({
        "agent_id": res.agent_id,
        "effective": sorted(res.effective),
        "fail_closed_reason": res.fail_closed_reason,
    }, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="capability-catalog",
        description="Federated capability catalog: project, list, resolve.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  capability-catalog build --db /tmp/cat.db --source all\n"
            "  capability-catalog list --db /tmp/cat.db --kind concept\n"
            "  capability-catalog resolve --db /tmp/cat.db "
            "--agent '[{\"server\":\"fd-open-data-mcp\",\"tools\":[\"read\"]}]'\n"
        ),
    )
    p.add_argument("--db", required=True, help="SQLite catalog path (created if absent)")
    p.add_argument("--scope", default=DEFAULT_SCOPE, help=f"ontology scope id (default {DEFAULT_SCOPE})")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="project all sources into the catalog")
    b.add_argument("--source", action="append", default=[], choices=["open-data", "business", "graph", "all"])
    b.add_argument("--verticals-dir", default=None, help="text-core kg verticals dir (graph source)")
    b.set_defaults(func=_cmd_build)

    lst = sub.add_parser("list", help="governed assembly view")
    lst.add_argument("--kind", default=None, help="narrow to one kind")
    lst.add_argument("--governor", default=None, help="governor's server_access JSON (omit = admin full view)")
    lst.set_defaults(func=_cmd_list)

    srv = sub.add_parser("serving", help="capability -> [[server,tool],...] serving edges (JSON)")
    srv.set_defaults(func=_cmd_serving)

    r = sub.add_parser("resolve", help="effective capability set for a principal (model C)")
    r.add_argument("--agent", required=True, help="agent server_access JSON")
    r.add_argument("--owner", default=None, help="owner server_access JSON (default = agent's)")
    r.add_argument("--needs", default=None, help="file of pack.needs capability ids (one per line)")
    r.add_argument("--agent-id", default="agent", help="label for the resolved principal")
    r.set_defaults(func=_cmd_resolve)

    args = p.parse_args(argv)
    if getattr(args, "source", None) == []:
        args.source = ["all"]
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())