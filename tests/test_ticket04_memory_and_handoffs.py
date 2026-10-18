"""TDD Verification Suite for Ticket 04: Track 02 Persistent Agent Memory & Cross-Agent Handoffs (Q8-Q10)."""

from __future__ import annotations

import pytest

from db.connection import FINTECH_PROD_TENANT, SANDBOX_TENANT, select_tenant_graph
from db.queries import (
    Q8_RECALL_MEMORY,
    Q9_RECORD_FINDING,
    Q10A_UPSERT_HANDOFF,
    Q10B_READ_HANDOFFS,
    read_handoffs,
    recall_memory,
    record_finding,
    upsert_handoff,
)
from db.seed import DEMO_NOW_TS


def test_q9_record_and_q8_recall_memory_with_categories(db_client):
    # Initially no findings exist on payments-api -> returns not_in_graph
    initial_recall = recall_memory(
        FINTECH_PROD_TENANT,
        service="payments-api",
        categories=["episodic", "semantic", "procedural"],
        limit=5,
        client=db_client,
    )
    assert initial_recall["outcome"] == "not_in_graph"
    assert initial_recall["rows"] == []

    # Record an episodic and a semantic finding via Q9 (obeying WITH s LIMIT 1 before CREATE)
    written_1 = record_finding(
        FINTECH_PROD_TENANT,
        service="payments-api",
        category="episodic",
        text="PR #402 bumped retry backoff to 5000ms in payments/retry_policy.py, violating ADR-014.",
        session_id="sess-demo-01",
        agent_role="release_governor",
        finding_id="FIND-402-EP",
        at=DEMO_NOW_TS + 10,
        client=db_client,
    )
    assert written_1["outcome"] == "answered_from_memory"
    assert written_1["finding_id"] == "FIND-402-EP"

    written_2 = record_finding(
        FINTECH_PROD_TENANT,
        service="payments-api",
        category="procedural",
        text="Always page @sarah-staff-eng and @priya-sre-oncall before merging sync retry changes.",
        session_id="sess-demo-01",
        agent_role="release_governor",
        finding_id="FIND-402-PROC",
        at=DEMO_NOW_TS + 20,
        client=db_client,
    )
    assert written_2["finding_id"] == "FIND-402-PROC"

    # Recall all categories -> returns answered_from_memory ordered newest first
    recalled_all = recall_memory(
        FINTECH_PROD_TENANT,
        service="payments-api",
        categories=["episodic", "semantic", "procedural"],
        limit=10,
        client=db_client,
    )
    assert recalled_all["outcome"] == "answered_from_memory"
    assert len(recalled_all["rows"]) == 2
    assert recalled_all["rows"][0]["finding_id"] == "FIND-402-PROC"
    assert recalled_all["rows"][1]["finding_id"] == "FIND-402-EP"

    # Filter strictly by category IN ['episodic'] (Limitation #3: positive IN list, no <>)
    recalled_episodic = recall_memory(
        FINTECH_PROD_TENANT,
        service="payments-api",
        categories=["episodic"],
        limit=10,
        client=db_client,
    )
    assert len(recalled_episodic["rows"]) == 1
    assert recalled_episodic["rows"][0]["category"] == "episodic"


def test_q10_upsert_and_read_handoffs_across_agents(db_client):
    h1 = upsert_handoff(
        FINTECH_PROD_TENANT,
        pr_number=402,
        service="payments-api",
        from_agent="memory_scout",
        to_agent="topology_analyst",
        status="completed",
        payload='{"prior_findings": 2}',
        handoff_id="HO-402-1",
        at=DEMO_NOW_TS + 30,
        client=db_client,
    )
    assert h1["outcome"] == "answered_with_path"
    assert h1["handoff_id"] == "HO-402-1"

    h2 = upsert_handoff(
        FINTECH_PROD_TENANT,
        pr_number=402,
        service="payments-api",
        from_agent="topology_analyst",
        to_agent="release_governor",
        status="ready_for_governance",
        payload='{"impacted_count": 5, "top_bottleneck": "payments-api"}',
        handoff_id="HO-402-2",
        at=DEMO_NOW_TS + 40,
        client=db_client,
    )
    assert h2["handoff_id"] == "HO-402-2"

    read_res = read_handoffs(FINTECH_PROD_TENANT, pr_number=402, client=db_client)
    assert read_res["outcome"] == "answered_with_path"
    assert [r["handoff_id"] for r in read_res["rows"]] == ["HO-402-1", "HO-402-2"]
    assert read_res["rows"][1]["from_agent"] == "topology_analyst"
    assert read_res["rows"][1]["to_agent"] == "release_governor"


def test_multi_tenant_memory_and_handoff_isolation(db_client):
    # Querying `sandbox-tenant` for `payments-api` returns `unknown_entity`
    sandbox_recall = recall_memory(
        SANDBOX_TENANT,
        service="payments-api",
        categories=["episodic", "semantic", "procedural"],
        limit=10,
        client=db_client,
    )
    assert sandbox_recall["outcome"] == "unknown_entity"
    assert sandbox_recall["rows"] == []

    # Querying `sandbox-tenant` for PR #402 handoffs returns `unknown_entity` (zero cross-tenant leakage)
    sandbox_handoffs = read_handoffs(SANDBOX_TENANT, pr_number=402, client=db_client)
    assert sandbox_handoffs["outcome"] == "unknown_entity"
    assert sandbox_handoffs["rows"] == []

    # Writing a finding to `sandbox-echo-api` in `sandbox-tenant` does not leak into `fintech-prod`
    sandbox_write = record_finding(
        SANDBOX_TENANT,
        service="sandbox-echo-api",
        category="semantic",
        text="Sandbox echo API is isolated for testing.",
        session_id="sess-sandbox-01",
        agent_role="memory_scout",
        finding_id="FIND-SBX-01",
        at=DEMO_NOW_TS + 50,
        client=db_client,
    )
    assert sandbox_write["outcome"] == "answered_from_memory"

    sbx_recall = recall_memory(
        SANDBOX_TENANT,
        service="sandbox-echo-api",
        categories=["semantic"],
        limit=5,
        client=db_client,
    )
    assert sbx_recall["outcome"] == "answered_from_memory"
    assert len(sbx_recall["rows"]) == 1


def test_q8_to_q10_explain_plans_and_limitation_compliance(db_client):
    # Verify Limitation #2 (WITH s LIMIT 1 before CREATE) and Limitation #3 (no <> operator)
    assert "WITH s LIMIT 1" in Q9_RECORD_FINDING
    assert "WITH s LIMIT 1" in Q10A_UPSERT_HANDOFF
    assert "<>" not in Q8_RECALL_MEMORY

    g = select_tenant_graph(FINTECH_PROD_TENANT, client=db_client)
    for cypher, params in [
        (
            Q8_RECALL_MEMORY,
            {
                "service": "payments-api",
                "categories": ["episodic", "semantic", "procedural"],
                "limit": 5,
            },
        ),
        (
            Q9_RECORD_FINDING,
            {
                "service": "payments-api",
                "finding_id": "EXPLAIN-1",
                "category": "episodic",
                "text": "test",
                "at": DEMO_NOW_TS,
                "session_id": "s1",
                "agent_role": "memory_scout",
            },
        ),
        (
            Q10A_UPSERT_HANDOFF,
            {
                "service": "payments-api",
                "handoff_id": "EXPLAIN-HO-1",
                "pr_number": 402,
                "from_agent": "memory_scout",
                "to_agent": "topology_analyst",
                "status": "ok",
                "payload": "{}",
                "at": DEMO_NOW_TS,
            },
        ),
        (Q10B_READ_HANDOFFS, {"pr_number": 402}),
    ]:
        plan = g.explain(cypher, params)
        plan_text = "\n".join(str(line) for line in plan)
        assert "Index Scan" in plan_text, f"Expected Index Scan in plan:\n{plan_text}"
