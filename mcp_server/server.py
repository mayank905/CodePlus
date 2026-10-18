"""CodePulse Typed MCP Server (Zero LLM-Written Cypher).

Exposes strictly the 10 pre-baked, parameterized Cypher queries (Q1-Q10) plus
`resolve_entity` (Q0) and `list_tenant_graphs`. Every call enforces parameter
bounds (`depth <= 4`, `limit <= 20`, `hours <= 168`) and returns an `audit_trace`
record with execution latency, row count, and epistemic outcome.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from falkordb import FalkorDB

from db.connection import FINTECH_PROD_TENANT, list_tenant_graphs
from db.queries import (
    DEFAULT_NOW_TS,
    VALID_MEMORY_CATEGORIES,
    blast_radius,
    load_bearing_services,
    path_between,
    pr_impact_radius,
    read_handoffs,
    recall_memory,
    recent_changes_in_radius,
    record_finding,
    resolve_entity,
    trace_decision_lineage,
    upsert_handoff,
    who_to_notify,
)

REGISTERED_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_tenant_graphs",
        "track": "Multi-Graph",
        "description": "List all isolated CodePulse tenant graphs in FalkorDB.",
        "parameters": ["prefix"],
    },
    {
        "name": "resolve_entity",
        "track": "Epistemic Guard (Q0)",
        "description": "Pre-check whether a Service, CodeModule, ADR, or Change exists in the tenant graph.",
        "parameters": ["tenant_id", "entity_type", "identifier"],
    },
    {
        "name": "recall_memory",
        "track": "Track 02 Memory (Q8)",
        "description": "Recall persistent episodic, semantic, or procedural agent findings for a service (limit<=10).",
        "parameters": ["tenant_id", "service", "categories", "limit"],
    },
    {
        "name": "blast_radius",
        "track": "Track 01 Blast Radius (Q1)",
        "description": "Multi-hop upstream service blast radius up to depth<=4 hops preserving sync/async parallel edges.",
        "parameters": ["tenant_id", "service", "depth"],
    },
    {
        "name": "pr_impact_radius",
        "track": "Track 01 PR Review (Q2)",
        "description": "Trace a Pull Request through touched CodeModules to owning and upstream Services (depth<=4).",
        "parameters": ["tenant_id", "pr_number", "depth"],
    },
    {
        "name": "path_between",
        "track": "Track 01 Shortest Chain (Q3)",
        "description": "Find the shortest weighted dependency path and cumulative latency between two services.",
        "parameters": ["tenant_id", "from_service", "to_service"],
    },
    {
        "name": "load_bearing_services",
        "track": "Track 01 PageRank (Q4)",
        "description": "Rank services by architectural centrality using CALL algo.pageRank('Service', 'CALLS') (limit<=20).",
        "parameters": ["tenant_id", "limit"],
    },
    {
        "name": "who_to_notify",
        "track": "Track 03 Company Brain (Q5)",
        "description": "Find owning teams, active on-call responders, and past incident counts in the blast radius (depth<=4).",
        "parameters": ["tenant_id", "service", "depth", "current_ts"],
    },
    {
        "name": "recent_changes_in_radius",
        "track": "Track 01 & 03 Temporal (Q6)",
        "description": "Find recent PRs and commits touching any module in the blast radius within hours<=168 (depth<=4).",
        "parameters": ["tenant_id", "service", "depth", "hours", "since_ts"],
    },
    {
        "name": "trace_decision_lineage",
        "track": "Track 03 Company Brain (Q7)",
        "description": "Trace active ADRs, approving engineers, and Slack discussion threads governing a service.",
        "parameters": ["tenant_id", "service"],
    },
    {
        "name": "record_finding",
        "track": "Track 02 Memory Write (Q9)",
        "description": "Persist a structured agent Finding node linked to a Service in FalkorDB.",
        "parameters": ["tenant_id", "service", "category", "text", "session_id", "agent_role"],
    },
    {
        "name": "handoff_state",
        "track": "Track 02 Handoff (Q10)",
        "description": "Read or upsert multi-agent Handoff state nodes for a PR in FalkorDB.",
        "parameters": [
            "tenant_id",
            "action",
            "pr_number",
            "service",
            "from_agent",
            "to_agent",
            "status",
            "payload",
        ],
    },
]

_TOOL_NAME_SET = {t["name"] for t in REGISTERED_TOOLS}


def list_registered_tools() -> list[dict[str, Any]]:
    """Return a copy of the strongly typed MCP tool registry."""
    return [dict(tool) for tool in REGISTERED_TOOLS]


def _clamp(val: int, low: int, high: int) -> int:
    return max(low, min(int(val), high))


def call_mcp_tool(
    tool_name: str,
    arguments: Optional[dict[str, Any]] = None,
    client: Optional[FalkorDB] = None,
) -> dict[str, Any]:
    """Validate arguments, invoke the pre-baked query function, and attach `audit_trace`."""
    if tool_name not in _TOOL_NAME_SET:
        raise ValueError(
            f"Unsupported MCP tool {tool_name!r}. Raw Cypher is prohibited; allowed tools: {sorted(_TOOL_NAME_SET)}"
        )

    args = dict(arguments or {})
    tenant_id = str(args.get("tenant_id", FINTECH_PROD_TENANT)).strip()
    t0 = time.perf_counter()

    if tool_name == "list_tenant_graphs":
        prefix = str(args.get("prefix", "codepulse:"))
        graphs = list_tenant_graphs(client=client, prefix=prefix)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        validated = {"prefix": prefix}
        return {
            "outcome": "answered_with_path",
            "graphs": graphs,
            "rows": [{"graph": g} for g in graphs],
            "latency_ms": latency_ms,
            "audit_trace": {
                "tool_name": tool_name,
                "validated_args": validated,
                "latency_ms": latency_ms,
                "row_count": len(graphs),
                "outcome": "answered_with_path",
            },
        }

    if tool_name == "resolve_entity":
        entity_type = str(args["entity_type"])
        identifier = args["identifier"]
        res = resolve_entity(tenant_id, entity_type, identifier, client=client)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        validated = {
            "tenant_id": tenant_id,
            "entity_type": entity_type,
            "identifier": identifier,
        }
        row_count = 1 if res.get("exists") else 0
        res["audit_trace"] = {
            "tool_name": tool_name,
            "validated_args": validated,
            "latency_ms": latency_ms,
            "row_count": row_count,
            "outcome": res["outcome"],
        }
        return res

    if tool_name == "recall_memory":
        service = str(args["service"])
        limit = _clamp(args.get("limit", 10), 1, 10)
        categories = list(args.get("categories") or VALID_MEMORY_CATEGORIES)
        validated = {
            "tenant_id": tenant_id,
            "service": service,
            "categories": categories,
            "limit": limit,
        }
        res = recall_memory(
            tenant_id=tenant_id,
            service=service,
            categories=categories,
            limit=limit,
            client=client,
        )

    elif tool_name == "blast_radius":
        service = str(args["service"])
        depth = _clamp(args.get("depth", 4), 1, 4)
        validated = {"tenant_id": tenant_id, "service": service, "depth": depth}
        res = blast_radius(
            tenant_id=tenant_id,
            service=service,
            depth=depth,
            client=client,
        )

    elif tool_name == "pr_impact_radius":
        pr_number = int(args["pr_number"])
        depth = _clamp(args.get("depth", 4), 1, 4)
        validated = {"tenant_id": tenant_id, "pr_number": pr_number, "depth": depth}
        res = pr_impact_radius(
            tenant_id=tenant_id,
            pr_number=pr_number,
            depth=depth,
            client=client,
        )

    elif tool_name == "path_between":
        from_service = str(args["from_service"])
        to_service = str(args["to_service"])
        validated = {
            "tenant_id": tenant_id,
            "from_service": from_service,
            "to_service": to_service,
        }
        res = path_between(
            tenant_id=tenant_id,
            from_service=from_service,
            to_service=to_service,
            client=client,
        )

    elif tool_name == "load_bearing_services":
        limit = _clamp(args.get("limit", 20), 1, 20)
        validated = {"tenant_id": tenant_id, "limit": limit}
        res = load_bearing_services(
            tenant_id=tenant_id,
            limit=limit,
            client=client,
        )

    elif tool_name == "who_to_notify":
        service = str(args["service"])
        depth = _clamp(args.get("depth", 4), 1, 4)
        current_ts = int(args.get("current_ts", DEFAULT_NOW_TS))
        validated = {
            "tenant_id": tenant_id,
            "service": service,
            "depth": depth,
            "current_ts": current_ts,
        }
        res = who_to_notify(
            tenant_id=tenant_id,
            service=service,
            depth=depth,
            current_ts=current_ts,
            client=client,
        )

    elif tool_name == "recent_changes_in_radius":
        service = str(args["service"])
        depth = _clamp(args.get("depth", 4), 1, 4)
        hours = _clamp(args.get("hours", 168), 1, 168)
        since_ts = int(args.get("since_ts", DEFAULT_NOW_TS - hours * 3600))
        validated = {
            "tenant_id": tenant_id,
            "service": service,
            "depth": depth,
            "hours": hours,
            "since_ts": since_ts,
        }
        res = recent_changes_in_radius(
            tenant_id=tenant_id,
            service=service,
            depth=depth,
            since_ts=since_ts,
            client=client,
        )

    elif tool_name == "trace_decision_lineage":
        service = str(args["service"])
        validated = {"tenant_id": tenant_id, "service": service}
        res = trace_decision_lineage(
            tenant_id=tenant_id,
            service=service,
            client=client,
        )

    elif tool_name == "record_finding":
        service = str(args["service"])
        category = str(args["category"])
        text = str(args["text"])
        session_id = str(args["session_id"])
        agent_role = str(args["agent_role"])
        validated = {
            "tenant_id": tenant_id,
            "service": service,
            "category": category,
            "session_id": session_id,
            "agent_role": agent_role,
        }
        res = record_finding(
            tenant_id=tenant_id,
            service=service,
            category=category,
            text=text,
            session_id=session_id,
            agent_role=agent_role,
            finding_id=args.get("finding_id"),
            at=args.get("at"),
            client=client,
        )

    elif tool_name == "handoff_state":
        action = str(args.get("action", "read")).lower()
        pr_number = int(args["pr_number"])
        if action == "upsert":
            service = str(args["service"])
            from_agent = str(args["from_agent"])
            to_agent = str(args["to_agent"])
            status = str(args.get("status", "completed"))
            payload = str(args.get("payload", "{}"))
            validated = {
                "tenant_id": tenant_id,
                "action": "upsert",
                "pr_number": pr_number,
                "service": service,
                "from_agent": from_agent,
                "to_agent": to_agent,
                "status": status,
            }
            res = upsert_handoff(
                tenant_id=tenant_id,
                pr_number=pr_number,
                service=service,
                from_agent=from_agent,
                to_agent=to_agent,
                status=status,
                payload=payload,
                handoff_id=args.get("handoff_id"),
                at=args.get("at"),
                client=client,
            )
        elif action == "read":
            validated = {
                "tenant_id": tenant_id,
                "action": "read",
                "pr_number": pr_number,
            }
            res = read_handoffs(
                tenant_id=tenant_id,
                pr_number=pr_number,
                client=client,
            )
        else:
            raise ValueError(f"Unsupported handoff_state action: {action!r}")

    else:
        raise ValueError(f"Unhandled MCP tool: {tool_name!r}")

    latency_ms = res.get("latency_ms", round((time.perf_counter() - t0) * 1000, 2))
    if "rows" in res:
        row_count = len(res["rows"])
    elif res.get("finding_id") or res.get("handoff_id"):
        row_count = 1
    else:
        row_count = 0

    res["audit_trace"] = {
        "tool_name": tool_name,
        "validated_args": validated,
        "latency_ms": latency_ms,
        "row_count": row_count,
        "outcome": res.get("outcome", "answered_with_path"),
    }
    return res
