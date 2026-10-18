# 03: Track 03 Company Brain — GraphRAG Corpus Ingestion, Decision Lineage & On-Call Routing (`Q5`–`Q7`)

**What to build:** Turn unstructured engineering documents (ADRs, incident postmortems, Slack architecture threads) into structured graph knowledge using `GraphRAG-SDK` with a locked ontology linked to canonical `:Service` nodes, and expose the Company Brain queries (`who_to_notify`, `recent_changes_in_radius`, `trace_decision_lineage`) so an engineer or agent can ask *who* to wake, *what* changed recently in the blast radius, and *why* an architectural rule exists.

**Blocked by:** 01: Multi-Tenant FalkorDB Schema, Golden Seed Estate & Entity Resolver (`Q0`)

**Status:** ready-for-agent

## Core Query Constraints

```cypher
// FalkorDB Limitation #4 Rule: Use size([...]) pattern comprehension instead of count() inside comprehension
size([(inc:Incident)-[:AFFECTED]->(upstream) | inc]) AS past_incident_count
```

## Acceptance Criteria

- [ ] Unstructured markdown corpus (`ADR-014.md`, `postmortem-INC-99.md`, `slack-export-arch.md`) is ingested via a locked ontology (`:ADR`, `:SlackThread`, `:Service`, `:Person`) and verified via read-only post-ingestion checks so extracted entities link cleanly to canonical `:Service` nodes.
- [ ] `who_to_notify` (`Q5`) returns owning `:Team`s, Slack channels, active `:ONCALL_FOR` engineers (`from_ts <= $current_ts <= to_ts`), and `past_incident_count` (using `size([...])` comprehension) across the multi-hop blast radius of a target service.
- [ ] `recent_changes_in_radius` (`Q6`) returns all `:Change` commits/PRs touching any `:CodeModule` within `$depth` hops of `$service` since `$since_ts`, ordered newest first with author handle and `via_path`.
- [ ] `trace_decision_lineage` (`Q7`) walks `(:Service)<-[:GOVERNS]-(:ADR {status: 'active'})` along with `:APPROVED` engineers and `:DISCUSSES` `:SlackThread`s, proving why `PR #402` (5000ms retry backoff in `payments-api`) violates `ADR-014` (<500ms fail-fast rule approved by `@sarah-staff-eng` after `INC-99`).
- [ ] `GRAPH.EXPLAIN` verification passes on `Q5`–`Q7`.
