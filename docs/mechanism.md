# Capability-Plane Mechanism

How the capability catalog, the composite-authorization resolver, and the three
readers fit together. This is the design record for `establish-capability-plane`;
the code lives beside it.

## The one-line shape

A federated **catalog** (what capabilities exist) + a three-layer **resolver**
(who may use which) + three **readers** (who sees what). The catalog is the only
full view; every agent sees a cut of it.

## 1. Catalog — a federated index, not a data copy

```
  fd-open-data-mcp  ─┐
  (concepts+registry)│  each source exposes list_capabilities()
  business-mcp      ─┼─► projector ──► catalog domain (ontology-kernel rules)
  text-kg / legal-kg ─┘                          │
                                   kind ∈ {concept, indicator, graph, dataset}
```

- Implemented as an `fd-ontology-kernel` domain (`adapter.py`): identity =
  scope + kind + federated_id, a closed `kind` vocabulary, one write path, an
  append-only event per change. The conformance gate passes.
- The projector (`projector.py`) pulls each source and upserts entries. It is
  idempotent (an unchanged re-run is an update, not a duplicate) and
  failure-isolated: a source that is down marks its existing rows `暂不可核` and
  never deletes them; other sources are unaffected.

## 2. Resolution — three layers, bridged by the serving map (model C)

Semantic capabilities (the catalog) and tool authorization (registry) are two ID
spaces. A **serving map** bridges them: each capability names the `(server, tool)`
pairs that expose it.

```
  capability is allowed  ⟺  some serving (server, tool) matches some AllowRule

  effective = pack_needs(:capability-id)
            ∩ { caps whose serving is granted by agent.server_access }
            ∩ { caps whose serving is granted by owner.server_access }
```

- **agent / owner** — evaluated at tool level from registry `server_access`
  (`AllowRule`), bridged to capabilities via the serving map. An agent with no
  tool grants resolves to EMPTY: fail-closed, never owner-only.
- **pack.needs** — capability-id level. A claim, never a grant: it only narrows.
- No stored scope: agent/owner grants come straight from registry's existing
  `server_access` (e.g. `{server: fd-open-data-mcp, tools: [all]}`).

Why model C: registry already authorizes by `{server, tools}`; the catalog wants
to speak semantics for the assembly view. Bridging keeps each side honest — the
assembly surface sees `price.close`, the authorization stays on the tool the
registry actually governs.

### Where the serving edges come from

A source declares, per entry, which `(server, tool)` pairs expose it. For
`fd-open-data-mcp`, `read(concept_id,...)` and `read_series(concept_id,...)` (both
verified from `server.py` signatures) serve every concept that source lists, so
each concept carries `[(fd-open-data-mcp, read), (fd-open-data-mcp, read_series)]`.
`projector.serving_map_from_catalog` reverses the stored edges into the
ServingMap the resolver consumes.

Verified end to end against live data: 300 real concepts pulled from the
production DB, projected, edge-mapped, and resolved with a real
`{server: fd-open-data-mcp, tools: [read]}` grant — all 300 effective. A catalog
row with no serving edge is unresolvable (fail-closed).

## 3. Readers — one resolution, three cuts

| Reader | Who | Sees |
|---|---|---|
| `assembly_view` | governor / builder | the full catalog (range-model pending, see below) |
| `resident_view` | an agent's runtime context | pack-used ∩ effective — never the whole catalog |
| `retrieval_view` | an agent's ad-hoc lookup | paginated, effective-only |

All three consume the SAME `Resolution`. A capability the subject lacks yields no
row at all — not a placeholder, not a count difference, not an ordering hint. It
is indistinguishable from one that does not exist (unperceivable). A reverse
self-proof test asserts that dropping the filter leaks, so the guard is real.

## Open edges (honest)

- **`assembly_view` range model**: "full catalog" is implemented, but the
  *governance range* (which subset a given governor may see) is not yet defined —
  domain / tenant / role source unspecified. Until it is, assembly returns all.
- **Live sources**: only `source_open_data.py` is wired to a real system; the
  second system and graph kind need their sources added.
- **Cross-system identity alignment** (crosswalk): not yet implemented; today a
  capability carried by two systems would appear as two rows unless a source
  de-duplicates. Alignment is the next layer.

## Verify

```bash
python -m fd_ontology_kernel.conformance.gate --adapter fd-capability-catalog
# PASS: 14 checks, 2 skipped
PYTHONPATH=../fd-ontology-kernel:. uv run --with pytest python -m pytest tests/
# 26 passed
```