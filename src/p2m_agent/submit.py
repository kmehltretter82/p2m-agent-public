"""Getting one approved candidate to the server exactly once.

Every submission is public and permanent under the user's account, so each step here refuses
rather than guesses. The worker has no access to this module: it holds no credentials and no
submit tool.
"""

from __future__ import annotations

import hashlib
import json

import httpx

from .ledger import Ledger
from .platform import PlatformClient, PlatformError


class SubmitRefused(RuntimeError):
    pass


def submit(
    ledger: Ledger,
    client: PlatformClient,
    candidate_id: int,
    mode: str = "prove",
    explanation: str | None = None,
) -> int:
    """Submit a candidate. Returns the ledger's submission id."""
    candidate = ledger.candidate(candidate_id)
    source = candidate["source"]
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != candidate["sha256"]:
        raise SubmitRefused("the stored candidate no longer matches its hash")

    gate = ledger.latest_gate(candidate_id, mode)
    if gate is None or not gate["passed"] or gate["file_sha256"] != digest:
        raise SubmitRefused(f"this exact file has not passed the gate in {mode} mode")

    approval = ledger.unused_approval(candidate_id, explanation)
    if approval is None:
        raise SubmitRefused("no unused approval for this exact file and explanation")

    existing = ledger.open_or_sent_for(candidate_id)
    if existing is not None:
        raise SubmitRefused(
            f"submission {existing['id']} already exists in state {existing['state']}; "
            "reconcile it first"
        )

    target = ledger.target(candidate["target_id"])
    if not target.theorem_id:
        raise SubmitRefused("this target has no platform theorem id")

    # Other solvers race for the same targets, and the snapshot must still be what we proved.
    theorem = client.theorem(target.theorem_id)
    if theorem.status != "Open":
        raise SubmitRefused(f"the theorem is no longer open (status {theorem.status})")
    if theorem.deprecated_at:
        raise SubmitRefused("the theorem has been deprecated")
    if (theorem.formal_statement, theorem.preamble, theorem.mathlib_rev) != (
        target.formal_statement, target.preamble, target.mathlib_rev,
    ):
        raise SubmitRefused("the statement on the server differs from the frozen snapshot")

    submission_id = ledger.record_intent(
        candidate_id, target.theorem_id, digest, mode, approval["id"], approval["approved_by"],
        explanation,
    )
    try:
        remote_id = client.verify(target.theorem_id, source, mode, explanation)
    except PlatformError as error:
        # A definite refusal means nothing was stored. A 5xx may have stored it: stay in intent.
        if error.status_code is not None and 400 <= error.status_code < 500:
            ledger.mark_not_sent(submission_id, str(error))
        raise
    except httpx.TransportError:
        # Unknown outcome. Stay in `intent` so reconcile decides, rather than risk a duplicate.
        raise
    ledger.mark_sent(submission_id, remote_id)

    # Keep reductions apart from proofs from the moment they exist.

    obligations = json.loads(gate["report"]).get("obligations") or []
    if obligations:
        ledger.add_obligations(submission_id, obligations)
    return submission_id


def reconcile(ledger: Ledger, client: PlatformClient, pages: int = 3) -> list[dict]:
    """Settle submissions whose POST had an unknown outcome, by matching what the server stored."""
    results = []
    for row in ledger.submissions(state="intent"):
        remote_id = _find_on_server(client, row["theorem_id"], row["file_sha256"], pages)
        if remote_id:
            ledger.mark_sent(row["id"], remote_id)
            results.append({"submission": row["id"], "state": "sent", "remote_id": remote_id})
        else:
            ledger.mark_not_sent(row["id"], "not found on the server during reconcile")
            results.append({"submission": row["id"], "state": "not_sent"})
    return results


def _find_on_server(client: PlatformClient, theorem_id: str, digest: str, pages: int) -> str | None:
    for page in range(1, pages + 1):
        mine = client.my_submissions(page=page)
        if not mine:
            return None
        for submission in mine:
            if submission.theorem_id != theorem_id:
                continue
            stored = client.submission_source(submission.id)
            if hashlib.sha256(stored.encode()).hexdigest() == digest:
                return submission.id
    return None


def poll_pending(ledger: Ledger, client: PlatformClient) -> list[dict]:
    results = []
    for row in ledger.pending_submissions():
        verdict = client.poll(row["remote_id"])
        outcome = ledger.record_verdict(row["id"], verdict.status, verdict.error_message)
        results.append(
            {
                "submission": row["id"],
                "remote_id": row["remote_id"],
                "status": verdict.status,
                "outcome": outcome,
                "error": verdict.error_message,
            }
        )
    return results
