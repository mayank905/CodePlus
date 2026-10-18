"""TDD Verification Suite for Ticket 06: 3-Panel War-Room UI & 3-Minute Judge Demo Harness."""

from __future__ import annotations

from demo_harness import (
    build_war_room_panels,
    run_all_judge_demo_moves,
    run_demo_move_1_depth_contrast,
    run_demo_move_2_pr_402_review,
    run_demo_move_3_memory_and_tenant_isolation,
    run_demo_move_4_honest_epistemics,
)


def test_move_1_depth_1_vs_depth_4_contrast(db_client):
    move1 = run_demo_move_1_depth_contrast(client=db_client)

    d1 = move1["depth_1"]
    d4 = move1["depth_4"]
    assert d1["outcome"] == "answered_with_path"
    assert set(d1["blast_radius"]["services"]) == {"cart-service", "ledger-core"}

    assert d4["outcome"] == "answered_with_path"
    assert "status-page" in d4["blast_radius"]["services"]
    assert len(d4["blast_radius"]["services"]) == 5

    # Verify PageRank ranks payments-api #1
    assert d4["pagerank"]["rows"][0]["service"] == "payments-api"


def test_move_2_pr_402_review_lineage_and_oncall(db_client):
    move2 = run_demo_move_2_pr_402_review(client=db_client)
    review = move2["pr_review"]

    assert review["outcome"] == "answered_with_path"
    assert review["guard_status"] == "PASS"
    assert "payments/retry_policy.py" in review["impact"]["touched_modules"]
    assert review["target_service"] == "payments-api"
    assert "ADR-014" in review["lineage"]["adrs"]

    slack_channels = {
        sc["channel"]
        for row in review["lineage"]["rows"]
        for sc in row.get("slack_context", [])
    }
    assert "#arch-payments" in slack_channels

    oncall_handles = {r["oncall_handle"] for r in review["notify"]["rows"]}
    assert "@sarah-staff-eng" in oncall_handles
    assert review["finding"]["finding_id"] is not None


def test_move_3_persistent_memory_and_tenant_isolation(db_client):
    move3 = run_demo_move_3_memory_and_tenant_isolation(client=db_client)

    prod_recall = move3["fintech_prod_memory"]
    sandbox_recall = move3["sandbox_tenant_memory"]

    assert prod_recall["outcome"] == "answered_from_memory"
    assert prod_recall["guard_status"] == "PASS"
    assert len(prod_recall["memory"]["rows"]) >= 1

    # Complete tenant isolation on codepulse:sandbox-tenant
    assert sandbox_recall["outcome"] in ("not_in_graph", "unknown_entity")
    assert sandbox_recall.get("memory", {}).get("rows", []) == []


def test_move_4_honest_epistemics_not_in_graph_and_unknown_entity(db_client):
    move4 = run_demo_move_4_honest_epistemics(client=db_client)

    island = move4["legacy_reporting"]
    unknown = move4["auth_gateway"]

    assert island["outcome"] == "not_in_graph"
    assert "visibility gap" in island["summary"].lower()

    assert unknown["outcome"] == "unknown_entity"
    assert unknown["traversals_executed"] == 0


def test_war_room_ui_view_model_and_full_demo_suite(db_client):
    suite = run_all_judge_demo_moves(client=db_client)
    assert set(suite.keys()) == {"move_1", "move_2", "move_3", "move_4"}

    panels = build_war_room_panels(suite["move_2"]["pr_review"])
    assert set(panels.keys()) == {"left_panel", "center_panel", "right_panel"}

    center = panels["center_panel"]
    assert center["outcome_badge"] == "Answered with Path"
    assert len(center["dependency_paths"]) >= 5
    assert "ADR-014" in [a["adr_id"] for a in center["adr_lineage"]]
    assert len(center["oncall_responders"]) >= 3

    right = panels["right_panel"]
    assert right["guard_badge"] == "PASS"
    assert len(right["mcp_traces"]) >= 8
    assert [a["agent_role"] for a in right["agent_traces"]] == [
        "memory_scout",
        "topology_analyst",
        "release_governor",
    ]
