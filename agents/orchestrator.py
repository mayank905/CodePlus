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

from guard import ADR_TOKEN, SERVICE_TOKEN, verify_or_fallback
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


def _build_unknown_entity_result(
    entity_label: str,
    simulated_llm_draft: Optional[str],
    agent_traces: list[dict[str, Any]],
    mcp_traces: list[dict[str, Any]],
    extra_fields: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    grounding = _build_grounding_answer(
        outcome="unknown_entity",
        target_service=entity_label,
        services=[],
        paths=[],
        adrs=[],
    )
    draft = simulated_llm_draft or f"Entity `{entity_label}` is not registered in the graph."
    verdict = verify_or_fallback(draft, grounding)
    res: dict[str, Any] = {
        "outcome": "unknown_entity",
        "target_service": entity_label,
        "summary": verdict["final_text"],
        "guard_status": "PASS" if verdict["accepted"] else "FALLBACK",
        "guard_reason": verdict["reason"],
        "agent_traces": agent_traces,
        "mcp_traces": mcp_traces,
        "traversals_executed": 0,
    }
    if extra_fields:
        res.update(extra_fields)
    return res


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
        return _build_unknown_entity_result(
            entity_label=f"PR-{pr_number}",
            simulated_llm_draft=simulated_llm_draft,
            agent_traces=agent_traces,
            mcp_traces=mcp_traces,
            extra_fields={"pr_number": int(pr_number)},
        )

    # -----------------------------------------------------------------------
    # Agent 1: memory_scout (runs first before topology_analyst)
    # -----------------------------------------------------------------------
    prior_handoffs = invoke(
        "handoff_state",
        {"tenant_id": tenant_id, "action": "read", "pr_number": int(pr_number)},
    )
    scout_service = (
        prior_handoffs["rows"][0]["service"]
        if prior_handoffs.get("rows") and prior_handoffs["rows"][0].get("service")
        else "payments-api"
    )
    prior_memory = invoke(
        "recall_memory",
        {"tenant_id": tenant_id, "service": scout_service, "limit": 5},
    )
    agent_traces.append(
        {
            "agent_role": "memory_scout",
            "status": "completed",
            "summary": (
                f"Checked prior memory for `{scout_service}` "
                f"({len(prior_memory.get('rows', []))} findings) and PR #{pr_number} handoffs "
                f"({len(prior_handoffs.get('rows', []))} prior handoffs)."
            ),
            "tools_called": ["handoff_state", "recall_memory"],
        }
    )

    # -----------------------------------------------------------------------
    # Agent 2: topology_analyst
    # -----------------------------------------------------------------------
    pr_impact = invoke(
        "pr_impact_radius",
        {"tenant_id": tenant_id, "pr_number": int(pr_number), "depth": depth},
    )
    primary_service = pr_impact.get("origin_service") or scout_service

    blast = invoke(
        "blast_radius",
        {"tenant_id": tenant_id, "service": primary_service, "depth": depth},
    )
    farthest_caller = (
        blast["rows"][-1]["service"]
        if blast.get("rows")
        else primary_service
    )
    shortest_chain = invoke(
        "path_between",
        {
            "tenant_id": tenant_id,
            "from_service": farthest_caller,
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
        return _build_unknown_entity_result(
            entity_label=service,
            simulated_llm_draft=simulated_llm_draft,
            agent_traces=agent_traces,
            mcp_traces=mcp_traces,
        )

    # Agent 1: memory_scout
    memory_res = invoke(
        "recall_memory",
        {"tenant_id": tenant_id, "service": service, "limit": 5},
    )
    prior_handoffs = invoke(
        "handoff_state",
        {"tenant_id": tenant_id, "action": "read", "pr_number": 0},
    )
    agent_traces.append(
        {
            "agent_role": "memory_scout",
            "status": "completed",
            "summary": (
                f"Queried persistent findings for `{service}` "
                f"({len(memory_res.get('rows', []))} rows) and prior handoffs "
                f"({len(prior_handoffs.get('rows', []))} rows)."
            ),
            "tools_called": ["recall_memory", "handoff_state"],
        }
    )

    if prefer_memory and memory_res.get("outcome") == "answered_from_memory" and memory_res.get("rows"):
        top_finding = memory_res["rows"][0]
        finding_texts = " ".join(str(r.get("finding", "")) for r in memory_res["rows"])
        recalled_services = [service] + SERVICE_TOKEN.findall(finding_texts)
        recalled_adrs = ADR_TOKEN.findall(finding_texts)
        grounding = _build_grounding_answer(
            outcome="answered_from_memory",
            target_service=service,
            services=recalled_services,
            paths=[],
            adrs=recalled_adrs,
        )
        grounding["allowed_tokens"] = [
            str(r.get("session_id", "")) for r in memory_res["rows"] if r.get("session_id")
        ]
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

    if blast["outcome"] == "not_in_graph":
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

    farthest_caller = (
        blast["rows"][-1]["service"]
        if blast.get("rows")
        else service
    )
    shortest_chain = invoke(
        "path_between",
        {
            "tenant_id": tenant_id,
            "from_service": farthest_caller,
            "to_service": service,
        },
    )
    handoff_payload = json.dumps(
        {
            "service": service,
            "blast_radius_services": blast.get("services", []),
            "shortest_path": (
                shortest_chain["rows"][0].get("via_nodes", [])
                if shortest_chain.get("rows")
                else []
            ),
        }
    )
    invoke(
        "handoff_state",
        {
            "tenant_id": tenant_id,
            "action": "upsert",
            "pr_number": 0,
            "service": service,
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
                f"Executed blast_radius (depth={depth}, outcome={blast['outcome']}), "
                f"path_between, load_bearing_services, and persisted `:Handoff`."
            ),
            "tools_called": [
                "blast_radius",
                "load_bearing_services",
                "path_between",
                "handoff_state",
            ],
        }
    )

    # Agent 3: release_governor
    handoffs_after = invoke(
        "handoff_state",
        {"tenant_id": tenant_id, "action": "read", "pr_number": 0},
    )
    lineage = invoke(
        "trace_decision_lineage",
        {"tenant_id": tenant_id, "service": service},
    )
    notify = invoke(
        "who_to_notify",
        {"tenant_id": tenant_id, "service": service, "depth": depth},
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
    guard_status = "PASS" if verdict["accepted"] else "FALLBACK"

    finding_res = invoke(
        "record_finding",
        {
            "tenant_id": tenant_id,
            "service": service,
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
                f"Read `:Handoff`, traced decision lineage ({len(lineage.get('adrs', []))} ADRs) "
                f"and on-call routing ({len(notify.get('rows', []))} responders), and recorded `:Finding`."
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
        "outcome": "answered_with_path",
        "target_service": service,
        "summary": verdict["final_text"],
        "guard_status": guard_status,
        "guard_reason": verdict["reason"],
        "agent_traces": agent_traces,
        "mcp_traces": mcp_traces,
        "memory": memory_res,
        "blast_radius": blast,
        "shortest_chain": shortest_chain,
        "pagerank": pagerank,
        "lineage": lineage,
        "notify": notify,
        "handoffs": handoffs_after,
        "finding": finding_res,
        "traversals_executed": 5,
    }
