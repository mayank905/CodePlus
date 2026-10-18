# 06: Three-Panel Explainable War-Room UI & 3-Minute Judge Demo Harness

**What to build:** An interactive 3-panel War-Room web application that lets an engineer or hackathon judge switch tenants, trigger PR reviews or architecture queries, inspect live multi-hop blast radius paths and epistemic outcome badges, and audit every typed MCP tool call and hallucination guard verdict in real time.

**Blocked by:** 05: Typed CodePulse MCP Server & 3-Agent Orchestrator Loop

**Status:** done

## Acceptance Criteria

- [x] **Left Panel (Controls & Triggers):** Provides a tenant selector (`codepulse:fintech-prod` vs `codepulse:sandbox-tenant`), depth toggle (`depth=1` vs `depth=4`), one-click demo triggers for the 4 Judge Demo Moves (including **PR #402 Review**), and free-form service/architecture query input.
- [x] **Center Panel (Visual Path & Epistemic Outcome):** Renders the discovered multi-hop dependency paths (`status-page -> refunds-worker -> ledger-core -> payments-api`) with `sync`/`async` edge badges, `algo.pageRank` load-bearing scores, ADR violations, on-call responders, and a prominent outcome badge (`Answered with Path`, `Answered from Memory`, `Not in Graph`, `Unknown Entity`).
- [x] **Right Panel (Live MCP & Guard Audit Trace):** Streams each executed MCP tool call with its validated arguments, execution latency in ms, number of rows returned from FalkorDB, agent handoff transitions (`memory_scout -> topology_analyst -> release_governor`), and Hallucination Guard verification badge (`PASS` vs `BLOCKED + FALLBACK`).
- [x] All 4 moves of the 3-Minute Winning Demo Script execute cleanly from the UI:
  1. **Move 1:** `payments-api` blast radius at `depth=1` (2 services) vs `depth=4` (reveals `status-page` 3 hops away + PageRank #1 bottleneck).
  2. **Move 2:** **PR #402** review traces `payments/retry_policy.py` -> `payments-api` -> `ADR-014` violation + `#arch-payments` Slack context + on-call notification list.
  3. **Move 3:** Fresh session query on `payments-api` surfaces the persisted `:Finding` (`Answered from Memory`), and switching to `codepulse:sandbox-tenant` proves complete multigraph isolation.
  4. **Move 4:** Querying `legacy-reporting` returns `Not in Graph` (visibility gap warning) and querying `auth-gateway` returns `Unknown Entity` (blocked by `resolve_entity` with 0 traversals executed).
