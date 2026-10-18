"""TDD Verification Suite for Ticket 03: Track 03 Company Brain (GraphRAG Ingestion + Q5-Q7)."""

from __future__ import annotations

from db.connection import FINTECH_PROD_TENANT, select_tenant_graph
from db.queries import (
    Q5_WHO_TO_NOTIFY_TEMPLATE,
    Q6_RECENT_CHANGES_TEMPLATE,
    Q7_TRACE_DECISION_LINEAGE,
    recent_changes_in_radius,
    trace_decision_lineage,
    who_to_notify,
)
from db.seed import DEMO_NOW_TS
from ingestion.doc_ingestor import build_locked_ontology, ingest_markdown_corpus, verify_ingestion


def test_locked_ontology_and_markdown_corpus_ingestion(db_client):
    ontology = build_locked_ontology()
    assert {"Service", "ADR", "SlackThread", "Person", "Incident"}.issubset(
        set(ontology["entities"].keys())
    )

    ingested = ingest_markdown_corpus(FINTECH_PROD_TENANT, client=db_client)
    assert ingested["documents_processed"] == 5
    assert "ADR-014" in ingested["adrs_merged"]
    assert "INC-99" in ingested["incidents_merged"]
    assert "SLACK-ARCH-881" in ingested["slack_threads_merged"]

    verification = verify_ingestion(FINTECH_PROD_TENANT, client=db_client)
    assert verification["valid"] is True
    assert verification["linked_adr_count"] >= 2
    assert verification["linked_incident_count"] >= 2


def test_q5_who_to_notify_in_blast_radius(db_client):
    res = who_to_notify(
        FINTECH_PROD_TENANT,
        service="payments-api",
        depth=4,
        current_ts=DEMO_NOW_TS,
        client=db_client,
    )
    assert res["outcome"] == "answered_with_path"
    oncall_handles = {r["oncall_handle"] for r in res["rows"]}
    assert "@sarah-staff-eng" in oncall_handles
    assert "@alex-checkout-oncall" in oncall_handles
    assert "@priya-sre-oncall" in oncall_handles

    # Verify past_incident_count computed via size([...]) pattern comprehension (Limitation #4)
    pay_rows = [r for r in res["rows"] if r["service"] == "payments-api"]
    assert pay_rows and pay_rows[0]["past_incident_count"] >= 1


def test_q6_recent_changes_in_radius(db_client):
    res = recent_changes_in_radius(
        FINTECH_PROD_TENANT,
        service="payments-api",
        depth=4,
        since_ts=DEMO_NOW_TS - 86400,
        client=db_client,
    )
    assert res["outcome"] == "answered_with_path"
    pr_numbers = [r["pr_number"] for r in res["rows"]]
    assert 402 in pr_numbers
    assert 401 in pr_numbers
    assert 398 in pr_numbers
    # Ordered newest first: PR 402 (-1800s) before PR 401 (-7200s) before PR 398 (-14400s)
    assert pr_numbers.index(402) < pr_numbers.index(401) < pr_numbers.index(398)


def test_q7_trace_decision_lineage_for_payments_api(db_client):
    res = trace_decision_lineage(
        FINTECH_PROD_TENANT,
        service="payments-api",
        client=db_client,
    )
    assert res["outcome"] == "answered_with_path"
    assert "ADR-014" in res["adrs"]

    adr_14 = next(r for r in res["rows"] if r["adr_id"] == "ADR-014")
    assert "500ms" in adr_14["decision_rule"]
    assert "@sarah-staff-eng" in adr_14["approved_by"]
    channels = [t["channel"] for t in adr_14["slack_context"] if t.get("channel")]
    assert "#arch-payments" in channels


def test_q5_to_q7_explain_plans_succeed(db_client):
    g = select_tenant_graph(FINTECH_PROD_TENANT, client=db_client)
    q5_cypher = Q5_WHO_TO_NOTIFY_TEMPLATE.format(depth=4)
    q6_cypher = Q6_RECENT_CHANGES_TEMPLATE.format(depth=4)

    for cypher, params in [
        (q5_cypher, {"service": "payments-api", "current_ts": DEMO_NOW_TS}),
        (q6_cypher, {"service": "payments-api", "since_ts": DEMO_NOW_TS - 86400}),
        (Q7_TRACE_DECISION_LINEAGE, {"service": "payments-api"}),
    ]:
        plan = g.explain(cypher, params)
        plan_text = "\n".join(str(line) for line in plan)
        assert "Index Scan" in plan_text, f"Expected Index Scan in plan:\n{plan_text}"
