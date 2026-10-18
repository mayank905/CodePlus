"""TDD Verification Suite for Ticket 05: Typed CodePulse MCP Server & 3-Agent Orchestrator Loop."""

from __future__ import annotations

import pytest

from agents.orchestrator import run_pr_review_workflow, run_service_query_workflow
from db.connection import FINTECH_PROD_TENANT, SANDBOX_TENANT
from mcp_server.server import call_mcp_tool, list_registered_tools


def test_mcp_server_registers_only_typed_tools_and_enforces_bounds(db_client):
    tools = list_registered_tools()
    tool_names = {t["name"] for t in tools}

    expected_domain_tools = {
        "recall_memory",
        "blast_radius",
        "pr_impact_radius",
        "path_between",
        "load_bearing_services",
        "who_to_notify",
        "recent_changes_in_radius",
        "trace_decision_lineage",
        "record_finding",
        "handoff_state",
        "list_tenant_graphs",
        "resolve_entity",
    }
    assert expected_domain_tools == tool_names

    # Zero open-ended Cypher tools exposed to LLM
    forbidden = {"query", "ro_query", "execute_cypher", "run_cypher", "raw_cypher"}
    assert tool_names.isdisjoint(forbidden)

    with pytest.raises(ValueError, match="Unsupported MCP tool"):
        call_mcp_tool("execute_cypher", {"cypher": "MATCH (n) RETURN n"}, client=db_client)

    # Parameter bounds enforcement (depth<=4, limit<=20, hours<=168) + audit_trace metadata
    res = call_mcp_tool(
        "blast_radius",
        {"tenant_id": FINTECH_PROD_TENANT, "service": "payments-api", "depth": 99},
        client=db_client,
    )
    assert res["outcome"] == "answered_with_path"
    trace = res["audit_trace"]
    assert trace["tool_name"] == "blast_radius"
    assert trace["validated_args"]["depth"] == 4
    assert trace["row_count"] >= 5
    assert trace["latency_ms"] >= 0
    assert trace["outcome"] == "answered_with_path"


def test_3_agent_pr_review_workflow_end_to_end(db_client):
    result = run_pr_review_workflow(
        tenant_id=FINTECH_PROD_TENANT,
        pr_number=402,
        session_id="t05-session-1",
        depth=4,
        client=db_client,
    )

    assert result["outcome"] == "answered_with_path"
    assert result["guard_status"] == "PASS"
    assert result["guard_reason"] == "ok"

    # Verify the 3 specialized agents ran in sequence
    agent_Order = [a["agent_role"] for a in result["agent_traces"]]
    assert agent_Order == ["memory_scout", "topology_analyst", "release_governor"]

    # Verify MCP tool audit trace captures every tool call with latency, row_count, and outcome
    executed_tools = [t["tool_name"] for t in result["mcp_traces"]]
    for expected_tool in [
        "recall_memory",
        "handoff_state",
        "pr_impact_radius",
        "blast_radius",
        "path_between",
        "load_bearing_services",
        "trace_decision_lineage",
        "who_to_notify",
        "record_finding",
    ]:
        assert expected_tool in executed_tools

    # Verify handoff was written by topology_analyst and read by release_governor
    handoff_rows = result["handoffs"]["rows"]
    assert any(
        h["from_agent"] == "topology_analyst" and h["to_agent"] == "release_governor"
        for h in handoff_rows
    )

    # Verify ADR-014 and Slack thread #arch-payments were surfaced
    assert "ADR-014" in result["lineage"]["adrs"]
    assert result["target_service"] == "payments-api"
    assert "status-page" in result["impact"]["services"]

    # Verify release_governor persisted a Finding node in FalkorDB
    assert result["finding"]["finding_id"] is not None
    assert result["finding"]["agent_role"] == "release_governor"


def test_orchestrator_hallucination_guard_blocks_ungrounded_draft_and_falls_back(db_client):
    result = run_pr_review_workflow(
        tenant_id=FINTECH_PROD_TENANT,
        pr_number=402,
        session_id="t05-session-guard",
        depth=4,
        simulated_llm_draft=(
            "PR #402 modifies `payments-api` which breaks `billing-v2` and violates `ADR-999`."
        ),
        client=db_client,
    )

    assert result["guard_status"] == "FALLBACK"
    assert "billing-v2" in result["guard_reason"]
    assert "billing-v2" not in result["summary"]
    assert "payments-api" in result["summary"]


def test_service_query_workflow_memory_recall_and_epistemic_outcomes(db_client):
    # 1. Querying payments-api in a fresh session recalls the finding persisted during PR #402 review
    mem_res = run_service_query_workflow(
        tenant_id=FINTECH_PROD_TENANT,
        service="payments-api",
        depth=4,
        session_id="t05-session-2",
        prefer_memory=True,
        client=db_client,
    )
    assert mem_res["outcome"] == "answered_from_memory"
    assert mem_res["guard_status"] == "PASS"
    assert len(mem_res["memory"]["rows"]) >= 1
    assert mem_res["memory"]["rows"][0]["recorded_by"] == "release_governor"

    # 2. Tenant isolation: sandbox-tenant has zero findings for payments-api
    sandbox_mem = call_mcp_tool(
        "recall_memory",
        {"tenant_id": SANDBOX_TENANT, "service": "payments-api"},
        client=db_client,
    )
    assert sandbox_mem["outcome"] in ("not_in_graph", "unknown_entity")
    assert sandbox_mem["rows"] == []

    # 3. Querying disconnected service legacy-reporting returns not_in_graph
    island_res = run_service_query_workflow(
        tenant_id=FINTECH_PROD_TENANT,
        service="legacy-reporting",
        depth=4,
        session_id="t05-session-3",
        prefer_memory=False,
        client=db_client,
    )
    assert island_res["outcome"] == "not_in_graph"
    assert "visibility gap" in island_res["summary"].lower()

    # 4. Querying non-existent service auth-gateway returns unknown_entity with 0 graph traversals
    unknown_res = run_service_query_workflow(
        tenant_id=FINTECH_PROD_TENANT,
        service="auth-gateway",
        depth=4,
        session_id="t05-session-4",
        prefer_memory=False,
        client=db_client,
    )
    assert unknown_res["outcome"] == "unknown_entity"
    assert unknown_res["traversals_executed"] == 0
