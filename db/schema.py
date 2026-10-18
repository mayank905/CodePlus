"""FalkorDB label-property index definitions (`db/schema.py`)."""

from __future__ import annotations

from typing import Optional

from falkordb import FalkorDB, Graph

from db.connection import select_tenant_graph

# All 11 mandatory indexes from Section 3.3 of CODEPULSE_IMPLEMENTATION_PLAN.md
INDEX_SPECS: list[tuple[str, str]] = [
    ("Service", "name"),
    ("Service", "tier"),
    ("CodeModule", "path"),
    ("Endpoint", "id"),
    ("Team", "name"),
    ("Person", "handle"),
    ("Change", "pr_number"),
    ("Change", "at"),
    ("ADR", "id"),
    ("Finding", "at"),
    ("Handoff", "pr_number"),
]

INDEX_QUERIES: list[str] = [
    f"CREATE INDEX FOR (n:{label}) ON (n.{prop})" for label, prop in INDEX_SPECS
]


def ensure_indexes(
    tenant_id: str,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> list[dict[str, str]]:
    """Create all 11 range indexes idempotently on `codepulse:{tenant_id}`."""
    g = graph or select_tenant_graph(tenant_id, client=client)
    results: list[dict[str, str]] = []

    for (label, prop), cypher in zip(INDEX_SPECS, INDEX_QUERIES):
        try:
            g.query(cypher)
            status = "created"
        except Exception as exc:
            msg = str(exc).lower()
            if "already indexed" in msg or "already exists" in msg:
                status = "already_exists"
            else:
                raise
        results.append(
            {
                "label": label,
                "property": prop,
                "cypher": cypher,
                "status": status,
            }
        )
    return results


def list_indexes(
    tenant_id: str,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> list[list]:
    """Return active indexes reported by `CALL db.indexes()`."""
    g = graph or select_tenant_graph(tenant_id, client=client)
    res = g.ro_query("CALL db.indexes()")
    return res.result_set
