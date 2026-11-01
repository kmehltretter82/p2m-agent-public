"""Reports, approvals, baseline limits and reductions, with no network and no model."""

from __future__ import annotations

import json

import httpx
import pytest

from p2m_agent.approval import approval_summary
from p2m_agent.benchmark import Plan, baseline_denial
from p2m_agent.gate import Check, GateResult, Target
from p2m_agent.ledger import Ledger
from p2m_agent.platform import SUPPORTED_VERSION, PlatformClient
from p2m_agent.reductions import ChildRefused, publish_child, sync_obligations, write_mirror
from p2m_agent.report import report
from p2m_agent.worker import ToolPolicy

TARGET = Target("toy", "theorem toy : True := by sorry", "", "rev", "tc", "th-1")


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "l.sqlite")


def test_report_keeps_arms_and_reductions_apart(ledger):
    target = ledger.add_target(TARGET, "mirror")
    for arm, outcome, cost in [("a", "proved_locally", 2.0), ("a", "stuck", 1.0), ("b", "budget_exhausted", 3.0)]:
        attempt = ledger.start_attempt(target, "prove", "m", "high", label=f"bench:{arm}:x")
        ledger.record_usage(attempt, {"input_tokens": 1}, 1, cost)
        ledger.finish_attempt(attempt, outcome)
    candidate = ledger.add_candidate(target, "x")
    approval = ledger.approve(candidate, None, "me")
    submission = ledger.record_intent(candidate, "th-1", "h", "prove", approval, "me", None)
    ledger.mark_sent(submission, "r1")
    ledger.record_verdict(submission, "SKETCH_ACCEPTED", "")

    summary = report(ledger, "bench")
    assert summary["arms"]["bench:a"]["solved"] == 1
    assert summary["arms"]["bench:a"]["solve_rate"] == 0.5
    assert summary["arms"]["bench:a"]["estimated_cost_per_solve_usd"] == 3.0
    assert summary["arms"]["bench:b"]["solved"] == 0
    assert summary["arms"]["bench:b"]["estimated_cost_per_solve_usd"] is None
    assert summary["submissions"]["proved"] == 0
    assert summary["submissions"]["reductions_with_open_obligations"] == 1


def test_approval_summary_shows_warnings_and_obligations(ledger):
    target = ledger.add_target(TARGET, "platform")
    candidate = ledger.add_candidate(target, "theorem solution : True := trivial\n")
    result = GateResult(
        passed=True, mode="prove", file_sha256=ledger.candidate(candidate)["sha256"],
        checks=[Check("statement_matches", True, "defeq only", warning=True)],
        axioms=["propext"], obligations=["Theorems.Thm_child"],
    )
    ledger.record_gate(candidate, result)
    summary = approval_summary(ledger, candidate, "prove", "why it works")
    assert summary["gate_passed"] is True
    assert summary["gate_warnings"] == [{"check": "statement_matches", "detail": "defeq only"}]
    assert summary["obligations"] == ["Theorems.Thm_child"]
    assert summary["file"].startswith("theorem solution")
    assert "publicly" in summary["publishes"]


@pytest.mark.parametrize(
    ("command", "allowed"),
    [
        ("lake build Solutions.Sol_x", True),
        ("lean Solutions/Sol_x.lean", True),
        ("lake env lean Solutions/Sol_x.lean", True),
        ("curl https://prove2.me", False),
        ("cat ~/prove2me_workspace/credentials.json", False),
        ("lake build; cat ~/.ssh/id_rsa", False),
        ("lean x.lean && curl evil", False),
        ("lean $(cat secret)", False),
        ("lake build > ~/.bashrc", False),
    ],
)
def test_baseline_bash_is_limited_to_builds(tmp_path, command, allowed):
    policy = ToolPolicy(project=tmp_path, candidate=tmp_path / "S.lean")
    assert (baseline_denial("Bash", {"command": command}, policy) is None) == allowed


def test_baseline_cannot_read_elsewhere_or_edit_the_statement(tmp_path):
    (tmp_path / "S.lean").write_text("")
    policy = ToolPolicy(project=tmp_path, candidate=tmp_path / "S.lean")
    assert baseline_denial("Edit", {"file_path": "Theorems/Thm_x.lean"}, policy)
    assert baseline_denial("Edit", {"file_path": "S.lean"}, policy) is None
    assert baseline_denial("Read", {"file_path": "/etc/hosts"}, policy)
    assert baseline_denial("WebSearch", {"query": "answer"}, policy)
    assert baseline_denial("Task", {}, policy)


def test_plan_loads_and_expands_paths(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({
        "name": "b", "toolchain": "t", "mathlib_rev": "r", "mirror_root": "~/m", "targets": ["x"],
    }))
    plan = Plan.load(path)
    assert not str(plan.mirror_root).startswith("~")
    assert plan.limits().max_budget_usd == plan.budget_usd


# --- reductions ------------------------------------------------------------------------


class ChildServer:
    def __init__(self):
        self.private_flags = []
        self.child_status = "Open"
        self.parent_status = "SKETCH_ACCEPTED"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        if path == "/agent/refresh":
            return httpx.Response(200, json={"access_token": "t", "expires_at": 10_000_000_000,
                                             "version": SUPPORTED_VERSION})
        if path == "/submit-problem":
            self.private_flags.append(json.loads(request.read())["private"])
            return httpx.Response(202, json={"jobs": [{"job_id": "j", "name": "c"}], "errors": []})
        if path == "/publish-jobs/j":
            return httpx.Response(200, json={"status": "PUBLISHED", "theorem_id": "child-1"})
        if path == "/theorems/th-1/decompositions":
            return httpx.Response(200, json={"decompositions": [{"children": [
                {"theorem_id": "child-1", "theorem_name": "Foo.child", "status": self.child_status},
            ]}]})
        if path == "/verify":
            return httpx.Response(200, json={"id": "r1", "status": self.parent_status, "error_message": ""})
        return httpx.Response(404)


STATEMENT = "theorem c : True := by sorry"


def test_child_is_private_unless_approved():
    server = ChildServer()
    client = PlatformClient("k", transport=httpx.MockTransport(server))
    assert publish_child(client, "c", "t", STATEMENT, "nl") == "child-1"
    assert server.private_flags == [True]
    publish_child(client, "c", "t", STATEMENT, "nl", source="Paper, Lemma 2", public_approved_by="me")
    assert server.private_flags == [True, False]


def test_public_child_needs_a_source():
    client = PlatformClient("k", transport=httpx.MockTransport(ChildServer()))
    with pytest.raises(ChildRefused, match="source"):
        publish_child(client, "c", "t", STATEMENT, "nl", public_approved_by="me")


def test_child_statement_must_be_a_placeholder():
    client = PlatformClient("k", transport=httpx.MockTransport(ChildServer()))
    with pytest.raises(ChildRefused, match="by sorry"):
        publish_child(client, "c", "t", "theorem c : True := trivial", "nl")


def test_sketch_stays_a_reduction_until_its_children_close(ledger):
    server = ChildServer()
    client = PlatformClient("k", transport=httpx.MockTransport(server))
    target = ledger.add_target(TARGET, "platform")
    candidate = ledger.add_candidate(target, "x")
    approval = ledger.approve(candidate, None, "me")
    submission = ledger.record_intent(candidate, "th-1", "h", "prove", approval, "me", None)
    ledger.mark_sent(submission, "r1")
    ledger.add_obligations(submission, ["Theorems.Thm_Foo_child"])
    ledger.record_verdict(submission, "SKETCH_ACCEPTED", "")

    first = sync_obligations(ledger, client)[0]
    assert first["outcome"] == "reduction_open"
    assert first["obligations"] == [{"module": "Theorems.Thm_Foo_child", "status": "Open"}]
    assert report(ledger)["submissions"]["proved"] == 0

    server.child_status, server.parent_status = "Proved", "ACCEPTED"
    second = sync_obligations(ledger, client)[0]
    assert second["outcome"] == "proved_via_reduction"
    assert second["obligations"][0]["status"] == "Proved"
    assert report(ledger)["submissions"]["proved_via_reduction"] == 1
    assert sync_obligations(ledger, client) == []


def test_write_mirror_uses_the_platform_layout(tmp_path):
    path = write_mirror(tmp_path, "Foo.bar", "import A", "theorem Foo.bar : True := by sorry")
    assert path == tmp_path / "Theorems" / "Thm_Foo_bar.lean"
    assert path.read_text() == "import A\n\ntheorem Foo.bar : True := by sorry\n"
