"""Target selection and the queue loop, with no network, no model and no Lean."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from p2m_agent import workqueue
from p2m_agent.gate import Target
from p2m_agent.ledger import Ledger
from p2m_agent.platform import Audit, Theorem
from p2m_agent.workqueue import namespace_of, rank_candidates, rejection, run_queue, work_one

REV = "rev"


def theorem(**changes) -> Theorem:
    fields = dict(theorem_id="t", theorem_name="Foo.bar", status="Open",
                  formal_statement="theorem Foo.bar : True := by sorry", mathlib_rev=REV)
    return Theorem(**{**fields, **changes})


def test_namespace_of():
    assert namespace_of("Foo.Bar.baz") == "Foo"
    assert namespace_of("MonoReset_IntervalBFS_lemma_4") == "MonoReset_IntervalBFS"


def test_ranking_prefers_leverage_and_skips_busy_namespaces_and_duplicates():
    leaves = [
        {"theorem_id": "a", "theorem_name": "Low.a", "closability": 0},
        {"theorem_id": "b", "theorem_name": "High.b", "closability": 5},
        {"theorem_id": "b", "theorem_name": "High.b", "closability": 5},
        {"theorem_id": "c", "theorem_name": "Busy.c", "closability": 9},
    ]
    assert [l["theorem_id"] for l in rank_candidates(leaves, {"Busy"})] == ["b", "a"]


@pytest.mark.parametrize(
    ("changes", "submissions", "expected"),
    [
        ({}, [], None),
        ({"status": "Proved"}, [], "not open"),
        ({"deprecated_at": "2026"}, [], "not open"),
        ({"mathlib_rev": "other"}, [], "outside the prepared environment"),
        ({"audits": [Audit(decision="flag", comment="false")]}, [], "flagged by a reviewer"),
        ({}, [{"status": "PENDING"}], "already being solved"),
        ({}, [{"status": "SKETCH_ACCEPTED"}], "already being solved"),
        ({}, [{"status": "WA"}] * 3, "several failed attempts already"),
        ({}, [{"status": "WA"}], None),
    ],
)
def test_rejection(changes, submissions, expected):
    assert rejection(theorem(**changes), submissions, REV) == expected


@pytest.fixture
def setup(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "l.sqlite")
    monkeypatch.setattr(workqueue, "wait_for_api", lambda *a, **k: None)
    monkeypatch.setattr(workqueue, "api_reachable", lambda *a, **k: True)
    root = tmp_path / "queue"
    for name in ("000_a", "001_b"):
        folder = root / name
        folder.mkdir(parents=True)
        target = Target(f"t.{name}", "theorem x : True := by sorry", "", REV, "tc", "id")
        (folder / "target.json").write_text(json.dumps(target.__dict__))
    monkeypatch.setattr(workqueue, "env_for", lambda toolchain, rev: None)
    monkeypatch.setattr(workqueue.time, "sleep", lambda seconds: None)
    return ledger, root


def scripted(ledger, outcomes, seen):
    """A stand-in for `run_attempt` that plays back outcomes and records its configs."""

    async def fake(_ledger, config):
        seen.append(config)
        if config.resume_attempt is None:
            attempt = ledger.start_attempt(config.target_id, "prove", "m", "high", label=config.label)
            ledger.record_session(attempt, "session")
        else:
            attempt = config.resume_attempt
        outcome, cost = outcomes.pop(0)
        ledger.record_usage(attempt, {}, 1, cost)
        if outcome == "limit_wait":
            ledger.set_attempt_state(attempt, "paused")
        else:
            ledger.finish_attempt(attempt, outcome)
        return SimpleNamespace(attempt_id=attempt, outcome=outcome, note="", resets_at=None)

    return fake


def test_limit_waits_and_budget_steps_resume_the_same_attempt(setup, monkeypatch):
    ledger, root = setup
    seen = []
    outcomes = [("limit_wait", 1.0), ("budget_exhausted", 6.0), ("proved_locally", 7.0)]
    monkeypatch.setattr(workqueue, "run_attempt", scripted(ledger, outcomes, seen))
    assert work_one(ledger, root / "000_a", 6.0, 18.0, False) == "proved_locally"
    assert seen[0].resume_attempt is None
    assert seen[1].resume_attempt == seen[2].resume_attempt == 1
    assert [c.limits.max_budget_usd for c in seen] == [6.0, 7.0, 12.0]
    assert len(ledger.attempts()) == 1


def test_a_target_stops_at_its_cap(setup, monkeypatch):
    ledger, root = setup
    outcomes = [("budget_exhausted", 6.0), ("budget_exhausted", 12.0), ("budget_exhausted", 18.0)]
    monkeypatch.setattr(workqueue, "run_attempt", scripted(ledger, outcomes, []))
    assert work_one(ledger, root / "000_a", 6.0, 18.0, False) == "cap_reached"
    assert outcomes == []


def test_a_settled_target_is_not_retried(setup, monkeypatch):
    ledger, root = setup
    seen = []
    monkeypatch.setattr(workqueue, "run_attempt", scripted(ledger, [("stuck", 2.0)], seen))
    assert work_one(ledger, root / "000_a", 6.0, 18.0, False) == "stuck"
    assert work_one(ledger, root / "000_a", 6.0, 18.0, False) == "stuck"
    assert len(seen) == 1


def test_queue_runs_each_target_once_and_finds_new_ones(setup, monkeypatch):
    ledger, root = setup
    outcomes = [("proved_locally", 1.0), ("stuck", 1.0)]
    monkeypatch.setattr(workqueue, "run_attempt", scripted(ledger, outcomes, []))
    assert run_queue(ledger, root, 6.0, 18.0, False) == {"000_a": "proved_locally", "001_b": "stuck"}


def test_repeated_errors_give_up_rather_than_spin(setup, monkeypatch):
    ledger, root = setup
    monkeypatch.setattr(workqueue, "api_reachable", lambda *a, **k: True)
    outcomes = [("error", 0.0)] * 4
    monkeypatch.setattr(workqueue, "run_attempt", scripted(ledger, outcomes, []))
    assert work_one(ledger, root / "000_a", 6.0, 18.0, False) == "error"


def test_without_a_cap_only_a_real_answer_ends_a_target(setup, monkeypatch):
    ledger, root = setup
    seen = []
    outcomes = [("budget_exhausted", 6.0 * n) for n in range(1, 11)] + [("proved_locally", 61.0)]
    monkeypatch.setattr(workqueue, "run_attempt", scripted(ledger, outcomes, seen))
    assert work_one(ledger, root / "000_a", 6.0, None, False) == "proved_locally"
    assert seen[-1].limits.max_budget_usd == 66.0


def test_review_with_null_fields_parses():
    parsed = Theorem(**{**theorem().model_dump(), "audits": [{"decision": "confirm", "comment": None, "reviewer_username": None}]})
    assert rejection(parsed, [], REV) is None


def test_an_outage_does_not_use_up_a_targets_retries(setup, monkeypatch):
    ledger, root = setup
    states = iter([False] * 6 + [True] * 20)
    monkeypatch.setattr(workqueue, "api_reachable", lambda *a, **k: next(states))
    outcomes = [("error", 0.0)] * 6 + [("proved_locally", 1.0)]
    monkeypatch.setattr(workqueue, "run_attempt", scripted(ledger, outcomes, []))
    assert work_one(ledger, root / "000_a", 6.0, None, False) == "proved_locally"


def test_wait_for_api_returns_once_reachable(monkeypatch):
    monkeypatch.setattr(workqueue.time, "sleep", lambda seconds: None)
    answers = iter([False, False, True])
    assert workqueue.wait_for_api(check=lambda: next(answers)) is None

