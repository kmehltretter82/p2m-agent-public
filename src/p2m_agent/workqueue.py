"""Choose open targets across missions, then work through them on a machine without the key.

Choosing needs the key and runs where the key is. Working needs only the exported files. Nothing
here submits: proofs wait in the ledger for a person to approve each exact file.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import ValidationError

from .envs import env_for
from .gate import Target
from .ledger import Ledger
from .platform import PlatformClient, PlatformError
from .scout import export_target
from .worker import AttemptConfig, Limits, run_attempt

SOLVED = ("proved_locally", "disproved_locally")
#: Outcomes a later pass should not retry: the worker gave a real answer about the target.
SETTLED = (*SOLVED, "statement_suspect", "stuck")
MAX_ERROR_RETRIES = 3
MAX_FAILED_SUBMISSIONS = 2
OPEN_PROBLEM = "OpenProblem"
API_HOST = "api.anthropic.com"


def namespace_of(theorem_name: str) -> str:
    if "." in theorem_name:
        return theorem_name.split(".", 1)[0]
    return "_".join(theorem_name.split("_")[:2])


def busy_namespaces(client: PlatformClient, pages: int = 3) -> set[str]:
    """Namespaces this account touched recently, so we do not race its other agents."""
    busy: set[str] = set()
    for page in range(1, pages + 1):
        rows = client._get("/submissions", page=page)["submissions"]
        busy |= {namespace_of(str(r.get("theorem_name", ""))) for r in rows}
        if not rows:
            break
    return busy


def _missions(client: PlatformClient) -> list[dict]:
    found, offset = [], 0
    while True:
        page = client._get("/missions", limit=100, offset=offset)
        found += page["missions"]
        offset += 100
        if offset >= page.get("total", 0):
            return found


def rank_candidates(leaves: list[dict], busy: set[str]) -> list[dict]:
    """Most leverage first. Each theorem once, and nothing in a busy namespace."""
    seen: set[str] = set()
    ranked = []
    for leaf in sorted(leaves, key=lambda l: -l.get("closability", 0)):
        if leaf["theorem_id"] in seen or namespace_of(leaf["theorem_name"]) in busy:
            continue
        seen.add(leaf["theorem_id"])
        ranked.append(leaf)
    return ranked


def rejection(theorem, submissions: list[dict], default_rev: str) -> str | None:
    """Why a candidate is unsuitable, or None."""
    if theorem.status != "Open" or theorem.deprecated_at:
        return "not open"
    if theorem.mathlib_rev != default_rev:
        return "outside the prepared environment"
    if any(a.decision == "flag" for a in theorem.audits):
        return "flagged by a reviewer"
    statuses = [s.get("status") for s in submissions]
    if any(s in ("PENDING", "ACCEPTED", "SKETCH_ACCEPTED") for s in statuses):
        return "already being solved"
    if len(statuses) > MAX_FAILED_SUBMISSIONS:
        return "several failed attempts already"
    return None


def export_queue(
    client: PlatformClient, directory: Path, limit: int, include_open_problems: bool = False
) -> dict:
    """Choose targets. By default skips missions labelled `OpenProblem`, where no proof may exist."""
    environments = client.environments()
    default_rev = next(e.mathlib_rev for e in environments if e.is_default)
    busy = busy_namespaces(client)
    open_roots = [
        m for m in _missions(client)
        if (m.get("main_theorem") or {}).get("status") == "Open"
        and (include_open_problems or m.get("mission_type") != OPEN_PROBLEM)
    ]

    def leaves_of(mission: dict) -> list[dict]:
        try:
            rows = client._get(f"/theorems/{mission['main_theorem']['theorem_id']}/open-leaves", limit=15)
        except PlatformError:
            return []
        return [
            {**leaf, "mission_id": mission["id"], "mission_name": mission["name"][:60],
             "mission_type": mission.get("mission_type")}
            for leaf in rows["open_leaves"]
        ]

    with ThreadPoolExecutor(4) as pool:
        leaves = [leaf for group in pool.map(leaves_of, open_roots) for leaf in group]

    exported: list[dict] = []
    skipped: dict[str, int] = {}
    position = sum(1 for _ in directory.glob("*/target.json")) if directory.exists() else 0
    for leaf in rank_candidates(leaves, busy):
        if len(exported) >= limit:
            break
        try:
            theorem = client.theorem(leaf["theorem_id"])
            reason = rejection(theorem, client.theorem_submissions(leaf["theorem_id"]), default_rev)
            if reason is None:
                slug = re.sub(r"[^A-Za-z0-9_]", "_", leaf["theorem_name"])[:80]
                folder = directory / f"{position + len(exported):03d}_{slug}"
                if any(directory.glob(f"*_{slug}")):
                    reason = "already queued"
                else:
                    try:
                        export_target(client, leaf["theorem_id"], folder, leaf["mission_id"])
                    except Exception:
                        # A half-written target would waste tokens failing at the gate.
                        shutil.rmtree(folder, ignore_errors=True)
                        raise
                    exported.append({"folder": folder.name, "closability": leaf.get("closability", 0),
                                     "mission": leaf["mission_name"], "type": leaf["mission_type"]})
        except (KeyError, PlatformError, ValidationError) as error:
            reason = f"could not export: {type(error).__name__}"
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
    return {
        "missions_with_open_roots": len(open_roots), "leaves_seen": len(leaves),
        "busy_namespaces": len(busy), "exported": exported, "skipped": skipped,
    }


def _status(message: dict) -> None:
    print(json.dumps({"at": time.strftime("%a %H:%M"), **message}), flush=True)
    print(json.dumps(message), file=sys.stderr, flush=True)


def _sleep_until(resets_at: int | None) -> None:
    until = resets_at or time.time() + 3600
    time.sleep(max(60.0, until - time.time() + 60.0))


def api_reachable(host: str = API_HOST, timeout: float = 10.0) -> bool:
    try:
        with socket.create_connection((host, 443), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_api(check=api_reachable, poll_seconds: float = 60.0) -> None:
    """A network outage is not a verdict on any target, so wait it out without spending attempts."""
    announced = False
    while not check():
        if not announced:
            _status({"waiting": "the Claude API is unreachable; retrying every minute"})
            announced = True
        time.sleep(poll_seconds)


def work_one(ledger: Ledger, folder: Path, step: float, max_total: float | None, web: bool) -> str:
    """Drive one target until it has a real answer, or reaches its cap if it has one."""
    target = Target(**json.loads((folder / "target.json").read_text()))
    target_id = ledger.add_target(target, "platform")
    label = f"queue:{folder.name}"
    env = env_for(target.toolchain, target.mathlib_rev)
    briefing_file = folder / "dossier.txt"
    briefing = briefing_file.read_text() if briefing_file.exists() else ""
    errors = 0
    while True:
        wait_for_api()
        existing = ledger.attempts(label)
        row = existing[-1] if existing else None
        if row is not None and row["outcome"] in SETTLED:
            return row["outcome"]
        spent = row["cost_usd"] if row is not None else 0.0
        if max_total is not None and spent >= max_total:
            return "cap_reached"
        ceiling = spent + step if max_total is None else min(spent + step, max_total)
        config = AttemptConfig(
            target_id=target_id, env=env, mirror_roots=[folder / "mirrors"], allow_web=web, label=label,
            # The SDK's cost counter is cumulative across a resume, so the limit must be too.
            limits=Limits(250, ceiling, 7200, 8),
            resume_attempt=row["id"] if row is not None and row["session_id"] else None,
            briefing=briefing,
        )
        result = asyncio.run(run_attempt(ledger, config))
        _status({"label": label, "outcome": result.outcome, "note": result.note[:200],
                 "cost_usd": round(ledger.attempt(result.attempt_id)["cost_usd"], 2)})
        if result.outcome == "limit_wait":
            _sleep_until(result.resets_at)
        elif result.outcome == "error":
            # A failure caused by losing the network says nothing about this target.
            if not api_reachable():
                continue
            errors += 1
            if errors > MAX_ERROR_RETRIES:
                return "error"
            time.sleep(300)
        elif result.outcome != "budget_exhausted":
            return result.outcome


def run_queue(ledger: Ledger, directory: Path, step: float, max_per_target: float | None, web: bool) -> dict:
    """One pass over the queue, re-reading the folder so that new targets can be added meanwhile."""
    done: dict[str, str] = {}
    while True:
        pending = [
            f for f in sorted(directory.iterdir())
            if (f / "target.json").is_file() and f.name not in done
        ]
        if not pending:
            return done
        done[pending[0].name] = work_one(ledger, pending[0], step, max_per_target, web)
        _status({"finished": pending[0].name, "outcome": done[pending[0].name],
                 "solved_so_far": sum(o in SOLVED for o in done.values()), "worked": len(done)})
