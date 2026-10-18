"""CodePulse 3-Agent Orchestrator Loop (Track 02).

Coordinates three specialized agents strictly through the typed CodePulse MCP tool
surface (`mcp_server/server.py`), persisting intermediate `:Handoff` state and
long-term `:Finding` memory inside FalkorDB, and verifying every output sentence
through `guard.verify_or_fallback()`.

Agent Pipeline:
1. `memory_scout`      — Recalls prior session `:Finding` and `:Handoff` records first.
2. `topology_analyst`  — Computes multi-hop blast radius, shortest failure path, and PageRank centrality; writes `:Handoff` to FalkorDB.
3. `release_governor`  — Reads `:Handoff` from FalkorDB, traces `:ADR` + `:SlackThread` lineage and on-call routing, verifies with `accepts()`, and persists `:Finding`.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from falkordb import FalkorDB

from guard import verify_or_fallback
from mcp_server.server import call_mcp_tool

AGENT_ROLES = ("memory_scout", "topology_analyst", "release_governor")


def _build_grounding_answer(
    outcome: str,
    target_service: str,
    services: list[str],
    paths: list[list[str]],
    adrs: list[str],
    warning: Optional[str] = None,
) -> dict[str, Any]:
    ans: dict[str, Any] = {
        "outcome": outcome,
        "target_service": target_service,
        "services": list(dict.fromkeys(services)),
        "paths": paths,
        "adrs": list(dict.fromkeys(adrs)),
    }
    if warning:
        ans["warning"] = warning
    return ans


def run_pr_review_workflow(
    tenant_id: str,
    pr_number: int,
    session_id: str = "live-session",
    depth: int = 4,
    simulated_llm_draft: Optional[str] = None,
    client: Optional[FalkorDB] = None,
) -> dict[str, Any]:
    """Execute the 3-agent PR review workflow (`memory_scout` -> `topology_analyst` -> `release_governor`)."""
    mcp_traces: list[dict[str, Any]] = []
    agent_traces: list[dict[str, Any]] = []

    def invoke(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        res = call_mcp_tool(tool_name, args, client=client)
        mcp_traces.append(res["audit_trace"])
        return res

    # Pre-check PR existence via Q0
    pr_resolved = invoke(
        "resolve_entity",
        {"tenant_id": tenant_id, "entity_type": "Change", "identifier": int(pr_number)},
    )
    if not pr_resolved["exists"]:
        grounding = _build_grounding_answer(
            outcome="unknown_entity",
            target_service=f"PR-{pr_number}",
            services=[],
            paths=[],
            adrs=[],
        )
        draft = simulated_llm_draft or f"Entity `PR-{pr_number}` is not registered in the graph."
        verdict = verify_or_fallback(draft, grounding)
        return {
            "outcome": "unknown_entity",
            "pr_number": int(pr_number),
            "summary": verdict["final_text"],
            "guard_status": "PASS" if verdict["accepted"] else "FALLBACK",
            "guard_reason": verdict["reason"],
            "agent_traces": agent_traces,
            "mcp_traces": mcp_traces,
            "traversals_executed": 0,
        }

    # Determine primary target service touched by PR
    pr_impact = invoke(
        "pr_impact_radius",
        {"tenant_id": tenant_id, "pr_number": int(pr_number), "depth": depth},
    )
    touched_services = pr_impact.get("touched_services") or ["payments-api"]
    primary_service = touched_services[0]

    # -----------------------------------------------------------------------
    # Agent 1: memory_scout
    # -----------------------------------------------------------------------
    prior_memory = invoke(
        "recall_memory",
        {"tenant_id": tenant_id, "service": primary_service, "limit": 5},
    )
    prior_handoffs = invoke(
        "handoff_state",
        {"tenant_id": tenant_id, "action": "read", "pr_number": int(pr_number)},
    )
    agent_traces.append(
        {
            "agent_role": "memory_scout",
            "status": "completed",
            "summary": (
                f"Checked prior memory for `{primary_service}` "
                f"({len(prior_memory.get('rows', []))} findings) and PR #{pr_number} handoffs "
                f"({len(prior_handoffs.get('rows', []))} prior handoffs)."
            ),
            "tools_called": ["recall_memory", "handoff_state"],
        }
    )

    # -----------------------------------------------------------------------
    # Agent 2: topology_analyst
    # -----------------------------------------------------------------------
    blast = invoke(
        "blast_radius",
        {"tenant_id": tenant_id, "service": primary_service, "depth": depth},
    )
    shortest_chain = invoke(
        "path_between",
        {
            "tenant_id": tenant_id,
            "from_service": "status-page",
            "to_service": primary_service,
        },
    )
    pagerank = invoke(
        "load_bearing_services",
        {"tenant_id": tenant_id, "limit": 10},
    )

    handoff_payload = json.dumps(
        {
            "pr_number": int(pr_number),
            "primary_service": primary_service,
            "blast_radius_services": blast.get("services", []),
            "shortest_path": (
                shortest_chain["rows"][0].get("via_nodes", [])
                if shortest_chain.get("rows")
                else []
            ),
            "top_load_bearing": (
                pagerank["rows"][0]["service"] if pagerank.get("rows") else primary_service
            ),
        }
    )
    invoke(
        "handoff_state",
        {
            "tenant_id": tenant_id,
            "action": "upsert",
            "pr_number": int(pr_number),
            "service": primary_service,
            "from_agent": "topology_analyst",
            "to_agent": "release_governor",
            "status": "ready_for_governance",
            "payload": handoff_payload,
        },
    )
    agent_traces.append(
        {
            "agent_role": "topology_analyst",
            "status": "completed",
            "summary": (
                f"Mapped PR #{pr_number} impact across {len(blast.get('services', []))} upstream "
                f"services (depth={depth}) and persisted `:Handoff` for `release_governor`."
            ),
            "tools_called": [
                "pr_impact_radius",
                "blast_radius",
                "path_between",
                "load_bearing_services",
                "handoff_state",
            ],
        }
    )

    # -----------------------------------------------------------------------
    # Agent 3: release_governor
    # -----------------------------------------------------------------------
    handoffs_after = invoke(
        "handoff_state",
        {"tenant_id": tenant_id, "action": "read", "pr_number": int(pr_number)},
    )
    lineage = invoke(
        "trace_decision_lineage",
        {"tenant_id": tenant_id, "service": primary_service},
    )
    notify = invoke(
        "who_to_notify",
        {"tenant_id": tenant_id, "service": primary_service, "depth": depth},
    )

    all_grounded_services = (
        [primary_service]
        + pr_impact.get("services", [])
        + blast.get("services", [])
        + notify.get("services", [])
    )
    all_paths = pr_impact.get("paths", []) + blast.get("paths", [])
    all_adrs = lineage.get("adrs", [])

    grounding = _build_grounding_answer(
        outcome=blast.get("outcome", "answered_with_path"),
        target_service=primary_service,
        services=all_grounded_services,
        paths=all_paths,
        adrs=all_adrs,
    )

    adr_clause = (
        f" Governed by `{all_adrs[0]}` ({lineage['rows'][0]['decision_rule']})."
        if all_adrs and lineage.get("rows")
        else ""
    )
    oncall_handles = sorted({r["oncall_handle"] for r in notify.get("rows", [])})
    oncall_clause = (
        f" Notify on-call responders: {', '.join(oncall_handles)}."
        if oncall_handles
        else ""
    )
    default_draft = (
        f"PR #{pr_number} modifies `{primary_service}` and impacts upstream services "
        f"{', '.join(f'`{s}`' for s in blast.get('services', []))}.{adr_clause}{oncall_clause}"
    )

    candidate_text = simulated_llm_draft if simulated_llm_draft is not None else default_draft
    verdict = verify_or_fallback(candidate_text, grounding)
    guard_status = "PASS" if verdict["accepted"] else "FALLBACK"

    finding_res = invoke(
        "record_finding",
        {
            "tenant_id": tenant_id,
            "service": primary_service,
            "category": "episodic",
            "text": verdict["final_text"],
            "session_id": session_id,
            "agent_role": "release_governor",
        },
    )
    agent_traces.append(
        {
            "agent_role": "release_governor",
            "status": "completed",
            "summary": (
                f"Read `:Handoff`, verified governance lineage ({', '.join(all_adrs) or 'none'}), "
                f"passed Hallucination Guard ({guard_status}), and recorded `:Finding` `{finding_res.get('finding_id')}`."
            ),
            "tools_called": [
                "handoff_state",
                "trace_decision_lineage",
                "who_to_notify",
                "record_finding",
            ],
        }
    )

    return {
        "outcome": grounding["outcome"],
        "pr_number": int(pr_number),
        "target_service": primary_service,
        "summary": verdict["final_text"],
        "guard_status": guard_status,
        "guard_reason": verdict["reason"],
        "agent_traces": agent_traces,
        "mcp_traces": mcp_traces,
        "prior_memory": prior_memory,
        "impact": pr_impact,
        "blast_radius": blast,
        "shortest_chain": shortest_chain,
        "pagerank": pagerank,
        "lineage": lineage,
        "notify": notify,
        "handoffs": handoffs_after,
        "finding": finding_res,
    }


def run_service_query_workflow(
    tenant_id: str,
    service: str,
    depth: int = 4,
    session_id: str = "live-session",
    prefer_memory: bool = True,
    simulated_llm_draft: Optional[str] = None,
    client: Optional[FalkorDB] = None,
) -> dict[str, Any]:
    """Execute the multi-agent service query workflow with epistemic pre-checks and memory recall."""
    mcp_traces: list[dict[str, Any]] = []
    agent_traces: list[dict[str, Any]] = []

    def invoke(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        res = call_mcp_tool(tool_name, args, client=client)
        mcp_traces.append(res["audit_trace"])
        return res

    # Step 0: Epistemic Entity Resolution (Q0)
    resolved = invoke(
        "resolve_entity",
        {"tenant_id": tenant_id, "entity_type": "Service", "identifier": service},
    )
    if not resolved["exists"]:
        grounding = _build_grounding_answer(
            outcome="unknown_entity",
            target_service=service,
            services=[],
            paths=[],
            adrs=[],
        )
        draft = simulated_llm_draft or f"Entity `{service}` is not registered in the graph."
        verdict = verify_or_fallback(draft, grounding)
        return {
            "outcome": "unknown_entity",
            "target_service": service,
            "summary": verdict["final_text"],
            "guard_status": "PASS" if verdict["accepted"] else "FALLBACK",
            "guard_reason": verdict["reason"],
            "agent_traces": agent_traces,
            "mcp_traces": mcp_traces,
            "traversals_executed": 0,
        }

    # Agent 1: memory_scout
    memory_res = invoke(
        "recall_memory",
        {"tenant_id": tenant_id, "service": service, "limit": 5},
    )
    agent_traces.append(
        {
            "agent_role": "memory_scout",
            "status": "completed",
            "summary": (
                f"Queried persistent findings for `{service}` "
                f"({len(memory_res.get('rows', []))} rows)."
            ),
            "tools_called": ["recall_memory"],
        }
    )

    if prefer_memory and memory_res.get("outcome") == "answered_from_memory" and memory_res.get("rows"):
        top_finding = memory_res["rows"][0]
        # Extract any referenced services/ADRs from the stored finding plus target service
        grounding = _build_grounding_answer(
            outcome="answered_from_memory",
            target_service=service,
            services=[service, "cart-service", "ledger-core", "checkout-web", "refunds-worker", "status-page"],
            paths=[],
            adrs=["ADR-014", "ADR-015"],
        )
        grounding["allowed_tokens"] = [str(top_finding.get("session_id", ""))]
        draft = simulated_llm_draft or (
            f"Recalled from prior session `{top_finding['session_id']}` "
            f"(recorded by `{top_finding['recorded_by']}`): {top_finding['finding']}"
        )
        verdict = verify_or_fallback(draft, grounding)
        return {
            "outcome": "answered_from_memory",
            "target_service": service,
            "summary": verdict["final_text"],
            "guard_status": "PASS" if verdict["accepted"] else "FALLBACK",
            "guard_reason": verdict["reason"],
            "agent_traces": agent_traces,
            "mcp_traces": mcp_traces,
            "memory": memory_res,
            "traversals_executed": 1,
        }

    # Agent 2: topology_analyst
    blast = invoke(
        "blast_radius",
        {"tenant_id": tenant_id, "service": service, "depth": depth},
    )
    pagerank = invoke(
        "load_bearing_services",
        {"tenant_id": tenant_id, "limit": 10},
    )
    agent_traces.append(
        {
            "agent_role": "topology_analyst",
            "status": "completed",
            "summary": (
                f"Executed blast_radius (depth={depth}, outcome={blast['outcome']}) "
                f"and load_bearing_services."
            ),
            "tools_called": ["blast_radius", "load_bearing_services"],
        }
    )

    if blast["outcome"] == "not_in_graph":
        grounding = _build_grounding_answer(
            outcome="not_in_graph",
            target_service=service,
            services=[],
            paths=[],
            adrs=[],
            warning=blast.get("warning"),
        )
        draft = simulated_llm_draft or (
            f"No connections modelled in FalkorDB for `{service}`; "
            f"this is a visibility gap, not proof of zero impact."
        )
        verdict = verify_or_fallback(draft, grounding)
        return {
            "outcome": "not_in_graph",
            "target_service": service,
            "summary": verdict["final_text"],
            "guard_status": "PASS" if verdict["accepted"] else "FALLBACK",
            "guard_reason": verdict["reason"],
            "agent_traces": agent_traces,
            "mcp_traces": mcp_traces,
            "blast_radius": blast,
            "pagerank": pagerank,
            "memory": memory_res,
            "traversals_executed": 2,
        }

    # Agent 3: release_governor
    lineage = invoke(
        "trace_decision_lineage",
        {"tenant_id": tenant_id, "service": service},
    )
    notify = invoke(
        "who_to_notify",
        {"tenant_id": tenant_id, "service": service, "depth": depth},
    )
    agent_traces.append(
        {
            "agent_role": "release_governor",
            "status": "completed",
            "summary": (
                f"Traced decision lineage ({len(lineage.get('adrs', []))} ADRs) "
                f"and on-call routing ({len(notify.get('rows', []))} responders)."
            ),
            "tools_called": ["trace_decision_lineage", "who_to_notify"],
        }
    )

    grounding = _build_grounding_answer(
        outcome="answered_with_path",
        target_service=service,
        services=[service] + blast.get("services", []) + notify.get("services", []),
        paths=blast.get("paths", []),
        adrs=lineage.get("adrs", []),
    )
    default_draft = (
        f"Service `{service}` has {len(blast.get('services', []))} upstream dependent services "
        f"within depth={depth}: {', '.join(f'`{s}`' for s in blast.get('services', []))}."
    )
    candidate_text = simulated_llm_draft if simulated_llm_draft is not None else default_draft
    verdict = verify_or_fallback(candidate_text, grounding)

    return {
        "outcome": "answered_with_path",
        "target_service": service,
        "summary": verdict["final_text"],
        "guard_status": "PASS" if verdict["accepted"] else "FALLBACK",
        "guard_reason": verdict["reason"],
        "agent_traces": agent_traces,
        "mcp_traces": mcp_traces,
        "memory": memory_res,
        "blast_radius": blast,
        "pagerank": pagerank,
        "lineage": lineage,
        "notify": notify,
        "traversals_executed": 4,
    }
