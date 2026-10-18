# 02: Track 01 Service & PR Blast Radius Engine + Epistemic & Hallucination Guard (`Q1`–`Q4`)

**What to build:** End-to-end service and Pull Request blast-radius analysis (`blast_radius`, `pr_impact_radius`, `path_between` via `algo.SPpaths`, and `load_bearing_services` via `algo.pageRank`) wrapped in the 4-outcome epistemic contract (`answered_with_path`, `answered_from_memory`, `not_in_graph`, `unknown_entity`) and the deterministic post-generation sentence verifier `accepts(sentence, answer)` that blocks hallucinated service or ADR identifiers.

**Blocked by:** 01: Multi-Tenant FalkorDB Schema, Golden Seed Estate & Entity Resolver (`Q0`)

**Status:** done

## Core Contracts

```python
# Epistemic Outcome Contract
Outcome = Literal[
    "answered_with_path",
    "answered_from_memory",
    "not_in_graph",
    "unknown_entity",
]
```

```cypher
// FalkorDB Limitation #1 Rule: Preserve parallel edges in variable-length paths
MATCH path = (upstream:Service)-[r:DEPENDS_ON*1..$depth]->(target:Service {name: $service})
WHERE ALL(edge IN relationships(path) WHERE ID(edge) >= 0)
RETURN DISTINCT upstream.name AS service,
       upstream.tier AS tier,
       length(path) AS hops,
       [n IN nodes(path) | n.name] AS via_nodes,
       [e IN relationships(path) | e.kind] AS via_edge_kinds
ORDER BY hops ASC, tier ASC, service ASC
```

## Acceptance Criteria

- [x] `blast_radius` (`Q1`, capped at `depth <= 4`) contrasts `depth=1` (`cart-service`, `ledger-core`) vs `depth=4` (revealing `checkout-web`, `refunds-worker`, and `status-page` 3 hops away) and preserves both `sync` and `async` parallel edges between `ledger-core` and `payments-api`.
- [x] `pr_impact_radius` (`Q2`) traces `PR #402` from `Change` -> `CodeModule` (`payments/retry_policy.py`) -> owning `Service` (`payments-api`) -> multi-hop upstream consumers.
- [x] `path_between` (`Q3`) executes `algo.SPpaths` weighted by `weightProp: 'weight'` (`sync=1`, `async=2`), and `load_bearing_services` (`Q4`) executes `algo.pageRank('Service', 'DEPENDS_ON')` ranking `payments-api` as the top bottleneck.
- [x] Querying `legacy-reporting` resolves the entity via `Q0` but returns `0` traversal rows, surfacing the `not_in_graph` epistemic warning rather than claiming zero impact.
- [x] Querying `auth-gateway` is short-circuited by `Q0` and returns `unknown_entity` with zero traversal queries run.
- [x] `accepts(sentence, answer)` deterministically rejects summary sentences containing unreturned hyphenated service tokens or `ADR-\d+` identifiers, falling back to a strict row-derived template and logging the blocked hallucination.
- [x] Automated test suite runs `GRAPH.EXPLAIN` across `Q1`–`Q4` to verify index usage and zero syntax errors.
