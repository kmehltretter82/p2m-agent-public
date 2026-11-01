"""Numbers from the ledger. Proofs and reductions are always counted separately."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .ledger import Ledger

SOLVED = ("proved_locally", "disproved_locally")


def _seconds(row) -> float:
    if not row["finished_at"]:
        return 0.0
    started = datetime.fromisoformat(row["started_at"])
    return (datetime.fromisoformat(row["finished_at"]) - started).total_seconds()


def _peak_memory(row) -> float | None:
    path = Path(row["workdir"] or "") / "events.jsonl"
    if not path.is_file():
        return None
    peaks = [
        event["peak_total_gb"]
        for event in map(json.loads, path.read_text().splitlines())
        if event.get("event") == "memory"
    ]
    return max(peaks) if peaks else None


def summarize(rows: list) -> dict:
    finished = [r for r in rows if r["state"] == "finished"]
    solved = [r for r in finished if r["outcome"] in SOLVED]
    memory = [m for m in map(_peak_memory, rows) if m is not None]
    solved_cost = sum(r["cost_usd"] for r in solved)
    return {
        "attempts": len(rows),
        "finished": len(finished),
        "solved": len(solved),
        "solve_rate": round(len(solved) / len(finished), 3) if finished else None,
        "outcomes": dict(Counter(r["outcome"] or r["state"] for r in rows)),
        "turns": sum(r["turns"] for r in rows),
        "tokens": {
            "input": sum(r["input_tokens"] for r in rows),
            "output": sum(r["output_tokens"] for r in rows),
            "cache_read": sum(r["cache_read_tokens"] for r in rows),
            "cache_write": sum(r["cache_write_tokens"] for r in rows),
        },
        "estimated_cost_usd": round(sum(r["cost_usd"] for r in rows), 3),
        "estimated_cost_per_solve_usd": round(sum(r["cost_usd"] for r in rows) / len(solved), 3) if solved else None,
        "cost_of_solved_attempts_usd": round(solved_cost, 3),
        "wall_clock_seconds": round(sum(_seconds(r) for r in rows)),
        "gate_seconds": round(sum(r["gate_seconds"] for r in rows), 1),
        "gate_runs": sum(r["gate_runs"] for r in rows),
        "interventions": sum(r["interventions"] for r in rows),
        "peak_memory_gb": max(memory) if memory else None,
    }


def report(ledger: Ledger, label: str | None = None) -> dict:
    rows = ledger.attempts()
    if label:
        rows = [r for r in rows if r["label"].startswith(label)]
    arms: dict[str, list] = {}
    for row in rows:
        arms.setdefault(row["label"].rsplit(":", 1)[0] or "(unlabelled)", []).append(row)

    names = {t["id"]: t["theorem_name"] for t in ledger.targets()}
    submissions = ledger.submissions()
    return {
        "arms": {arm: summarize(group) for arm, group in sorted(arms.items())},
        "per_target": [
            {
                "attempt": r["id"], "label": r["label"], "target": names.get(r["target_id"]),
                "outcome": r["outcome"] or r["state"], "turns": r["turns"],
                "cost_usd": round(r["cost_usd"], 3), "seconds": round(_seconds(r)), "note": (r["note"] or "")[:160],
            }
            for r in rows
        ],
        # A reduction with open children is real progress, but it is not a proof.
        "submissions": {
            "proved": sum(s["outcome"] == "proved" for s in submissions),
            "proved_via_reduction": sum(s["outcome"] == "proved_via_reduction" for s in submissions),
            "disproved": sum(s["outcome"] == "disproved" for s in submissions),
            "reductions_with_open_obligations": sum(s["outcome"] == "reduction_open" for s in submissions),
            "rejected": sum(s["outcome"] == "rejected" for s in submissions),
            "pending_or_unknown": sum(s["outcome"] in (None, "pending") for s in submissions),
        },
    }
