# 04: Track 02 Persistent Agent Memory & Cross-Agent Handoff State (`Q8`–`Q10`)

**What to build:** Persistent cross-session agent memory (`recall_memory`, `record_finding`) and shared multi-agent coordination state (`upsert_handoff`, `read_handoffs`) inside FalkorDB so specialized agents can coordinate on a PR review within a session, recall findings across sessions (`answered_from_memory`), and guarantee strict tenant isolation between `codepulse:fintech-prod` and `codepulse:sandbox-tenant`.

**Blocked by:** 01: Multi-Tenant FalkorDB Schema, Golden Seed Estate & Entity Resolver (`Q0`)

**Status:** done

## Core Query Constraints

```cypher
// FalkorDB Limitation #2 & #3 Rules:
// - Filter with positive IN list (never <>) on category
// - Apply WITH s LIMIT 1 BEFORE eager CREATE clauses
MATCH (s:Service {name: $service})
WITH s LIMIT 1
CREATE (f:Finding {
  id: $finding_id,
  category: $category,
  text: $text,
  at: $at,
  session_id: $session_id,
  agent_role: $agent_role
})-[:ABOUT_SERVICE]->(s)
RETURN f.id AS finding_id, f.at AS recorded_at
```

## Acceptance Criteria

- [x] `record_finding` (`Q9`) persists `:Finding` nodes (`episodic`, `semantic`, `procedural`) linked via `[:ABOUT_SERVICE]` using `WITH s LIMIT 1` before `CREATE` to obey FalkorDB's eager-`LIMIT` rule.
- [x] `recall_memory` (`Q8`) retrieves prior `:Finding` nodes for a service filtered by `f.category IN $categories` (avoiding `<>`), returning `answered_from_memory` with `session_id`, `recorded_by`, and `recorded_at` timestamps.
- [x] `upsert_handoff` (`Q10A`) and `read_handoffs` (`Q10B`) write and read ordered `:Handoff` coordination payloads (`from_agent`, `to_agent`, `status`, `payload`) linked to `:Service` by `pr_number`.
- [x] Multi-tenant isolation test proves a `:Finding` or `:Handoff` written to `codepulse:fintech-prod` is completely invisible when querying `codepulse:sandbox-tenant`.
- [x] `GRAPH.EXPLAIN` verification passes on `Q8`–`Q10`.
