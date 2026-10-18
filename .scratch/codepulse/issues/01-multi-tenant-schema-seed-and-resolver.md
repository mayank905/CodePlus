# 01: Multi-Tenant FalkorDB Schema, Golden Seed Estate & Entity Resolver (`Q0`)

**What to build:** Establish the multi-tenant FalkorDB graph foundation (`codepulse:{tenant_id}`), provision all label-property indexes, seed the deterministic Golden FinTech Estate (`codepulse:fintech-prod`) and isolated secondary tenant (`codepulse:sandbox-tenant`), and expose the mandatory `resolve_entity` pre-check (`Q0`) that validates whether a `Service`, `CodeModule`, `ADR`, or `Change` exists in the active tenant's graph before any traversal can execute.

**Blocked by:** None (can start immediately)

**Status:** done

## Schema & Index Decisions

```cypher
// Node Labels & Primary Keys
(:Service {name, tier, repo, language})
(:Endpoint {id, protocol, schema_hash})
(:CodeModule {path, complexity})
(:Change {sha, pr_number, at, author, summary, status})
(:Incident {id, at, severity, summary})
(:Team {name, slack_channel})
(:Person {handle, name, role})
(:ADR {id, title, decision, decided_at, status})
(:SlackThread {id, channel, at, summary, url})
(:Finding {id, category, text, at, session_id, agent_role})
(:Handoff {id, pr_number, from_agent, to_agent, status, payload, at})
```

## Acceptance Criteria

- [x] Multi-tenant graph selector connects to FalkorDB and isolates operations strictly by `codepulse:{tenant_id}`.
- [x] All 11 label-property indexes (`Service.name`, `Service.tier`, `CodeModule.path`, `Endpoint.id`, `Team.name`, `Person.handle`, `Change.pr_number`, `Change.at`, `ADR.id`, `Finding.at`, `Handoff.pr_number`) are created idempotently and verified via `GRAPH.EXPLAIN`.
- [x] Golden seed populates `codepulse:fintech-prod` with the 4-hop topology (`status-page -> refunds-worker -> ledger-core -> payments-api` and `checkout-web -> cart-service -> payments-api`), parallel `sync` (weight 1) and `async` (weight 2) `:DEPENDS_ON` edges between `ledger-core` and `payments-api`, disconnected island `legacy-reporting`, `PR #402` touching `payments/retry_policy.py`, `Incident INC-99`, `ADR-014` approved by `@sarah-staff-eng`, and `#arch-payments` Slack thread.
- [x] `codepulse:sandbox-tenant` is seeded with an isolated minimal topology to prove zero cross-tenant leakage.
- [x] `resolve_entity` (`Q0`) runs via read-only query (`ro_query`) for `Service`, `CodeModule`, `ADR`, and `Change`, returning canonical metadata when present and `unknown_entity` (`None`) for unmodelled entities like `auth-gateway` without executing downstream queries.
