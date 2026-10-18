# 05: Typed CodePulse MCP Server & 3-Agent Orchestrator Loop

**What to build:** Expose the 10 pre-baked, validated Cypher tools (plus tenant graph listing and `resolve_entity` pre-checks) via the CodePulse MCP Server—with zero raw Cypher generation exposed to the LLM—and coordinate the 3 specialized agents (`memory_scout`, `topology_analyst`, `release_governor`) end-to-end with automatic FalkorDB handoffs, persistent memory writes, and post-generation `accepts()` verification.

**Blocked by:**
- 02: Track 01 Service & PR Blast Radius Engine + Epistemic & Hallucination Guard (`Q1`–`Q4`)
- 03: Track 03 Company Brain — GraphRAG Corpus Ingestion, Decision Lineage & On-Call Routing (`Q5`–`Q7`)
- 04: Track 02 Persistent Agent Memory & Cross-Agent Handoff State (`Q8`–`Q10`)

**Status:** ready-for-agent

## MCP Tool Surface (Zero LLM-Written Cypher)

- `recall_memory(tenant_id, service, categories, limit<=10)`
- `blast_radius(tenant_id, service, depth<=4)`
- `pr_impact_radius(tenant_id, pr_number, depth<=4)`
- `path_between(tenant_id, from_service, to_service)`
- `load_bearing_services(tenant_id, limit<=20)`
- `who_to_notify(tenant_id, service, depth<=4)`
- `recent_changes_in_radius(tenant_id, service, depth<=4, hours<=168)`
- `trace_decision_lineage(tenant_id, service)`
- `record_finding(tenant_id, service, category, text, session_id, agent_role)`
- `handoff_state(tenant_id, action, pr_number, ...)`

## Acceptance Criteria

- [ ] CodePulse MCP Server registers only the 10 strongly typed tools (plus tenant listing), enforcing parameter bounds (`depth <= 4`, `limit <= 20`) and running `resolve_entity` (`Q0`) before every read/write tool call.
- [ ] **Agent 1 (`memory_scout`)** runs first on every request, invoking `recall_memory` and `read_handoffs` to surface prior session findings before deep traversals run.
- [ ] **Agent 2 (`topology_analyst`)** executes `pr_impact_radius`, `blast_radius`, `path_between`, and `load_bearing_services`, then records a structured `:Handoff` node in FalkorDB for Agent 3.
- [ ] **Agent 3 (`release_governor`)** reads Agent 2's `:Handoff` from FalkorDB, invokes `trace_decision_lineage` and `who_to_notify`, and writes the synthesized governance verdict back as a `:Finding` via `record_finding`.
- [ ] Every agent response emits a structured execution trace (tool name, validated parameters, latency in ms, row count, epistemic outcome badge, and `accepts()` hallucination guard status `PASS` or `FALLBACK`).
