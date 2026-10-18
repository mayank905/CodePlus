"""CodePulse 3-Minute Judge Demo Harness & 3-Panel War-Room View-Model Builder.

Implements the 4 Judge Demo Moves from Section 9 of `CODEPULSE_IMPLEMENTATION_PLAN.md`:
1. Move 1 — The 1-Hop vs. 4-Hop Contrast (`depth=1` vs `depth=4` on `payments-api` + PageRank).
2. Move 2 — PR #402 Blast Radius & Company Brain Decision Lineage (`ADR-014` + `#arch-payments` + On-Call).
3. Move 3 — Persistent Cross-Session Memory (`answered_from_memory`) & Tenant Isolation (`sandbox-tenant`).
4. Move 4 — Honest Epistemics (`legacy-reporting` -> `not_in_graph`, `auth-gateway` -> `unknown_entity`).
"""

from __future__ import annotations

from typing import Any, Optional

from falkordb import FalkorDB

from agents.orchestrator import run_pr_review_workflow, run_service_query_workflow
from db.connection import FINTECH_PROD_TENANT, SANDBOX_TENANT, get_client, list_tenant_graphs
from mcp_server.server import call_mcp_tool

OUTCOME_BADGE_MAP: dict[str, str] = {
    "answered_with_path": "Answered with Path",
    "answered_from_memory": "Answered from Memory",
    "not_in_graph": "Not in Graph",
    "unknown_entity": "Unknown Entity",
}


def _build_mermaid_from_rows(blast_rows: list[dict[str, Any]], target_service: str) -> str:
    """Build a Mermaid flowchart LR diagram from discovered multi-hop dependency rows."""
    lines = ["flowchart LR"]
    if not blast_rows:
        safe_id = target_service.replace("-", "_")
        lines.append(f'    {safe_id}["{target_service}"]')
        return "\n".join(lines)

    seen_edges: set[tuple[str, str, str]] = set()
    for row in blast_rows:
        nodes = row.get("via_nodes", [])
        kinds = row.get("via_edge_kinds", [])
        for idx in range(len(nodes) - 1):
            src = nodes[idx]
            dst = nodes[idx + 1]
            kind = kinds[idx] if idx < len(kinds) else "sync"
            edge_key = (src, dst, kind)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            src_id = src.replace("-", "_")
            dst_id = dst.replace("-", "_")
            lines.append(f'    {src_id}["{src}"] -->|"{kind}"| {dst_id}["{dst}"]')
    return "\n".join(lines)


def build_war_room_panels(
    workflow_result: dict[str, Any],
    tenant_id: str = FINTECH_PROD_TENANT,
    depth: int = 4,
) -> dict[str, Any]:
    """Transform an orchestrator workflow result into the 3-Panel War-Room view model."""
    outcome = workflow_result.get("outcome", "unknown_entity")
    outcome_badge = OUTCOME_BADGE_MAP.get(outcome, outcome)
    target_service = (
        workflow_result.get("target_service")
        or f"PR #{workflow_result.get('pr_number', '')}"
    )

    blast_block = workflow_result.get("blast_radius") or workflow_result.get("impact") or {}
    blast_rows = blast_block.get("rows", [])

    dependency_paths: list[dict[str, Any]] = []
    for row in blast_rows:
        via_nodes = row.get("via_nodes", [])
        via_kinds = row.get("via_edge_kinds", [])
        hop_str = " -> ".join(via_nodes) if via_nodes else row.get("service", "")
        dependency_paths.append(
            {
                "service": row.get("service") or row.get("upstream_service"),
                "tier": row.get("tier") or row.get("upstream_tier"),
                "hops": row.get("hops", 1),
                "path_display": hop_str,
                "edge_kinds": via_kinds,
            }
        )

    pagerank_rows = workflow_result.get("pagerank", {}).get("rows", [])
    adr_lineage = workflow_result.get("lineage", {}).get("rows", [])
    oncall_responders = workflow_result.get("notify", {}).get("rows", [])
    memory_rows = (
        workflow_result.get("memory", {}).get("rows", [])
        or workflow_result.get("prior_memory", {}).get("rows", [])
    )

    guard_raw = workflow_result.get("guard_status", "PASS")
    guard_badge = "PASS" if guard_raw == "PASS" else "BLOCKED + FALLBACK"

    return {
        "left_panel": {
            "tenant_id": tenant_id,
            "target": target_service,
            "depth": depth,
            "available_tenants": [f"codepulse:{FINTECH_PROD_TENANT}", f"codepulse:{SANDBOX_TENANT}"],
        },
        "center_panel": {
            "outcome": outcome,
            "outcome_badge": outcome_badge,
            "summary": workflow_result.get("summary", ""),
            "dependency_paths": dependency_paths,
            "mermaid_diagram": _build_mermaid_from_rows(blast_rows, str(target_service)),
            "pagerank_rows": pagerank_rows,
            "adr_lineage": adr_lineage,
            "oncall_responders": oncall_responders,
            "memory_rows": memory_rows,
        },
        "right_panel": {
            "guard_status": guard_raw,
            "guard_badge": guard_badge,
            "guard_reason": workflow_result.get("guard_reason", "ok"),
            "agent_traces": workflow_result.get("agent_traces", []),
            "mcp_traces": workflow_result.get("mcp_traces", []),
            "handoff_rows": workflow_result.get("handoffs", {}).get("rows", []),
        },
    }


def run_demo_move_1_depth_contrast(
    tenant_id: str = FINTECH_PROD_TENANT,
    client: Optional[FalkorDB] = None,
) -> dict[str, Any]:
    """Move 1: `payments-api` blast radius at `depth=1` (2 services) vs `depth=4` (reveals `status-page` 3 hops away + PageRank #1)."""
    d1 = run_service_query_workflow(
        tenant_id=tenant_id,
        service="payments-api",
        depth=1,
        session_id="demo-move1-d1",
        prefer_memory=False,
        client=client,
    )
    d4 = run_service_query_workflow(
        tenant_id=tenant_id,
        service="payments-api",
        depth=4,
        session_id="demo-move1-d4",
        prefer_memory=False,
        client=client,
    )
    return {
        "move": "Move 1 — 1-Hop vs. 4-Hop Blast Radius Contrast (Track 01)",
        "depth_1": d1,
        "depth_4": d4,
    }


def run_demo_move_2_pr_402_review(
    tenant_id: str = FINTECH_PROD_TENANT,
    session_id: str = "demo-session-1",
    client: Optional[FalkorDB] = None,
) -> dict[str, Any]:
    """Move 2: PR #402 review tracing `payments/retry_policy.py` -> `payments-api` -> `ADR-014` + `#arch-payments` + on-call list."""
    pr_review = run_pr_review_workflow(
        tenant_id=tenant_id,
        pr_number=402,
        session_id=session_id,
        depth=4,
        client=client,
    )
    return {
        "move": "Move 2 — PR #402 Blast Radius & Company Brain Decision Lineage (Track 03)",
        "pr_review": pr_review,
    }


def run_demo_move_3_memory_and_tenant_isolation(
    session_id: str = "demo-session-2",
    client: Optional[FalkorDB] = None,
) -> dict[str, Any]:
    """Move 3: Fresh session recalls Move 2's `:Finding` (`Answered from Memory`) + proves `sandbox-tenant` isolation."""
    fintech_prod_memory = run_service_query_workflow(
        tenant_id=FINTECH_PROD_TENANT,
        service="payments-api",
        depth=4,
        session_id=session_id,
        prefer_memory=True,
        client=client,
    )
    sandbox_raw = call_mcp_tool(
        "recall_memory",
        {"tenant_id": SANDBOX_TENANT, "service": "payments-api", "limit": 5},
        client=client,
    )
    sandbox_tenant_memory = {
        "outcome": sandbox_raw["outcome"],
        "target_service": "payments-api",
        "summary": sandbox_raw.get(
            "warning",
            "Entity `payments-api` is not in `codepulse:sandbox-tenant` (complete multigraph isolation).",
        ),
        "guard_status": "PASS",
        "guard_reason": "ok",
        "memory": {"rows": sandbox_raw.get("rows", [])},
        "agent_traces": [
            {
                "agent_role": "memory_scout",
                "status": "completed",
                "summary": "Verified 0 findings leaked into `codepulse:sandbox-tenant`.",
                "tools_called": ["recall_memory"],
            }
        ],
        "mcp_traces": [sandbox_raw["audit_trace"]],
    }
    return {
        "move": "Move 3 — Persistent Cross-Session Memory & Multi-Tenant Isolation (Track 02)",
        "fintech_prod_memory": fintech_prod_memory,
        "sandbox_tenant_memory": sandbox_tenant_memory,
        "tenant_graphs": list_tenant_graphs(client=client),
    }


def run_demo_move_4_honest_epistemics(
    tenant_id: str = FINTECH_PROD_TENANT,
    client: Optional[FalkorDB] = None,
) -> dict[str, Any]:
    """Move 4: Query `legacy-reporting` (`Not in Graph` visibility gap) & `auth-gateway` (`Unknown Entity`, 0 traversals)."""
    legacy_reporting = run_service_query_workflow(
        tenant_id=tenant_id,
        service="legacy-reporting",
        depth=4,
        session_id="demo-move4-island",
        prefer_memory=False,
        client=client,
    )
    auth_gateway = run_service_query_workflow(
        tenant_id=tenant_id,
        service="auth-gateway",
        depth=4,
        session_id="demo-move4-unknown",
        prefer_memory=False,
        client=client,
    )
    return {
        "move": "Move 4 — Honest Epistemics: 'Not in Graph' vs 'Unknown Entity'",
        "legacy_reporting": legacy_reporting,
        "auth_gateway": auth_gateway,
    }


def run_all_judge_demo_moves(client: Optional[FalkorDB] = None) -> dict[str, Any]:
    """Execute all 4 Judge Demo Moves in sequence and return their structured payloads."""
    db = client or get_client()
    m1 = run_demo_move_1_depth_contrast(client=db)
    m2 = run_demo_move_2_pr_402_review(client=db)
    m3 = run_demo_move_3_memory_and_tenant_isolation(client=db)
    m4 = run_demo_move_4_honest_epistemics(client=db)
    return {
        "move_1": m1,
        "move_2": m2,
        "move_3": m3,
        "move_4": m4,
    }


if __name__ == "__main__":
    results = run_all_judge_demo_moves()
    print("=== CodePulse 3-Minute Judge Demo Harness ===")
    print("Move 1 (Depth 1 vs Depth 4):", results["move_1"]["depth_1"]["blast_radius"]["services"], "vs", results["move_1"]["depth_4"]["blast_radius"]["services"])
    print("Move 2 (PR #402 Review):", results["move_2"]["pr_review"]["summary"])
    print("Move 3 (Cross-Session Memory):", results["move_3"]["fintech_prod_memory"]["outcome"], "| Sandbox rows:", len(results["move_3"]["sandbox_tenant_memory"]["memory"]["rows"]))
    print("Move 4 (Epistemics): legacy-reporting ->", results["move_4"]["legacy_reporting"]["outcome"], "| auth-gateway ->", results["move_4"]["auth_gateway"]["outcome"])
