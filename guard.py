"""Deterministic Post-Generation Sentence Verifier (`guard.py`).

Blocks hallucinated hyphenated service tokens and `ADR-\\d+` identifiers that were not returned
by the FalkorDB graph traversal, and provides a deterministic row-backed fallback template.
"""

from __future__ import annotations

import re
from typing import Any

SERVICE_TOKEN = re.compile(r"(?<![@#\w-])[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b")
ADR_TOKEN = re.compile(r"\bADR-\d+\b")

# Common English/engineering hyphenated compound words that are not microservice names
ALLOWED_STOP_HYPHENS = {
    "fail-fast",
    "thread-pool",
    "multi-hop",
    "multi-tenant",
    "multi-agent",
    "on-call",
    "tier-1",
    "tier-2",
    "tier-3",
    "sev-1",
    "sev-2",
    "sev-3",
    "inc-99",
    "real-time",
    "read-only",
    "end-to-end",
    "cross-session",
    "cross-tenant",
    "load-bearing",
    "pre-baked",
    "non-existent",
    "short-circuit",
    "fintech-prod",
    "sandbox-tenant",
    "live-session",
}


def accepts(sentence: str, answer: dict[str, Any]) -> tuple[bool, str]:
    """Verify that `sentence` does not invent services, ADRs, or false impact claims."""
    known_services = (
        set(answer.get("services", []))
        | set(answer.get("allowed_tokens", []))
        | {hop for path in answer.get("paths", []) for hop in path}
    )
    if answer.get("target_service"):
        known_services.add(str(answer["target_service"]))
    if answer.get("origin_service"):
        known_services.add(str(answer["origin_service"]))
    if answer.get("from_service"):
        known_services.add(str(answer["from_service"]))
    if answer.get("to_service"):
        known_services.add(str(answer["to_service"]))

    invented_services = (
        set(SERVICE_TOKEN.findall(sentence)) - known_services - ALLOWED_STOP_HYPHENS
    )
    if invented_services:
        return (
            False,
            f"Hallucinated services not in graph result: {sorted(invented_services)}",
        )

    known_adrs = set(answer.get("adrs", []))
    invented_adrs = set(ADR_TOKEN.findall(sentence)) - known_adrs
    if invented_adrs:
        return (
            False,
            f"Hallucinated ADRs not in graph result: {sorted(invented_adrs)}",
        )

    if (
        not answer.get("services")
        and "no " not in sentence.lower()
        and "not " not in sentence.lower()
        and "0 " not in sentence.lower()
        and "zero" not in sentence.lower()
    ):
        return False, "Claimed impact when traversal returned empty"

    return True, "ok"


def build_deterministic_summary(answer: dict[str, Any]) -> str:
    """Build a 100% deterministic, row-backed summary when the LLM sentence is rejected."""
    outcome = answer.get("outcome", "unknown_entity")
    target = (
        answer.get("target_service")
        or answer.get("origin_service")
        or answer.get("identifier")
        or "target"
    )

    if outcome == "unknown_entity":
        return f"Entity '{target}' is not registered in this tenant's FalkorDB graph (0 queries executed)."

    if outcome == "not_in_graph":
        return (
            f"Service '{target}' exists in FalkorDB, but has 0 modelled dependency edges. "
            "Treat this as a visibility gap, not proof of zero impact."
        )

    services = answer.get("services", [])
    adrs = answer.get("adrs", [])
    adr_clause = f" governed by {', '.join(adrs)}" if adrs else ""
    if services:
        return (
            f"Verified FalkorDB traversal from '{target}' impacts {len(services)} upstream "
            f"service(s): {', '.join(services)}{adr_clause}."
        )
    return f"No upstream service impact found for '{target}'."


def verify_or_fallback(sentence: str, answer: dict[str, Any]) -> dict[str, Any]:
    """Run `accepts(sentence, answer)` and fall back to deterministic prose if rejected."""
    ok, reason = accepts(sentence, answer)
    if ok:
        return {
            "accepted": True,
            "guard_status": "PASS",
            "reason": "ok",
            "original_text": sentence,
            "final_text": sentence,
        }
    fallback = build_deterministic_summary(answer)
    return {
        "accepted": False,
        "guard_status": "BLOCKED_FALLBACK",
        "reason": reason,
        "original_text": sentence,
        "final_text": fallback,
    }
