"""CodePulse Three-Panel Explainable War-Room UI (`app.py`).

Run with:
    .\.venv\Scripts\streamlit.exe run app.py

Provides:
- Left Panel: Tenant selector (`codepulse:fintech-prod` vs `codepulse:sandbox-tenant`),
  depth toggle (`1` vs `4`), Hallucination Guard test toggle, 4 One-Click Judge Demo Moves,
  and free-form Service / PR query controls.
- Center Panel: Epistemic Outcome Badge (`Answered with Path`, `Answered from Memory`,
  `Not in Graph`, `Unknown Entity`), multi-hop dependency paths with `sync`/`async` edge
  badges, `algo.pageRank` load-bearing scores, `ADR-014` + `#arch-payments` lineage, and
  active on-call responders.
- Right Panel: Hallucination Guard status badge (`PASS` vs `BLOCKED + FALLBACK`),
  3-agent handoff timeline (`memory_scout -> topology_analyst -> release_governor`),
  and live typed MCP tool audit trace (validated args, latency_ms, row_count).
"""

from __future__ import annotations

from typing import Any

from agents.orchestrator import run_pr_review_workflow, run_service_query_workflow
from db.connection import FINTECH_PROD_TENANT, SANDBOX_TENANT
from db.seed import seed_all
from demo_harness import (
    OUTCOME_BADGE_MAP,
    build_war_room_panels,
    run_demo_move_1_depth_contrast,
    run_demo_move_2_pr_402_review,
    run_demo_move_3_memory_and_tenant_isolation,
    run_demo_move_4_honest_epistemics,
)
from mcp_server.server import list_registered_tools

OUTCOME_COLOR_MAP: dict[str, str] = {
    "Answered with Path": "#16a34a",
    "Answered from Memory": "#2563eb",
    "Not in Graph": "#d97706",
    "Unknown Entity": "#dc2626",
}


def render_war_room_app() -> None:
    """Render the 3-panel CodePulse War-Room UI in Streamlit."""
    import streamlit as st

    st.set_page_config(
        page_title="CodePulse War-Room | FalkorDB Graph Intelligence",
        layout="wide",
    )

    st.markdown(
        """
        <div style="display:flex;justify-content:space-between;align-items:center;padding-bottom:8px;border-bottom:1px solid #334155;">
            <div>
                <h2 style="margin:0;">CodePulse Explainable Architecture & Release War-Room</h2>
                <span style="color:#94a3b8;font-size:0.9rem;">
                    Powered by FalkorDB Multi-Graph • Zero LLM-Written Cypher • Deterministic Hallucination Guard
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "active_result" not in st.session_state:
        st.session_state["active_result"] = run_pr_review_workflow(
            tenant_id=FINTECH_PROD_TENANT,
            pr_number=402,
            session_id="war-room-init",
            depth=4,
        )
        st.session_state["active_title"] = "Move 2 — PR #402 Multi-Agent Review"
        st.session_state["move1_comparison"] = None

    left_col, center_col, right_col = st.columns([1.15, 2.1, 1.55], gap="medium")

    # =========================================================================
    # LEFT PANEL: Controls & 4 Judge Demo Triggers
    # =========================================================================
    with left_col:
        st.subheader("1. Controls & Triggers")
        tenant_label = st.selectbox(
            "FalkorDB Tenant Graph",
            options=[
                f"codepulse:{FINTECH_PROD_TENANT}",
                f"codepulse:{SANDBOX_TENANT}",
            ],
            index=0,
        )
        tenant_id = tenant_label.split("codepulse:")[-1]

        depth = st.radio(
            "Traversal Hop Depth (`*1..depth`)",
            options=[1, 4],
            index=1,
            horizontal=True,
        )

        inject_hallucination = st.checkbox(
            "Simulate LLM Hallucination (`billing-v2` + `ADR-999`)",
            value=False,
            help="Injects ungrounded service and ADR tokens to demonstrate `guard.py` blocking and deterministic fallback.",
        )
        simulated_draft = (
            "Impact extends to `billing-v2` and violates `ADR-999`."
            if inject_hallucination
            else None
        )

        st.markdown("---")
        st.markdown("**3-Minute Judge Demo Script**")

        if st.button("Move 1: Depth 1 vs Depth 4 Contrast", use_container_width=True):
            m1 = run_demo_move_1_depth_contrast(tenant_id=tenant_id)
            st.session_state["move1_comparison"] = m1
            st.session_state["active_result"] = m1["depth_4"] if depth == 4 else m1["depth_1"]
            st.session_state["active_title"] = m1["move"]

        if st.button("Move 2: Review PR #402 (Lineage + On-Call)", use_container_width=True):
            st.session_state["move1_comparison"] = None
            st.session_state["active_result"] = run_pr_review_workflow(
                tenant_id=tenant_id,
                pr_number=402,
                session_id="judge-move-2",
                depth=depth,
                simulated_llm_draft=simulated_draft,
            )
            st.session_state["active_title"] = "Move 2 — PR #402 Blast Radius & Decision Lineage"

        if st.button("Move 3: Cross-Session Memory & Isolation", use_container_width=True):
            st.session_state["move1_comparison"] = None
            m3 = run_demo_move_3_memory_and_tenant_isolation()
            st.session_state["active_result"] = (
                m3["fintech_prod_memory"]
                if tenant_id == FINTECH_PROD_TENANT
                else m3["sandbox_tenant_memory"]
            )
            st.session_state["active_title"] = m3["move"]

        if st.button("Move 4a: `legacy-reporting` (Not in Graph)", use_container_width=True):
            st.session_state["move1_comparison"] = None
            m4 = run_demo_move_4_honest_epistemics(tenant_id=tenant_id)
            st.session_state["active_result"] = m4["legacy_reporting"]
            st.session_state["active_title"] = "Move 4a — Disconnected Service (`Not in Graph`)"

        if st.button("Move 4b: `auth-gateway` (Unknown Entity)", use_container_width=True):
            st.session_state["move1_comparison"] = None
            m4 = run_demo_move_4_honest_epistemics(tenant_id=tenant_id)
            st.session_state["active_result"] = m4["auth_gateway"]
            st.session_state["active_title"] = "Move 4b — Non-Existent Service (`Unknown Entity`)"

        st.markdown("---")
        st.markdown("**Free-Form Service / PR Query**")
        custom_query = st.text_input("Service Name or PR (e.g. `payments-api` or `PR-402`)", value="payments-api")
        prefer_mem = st.checkbox("Prefer Recall from Graph Memory", value=False)
        if st.button("Run Service / PR Analysis", use_container_width=True):
            st.session_state["move1_comparison"] = None
            q_str = custom_query.strip()
            pr_cleaned = q_str.upper().removeprefix("PR-").removeprefix("PR#").removeprefix("#").strip()
            if pr_cleaned.isdigit():
                pr_num = int(pr_cleaned)
                st.session_state["active_result"] = run_pr_review_workflow(
                    tenant_id=tenant_id,
                    pr_number=pr_num,
                    depth=depth,
                    session_id="custom-pr-query",
                    simulated_llm_draft=simulated_draft,
                )
                st.session_state["active_title"] = f"PR Review: `PR #{pr_num}` (depth={depth})"
            else:
                st.session_state["active_result"] = run_service_query_workflow(
                    tenant_id=tenant_id,
                    service=q_str,
                    depth=depth,
                    session_id="custom-query",
                    prefer_memory=prefer_mem,
                    simulated_llm_draft=simulated_draft,
                )
                st.session_state["active_title"] = f"Service Query: `{q_str}` (depth={depth})"

        if st.button("Reset & Reseed Golden Graph", use_container_width=True):
            seed_all(reset=True)
            st.session_state["active_result"] = None
            st.session_state["active_title"] = ""
            st.session_state["move1_comparison"] = None
            st.success("Reseeded `codepulse:fintech-prod` and `codepulse:sandbox-tenant`. Previous results cleared.")

    if st.session_state.get("active_result") is None:
        with center_col:
            st.subheader("2. Visual Path & Epistemic Outcome")
            st.info("Select a Judge Demo Move or run a Service / PR query from the Left Panel to view graph results.")
        with right_col:
            st.subheader("3. Live MCP & Guard Audit Trace")
            st.info("No active execution trace. Trigger an action from the Left Panel.")
            with st.expander("Registered Typed MCP Tool Surface (12 Tools)", expanded=False):
                st.dataframe(list_registered_tools(), use_container_width=True, hide_index=True)
        return

    panels = build_war_room_panels(
        st.session_state["active_result"],
        tenant_id=tenant_id,
        depth=depth,
    )
    center = panels["center_panel"]
    right = panels["right_panel"]

    # =========================================================================
    # CENTER PANEL: Visual Path & Epistemic Outcome
    # =========================================================================
    with center_col:
        st.subheader("2. Visual Path & Epistemic Outcome")
        badge_text = center["outcome_badge"]
        badge_color = OUTCOME_COLOR_MAP.get(badge_text, "#475569")

        st.markdown(
            f"""
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
                <span style="background:{badge_color};color:white;padding:6px 14px;border-radius:999px;font-weight:700;font-size:0.95rem;">
                    {badge_text}
                </span>
                <span style="color:#64748b;font-weight:600;">{st.session_state.get("active_title", "")}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.info(center["summary"])

        if st.session_state.get("move1_comparison"):
            m1 = st.session_state["move1_comparison"]
            c1, c2 = st.columns(2)
            with c1:
                st.metric(
                    "Depth = 1 Upstream Callers",
                    len(m1["depth_1"]["blast_radius"]["services"]),
                    ", ".join(m1["depth_1"]["blast_radius"]["services"]),
                )
            with c2:
                st.metric(
                    "Depth = 4 Upstream Callers",
                    len(m1["depth_4"]["blast_radius"]["services"]),
                    "Reveals status-page (3 hops away)",
                )

        if center["dependency_paths"]:
            st.markdown("**Discovered Multi-Hop Dependency Paths (`sync` / `async` parallel edges preserved)**")
            for p in center["dependency_paths"]:
                edge_badges = " → ".join(f"`{k}`" for k in p["edge_kinds"])
                st.markdown(
                    f"- **Hop {p['hops']}** (`{p['service']}`): `{p['path_display']}` &nbsp; [{edge_badges}]"
                )
            with st.expander("Mermaid Topology Graph", expanded=True):
                st.code(center["mermaid_diagram"], language="mermaid")

        if center["adr_lineage"]:
            st.markdown("**Company Brain Decision Lineage (`:ADR` & `:SlackThread`)**")
            for adr in center["adr_lineage"]:
                st.warning(
                    f"**{adr['adr_id']}: {adr['title']}**\n\n"
                    f"- **Rule:** {adr['decision_rule']}\n"
                    f"- **Approved By:** {', '.join(adr.get('approved_by', []))}\n"
                    f"- **Slack Context:** "
                    + ", ".join(
                        f"`{s['channel']}` ({s['summary']})"
                        for s in adr.get("slack_context", [])
                    )
                )

        if center["oncall_responders"]:
            st.markdown("**Who to Notify in Blast Radius (`Q5`)**")
            st.dataframe(center["oncall_responders"], use_container_width=True, hide_index=True)

        if center["pagerank_rows"]:
            with st.expander("Load-Bearing Services (`CALL algo.pageRank('Service', 'CALLS')`)", expanded=False):
                st.dataframe(center["pagerank_rows"], use_container_width=True, hide_index=True)

        if center["memory_rows"]:
            st.markdown("**Persisted Graph Memory (`:Finding` nodes)**")
            st.dataframe(center["memory_rows"], use_container_width=True, hide_index=True)

    # =========================================================================
    # RIGHT PANEL: Live MCP & Hallucination Guard Audit Trace
    # =========================================================================
    with right_col:
        st.subheader("3. Live MCP & Guard Audit Trace")
        guard_color = "#16a34a" if right["guard_status"] == "PASS" else "#dc2626"
        st.markdown(
            f"""
            <div style="border:1px solid {guard_color};padding:10px 14px;border-radius:8px;margin-bottom:12px;">
                <strong>Hallucination Guard (`guard.accepts`):</strong>
                <span style="background:{guard_color};color:white;padding:3px 10px;border-radius:6px;margin-left:8px;font-weight:700;">
                    {right['guard_badge']}
                </span>
                <div style="font-size:0.85rem;color:#64748b;margin-top:4px;">
                    Reason: <code>{right['guard_reason']}</code>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("**Multi-Agent Execution & Handoffs (`Track 02`)**")
        for agent in right["agent_traces"]:
            st.markdown(
                f"- **`{agent['agent_role']}`** ({agent['status']}): {agent['summary']}"
            )

        if right["handoff_rows"]:
            with st.expander("FalkorDB `:Handoff` State Nodes (`Q10`)", expanded=False):
                st.dataframe(right["handoff_rows"], use_container_width=True, hide_index=True)

        st.markdown("**Typed MCP Tool Execution Log (Zero Raw Cypher)**")
        for idx, trace in enumerate(right["mcp_traces"], start=1):
            st.markdown(
                f"`#{idx}` **`{trace['tool_name']}`** — `{trace['latency_ms']} ms` • "
                f"`{trace['row_count']} rows` • `{OUTCOME_BADGE_MAP.get(trace['outcome'], trace['outcome'])}`\n"
                f"  - Args: `{trace['validated_args']}`"
            )

        with st.expander("Registered Typed MCP Tool Surface (12 Tools)", expanded=False):
            st.dataframe(list_registered_tools(), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    render_war_room_app()
