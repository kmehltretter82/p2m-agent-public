"""What a person needs to see before approving one exact file for public submission."""

from __future__ import annotations

import json

from .ledger import Ledger, sha256


def approval_summary(ledger: Ledger, candidate_id: int, mode: str, explanation: str | None) -> dict:
    candidate = ledger.candidate(candidate_id)
    target = ledger.target(candidate["target_id"])
    gate = ledger.latest_gate(candidate_id, mode)
    report = json.loads(gate["report"]) if gate else {}
    warnings = [
        {"check": c["name"], "detail": c["detail"]}
        for c in report.get("checks", [])
        if c.get("warning")
    ]
    return {
        "theorem_name": target.theorem_name,
        "theorem_id": target.theorem_id,
        "mode": mode,
        "statement": target.mirror_source(),
        "file_sha256": candidate["sha256"],
        "file": candidate["source"],
        "gate_passed": bool(gate and gate["passed"]),
        "gate_warnings": warnings,
        "axioms": report.get("axioms", []),
        "obligations": report.get("obligations", []),
        "explanation_sha256": sha256(explanation or ""),
        "explanation": explanation,
        "publishes": "under your account, publicly, under the platform's Apache 2.0 terms",
    }
