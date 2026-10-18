"""TDD Verification Suite for Ticket 02: Track 01 Blast Radius Engine (Q1-Q4) & Hallucination Guard."""

from __future__ import annotations

from db.connection import FINTECH_PROD_TENANT, select_tenant_graph
from db.queries import (
    blast_radius,
    get_q1_blast_radius_cypher,
    Q2_PR_IMPACT_RADIUS_TEMPLATE,
    Q3_PATH_BETWEEN,
    Q4_LOAD_BEARING_SERVICES,
    load_bearing_services,
    path_between,
    pr_impact_radius,
)
from guard import accepts, verify_or_fallback


def test_q1_blast_radius_depth_1_vs_depth_4_and_parallel_edges(db_client):
    # Depth 1 on payments-api: only cart-service and ledger-core (with parallel sync + async edges)
    res_d1 = blast_radius(FINTECH_PROD_TENANT, "payments-api", depth=1, client=db_client)
    assert res_d1["outcome"] == "answered_with_path"
    assert set(res_d1["services"]) == {"cart-service", "ledger-core"}

    # Verify Limitation #1: parallel edges between ledger-core -> payments-api are both returned
    ledger_edge_kinds = [
        row["via_edge_kinds"]
        for row in res_d1["rows"]
        if row["service"] == "ledger-core"
    ]
    assert ["sync"] in ledger_edge_kinds
    assert ["async"] in ledger_edge_kinds

    # Depth 4 on payments-api: reveals checkout-web (2 hops), refunds-worker (2 hops), and status-page (3 hops)
    res_d4 = blast_radius(FINTECH_PROD_TENANT, "payments-api", depth=4, client=db_client)
    assert res_d4["outcome"] == "answered_with_path"
    assert set(res_d4["services"]) == {
        "cart-service",
        "ledger-core",
        "checkout-web",
        "refunds-worker",
        "status-page",
    }
    status_rows = [r for r in res_d4["rows"] if r["service"] == "status-page"]
    assert status_rows
    assert status_rows[0]["hops"] == 3
    assert status_rows[0]["via_nodes"] == [
        "status-page",
        "refunds-worker",
        "ledger-core",
        "payments-api",
    ]


def test_q1_epistemic_outcomes_not_in_graph_and_unknown_entity(db_client):
    # Disconnected service `legacy-reporting` exists in Q0, but has 0 DEPENDS_ON edges -> not_in_graph
    island_res = blast_radius(
        FINTECH_PROD_TENANT, "legacy-reporting", depth=4, client=db_client
    )
    assert island_res["outcome"] == "not_in_graph"
    assert island_res["rows"] == []
    assert "visibility gap" in island_res["warning"].lower()

    # Non-existent service `auth-gateway` is blocked at Q0 -> unknown_entity
    unknown_res = blast_radius(
        FINTECH_PROD_TENANT, "auth-gateway", depth=4, client=db_client
    )
    assert unknown_res["outcome"] == "unknown_entity"
    assert unknown_res["rows"] == []


def test_q2_pr_impact_radius_for_pr_402(db_client):
    res = pr_impact_radius(FINTECH_PROD_TENANT, pr_number=402, depth=4, client=db_client)
    assert res["outcome"] == "answered_with_path"
    assert res["origin_service"] == "payments-api"
    assert "payments/retry_policy.py" in res["touched_modules"]
    assert set(res["services"]) >= {
        "cart-service",
        "ledger-core",
        "checkout-web",
        "refunds-worker",
        "status-page",
    }

    # Unknown PR returns unknown_entity
    unknown_pr = pr_impact_radius(FINTECH_PROD_TENANT, pr_number=9999, depth=4, client=db_client)
    assert unknown_pr["outcome"] == "unknown_entity"


def test_q3_shortest_weighted_path_and_q4_pagerank(db_client):
    path_res = path_between(
        FINTECH_PROD_TENANT,
        from_service="status-page",
        to_service="payments-api",
        client=db_client,
    )
    assert path_res["outcome"] == "answered_with_path"
    assert path_res["hops"] == [
        "status-page",
        "refunds-worker",
        "ledger-core",
        "payments-api",
    ]
    # status-page -(2)-> refunds-worker -(1)-> ledger-core -(1 sync)-> payments-api = total weight 4
    assert path_res["total_weight"] == 4

    pr_res = load_bearing_services(FINTECH_PROD_TENANT, limit=5, client=db_client)
    assert pr_res["outcome"] == "answered_with_path"
    assert len(pr_res["rows"]) <= 5
    # payments-api is the sink of the dependency graph and must be the #1 load-bearing service
    assert pr_res["rows"][0]["service"] == "payments-api"


def test_hallucination_guard_accepts_and_rejects():
    sample_answer = {
        "outcome": "answered_with_path",
        "target_service": "payments-api",
        "services": ["cart-service", "ledger-core", "checkout-web"],
        "paths": [["checkout-web", "cart-service", "payments-api"]],
        "adrs": ["ADR-014"],
    }

    # Valid sentence referencing only graph-backed services and ADRs
    ok, reason = accepts(
        "Changes to payments-api impact cart-service and checkout-web under ADR-014.",
        sample_answer,
    )
    assert ok is True
    assert reason == "ok"

    # Hallucinated service `auth-gateway` must be blocked
    ok_svc, reason_svc = accepts(
        "Changes to payments-api will also break auth-gateway and cart-service.",
        sample_answer,
    )
    assert ok_svc is False
    assert "auth-gateway" in reason_svc

    # Hallucinated ADR `ADR-099` must be blocked
    ok_adr, reason_adr = accepts(
        "payments-api is governed by ADR-014 and ADR-099.",
        sample_answer,
    )
    assert ok_adr is False
    assert "ADR-099" in reason_adr

    # Claiming impact when traversal returned empty (`not_in_graph`) must be blocked
    empty_answer = {
        "outcome": "not_in_graph",
        "target_service": "legacy-reporting",
        "services": [],
        "paths": [],
        "adrs": [],
    }
    ok_empty, reason_empty = accepts(
        "legacy-reporting impacts several downstream billing services.",
        empty_answer,
    )
    assert ok_empty is False
    assert "empty" in reason_empty.lower()

    # Fallback deterministic synthesis when guard blocks a hallucination
    guarded = verify_or_fallback(
        "payments-api breaks auth-gateway in production.",
        sample_answer,
    )
    assert guarded["guard_status"] == "BLOCKED_FALLBACK"
    assert "auth-gateway" not in guarded["final_text"]
    assert "cart-service" in guarded["final_text"]


def test_q1_to_q4_explain_plans_succeed(db_client):
    g = select_tenant_graph(FINTECH_PROD_TENANT, client=db_client)
    q1_cypher = get_q1_blast_radius_cypher(depth=4)
    q2_cypher = Q2_PR_IMPACT_RADIUS_TEMPLATE.format(depth=4)

    for cypher, params in [
        (q1_cypher, {"service": "payments-api"}),
        (q2_cypher, {"pr_number": 402}),
        (Q3_PATH_BETWEEN, {"from_service": "status-page", "to_service": "payments-api"}),
    ]:
        plan = g.explain(cypher, params)
        plan_text = "\n".join(str(line) for line in plan)
        assert "Index Scan" in plan_text, f"Expected Index Scan in plan:\n{plan_text}"

    # Q4 uses CALL algo.pageRank procedure
    q4_plan = "\n".join(str(line) for line in g.explain(Q4_LOAD_BEARING_SERVICES, {"limit": 5}))
    assert "ProcedureCall" in q4_plan
