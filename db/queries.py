"""Pre-Baked Cypher Query Catalogue (`db/queries.py`).

Ticket 01 implements Q0 (`resolve_entity`) and its parameterized Cypher constants.
Subsequent tickets add Q1-Q10 in this same catalogue without any runtime LLM-generated Cypher.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from falkordb import FalkorDB, Graph

from db.connection import select_tenant_graph

EntityType = Literal["Service", "CodeModule", "ADR", "Change"]
OutcomeType = Literal[
    "answered_with_path",
    "answered_from_memory",
    "not_in_graph",
    "unknown_entity",
]

# ---------------------------------------------------------------------------
# Q0: Entity Resolver Queries (Mandatory Read-Only Pre-Check)
# ---------------------------------------------------------------------------

Q0_RESOLVE_SERVICE = """
MATCH (s:Service {name: $name})
RETURN s.name AS name, s.tier AS tier, s.repo AS repo, s.language AS language
"""

Q0_RESOLVE_CODE_MODULE = """
MATCH (m:CodeModule {path: $path})
OPTIONAL MATCH (m)-[:PART_OF]->(s:Service)
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


def resolve_entity(
    tenant_id: str,
    entity_type: EntityType,
    identifier: str | int,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Run mandatory Q0 read-only lookup to verify an entity exists before any traversal.

    Returns a dictionary containing:
      - `exists` (`bool`): True if found in `codepulse:{tenant_id}`, False otherwise
      - `outcome` (`str`): `"resolved"` if found, `"unknown_entity"` if missing
      - `entity_type` (`EntityType`): The queried entity label
      - `identifier`: The normalized identifier
      - `entity` (`dict | None`): Canonical node properties if found, else `None`
    """
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
