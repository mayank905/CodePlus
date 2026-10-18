"""Pre-Baked Cypher Query Catalogue (`db/queries.py`).

Implements the complete Phase 1 Pre-Baked Cypher Catalogue:
  - Q0 (`resolve_entity`)
  - Track 01 Queries Q1-Q4 (`blast_radius`, `pr_impact_radius`, `path_between`, `load_bearing_services`)
  - Track 03 Queries Q5-Q7 (`who_to_notify`, `recent_changes_in_radius`, `trace_decision_lineage`)
  - Track 02 Queries Q8-Q10 (`recall_memory`, `record_finding`, `upsert_handoff`, `read_handoffs`)

All variable-length hop bounds are integer-clamped (`1 <= depth <= 4`) in Python before execution.
Zero LLM-generated Cypher is ever executed at runtime.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional, Sequence

from falkordb import FalkorDB, Graph

from db.connection import select_tenant_graph

EntityType = Literal["Service", "CodeModule", "ADR", "Change"]
OutcomeType = Literal[
    "answered_with_path",
    "answered_from_memory",
    "not_in_graph",
    "unknown_entity",
]

MAX_DEPTH = 4
MAX_LIMIT = 20
DEFAULT_NOW_TS = 1760000000
VALID_MEMORY_CATEGORIES = ("episodic", "semantic", "procedural")

NOT_IN_GRAPH_WARNING = (
    "No connections modelled in FalkorDB; this is a visibility gap, not proof of zero impact."
)

# ---------------------------------------------------------------------------
# Q0: Entity Resolver Queries (Mandatory Read-Only Pre-Check)
# ---------------------------------------------------------------------------

Q0_RESOLVE_SERVICE = """
MATCH (s:Service {name: $name})
RETURN s.name AS name, s.tier AS tier, s.repo AS repo, s.language AS language
"""

Q0_RESOLVE_CODE_MODULE = """
MATCH (m:CodeModule {path: $path})
OPTIONAL MATCH (m)-[p:PART_OF]->(s:Service)
WHERE ID(p) >= 0
RETURN m.path AS path, m.complexity AS complexity, s.name AS service
"""

Q0_RESOLVE_ADR = """
MATCH (a:ADR {id: $id})
RETURN a.id AS id, a.title AS title, a.status AS status, a.decided_at AS decided_at
"""

Q0_RESOLVE_CHANGE = """
MATCH (c:Change {pr_number: $pr_number})
RETURN c.pr_number AS pr_number, c.sha AS sha, c.summary AS summary, c.status AS status, c.author AS author
"""


def _clamp_depth(depth: int) -> int:
    d = int(depth)
    return max(1, min(d, MAX_DEPTH))


def _clamp_limit(limit: int, max_val: int = MAX_LIMIT) -> int:
    lim = int(limit)
    return max(1, min(lim, max_val))


def resolve_entity(
    tenant_id: str,
    entity_type: EntityType,
    identifier: str | int,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Run mandatory Q0 read-only lookup to verify an entity exists before any traversal."""
    g = graph or select_tenant_graph(tenant_id, client=client)

    if entity_type == "Service":
        name = str(identifier).strip()
        rows = g.ro_query(Q0_RESOLVE_SERVICE, {"name": name}).result_set
        if not rows:
            return {
                "exists": False,
                "outcome": "unknown_entity",
                "entity_type": entity_type,
                "identifier": name,
                "entity": None,
            }
        row = rows[0]
        return {
            "exists": True,
            "outcome": "resolved",
            "entity_type": entity_type,
            "identifier": name,
            "entity": {
                "name": row[0],
                "tier": row[1],
                "repo": row[2],
                "language": row[3],
            },
        }

    if entity_type == "CodeModule":
        path = str(identifier).strip()
        rows = g.ro_query(Q0_RESOLVE_CODE_MODULE, {"path": path}).result_set
        if not rows:
            return {
                "exists": False,
                "outcome": "unknown_entity",
                "entity_type": entity_type,
                "identifier": path,
                "entity": None,
            }
        row = rows[0]
        return {
            "exists": True,
            "outcome": "resolved",
            "entity_type": entity_type,
            "identifier": path,
            "entity": {
                "path": row[0],
                "complexity": row[1],
                "service": row[2],
            },
        }

    if entity_type == "ADR":
        adr_id = str(identifier).strip()
        rows = g.ro_query(Q0_RESOLVE_ADR, {"id": adr_id}).result_set
        if not rows:
            return {
                "exists": False,
                "outcome": "unknown_entity",
                "entity_type": entity_type,
                "identifier": adr_id,
                "entity": None,
            }
        row = rows[0]
        return {
            "exists": True,
            "outcome": "resolved",
            "entity_type": entity_type,
            "identifier": adr_id,
            "entity": {
                "id": row[0],
                "title": row[1],
                "status": row[2],
                "decided_at": row[3],
            },
        }

    if entity_type == "Change":
        pr_number = int(identifier)
        rows = g.ro_query(Q0_RESOLVE_CHANGE, {"pr_number": pr_number}).result_set
        if not rows:
            return {
                "exists": False,
                "outcome": "unknown_entity",
                "entity_type": entity_type,
                "identifier": pr_number,
                "entity": None,
            }
        row = rows[0]
        return {
            "exists": True,
            "outcome": "resolved",
            "entity_type": entity_type,
            "identifier": pr_number,
            "entity": {
                "pr_number": row[0],
                "sha": row[1],
                "summary": row[2],
                "status": row[3],
                "author": row[4],
            },
        }

    raise ValueError(f"Unsupported entity_type: {entity_type}")


# ---------------------------------------------------------------------------
# Q1: Multi-Hop Service Blast Radius (`blast_radius`) — Track 01
# ---------------------------------------------------------------------------

Q1_BLAST_RADIUS_TEMPLATE = """
MATCH path = (upstream:Service)-[r:DEPENDS_ON*1..{depth}]->(target:Service {{name: $service}})
WHERE ALL(edge IN relationships(path) WHERE ID(edge) >= 0)
RETURN DISTINCT upstream.name AS service,
       upstream.tier AS tier,
       length(path) AS hops,
       [n IN nodes(path) | n.name] AS via_nodes,
       [e IN relationships(path) | e.kind] AS via_edge_kinds
ORDER BY hops ASC, tier ASC, service ASC
"""


def get_q1_blast_radius_cypher(depth: int = 4) -> str:
    return Q1_BLAST_RADIUS_TEMPLATE.format(depth=_clamp_depth(depth))


def blast_radius(
    tenant_id: str,
    service: str,
    depth: int = 4,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q1: Find every upstream service that breaks if `service` fails or changes, up to `depth` hops."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)
    clamped_depth = _clamp_depth(depth)

    resolved = resolve_entity(tenant_id, "Service", service, client=client, graph=g)
    if not resolved["exists"]:
        return {
            "outcome": "unknown_entity",
            "target_service": service,
            "depth": clamped_depth,
            "services": [],
            "paths": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    cypher = get_q1_blast_radius_cypher(clamped_depth)
    raw_rows = g.ro_query(cypher, {"service": resolved["identifier"]}).result_set

    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "target_service": resolved["identifier"],
            "depth": clamped_depth,
            "warning": NOT_IN_GRAPH_WARNING,
            "services": [],
            "paths": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    rows: list[dict[str, Any]] = []
    services_ordered: list[str] = []
    paths: list[list[str]] = []

    for r in raw_rows:
        svc_name, tier, hops, via_nodes, via_edge_kinds = r[0], r[1], r[2], r[3], r[4]
        rows.append(
            {
                "service": svc_name,
                "tier": tier,
                "hops": hops,
                "via_nodes": via_nodes,
                "via_edge_kinds": via_edge_kinds,
            }
        )
        if svc_name not in services_ordered:
            services_ordered.append(svc_name)
        if via_nodes not in paths:
            paths.append(via_nodes)

    return {
        "outcome": "answered_with_path",
        "target_service": resolved["identifier"],
        "depth": clamped_depth,
        "services": services_ordered,
        "paths": paths,
        "rows": rows,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---------------------------------------------------------------------------
# Q2: PR / Code-to-Service Blast Radius (`pr_impact_radius`) — Track 01
# ---------------------------------------------------------------------------

Q2_PR_IMPACT_RADIUS_TEMPLATE = """
MATCH (c:Change {{pr_number: $pr_number}})-[t:TOUCHED]->(m:CodeModule)-[:PART_OF]->(origin:Service)
WHERE ID(t) >= 0
OPTIONAL MATCH path = (consumer:Service)-[r:DEPENDS_ON*1..{depth}]->(origin)
WHERE ALL(edge IN relationships(path) WHERE ID(edge) >= 0)
RETURN m.path AS touched_module,
       t.breaking_schema AS breaking_schema,
       origin.name AS origin_service,
       consumer.name AS impacted_service,
       length(path) AS hops,
       [n IN nodes(path) | n.name] AS via_path
ORDER BY hops ASC, impacted_service ASC
"""


def pr_impact_radius(
    tenant_id: str,
    pr_number: int,
    depth: int = 4,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q2: Trace from a Pull Request -> modified CodeModule -> owning Service -> upstream consumers."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)
    clamped_depth = _clamp_depth(depth)

    resolved = resolve_entity(tenant_id, "Change", pr_number, client=client, graph=g)
    if not resolved["exists"]:
        return {
            "outcome": "unknown_entity",
            "pr_number": int(pr_number),
            "depth": clamped_depth,
            "origin_service": None,
            "touched_modules": [],
            "services": [],
            "paths": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    cypher = Q2_PR_IMPACT_RADIUS_TEMPLATE.format(depth=clamped_depth)
    raw_rows = g.ro_query(cypher, {"pr_number": int(pr_number)}).result_set

    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "pr_number": int(pr_number),
            "depth": clamped_depth,
            "warning": NOT_IN_GRAPH_WARNING,
            "origin_service": None,
            "touched_modules": [],
            "services": [],
            "paths": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    rows: list[dict[str, Any]] = []
    touched_modules: list[str] = []
    services: list[str] = []
    paths: list[list[str]] = []
    origin_service: Optional[str] = None
    breaking_schema = False

    for r in raw_rows:
        mod_path, is_breaking, origin_svc, consumer_svc, hops, via_path = (
            r[0],
            bool(r[1]),
            r[2],
            r[3],
            r[4],
            r[5],
        )
        origin_service = origin_svc
        breaking_schema = breaking_schema or is_breaking
        if mod_path and mod_path not in touched_modules:
            touched_modules.append(mod_path)
        if consumer_svc and consumer_svc not in services:
            services.append(consumer_svc)
        if via_path and via_path not in paths:
            paths.append(via_path)
        rows.append(
            {
                "touched_module": mod_path,
                "breaking_schema": is_breaking,
                "origin_service": origin_svc,
                "impacted_service": consumer_svc,
                "hops": hops,
                "via_path": via_path,
            }
        )

    outcome: OutcomeType = "answered_with_path" if services else "not_in_graph"
    result: dict[str, Any] = {
        "outcome": outcome,
        "pr_number": int(pr_number),
        "change": resolved["entity"],
        "depth": clamped_depth,
        "origin_service": origin_service,
        "breaking_schema": breaking_schema,
        "touched_modules": touched_modules,
        "services": services,
        "paths": paths,
        "rows": rows,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
    if outcome == "not_in_graph":
        result["warning"] = NOT_IN_GRAPH_WARNING
    return result


# ---------------------------------------------------------------------------
# Q3: Shortest Dependency Route (`path_between`) — Track 01 Algorithm
# ---------------------------------------------------------------------------

Q3_PATH_BETWEEN = """
MATCH (a:Service {name: $from_service}), (b:Service {name: $to_service})
CALL algo.SPpaths({
  sourceNode: a,
  targetNode: b,
  relTypes: ['DEPENDS_ON'],
  relDirection: 'outgoing',
  weightProp: 'weight',
  pathCount: 1
})
YIELD path, pathWeight
RETURN [n IN nodes(path) | n.name] AS hops,
       pathWeight AS total_weight
"""


def path_between(
    tenant_id: str,
    from_service: str,
    to_service: str,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q3: Compute shortest weighted dependency path using FalkorDB `algo.SPpaths`."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)

    res_from = resolve_entity(tenant_id, "Service", from_service, client=client, graph=g)
    res_to = resolve_entity(tenant_id, "Service", to_service, client=client, graph=g)
    if not res_from["exists"] or not res_to["exists"]:
        return {
            "outcome": "unknown_entity",
            "from_service": from_service,
            "to_service": to_service,
            "hops": [],
            "total_weight": None,
            "services": [],
            "paths": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    raw_rows = g.ro_query(
        Q3_PATH_BETWEEN,
        {"from_service": res_from["identifier"], "to_service": res_to["identifier"]},
    ).result_set

    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "from_service": res_from["identifier"],
            "to_service": res_to["identifier"],
            "warning": NOT_IN_GRAPH_WARNING,
            "hops": [],
            "total_weight": None,
            "services": [],
            "paths": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    hops, total_weight = raw_rows[0][0], raw_rows[0][1]
    return {
        "outcome": "answered_with_path",
        "from_service": res_from["identifier"],
        "to_service": res_to["identifier"],
        "hops": hops,
        "total_weight": total_weight,
        "services": list(hops),
        "paths": [list(hops)],
        "rows": [{"hops": hops, "total_weight": total_weight}],
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---------------------------------------------------------------------------
# Q4: Architectural Load-Bearing Ranking (`load_bearing_services`) — Track 01
# ---------------------------------------------------------------------------

Q4_LOAD_BEARING_SERVICES = """
CALL algo.pageRank('Service', 'DEPENDS_ON')
YIELD node, score
RETURN node.name AS service,
       node.tier AS tier,
       score
ORDER BY score DESC
LIMIT $limit
"""


def load_bearing_services(
    tenant_id: str,
    limit: int = 10,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q4: Rank services by architectural criticality using `algo.pageRank('Service', 'DEPENDS_ON')`."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)
    clamped_limit = _clamp_limit(limit)

    raw_rows = g.ro_query(Q4_LOAD_BEARING_SERVICES, {"limit": clamped_limit}).result_set
    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "warning": NOT_IN_GRAPH_WARNING,
            "services": [],
            "paths": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    rows = [
        {"service": r[0], "tier": r[1], "score": float(r[2])} for r in raw_rows
    ]
    return {
        "outcome": "answered_with_path",
        "services": [r["service"] for r in rows],
        "paths": [],
        "rows": rows,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---------------------------------------------------------------------------
# Q5: Who to Wake / Notify in Blast Radius (`who_to_notify`) — Track 03
# ---------------------------------------------------------------------------

Q5_WHO_TO_NOTIFY_TEMPLATE = """
MATCH path = (upstream:Service)-[r:DEPENDS_ON*0..{depth}]->(target:Service {{name: $service}})
WHERE ALL(edge IN relationships(path) WHERE ID(edge) >= 0)
WITH DISTINCT upstream, min(length(path)) AS hops
MATCH (team:Team)-[o:OWNS]->(upstream)
MATCH (person:Person)-[oc:ONCALL_FOR]->(team)
WHERE ID(o) >= 0 AND ID(oc) >= 0
  AND oc.from_ts <= $current_ts AND oc.to_ts >= $current_ts
RETURN upstream.name AS service,
       hops,
       team.name AS owning_team,
       team.slack_channel AS slack_channel,
       person.handle AS oncall_handle,
       person.name AS oncall_name,
       size([(inc:Incident)-[:AFFECTED]->(upstream) | inc]) AS past_incident_count
ORDER BY hops ASC, upstream.name ASC
"""


def who_to_notify(
    tenant_id: str,
    service: str,
    depth: int = 4,
    current_ts: int = DEFAULT_NOW_TS,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q5: Find owning teams, active on-call engineers, and past incident counts in the blast radius."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)
    clamped_depth = _clamp_depth(depth)

    resolved = resolve_entity(tenant_id, "Service", service, client=client, graph=g)
    if not resolved["exists"]:
        return {
            "outcome": "unknown_entity",
            "target_service": service,
            "depth": clamped_depth,
            "services": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    cypher = Q5_WHO_TO_NOTIFY_TEMPLATE.format(depth=clamped_depth)
    raw_rows = g.ro_query(
        cypher,
        {"service": resolved["identifier"], "current_ts": int(current_ts)},
    ).result_set

    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "target_service": resolved["identifier"],
            "depth": clamped_depth,
            "warning": NOT_IN_GRAPH_WARNING,
            "services": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    rows = [
        {
            "service": r[0],
            "hops": r[1],
            "owning_team": r[2],
            "slack_channel": r[3],
            "oncall_handle": r[4],
            "oncall_name": r[5],
            "past_incident_count": r[6],
        }
        for r in raw_rows
    ]
    return {
        "outcome": "answered_with_path",
        "target_service": resolved["identifier"],
        "depth": clamped_depth,
        "services": [r["service"] for r in rows],
        "rows": rows,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---------------------------------------------------------------------------
# Q6: Recent Changes in the Blast Radius (`recent_changes_in_radius`) — Track 01 & 03
# ---------------------------------------------------------------------------

Q6_RECENT_CHANGES_TEMPLATE = """
MATCH path = (upstream:Service)-[r:DEPENDS_ON*0..{depth}]->(target:Service {{name: $service}})
WHERE ALL(edge IN relationships(path) WHERE ID(edge) >= 0)
WITH DISTINCT upstream, [n IN nodes(path) | n.name] AS via_path
MATCH (c:Change)-[t:TOUCHED]->(m:CodeModule)-[:PART_OF]->(upstream)
MATCH (author:Person)-[au:AUTHORED]->(c)
WHERE ID(t) >= 0 AND ID(au) >= 0 AND c.at >= $since_ts
RETURN upstream.name AS service,
       c.pr_number AS pr_number,
       c.sha AS sha,
       c.summary AS summary,
       c.at AS changed_at,
       author.handle AS author_handle,
       m.path AS module_path,
       via_path
ORDER BY c.at DESC
"""


def recent_changes_in_radius(
    tenant_id: str,
    service: str,
    depth: int = 4,
    since_ts: int = DEFAULT_NOW_TS - 86400 * 7,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q6: Find all commits/PRs touching any module in the blast radius of `service` since `since_ts`."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)
    clamped_depth = _clamp_depth(depth)

    resolved = resolve_entity(tenant_id, "Service", service, client=client, graph=g)
    if not resolved["exists"]:
        return {
            "outcome": "unknown_entity",
            "target_service": service,
            "depth": clamped_depth,
            "services": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    cypher = Q6_RECENT_CHANGES_TEMPLATE.format(depth=clamped_depth)
    raw_rows = g.ro_query(
        cypher,
        {"service": resolved["identifier"], "since_ts": int(since_ts)},
    ).result_set

    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "target_service": resolved["identifier"],
            "depth": clamped_depth,
            "warning": NOT_IN_GRAPH_WARNING,
            "services": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    rows: list[dict[str, Any]] = []
    seen_pr_modules: set[tuple[int, str]] = set()
    services: list[str] = []

    for r in raw_rows:
        pr_num = int(r[1])
        mod_path = str(r[6])
        key = (pr_num, mod_path)
        if key in seen_pr_modules:
            continue
        seen_pr_modules.add(key)
        svc = r[0]
        if svc not in services:
            services.append(svc)
        rows.append(
            {
                "service": svc,
                "pr_number": pr_num,
                "sha": r[2],
                "summary": r[3],
                "changed_at": r[4],
                "author_handle": r[5],
                "module_path": mod_path,
                "via_path": r[7],
            }
        )

    return {
        "outcome": "answered_with_path",
        "target_service": resolved["identifier"],
        "depth": clamped_depth,
        "services": services,
        "rows": rows,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---------------------------------------------------------------------------
# Q7: Decision Lineage & Governance Lookup (`trace_decision_lineage`) — Track 03
# ---------------------------------------------------------------------------

Q7_TRACE_DECISION_LINEAGE = """
MATCH (s:Service {name: $service})
WITH s
MATCH (s)<-[g:GOVERNS]-(adr:ADR)
WHERE ID(g) >= 0 AND adr.status = 'active'
OPTIONAL MATCH (approver:Person)-[ap:APPROVED]->(adr)
WHERE ID(ap) >= 0
OPTIONAL MATCH (thread:SlackThread)-[d:DISCUSSES]->(adr)
WHERE ID(d) >= 0
RETURN adr.id AS adr_id,
       adr.title AS title,
       adr.decision AS decision_rule,
       adr.decided_at AS decided_at,
       collect(DISTINCT approver.handle) AS approved_by,
       collect(DISTINCT {thread_id: thread.id, channel: thread.channel, summary: thread.summary}) AS slack_context
ORDER BY adr.decided_at DESC
"""


def trace_decision_lineage(
    tenant_id: str,
    service: str,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q7: Trace active ADRs, approvers, and Slack threads governing `service`."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)

    resolved = resolve_entity(tenant_id, "Service", service, client=client, graph=g)
    if not resolved["exists"]:
        return {
            "outcome": "unknown_entity",
            "target_service": service,
            "services": [],
            "adrs": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    raw_rows = g.ro_query(
        Q7_TRACE_DECISION_LINEAGE,
        {"service": resolved["identifier"]},
    ).result_set

    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "target_service": resolved["identifier"],
            "warning": NOT_IN_GRAPH_WARNING,
            "services": [],
            "adrs": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    rows = [
        {
            "adr_id": r[0],
            "title": r[1],
            "decision_rule": r[2],
            "decided_at": r[3],
            "approved_by": r[4],
            "slack_context": [t for t in r[5] if t.get("thread_id") is not None],
        }
        for r in raw_rows
    ]
    return {
        "outcome": "answered_with_path",
        "target_service": resolved["identifier"],
        "services": [resolved["identifier"]],
        "adrs": [r["adr_id"] for r in rows],
        "rows": rows,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---------------------------------------------------------------------------
# Q8: Recall Agent Memory (`recall_memory`) — Track 02 Memory
# ---------------------------------------------------------------------------

Q8_RECALL_MEMORY = """
MATCH (s:Service {name: $service})
WITH s
MATCH (f:Finding)-[ab:ABOUT_SERVICE]->(s)
WHERE ID(ab) >= 0 AND f.category IN $categories
RETURN f.id AS finding_id,
       f.category AS category,
       f.text AS finding,
       f.agent_role AS recorded_by,
       f.session_id AS session_id,
       f.at AS recorded_at
ORDER BY f.at DESC
LIMIT $limit
"""


def recall_memory(
    tenant_id: str,
    service: str,
    categories: Optional[Sequence[str]] = None,
    limit: int = 10,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q8: Recall persistent agent findings for `service` across sessions."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)
    clamped_limit = _clamp_limit(limit, max_val=10)
    cat_list = list(categories) if categories else list(VALID_MEMORY_CATEGORIES)

    resolved = resolve_entity(tenant_id, "Service", service, client=client, graph=g)
    if not resolved["exists"]:
        return {
            "outcome": "unknown_entity",
            "target_service": service,
            "services": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    raw_rows = g.ro_query(
        Q8_RECALL_MEMORY,
        {
            "service": resolved["identifier"],
            "categories": cat_list,
            "limit": clamped_limit,
        },
    ).result_set

    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "target_service": resolved["identifier"],
            "warning": "No prior session findings recorded for this service.",
            "services": [],
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    rows = [
        {
            "finding_id": r[0],
            "category": r[1],
            "finding": r[2],
            "recorded_by": r[3],
            "session_id": r[4],
            "recorded_at": r[5],
        }
        for r in raw_rows
    ]
    return {
        "outcome": "answered_from_memory",
        "target_service": resolved["identifier"],
        "services": [resolved["identifier"]],
        "rows": rows,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---------------------------------------------------------------------------
# Q9: Write Agent Memory (`record_finding`) — Track 02 Memory (Write Path)
# ---------------------------------------------------------------------------

Q9_RECORD_FINDING = """
MATCH (s:Service {name: $service})
WITH s LIMIT 1
CREATE (f:Finding {
  id: $finding_id,
  category: $category,
  text: $text,
  at: $at,
  session_id: $session_id,
  agent_role: $agent_role
})-[:ABOUT_SERVICE]->(s)
RETURN f.id AS finding_id, f.at AS recorded_at
"""


def record_finding(
    tenant_id: str,
    service: str,
    category: str,
    text: str,
    session_id: str,
    agent_role: str,
    finding_id: Optional[str] = None,
    at: Optional[int] = None,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q9: Persist a `:Finding` node linked to `:Service` (obeying Limitation #2 `WITH s LIMIT 1`)."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)

    if category not in VALID_MEMORY_CATEGORIES:
        raise ValueError(
            f"category must be one of {VALID_MEMORY_CATEGORIES}, got {category!r}"
        )

    resolved = resolve_entity(tenant_id, "Service", service, client=client, graph=g)
    if not resolved["exists"]:
        return {
            "outcome": "unknown_entity",
            "target_service": service,
            "finding_id": None,
            "recorded_at": None,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    fid = finding_id or f"FIND-{uuid.uuid4().hex[:8].upper()}"
    ts = int(at) if at is not None else int(time.time())

    raw_rows = g.query(
        Q9_RECORD_FINDING,
        {
            "service": resolved["identifier"],
            "finding_id": fid,
            "category": category,
            "text": text,
            "at": ts,
            "session_id": session_id,
            "agent_role": agent_role,
        },
    ).result_set

    row = raw_rows[0]
    return {
        "outcome": "answered_from_memory",
        "target_service": resolved["identifier"],
        "finding_id": row[0],
        "recorded_at": row[1],
        "category": category,
        "session_id": session_id,
        "agent_role": agent_role,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---------------------------------------------------------------------------
# Q10: Multi-Agent Handoff State (`upsert_handoff` & `read_handoffs`) — Track 02
# ---------------------------------------------------------------------------

Q10A_UPSERT_HANDOFF = """
MATCH (s:Service {name: $service})
WITH s LIMIT 1
CREATE (h:Handoff {
  id: $handoff_id,
  pr_number: $pr_number,
  from_agent: $from_agent,
  to_agent: $to_agent,
  status: $status,
  payload: $payload,
  at: $at
})-[:FOR_SERVICE]->(s)
RETURN h.id AS handoff_id
"""

Q10B_READ_HANDOFFS = """
MATCH (h:Handoff {pr_number: $pr_number})-[r:FOR_SERVICE]->(s:Service)
WHERE ID(r) >= 0
RETURN h.id AS handoff_id,
       h.from_agent AS from_agent,
       h.to_agent AS to_agent,
       h.status AS status,
       h.payload AS payload,
       s.name AS service,
       h.at AS at
ORDER BY h.at ASC
"""


def upsert_handoff(
    tenant_id: str,
    pr_number: int,
    service: str,
    from_agent: str,
    to_agent: str,
    status: str,
    payload: str,
    handoff_id: Optional[str] = None,
    at: Optional[int] = None,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q10A: Write a multi-agent `:Handoff` state node in FalkorDB."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)

    resolved_svc = resolve_entity(tenant_id, "Service", service, client=client, graph=g)
    resolved_pr = resolve_entity(
        tenant_id, "Change", int(pr_number), client=client, graph=g
    )
    if not resolved_svc["exists"] or not resolved_pr["exists"]:
        return {
            "outcome": "unknown_entity",
            "pr_number": int(pr_number),
            "target_service": service,
            "handoff_id": None,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    hid = handoff_id or f"HO-{pr_number}-{uuid.uuid4().hex[:6].upper()}"
    ts = int(at) if at is not None else int(time.time())

    raw_rows = g.query(
        Q10A_UPSERT_HANDOFF,
        {
            "service": resolved_svc["identifier"],
            "handoff_id": hid,
            "pr_number": int(pr_number),
            "from_agent": from_agent,
            "to_agent": to_agent,
            "status": status,
            "payload": payload,
            "at": ts,
        },
    ).result_set

    return {
        "outcome": "answered_with_path",
        "pr_number": int(pr_number),
        "target_service": resolved_svc["identifier"],
        "handoff_id": raw_rows[0][0],
        "from_agent": from_agent,
        "to_agent": to_agent,
        "status": status,
        "at": ts,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def read_handoffs(
    tenant_id: str,
    pr_number: int,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Q10B: Read all multi-agent `:Handoff` records for `pr_number` ordered chronologically."""
    t0 = time.perf_counter()
    g = graph or select_tenant_graph(tenant_id, client=client)

    resolved_pr = resolve_entity(
        tenant_id, "Change", int(pr_number), client=client, graph=g
    )
    if not resolved_pr["exists"]:
        return {
            "outcome": "unknown_entity",
            "pr_number": int(pr_number),
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    raw_rows = g.ro_query(
        Q10B_READ_HANDOFFS,
        {"pr_number": int(pr_number)},
    ).result_set

    if not raw_rows:
        return {
            "outcome": "not_in_graph",
            "pr_number": int(pr_number),
            "warning": "No agent handoffs recorded yet for this PR.",
            "rows": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    rows = [
        {
            "handoff_id": r[0],
            "from_agent": r[1],
            "to_agent": r[2],
            "status": r[3],
            "payload": r[4],
            "service": r[5],
            "at": r[6],
        }
        for r in raw_rows
    ]
    return {
        "outcome": "answered_with_path",
        "pr_number": int(pr_number),
        "rows": rows,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
