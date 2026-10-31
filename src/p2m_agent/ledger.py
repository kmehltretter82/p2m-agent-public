"""The durable record: what was attempted, what passed, what was sent, what came back.

The controller owns this state so that it survives a model session ending or a crash.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .gate import GateResult, Target

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    theorem_id TEXT,
    theorem_name TEXT NOT NULL,
    formal_statement TEXT NOT NULL,
    preamble TEXT NOT NULL,
    mathlib_rev TEXT NOT NULL,
    toolchain TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    target_id INTEGER NOT NULL REFERENCES targets(id),
    label TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL,
    model TEXT,
    effort TEXT,
    workdir TEXT,
    state TEXT NOT NULL,
    outcome TEXT,
    note TEXT,
    session_id TEXT,
    turns INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    gate_runs INTEGER NOT NULL DEFAULT 0,
    gate_seconds REAL NOT NULL DEFAULT 0,
    interventions INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    target_id INTEGER NOT NULL REFERENCES targets(id),
    attempt_id INTEGER REFERENCES attempts(id),
    sha256 TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (target_id, sha256)
);
CREATE TABLE IF NOT EXISTS gate_results (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    passed INTEGER NOT NULL,
    mode TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    report TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    theorem_id TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    proof_type TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    explanation TEXT,
    -- intent: about to POST, or outcome unknown. sent: the server has it.
    -- not_sent: confirmed absent from the server, so it may be retried.
    state TEXT NOT NULL,
    remote_id TEXT,
    status TEXT,
    error_message TEXT,
    outcome TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS obligations (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    module TEXT NOT NULL,
    theorem_id TEXT,
    status TEXT NOT NULL DEFAULT 'Open',
    updated_at TEXT NOT NULL,
    UNIQUE (submission_id, module)
);
CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    file_sha256 TEXT NOT NULL,
    explanation_sha256 TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    consumed_at TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def outcome_of(status: str, proof_type: str, obligations: int) -> str:
    """What a verdict means. Keeps proofs and reductions apart, as PLAN.md requires."""
    if status == "PENDING":
        return "pending"
    if status == "SKETCH_ACCEPTED":
        return "reduction_open"
    if status == "ACCEPTED":
        if proof_type == "disprove":
            return "disproved"
        return "proved_via_reduction" if obligations else "proved"
    return "rejected"


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _tx(self):
        with self.db:
            yield self.db

    # --- targets -----------------------------------------------------------------------

    def add_target(self, target: Target, source: str) -> int:
        """Freeze a target. Adding the same statement again returns the existing row."""
        digest = target.sha256()
        with self._tx() as db:
            db.execute(
                "INSERT OR IGNORE INTO targets (sha256, theorem_id, theorem_name, formal_statement,"
                " preamble, mathlib_rev, toolchain, source, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (digest, target.theorem_id, target.theorem_name, target.formal_statement,
                 target.preamble, target.mathlib_rev, target.toolchain, source, now()),
            )
        return self.db.execute("SELECT id FROM targets WHERE sha256 = ?", (digest,)).fetchone()[0]

    def target(self, target_id: int) -> Target:
        row = self.db.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        if row is None:
            raise KeyError(f"no target {target_id}")
        target = Target(row["theorem_name"], row["formal_statement"], row["preamble"],
                        row["mathlib_rev"], row["toolchain"], row["theorem_id"])
        if target.sha256() != row["sha256"]:
            raise ValueError(f"target {target_id} no longer matches its frozen hash")
        return target

    def targets(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM targets ORDER BY id").fetchall()

    # --- attempts ----------------------------------------------------------------------

    def start_attempt(
        self, target_id: int, mode: str, model: str | None, effort: str | None,
        workdir: str | None = None, label: str = "",
    ) -> int:
        with self._tx() as db:
            cursor = db.execute(
                "INSERT INTO attempts (target_id, label, mode, model, effort, workdir, state, started_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
                (target_id, label, mode, model, effort, workdir, now()),
            )
        return cursor.lastrowid

    def attempt(self, attempt_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise KeyError(f"no attempt {attempt_id}")
        return row

    def attempts(self, label: str | None = None) -> list[sqlite3.Row]:
        if label is None:
            return self.db.execute("SELECT * FROM attempts ORDER BY id").fetchall()
        return self.db.execute(
            "SELECT * FROM attempts WHERE label = ? ORDER BY id", (label,)
        ).fetchall()

    def record_session(self, attempt_id: int, session_id: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE attempts SET session_id = ? WHERE id = ?", (session_id, attempt_id))

    def record_usage(
        self, attempt_id: int, usage: dict | None, turns: int, cumulative_cost_usd: float | None,
        prior_cost_usd: float = 0.0,
    ) -> None:
        """Add one run's usage to the attempt.

        Token counts in a result cover that run only, so they add up. `total_cost_usd` is
        cumulative across resumes of one session (measured in M0), so it replaces rather than
        adds. `prior_cost_usd` carries the total of earlier sessions in the same attempt.
        """
        usage = usage or {}
        with self._tx() as db:
            db.execute(
                "UPDATE attempts SET turns = turns + ?, input_tokens = input_tokens + ?,"
                " output_tokens = output_tokens + ?, cache_read_tokens = cache_read_tokens + ?,"
                " cache_write_tokens = cache_write_tokens + ?, cost_usd = COALESCE(?, cost_usd)"
                " WHERE id = ?",
                (
                    turns,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("cache_read_input_tokens", 0),
                    usage.get("cache_creation_input_tokens", 0),
                    None if cumulative_cost_usd is None else prior_cost_usd + cumulative_cost_usd,
                    attempt_id,
                ),
            )

    def set_attempt_state(self, attempt_id: int, state: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE attempts SET state = ? WHERE id = ?", (state, attempt_id))

    def finish_attempt(self, attempt_id: int, outcome: str, note: str = "") -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE attempts SET state = 'finished', outcome = ?, note = ?, finished_at = ?"
                " WHERE id = ?",
                (outcome, note, now(), attempt_id),
            )

    # --- candidates and gate results ---------------------------------------------------

    def add_candidate(self, target_id: int, source: str, attempt_id: int | None = None) -> int:
        digest = sha256(source)
        with self._tx() as db:
            db.execute(
                "INSERT OR IGNORE INTO candidates (target_id, attempt_id, sha256, source, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (target_id, attempt_id, digest, source, now()),
            )
        return self.db.execute(
            "SELECT id FROM candidates WHERE target_id = ? AND sha256 = ?", (target_id, digest)
        ).fetchone()[0]

    def candidate(self, candidate_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        if row is None:
            raise KeyError(f"no candidate {candidate_id}")
        return row

    def record_gate(self, candidate_id: int, result: GateResult, attempt_id: int | None = None) -> int:
        with self._tx() as db:
            cursor = db.execute(
                "INSERT INTO gate_results (candidate_id, passed, mode, file_sha256, report, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (candidate_id, int(result.passed), result.mode, result.file_sha256,
                 json.dumps(result.to_json()), now()),
            )
            if attempt_id is not None:
                db.execute(
                    "UPDATE attempts SET gate_runs = gate_runs + 1, gate_seconds = gate_seconds + ?"
                    " WHERE id = ?",
                    (result.compile_seconds or 0.0, attempt_id),
                )
        return cursor.lastrowid

    def latest_gate(self, candidate_id: int, mode: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM gate_results WHERE candidate_id = ? AND mode = ? ORDER BY id DESC LIMIT 1",
            (candidate_id, mode),
        ).fetchone()

    # --- approvals ---------------------------------------------------------------------

    def approve(self, candidate_id: int, explanation: str | None, approved_by: str) -> int:
        row = self.candidate(candidate_id)
        with self._tx() as db:
            cursor = db.execute(
                "INSERT INTO approvals (candidate_id, file_sha256, explanation_sha256, approved_by,"
                " created_at) VALUES (?, ?, ?, ?, ?)",
                (candidate_id, row["sha256"], sha256(explanation or ""), approved_by, now()),
            )
        return cursor.lastrowid

    def unused_approval(self, candidate_id: int, explanation: str | None) -> sqlite3.Row | None:
        row = self.candidate(candidate_id)
        return self.db.execute(
            "SELECT * FROM approvals WHERE candidate_id = ? AND file_sha256 = ?"
            " AND explanation_sha256 = ? AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
            (candidate_id, row["sha256"], sha256(explanation or "")),
        ).fetchone()

    # --- submissions -------------------------------------------------------------------

    def record_intent(
        self, candidate_id: int, theorem_id: str, file_sha256: str, proof_type: str,
        approval_id: int, approved_by: str, explanation: str | None,
    ) -> int:
        """Written and committed before the POST, and consumes the approval with it."""
        stamp = now()
        with self._tx() as db:
            cursor = db.execute(
                "INSERT INTO submissions (candidate_id, theorem_id, file_sha256, proof_type,"
                " approved_by, approved_at, explanation, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'intent', ?, ?)",
                (candidate_id, theorem_id, file_sha256, proof_type, approved_by, stamp,
                 explanation, stamp, stamp),
            )
            db.execute("UPDATE approvals SET consumed_at = ? WHERE id = ?", (stamp, approval_id))
        return cursor.lastrowid

    def mark_sent(self, submission_id: int, remote_id: str) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE submissions SET state = 'sent', remote_id = ?, status = 'PENDING',"
                " outcome = 'pending', updated_at = ? WHERE id = ?",
                (remote_id, now(), submission_id),
            )

    def mark_not_sent(self, submission_id: int, reason: str) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE submissions SET state = 'not_sent', error_message = ?, updated_at = ?"
                " WHERE id = ?",
                (reason, now(), submission_id),
            )

    def record_verdict(self, submission_id: int, status: str, error_message: str | None) -> str:
        row = self.submission(submission_id)
        count = self.db.execute(
            "SELECT COUNT(*) FROM obligations WHERE submission_id = ?", (submission_id,)
        ).fetchone()[0]
        outcome = outcome_of(status, row["proof_type"], count)
        with self._tx() as db:
            db.execute(
                "UPDATE submissions SET status = ?, error_message = ?, outcome = ?, updated_at = ?"
                " WHERE id = ?",
                (status, error_message, outcome, now(), submission_id),
            )
        return outcome

    def submission(self, submission_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
        if row is None:
            raise KeyError(f"no submission {submission_id}")
        return row

    def submissions(self, state: str | None = None) -> list[sqlite3.Row]:
        if state is None:
            return self.db.execute("SELECT * FROM submissions ORDER BY id").fetchall()
        return self.db.execute(
            "SELECT * FROM submissions WHERE state = ? ORDER BY id", (state,)
        ).fetchall()

    def pending_submissions(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM submissions WHERE state = 'sent' AND status = 'PENDING' ORDER BY id"
        ).fetchall()

    def open_or_sent_for(self, candidate_id: int) -> sqlite3.Row | None:
        """A submission that exists or might exist on the server for this candidate."""
        return self.db.execute(
            "SELECT * FROM submissions WHERE candidate_id = ? AND state IN ('intent', 'sent')"
            " ORDER BY id DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()

    # --- obligations (reductions) ------------------------------------------------------

    def add_obligations(self, submission_id: int, modules: list[str]) -> None:
        stamp = now()
        with self._tx() as db:
            db.executemany(
                "INSERT OR IGNORE INTO obligations (submission_id, module, updated_at) VALUES (?, ?, ?)",
                [(submission_id, module, stamp) for module in modules],
            )

    def obligations(self, submission_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM obligations WHERE submission_id = ? ORDER BY id", (submission_id,)
        ).fetchall()

    def update_obligation(self, obligation_id: int, status: str, theorem_id: str | None = None) -> None:
        with self._tx() as db:
            db.execute(
                "UPDATE obligations SET status = ?, theorem_id = COALESCE(?, theorem_id),"
                " updated_at = ? WHERE id = ?",
                (status, theorem_id, now(), obligation_id),
            )
