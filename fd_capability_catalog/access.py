"""Tool-level authorization meets capability-level resolution (model C).

Registry authorizes by ``server_access``: a set of ``{server, tools}`` grants
(e.g. ``{server: fd-open-data-mcp, tools: [all]}``). The capability catalog lists
SEMANTIC capabilities (``price.close``, ``gdp.cn``). These are two ID spaces.

The bridge is a **serving map**: each capability names the (server, tool) pairs
that expose it. ``read(concept=price.close)`` on fd-open-data-mcp is the tool
``read`` serving ``price.close``. With serving edges, a tool-level grant becomes a
capability-level grant:

    a capability is ALLOWED by a grant set
      iff some serving (server, tool) of it matches some grant

The three-layer resolution then runs at the capability level, but the agent/owner
layers are evaluated through this bridge, while ``pack.needs`` stays capability-
id. That is the whole point of model C: assembly sees semantics, authorization
stays on the tools registry already governs.

Change: establish-capability-plane (resolution model C).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

WILDCARD = "*"
ANY_TOOL = "all"  # registry spells "every tool" as the literal "all"


@dataclass(frozen=True)
class AllowRule:
    """One ``server_access`` grant: which tools on which server are allowed."""

    server: str
    tools: frozenset[str]

    @classmethod
    def from_access(cls, spec: Mapping) -> "AllowRule":
        server = str(spec.get("server", WILDCARD) or WILDCARD)
        raw_tools = spec.get("tools") or []
        tools = frozenset(
            ANY_TOOL if t in (ANY_TOOL, WILDCARD) else str(t) for t in raw_tools
        ) or frozenset({ANY_TOOL})
        return cls(server=server, tools=tools)

    def matches(self, server: str, tool: str) -> bool:
        if self.server != WILDCARD and self.server != server:
            return False
        if ANY_TOOL in self.tools:
            return True
        return tool in self.tools


def rules_from_server_access(access: Sequence[Mapping]) -> list[AllowRule]:
    """Turn a registry scope's ``server_access`` list into AllowRules.

    Entries without a ``server`` key (e.g. ``{agents: {...}}``) are not tool
    grants and are ignored here — they are registry's own concern.
    """
    return [AllowRule.from_access(a) for a in access if isinstance(a, Mapping) and "server" in a]


def grants_allowed(rules: Iterable[AllowRule], server: str, tool: str) -> bool:
    return any(r.matches(server, tool) for r in rules)


#: capability_id -> the (server, tool) pairs that serve it.
ServingMap = Mapping[str, Sequence[tuple[str, str]]]


def capability_allowed(
    capability_id: str, rules: Iterable[AllowRule], serving: ServingMap
) -> bool:
    """A capability is allowed iff SOME serving (server, tool) is granted."""
    edges = serving.get(capability_id)
    if not edges:
        return False
    rule_list = list(rules)
    return any(grants_allowed(rule_list, server, tool) for (server, tool) in edges)


def effective_capabilities(
    *,
    agent_rules: Iterable[AllowRule],
    owner_rules: Iterable[AllowRule],
    pack_needs: Iterable[str],
    serving: ServingMap,
    catalog_ids: Iterable[str],
) -> frozenset[str]:
    """Model C resolution: capability effective iff needs it AND both tool layers allow it.

    - ``pack_needs`` is capability-id level (a claim, narrows only).
    - agent/owner are tool-level grants, bridged to capabilities via ``serving``.
    """
    agent_rules = list(agent_rules)
    owner_rules = list(owner_rules)
    needs = set(pack_needs)
    out = set()
    for cid in catalog_ids:
        if needs and cid not in needs:
            continue
        if not capability_allowed(cid, agent_rules, serving):
            continue
        if not capability_allowed(cid, owner_rules, serving):
            continue
        out.add(cid)
    return frozenset(out)


def resolution_from_tool_grants(
    *,
    agent_id: str,
    agent_rules: Iterable[AllowRule],
    owner_rules: Iterable[AllowRule],
    pack_needs: Iterable[str],
    serving: ServingMap,
    catalog_ids: Iterable[str],
):
    """Build a ``Resolution`` (readers' input) from tool-level grants (model C).

    Fail-closed: an agent with no tool grants resolves to EMPTY — an unregistered
    or grant-less agent never inherits its owner's caps.
    """
    from .resolution import Resolution

    agent_rules = list(agent_rules)
    if not agent_rules:
        return Resolution(
            agent_id=agent_id,
            effective=frozenset(),
            fail_closed_reason="agent has no tool grants (fail-closed)",
        )
    eff = effective_capabilities(
        agent_rules=agent_rules, owner_rules=list(owner_rules),
        pack_needs=pack_needs, serving=serving, catalog_ids=catalog_ids,
    )
    return Resolution(agent_id=agent_id, effective=eff)