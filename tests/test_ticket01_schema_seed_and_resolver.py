"""Verification tests for Ticket 01: Multi-Tenant Schema, Golden Seed & Entity Resolver (Q0)."""

from __future__ import annotations

import pytest

from db.connection import (
    FINTECH_PROD_TENANT,
    SANDBOX_TENANT,
    format_graph_name,
    get_client,
    list_tenant_graphs,
    select_tenant_graph,
)
from db.queries import (
    Q0_RESOLVE_ADR,
    Q0_RESOLVE_CHANGE,
    Q0_RESOLVE_CODE_MODULE,
    Q0_RESOLVE_SERVICE,
    resolve_entity,
)
from db.schema import INDEX_SPECS, ensure_indexes, list_indexes
from db.seed import seed_all


@pytest.fixture(scope="module")
def db_client():
    client = get_client()
    seed_all(client=client, reset=True)
    return client


def test_multi_tenant_graph_isolation_and_listing(db_client):
    assert format_graph_name("fintech-prod") == "codepulse:fintech-prod"
    assert format_graph_name("codepulse:sandbox-tenant") == "codepulse:sandbox-tenant"

    tenant_graphs = list_tenant_graphs(client=db_client)
    assert "codepulse:fintech-prod" in tenant_graphs
    assert "codepulse:sandbox-tenant" in tenant_graphs


def test_all_11_indexes_created_and_idempotent(db_client):
    # Running ensure_indexes a second time must succeed idempotently
    statuses = ensure_indexes(FINTECH_PROD_TENANT, client=db_client)
    assert len(statuses) == 11

    active_indexes = list_indexes(FINTECH_PROD_TENANT, client=db_client)
    indexed_pairs = {
        (row[0], prop) for row in active_indexes for prop in row[1]
    }
    for label, prop in INDEX_SPECS:
        assert (label, prop) in indexed_pairs, f"Missing index on :{label}({prop})"


def test_q0_queries_use_index_scan_via_explain(db_client):
    g = select_tenant_graph(FINTECH_PROD_TENANT, client=db_client)
    for cypher, params in [
        (Q0_RESOLVE_SERVICE, {"name": "payments-api"}),
        (Q0_RESOLVE_CODE_MODULE, {"path": "payments/retry_policy.py"}),
        (Q0_RESOLVE_ADR, {"id": "ADR-014"}),
        (Q0_RESOLVE_CHANGE, {"pr_number": 402}),
    ]:
        plan_lines = g.explain(cypher, params)
        plan_text = "\n".join(str(line) for line in plan_lines)
        assert "Index Scan" in plan_text, f"Expected Index Scan in plan:\n{plan_text}"


def test_golden_seed_topology_and_parallel_edges(db_client):
    g = select_tenant_graph(FINTECH_PROD_TENANT, client=db_client)

    # Verify parallel sync + async edges between ledger-core and payments-api (Limitation #1)
    parallel_rows = g.ro_query(
        """
        MATCH (:Service {name: 'ledger-core'})-[r:DEPENDS_ON]->(:Service {name: 'payments-api'})
        WHERE ID(r) >= 0
        RETURN r.kind AS kind, r.weight AS weight
        ORDER BY r.weight ASC
        """
    ).result_set
    assert parallel_rows == [["sync", 1], ["async", 2]]

    # Verify legacy-reporting exists as a node but has 0 DEPENDS_ON edges
    island_edges = g.ro_query(
        """
        MATCH (s:Service {name: 'legacy-reporting'})-[r:DEPENDS_ON]-()
        WHERE ID(r) >= 0
        RETURN count(r)
        """
    ).result_set
    assert island_edges[0][0] == 0


def test_resolve_entity_known_and_unknown_across_tenants(db_client):
    # 1. Known Service in fintech-prod
    svc = resolve_entity(FINTECH_PROD_TENANT, "Service", "payments-api", client=db_client)
    assert svc["exists"] is True
    assert svc["outcome"] == "resolved"
    assert svc["entity"]["tier"] == 1

    # 2. Disconnected island service still resolves at Q0 (it's in graph, just 0 edges)
    island = resolve_entity(FINTECH_PROD_TENANT, "Service", "legacy-reporting", client=db_client)
    assert island["exists"] is True
    assert island["outcome"] == "resolved"

    # 3. Unknown entity (`auth-gateway`) short-circuits with `unknown_entity`
    unknown = resolve_entity(FINTECH_PROD_TENANT, "Service", "auth-gateway", client=db_client)
    assert unknown["exists"] is False
    assert unknown["outcome"] == "unknown_entity"
    assert unknown["entity"] is None

    # 4. Known CodeModule, ADR, and Change (PR #402)
    mod = resolve_entity(
        FINTECH_PROD_TENANT, "CodeModule", "payments/retry_policy.py", client=db_client
    )
    assert mod["exists"] is True
    assert mod["entity"]["service"] == "payments-api"

    adr = resolve_entity(FINTECH_PROD_TENANT, "ADR", "ADR-014", client=db_client)
    assert adr["exists"] is True
    assert adr["entity"]["status"] == "active"

    pr = resolve_entity(FINTECH_PROD_TENANT, "Change", 402, client=db_client)
    assert pr["exists"] is True
    assert pr["entity"]["author"] == "@dev-dan"

    # 5. Cross-tenant check: `payments-api` does NOT exist in `sandbox-tenant`
    cross_tenant = resolve_entity(SANDBOX_TENANT, "Service", "payments-api", client=db_client)
    assert cross_tenant["exists"] is False
    assert cross_tenant["outcome"] == "unknown_entity"

    sandbox_svc = resolve_entity(SANDBOX_TENANT, "Service", "sandbox-echo-api", client=db_client)
    assert sandbox_svc["exists"] is True
    assert sandbox_svc["outcome"] == "resolved"
