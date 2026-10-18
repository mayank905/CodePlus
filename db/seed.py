"""Golden Seed Dataset (`db/seed.py`) for `codepulse:fintech-prod` and `codepulse:sandbox-tenant`."""

from __future__ import annotations

from typing import Any, Optional

from falkordb import FalkorDB, Graph

from db.connection import FINTECH_PROD_TENANT, SANDBOX_TENANT, get_client, select_tenant_graph
from db.schema import ensure_indexes

# Reference timestamp used across deterministic demo assertions (epoch hours / unix ts)
DEMO_NOW_TS = 1760000000


def clear_graph(graph: Graph) -> None:
    """Remove all nodes and relationships from the given tenant graph while keeping indexes."""
    graph.query("MATCH (n) DETACH DELETE n")


def seed_fintech_prod(
    client: Optional[FalkorDB] = None,
    reset: bool = True,
) -> dict[str, Any]:
    """Seed the Golden FinTech Estate (`codepulse:fintech-prod`) for all 3 Hackathon Tracks."""
    db = client or get_client()
    g = select_tenant_graph(FINTECH_PROD_TENANT, client=db)

    if reset:
        clear_graph(g)
    ensure_indexes(FINTECH_PROD_TENANT, client=db, graph=g)

    # 1. Seed Services (including 4-hop chain + disconnected island `legacy-reporting`)
    services = [
        {"name": "payments-api", "tier": 1, "repo": "org/payments-api", "language": "Python"},
        {"name": "cart-service", "tier": 1, "repo": "org/cart-service", "language": "Go"},
        {"name": "checkout-web", "tier": 1, "repo": "org/checkout-web", "language": "TypeScript"},
        {"name": "ledger-core", "tier": 1, "repo": "org/ledger-core", "language": "Rust"},
        {"name": "refunds-worker", "tier": 2, "repo": "org/refunds-worker", "language": "Python"},
        {"name": "status-page", "tier": 3, "repo": "org/status-page", "language": "TypeScript"},
        {"name": "legacy-reporting", "tier": 3, "repo": "org/legacy-reporting", "language": "Java"},
    ]
    g.query(
        """
        UNWIND $services AS svc
        MERGE (s:Service {name: svc.name})
        SET s.tier = svc.tier,
            s.repo = svc.repo,
            s.language = svc.language
        """,
        {"services": services},
    )

    # 2. Seed DEPENDS_ON edges (including parallel sync + async edges between ledger-core and payments-api)
    # Note: legacy-reporting intentionally has ZERO DEPENDS_ON edges (Demo Move 4: not_in_graph)
    dependencies = [
        {"from": "checkout-web", "to": "cart-service", "kind": "sync", "weight": 1},
        {"from": "cart-service", "to": "payments-api", "kind": "sync", "weight": 1},
        {"from": "ledger-core", "to": "payments-api", "kind": "sync", "weight": 1},
        {"from": "ledger-core", "to": "payments-api", "kind": "async", "weight": 2},
        {"from": "refunds-worker", "to": "ledger-core", "kind": "sync", "weight": 1},
        {"from": "status-page", "to": "refunds-worker", "kind": "async", "weight": 2},
    ]
    g.query(
        """
        UNWIND $deps AS dep
        MATCH (u:Service {name: dep.from}), (d:Service {name: dep.to})
        CREATE (u)-[:DEPENDS_ON {kind: dep.kind, weight: dep.weight}]->(d)
        """,
        {"deps": dependencies},
    )

    # 3. Seed Endpoints & Contracts
    endpoints = [
        {
            "id": "POST /v2/charge",
            "protocol": "gRPC",
            "schema_hash": "sha256:9f8a1b",
            "exposing_service": "payments-api",
        },
        {
            "id": "topic://payments.events.v1",
            "protocol": "Kafka",
            "schema_hash": "sha256:3c4d2e",
            "exposing_service": "payments-api",
        },
        {
            "id": "POST /v1/cart/checkout",
            "protocol": "REST",
            "schema_hash": "sha256:7b2e11",
            "exposing_service": "cart-service",
        },
    ]
    g.query(
        """
        UNWIND $endpoints AS ep
        MATCH (s:Service {name: ep.exposing_service})
        MERGE (e:Endpoint {id: ep.id})
        SET e.protocol = ep.protocol,
            e.schema_hash = ep.schema_hash
        MERGE (s)-[:EXPOSES]->(e)
        """,
        {"endpoints": endpoints},
    )

    consumers = [
        {"service": "cart-service", "endpoint": "POST /v2/charge", "criticality": "hard"},
        {"service": "ledger-core", "endpoint": "POST /v2/charge", "criticality": "hard"},
        {"service": "ledger-core", "endpoint": "topic://payments.events.v1", "criticality": "soft"},
        {"service": "checkout-web", "endpoint": "POST /v1/cart/checkout", "criticality": "hard"},
    ]
    g.query(
        """
        UNWIND $consumers AS c
        MATCH (s:Service {name: c.service}), (e:Endpoint {id: c.endpoint})
        CREATE (s)-[:CONSUMES {criticality: c.criticality}]->(e)
        """,
        {"consumers": consumers},
    )

    # 4. Seed CodeModules
    modules = [
        {"path": "payments/retry_policy.py", "complexity": 24, "service": "payments-api"},
        {"path": "payments/grpc_handler.py", "complexity": 18, "service": "payments-api"},
        {"path": "cart/checkout_client.go", "complexity": 15, "service": "cart-service"},
        {"path": "ledger/reconcile.rs", "complexity": 31, "service": "ledger-core"},
        {"path": "refunds/worker_loop.py", "complexity": 12, "service": "refunds-worker"},
    ]
    g.query(
        """
        UNWIND $modules AS mod
        MATCH (s:Service {name: mod.service})
        MERGE (m:CodeModule {path: mod.path})
        SET m.complexity = mod.complexity
        MERGE (m)-[:PART_OF]->(s)
        """,
        {"modules": modules},
    )

    # 5. Seed Teams & People (Track 03 Company Brain)
    teams = [
        {
            "name": "Core Payments",
            "slack_channel": "#team-core-payments",
            "services": ["payments-api", "ledger-core"],
        },
        {
            "name": "Checkout Experience",
            "slack_channel": "#team-checkout",
            "services": ["checkout-web", "cart-service"],
        },
        {
            "name": "Platform Reliability",
            "slack_channel": "#team-reliability",
            "services": ["refunds-worker", "status-page", "legacy-reporting"],
        },
    ]
    g.query(
        """
        UNWIND $teams AS t
        MERGE (team:Team {name: t.name})
        SET team.slack_channel = t.slack_channel
        WITH team, t
        UNWIND t.services AS svc_name
        MATCH (s:Service {name: svc_name})
        MERGE (team)-[:OWNS]->(s)
        """,
        {"teams": teams},
    )

    people = [
        {
            "handle": "@sarah-staff-eng",
            "name": "Sarah Jenkins",
            "role": "Staff Principal Engineer",
            "team": "Core Payments",
            "oncall_from": DEMO_NOW_TS - 86400 * 7,
            "oncall_to": DEMO_NOW_TS + 86400 * 7,
        },
        {
            "handle": "@alex-checkout-oncall",
            "name": "Alex Rivera",
            "role": "Senior Engineer",
            "team": "Checkout Experience",
            "oncall_from": DEMO_NOW_TS - 86400 * 7,
            "oncall_to": DEMO_NOW_TS + 86400 * 7,
        },
        {
            "handle": "@priya-sre-oncall",
            "name": "Priya Nair",
            "role": "Staff SRE",
            "team": "Platform Reliability",
            "oncall_from": DEMO_NOW_TS - 86400 * 7,
            "oncall_to": DEMO_NOW_TS + 86400 * 7,
        },
        {
            "handle": "@dev-dan",
            "name": "Dan Miller",
            "role": "Product Engineer",
            "team": "Core Payments",
            "oncall_from": 0,
            "oncall_to": 0,
        },
    ]
    g.query(
        """
        UNWIND $people AS p
        MATCH (team:Team {name: p.team})
        MERGE (person:Person {handle: p.handle})
        SET person.name = p.name,
            person.role = p.role
        MERGE (person)-[:MEMBER_OF]->(team)
        WITH person, team, p
        WHERE p.oncall_to > 0
        CREATE (person)-[:ONCALL_FOR {from_ts: p.oncall_from, to_ts: p.oncall_to}]->(team)
        """,
        {"people": people},
    )

    # 6. Seed Changes / PRs (PR #402 is the hero demo breaking PR)
    changes = [
        {
            "sha": "a1b2c3d402",
            "pr_number": 402,
            "at": DEMO_NOW_TS - 1800,
            "author": "@dev-dan",
            "summary": "Bump payments-api synchronous retry backoff from 100ms to 5000ms",
            "status": "open",
            "module_path": "payments/retry_policy.py",
            "lines_changed": 42,
            "breaking_schema": True,
            "endpoint": "POST /v2/charge",
        },
        {
            "sha": "f9e8d7c398",
            "pr_number": 398,
            "at": DEMO_NOW_TS - 14400,
            "author": "@sarah-staff-eng",
            "summary": "Add idempotency key validation in ledger reconciliation",
            "status": "merged",
            "module_path": "ledger/reconcile.rs",
            "lines_changed": 68,
            "breaking_schema": False,
            "endpoint": "",
        },
        {
            "sha": "c4d5e6f401",
            "pr_number": 401,
            "at": DEMO_NOW_TS - 7200,
            "author": "@alex-checkout-oncall",
            "summary": "Tighten cart checkout gRPC client deadline to 600ms",
            "status": "merged",
            "module_path": "cart/checkout_client.go",
            "lines_changed": 19,
            "breaking_schema": False,
            "endpoint": "POST /v1/cart/checkout",
        },
    ]
    g.query(
        """
        UNWIND $changes AS ch
        MATCH (m:CodeModule {path: ch.module_path})
        MATCH (author:Person {handle: ch.author})
        MERGE (c:Change {sha: ch.sha})
        SET c.pr_number = ch.pr_number,
            c.at = ch.at,
            c.author = ch.author,
            c.summary = ch.summary,
            c.status = ch.status
        CREATE (c)-[:TOUCHED {lines_changed: ch.lines_changed, breaking_schema: ch.breaking_schema}]->(m)
        MERGE (author)-[:AUTHORED]->(c)
        WITH c, ch
        WHERE ch.endpoint <> ''
        MATCH (e:Endpoint {id: ch.endpoint})
        MERGE (c)-[:MODIFIED_CONTRACT]->(e)
        """,
        {"changes": changes},
    )

    # 7. Seed Incidents, ADRs, and SlackThreads (Track 03 Decision Lineage)
    g.query(
        """
        MERGE (inc:Incident {id: 'INC-99'})
        SET inc.at = $inc_at,
            inc.severity = 'SEV-1',
            inc.summary = 'Checkout cascading thread-pool exhaustion caused by 5s retry storm in payments-api'
        WITH inc
        MATCH (pay:Service {name: 'payments-api'}),
              (cart:Service {name: 'cart-service'}),
              (chk:Service {name: 'checkout-web'})
        CREATE (inc)-[:AFFECTED {root_cause: true}]->(pay)
        CREATE (inc)-[:AFFECTED {root_cause: false}]->(cart)
        CREATE (inc)-[:AFFECTED {root_cause: false}]->(chk)
        """,
        {"inc_at": DEMO_NOW_TS - 86400 * 30},
    )

    g.query(
        """
        MATCH (pay:Service {name: 'payments-api'}),
              (sarah:Person {handle: '@sarah-staff-eng'}),
              (priya:Person {handle: '@priya-sre-oncall'})
        MERGE (adr:ADR {id: 'ADR-014'})
        SET adr.title = 'Fail-Fast Synchronous Payment Retries (<500ms Ceiling)',
            adr.decision = 'Tier-1 synchronous payment paths must fail fast under 500ms to prevent checkout thread-pool exhaustion. Any retry >500ms must be offloaded to async Kafka.',
            adr.decided_at = $adr_at,
            adr.status = 'active'
        CREATE (adr)-[:GOVERNS]->(pay)
        CREATE (sarah)-[:APPROVED]->(adr)
        CREATE (priya)-[:APPROVED]->(adr)
        MERGE (st:SlackThread {id: 'SLACK-ARCH-881'})
        SET st.channel = '#arch-payments',
            st.at = $adr_at,
            st.summary = 'Post-INC-99 consensus: never allow >500ms sync retry in payments/retry_policy.py or checkout-web worker pools saturate within 12 seconds.',
            st.url = 'https://fintech.slack.com/archives/C01ARCHPAY/p1757400000'
        CREATE (st)-[:DISCUSSES]->(adr)
        CREATE (st)-[:DISCUSSES]->(pay)
        """,
        {"adr_at": DEMO_NOW_TS - 86400 * 28},
    )

    counts = g.ro_query(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY label"
    ).result_set
    return {
        "tenant_id": FINTECH_PROD_TENANT,
        "graph_name": g.name,
        "node_counts": {row[0]: row[1] for row in counts},
    }


def seed_sandbox_tenant(
    client: Optional[FalkorDB] = None,
    reset: bool = True,
) -> dict[str, Any]:
    """Seed an isolated secondary tenant (`codepulse:sandbox-tenant`) to prove zero cross-tenant leakage."""
    db = client or get_client()
    g = select_tenant_graph(SANDBOX_TENANT, client=db)

    if reset:
        clear_graph(g)
    ensure_indexes(SANDBOX_TENANT, client=db, graph=g)

    g.query(
        """
        CREATE (web:Service {name: 'sandbox-portal', tier: 2, repo: 'sandbox/portal', language: 'TypeScript'})
        CREATE (api:Service {name: 'sandbox-echo-api', tier: 2, repo: 'sandbox/echo-api', language: 'Python'})
        CREATE (web)-[:DEPENDS_ON {kind: 'sync', weight: 1}]->(api)
        CREATE (t:Team {name: 'Sandbox Team', slack_channel: '#sandbox-dev'})-[:OWNS]->(api)
        CREATE (p:Person {handle: '@sandbox-bot', name: 'Sandbox Bot', role: 'Automation'})-[:MEMBER_OF]->(t)
        """
    )

    counts = g.ro_query(
        "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count ORDER BY label"
    ).result_set
    return {
        "tenant_id": SANDBOX_TENANT,
        "graph_name": g.name,
        "node_counts": {row[0]: row[1] for row in counts},
    }


def seed_all(client: Optional[FalkorDB] = None, reset: bool = True) -> dict[str, Any]:
    """Seed both `codepulse:fintech-prod` and `codepulse:sandbox-tenant`."""
    db = client or get_client()
    return {
        FINTECH_PROD_TENANT: seed_fintech_prod(client=db, reset=reset),
        SANDBOX_TENANT: seed_sandbox_tenant(client=db, reset=reset),
    }


if __name__ == "__main__":
    summary = seed_all(reset=True)
    print("Seeded CodePulse multi-tenant graphs:")
    for tenant, info in summary.items():
        print(f"  - {info['graph_name']}: {info['node_counts']}")
