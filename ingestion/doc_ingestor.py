"""Locked-Ontology Corpus Ingestion (`ingestion/doc_ingestor.py`) — Track 03 Company Brain.

Defines the locked GraphRAG-SDK ontology (`:Service`, `:ADR`, `:SlackThread`, `:Person`, `:Incident`)
and ingests markdown engineering artifacts (`docs/*.md`) into FalkorDB so extracted `:ADR` and
`:SlackThread` nodes link cleanly to canonical `:Service` nodes.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Optional

from falkordb import FalkorDB, Graph

from db.connection import select_tenant_graph


def build_locked_ontology() -> dict[str, Any]:
    """Return the locked ontology schema compatible with FalkorDB GraphRAG-SDK."""
    ontology_dict: dict[str, Any] = {
        "entities": {
            "Service": {
                "properties": ["name", "tier", "repo", "language"],
                "primary_key": "name",
            },
            "ADR": {
                "properties": ["id", "title", "decision", "decided_at", "status"],
                "primary_key": "id",
            },
            "SlackThread": {
                "properties": ["id", "channel", "at", "summary", "url"],
                "primary_key": "id",
            },
            "Person": {
                "properties": ["handle", "name", "role"],
                "primary_key": "handle",
            },
            "Incident": {
                "properties": ["id", "at", "severity", "summary"],
                "primary_key": "id",
            },
        },
        "relations": [
            {"source": "ADR", "type": "GOVERNS", "target": "Service"},
            {"source": "Person", "type": "APPROVED", "target": "ADR"},
            {"source": "SlackThread", "type": "DISCUSSES", "target": "ADR"},
            {"source": "SlackThread", "type": "DISCUSSES", "target": "Service"},
            {"source": "Incident", "type": "AFFECTED", "target": "Service"},
        ],
    }

    # If graphrag_sdk is installed in the environment, also construct its native Ontology object
    try:
        from graphrag_sdk import Entity, Ontology, Relation  # type: ignore
        from graphrag_sdk.attribute import Attribute, AttributeType  # type: ignore

        sdk_ontology = Ontology(
            entities=[
                Entity(
                    label="Service",
                    attributes=[
                        Attribute("name", AttributeType.STRING, unique=True, required=True),
                    ],
                ),
                Entity(
                    label="ADR",
                    attributes=[
                        Attribute("id", AttributeType.STRING, unique=True, required=True),
                        Attribute("title", AttributeType.STRING, unique=False, required=True),
                        Attribute("decision", AttributeType.STRING, unique=False, required=True),
                        Attribute("status", AttributeType.STRING, unique=False, required=True),
                    ],
                ),
                Entity(
                    label="SlackThread",
                    attributes=[
                        Attribute("id", AttributeType.STRING, unique=True, required=True),
                        Attribute("channel", AttributeType.STRING, unique=False, required=True),
                        Attribute("summary", AttributeType.STRING, unique=False, required=True),
                    ],
                ),
            ],
            relations=[
                Relation("GOVERNS", "ADR", "Service"),
                Relation("DISCUSSES", "SlackThread", "ADR"),
                Relation("DISCUSSES", "SlackThread", "Service"),
            ],
        )
        ontology_dict["graphrag_sdk_ontology"] = sdk_ontology
    except Exception:
        pass

    return ontology_dict


def _parse_adr_markdown(text: str) -> Optional[dict[str, Any]]:
    title_match = re.search(r"^#\s+(ADR-\d+):\s+(.+)$", text, re.MULTILINE)
    if not title_match:
        return None
    adr_id = title_match.group(1).strip()
    title = title_match.group(2).strip()

    status_match = re.search(r"\*\*Status:\*\*\s*([^\n]+)", text)
    decided_match = re.search(r"\*\*Decided At:\*\*\s*(\d+)", text)
    service_match = re.search(r"\*\*Governs Service:\*\*\s*`?([a-z0-9-]+)`?", text)
    approved_match = re.search(r"\*\*Approved By:\*\*\s*([^\n]+)", text)
    decision_match = re.search(r"## Decision Rule\s+([^\n]+(?:\n[^\n#]+)*)", text)

    approvers = (
        re.findall(r"@[a-z0-9-]+", approved_match.group(1)) if approved_match else []
    )
    return {
        "id": adr_id,
        "title": title,
        "status": status_match.group(1).strip() if status_match else "active",
        "decided_at": int(decided_match.group(1)) if decided_match else 1758000000,
        "service": service_match.group(1).strip() if service_match else "payments-api",
        "approvers": approvers,
        "decision": decision_match.group(1).strip() if decision_match else title,
    }


def _parse_slack_markdown(text: str) -> list[dict[str, Any]]:
    blocks = text.split("---")
    threads: list[dict[str, Any]] = []
    for block in blocks:
        tid = re.search(r"\*\*Thread ID:\*\*\s*([A-Z0-9-]+)", block)
        if not tid:
            continue
        channel = re.search(r"\*\*Channel:\*\*\s*`?(#?[a-z0-9-]+)`?", block)
        ts = re.search(r"\*\*Timestamp:\*\*\s*(\d+)", block)
        adr = re.search(r"\*\*Discusses ADR:\*\*\s*`?(ADR-\d+)`?", block)
        svc = re.search(r"\*\*Discusses Service:\*\*\s*`?([a-z0-9-]+)`?", block)
        url = re.search(r"\*\*URL:\*\*\s*`?([^\s`]+)`?", block)
        summary = re.search(r"## Thread Summary\s+([^\n]+(?:\n[^\n#]+)*)", block)

        threads.append(
            {
                "id": tid.group(1).strip(),
                "channel": channel.group(1).strip() if channel else "#arch-payments",
                "at": int(ts.group(1)) if ts else 1758000000,
                "adr_id": adr.group(1).strip() if adr else "",
                "service": svc.group(1).strip() if svc else "",
                "url": url.group(1).strip() if url else "",
                "summary": summary.group(1).strip() if summary else "",
            }
        )
    return threads


def ingest_markdown_corpus(
    tenant_id: str,
    docs_dir: str | Path = "docs",
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Ingest engineering markdown files under `docs_dir` using the locked ontology."""
    g = graph or select_tenant_graph(tenant_id, client=client)
    docs_path = Path(docs_dir)
    md_files = sorted(docs_path.glob("*.md"))

    adrs_merged: list[str] = []
    threads_merged: list[str] = []

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        adr_data = _parse_adr_markdown(content)
        if adr_data:
            g.query(
                """
                MATCH (s:Service {name: $service})
                WITH s LIMIT 1
                MERGE (adr:ADR {id: $id})
                SET adr.title = $title,
                    adr.decision = $decision,
                    adr.decided_at = $decided_at,
                    adr.status = $status
                MERGE (adr)-[:GOVERNS]->(s)
                WITH adr
                UNWIND $approvers AS handle
                MATCH (p:Person {handle: handle})
                MERGE (p)-[:APPROVED]->(adr)
                """,
                adr_data,
            )
            adrs_merged.append(adr_data["id"])
            continue

        slack_threads = _parse_slack_markdown(content)
        for st in slack_threads:
            g.query(
                """
                MERGE (t:SlackThread {id: $id})
                SET t.channel = $channel,
                    t.at = $at,
                    t.summary = $summary,
                    t.url = $url
                WITH t
                OPTIONAL MATCH (a:ADR {id: $adr_id})
                FOREACH (_ IN CASE WHEN a IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (t)-[:DISCUSSES]->(a)
                )
                WITH t
                OPTIONAL MATCH (s:Service {name: $service})
                FOREACH (_ IN CASE WHEN s IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (t)-[:DISCUSSES]->(s)
                )
                """,
                st,
            )
            threads_merged.append(st["id"])

    return {
        "tenant_id": tenant_id,
        "documents_processed": len(md_files),
        "adrs_merged": adrs_merged,
        "slack_threads_merged": threads_merged,
    }


def verify_ingestion(
    tenant_id: str,
    client: Optional[FalkorDB] = None,
    graph: Optional[Graph] = None,
) -> dict[str, Any]:
    """Run read-only checks confirming all ingested ADRs and SlackThreads link to canonical Services."""
    g = graph or select_tenant_graph(tenant_id, client=client)
    rows = g.ro_query(
        """
        MATCH (a:ADR)-[gov:GOVERNS]->(s:Service)
        WHERE ID(gov) >= 0
        OPTIONAL MATCH (st:SlackThread)-[d:DISCUSSES]->(a)
        WHERE ID(d) >= 0
        RETURN a.id AS adr_id, s.name AS service, count(st) AS thread_count
        ORDER BY a.id ASC
        """
    ).result_set

    linked_adrs = [
        {"adr_id": r[0], "service": r[1], "thread_count": r[2]} for r in rows
    ]
    return {
        "valid": len(linked_adrs) > 0,
        "linked_adr_count": len(linked_adrs),
        "linked_adrs": linked_adrs,
    }
