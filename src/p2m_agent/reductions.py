"""M6: reductions, whose imported theorems remain obligations until they are proved.

A published `formal_statement` cannot be edited afterwards, only deprecated, and a public child
becomes an obligation other people may spend effort on. So creating a public child requires a
named approval here, and private is what you get if you ask for nothing else.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .ledger import Ledger
from .platform import PlatformClient, PlatformError


class ChildRefused(RuntimeError):
    pass


def slug_of(theorem_name: str) -> str:
    return theorem_name.replace(".", "_")


def write_mirror(root: Path, theorem_name: str, preamble: str, formal_statement: str) -> Path:
    """Save an imported theorem's statement verbatim, as the platform's module layout expects."""
    path = root / "Theorems" / f"Thm_{slug_of(theorem_name)}.lean"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{preamble.strip()}\n\n{formal_statement.strip()}\n")
    return path


def publish_child(
    client: PlatformClient, theorem_name: str, title: str, formal_statement: str,
    natural_language: str, source: str | None = None, preamble: str = "", env: str | None = None,
    public_approved_by: str | None = None, poll_seconds: float = 3.0, max_polls: int = 100,
) -> str:
    """Create a child lemma and return its theorem id. Private unless a person approved public."""
    if not re.search(r":=\s*by\s+sorry\s*(end\s+\S+\s*)?$", formal_statement.strip()):
        raise ChildRefused("a child statement must end with `:= by sorry`")
    private = public_approved_by is None
    if not private and not source:
        raise ChildRefused("a public child needs an exact source reference")
    job = client.submit_problem(
        theorem_name, title, formal_statement, natural_language, private=private,
        preamble=preamble, source=source, env=env,
    )
    for _ in range(max_polls):
        state = client.publish_job(job)
        if state.get("status") == "PUBLISHED":
            return state["theorem_id"]
        if state.get("status") in ("FAILED", "ERROR"):
            raise PlatformError(f"child was not published: {state.get('error_message')}")
        time.sleep(poll_seconds)
    raise PlatformError(f"child publish job {job} did not finish")


def sync_obligations(ledger: Ledger, client: PlatformClient) -> list[dict]:
    """Refresh each reduction's children, and its own verdict, from the server."""
    results = []
    open_rows = [s for s in ledger.submissions() if s["outcome"] in ("reduction_open", "pending")]
    for row in open_rows:
        obligations = ledger.obligations(row["id"])
        if not obligations:
            continue
        children: dict[str, dict] = {}
        for decomposition in client.decompositions(row["theorem_id"]):
            for child in decomposition.get("children", []):
                if child.get("theorem_name"):
                    children[f"Theorems.Thm_{slug_of(child['theorem_name'])}"] = child
        for obligation in obligations:
            child = children.get(obligation["module"])
            if child:
                ledger.update_obligation(obligation["id"], child.get("status", "Open"), child.get("theorem_id"))
        verdict = client.poll(row["remote_id"])
        outcome = ledger.record_verdict(row["id"], verdict.status, verdict.error_message)
        results.append(
            {
                "submission": row["id"], "status": verdict.status, "outcome": outcome,
                "obligations": [
                    {"module": o["module"], "status": o["status"]} for o in ledger.obligations(row["id"])
                ],
            }
        )
    return results
