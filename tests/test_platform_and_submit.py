"""The client, ledger and submit path against a fake server. No network, no real key."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from p2m_agent.gate import Check, GateResult, Target
from p2m_agent.ledger import Ledger, outcome_of, sha256
from p2m_agent.platform import (
    SUPPORTED_VERSION,
    PlatformClient,
    PlatformError,
    VersionMismatch,
)
from p2m_agent.submit import SubmitRefused, poll_pending, reconcile, submit

STATEMENT = "theorem toy (n : Nat) : 0 + n = n := by sorry"
REV = "abc123"
TOOLCHAIN = "leanprover/lean4:v4.33.1"
PROOF = "theorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\n"


class FakeServer:
    def __init__(self):
        self.version = SUPPORTED_VERSION
        self.status = "Open"
        self.statement = STATEMENT
        self.refreshes = 0
        self.posts: list[dict] = []
        self.stored: dict[str, str] = {}
        self.fail_post: Exception | int | None = None
        self.store_then_drop = False
        self.expire_token_once = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        if path == "/agent/refresh":
            self.refreshes += 1
            return httpx.Response(
                200,
                json={"access_token": f"t{self.refreshes}", "expires_at": 10_000_000_000,
                      "version": self.version},
            )
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": self.version})
        if self.expire_token_once:
            self.expire_token_once = False
            return httpx.Response(401, json={"error": "expired"})
        assert request.headers["authorization"].startswith("Bearer t")
        if path == "/theorems/th-1":
            return httpx.Response(
                200,
                json={"theorem_id": "th-1", "theorem_name": "toy", "status": self.status,
                      "formal_statement": self.statement, "preamble": "", "mathlib_rev": REV,
                      "audits": [{"decision": "flag", "comment": "looks odd"}],
                      "unexpected_new_field": 1},
            )
        if path == "/verify" and request.method == "POST":
            return self._post(request)
        if path == "/verify":
            return httpx.Response(
                200,
                json={"id": request.url.params["submission_id"], "theorem_id": "th-1",
                      "status": "ACCEPTED", "error_message": ""},
            )
        if path == "/submissions":
            rows = [{"id": k, "theorem_id": "th-1", "status": "PENDING"} for k in self.stored]
            if request.url.params.get("page", "1") != "1":
                rows = []
            return httpx.Response(200, json={"submissions": rows, "total": len(rows)})
        if path.endswith("/solution"):
            return httpx.Response(200, json={"content": self.stored[path.split("/")[2]]})
        return httpx.Response(404, json={"error": path})

    def _post(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self.fail_post, Exception):
            raise self.fail_post
        if isinstance(self.fail_post, int):
            return httpx.Response(self.fail_post, json={"error": "nope"})
        body = request.read()
        source = PROOF if PROOF.encode() in body else body.decode(errors="replace")
        remote = f"sub-{len(self.posts) + 1}"
        self.posts.append({"remote": remote, "body": body})
        self.stored[remote] = source
        if self.store_then_drop:
            raise httpx.ReadTimeout("connection dropped after the server stored it")
        return httpx.Response(200, json={"submission_id": remote, "status": "PENDING"})


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def client(server):
    return PlatformClient("p2m_fake", transport=httpx.MockTransport(server))


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.sqlite")


def toy_target() -> Target:
    return Target("toy", STATEMENT, "", REV, TOOLCHAIN, "th-1")


def passed_gate(source: str, obligations: list[str] | None = None) -> GateResult:
    return GateResult(
        passed=True, mode="prove", file_sha256=hashlib.sha256(source.encode()).hexdigest(),
        checks=[Check("compiles", True)], obligations=obligations or [],
    )


def ready(ledger: Ledger, source: str = PROOF, gate: GateResult | None = None, explanation=None) -> int:
    target_id = ledger.add_target(toy_target(), "platform")
    candidate = ledger.add_candidate(target_id, source)
    ledger.record_gate(candidate, gate or passed_gate(source))
    ledger.approve(candidate, explanation, "tester")
    return candidate


# --- client ----------------------------------------------------------------------------


def test_extra_fields_and_audits_parse(client):
    theorem = client.theorem("th-1")
    assert theorem.status == "Open"
    assert theorem.audits[0].decision == "flag"


def test_version_change_stops_the_client(server, client):
    server.version = "9.9.9"
    with pytest.raises(VersionMismatch):
        client.theorem("th-1")
    with pytest.raises(VersionMismatch):
        client.health()


def test_expired_token_refreshes_once(server, client):
    client.theorem("th-1")
    server.expire_token_once = True
    client.theorem("th-1")
    assert server.refreshes == 2


def test_no_key_is_a_clear_error():
    client = PlatformClient(None, transport=httpx.MockTransport(FakeServer()))
    with pytest.raises(PlatformError, match="no Prove2Me API key"):
        client.theorem("th-1")


# --- ledger ----------------------------------------------------------------------------


def test_target_snapshot_is_frozen_and_deduplicated(ledger):
    first = ledger.add_target(toy_target(), "platform")
    assert ledger.add_target(toy_target(), "platform") == first
    assert ledger.target(first) == toy_target()
    ledger.db.execute("UPDATE targets SET formal_statement = 'tampered' WHERE id = ?", (first,))
    with pytest.raises(ValueError, match="frozen hash"):
        ledger.target(first)


@pytest.mark.parametrize(
    ("status", "kind", "obligations", "expected"),
    [
        ("ACCEPTED", "prove", 0, "proved"),
        ("ACCEPTED", "prove", 2, "proved_via_reduction"),
        ("ACCEPTED", "disprove", 0, "disproved"),
        ("SKETCH_ACCEPTED", "prove", 2, "reduction_open"),
        ("PENDING", "prove", 0, "pending"),
        ("CE", "prove", 0, "rejected"),
        ("WA", "prove", 0, "rejected"),
        ("SORRY", "prove", 0, "rejected"),
        ("FAILED", "prove", 0, "rejected"),
        ("ERROR", "prove", 0, "rejected"),
    ],
)
def test_outcomes_keep_proofs_and_reductions_apart(status, kind, obligations, expected):
    assert outcome_of(status, kind, obligations) == expected


def test_resumed_session_cost_replaces_while_tokens_add(ledger):
    target_id = ledger.add_target(toy_target(), "local")
    attempt = ledger.start_attempt(target_id, "prove", "m", "high")
    ledger.record_usage(attempt, {"input_tokens": 10, "output_tokens": 1}, 1, 0.0135)
    ledger.record_usage(attempt, {"input_tokens": 5, "output_tokens": 2}, 1, 0.0169)
    row = ledger.attempt(attempt)
    assert (row["input_tokens"], row["output_tokens"], row["turns"]) == (15, 3, 2)
    assert row["cost_usd"] == pytest.approx(0.0169)


# --- submit refusals -------------------------------------------------------------------


def test_refuses_without_a_passing_gate(ledger, client):
    target_id = ledger.add_target(toy_target(), "platform")
    candidate = ledger.add_candidate(target_id, PROOF)
    ledger.approve(candidate, None, "tester")
    with pytest.raises(SubmitRefused, match="has not passed the gate"):
        submit(ledger, client, candidate)


def test_refuses_a_failed_gate(ledger, client):
    target_id = ledger.add_target(toy_target(), "platform")
    candidate = ledger.add_candidate(target_id, PROOF)
    failed = passed_gate(PROOF)
    failed.passed = False
    ledger.record_gate(candidate, failed)
    ledger.approve(candidate, None, "tester")
    with pytest.raises(SubmitRefused, match="has not passed the gate"):
        submit(ledger, client, candidate)


def test_gate_pass_for_another_mode_does_not_count(ledger, client):
    candidate = ready(ledger)
    with pytest.raises(SubmitRefused, match="disprove"):
        submit(ledger, client, candidate, mode="disprove")


def test_refuses_without_approval(ledger, client, server):
    target_id = ledger.add_target(toy_target(), "platform")
    candidate = ledger.add_candidate(target_id, PROOF)
    ledger.record_gate(candidate, passed_gate(PROOF))
    with pytest.raises(SubmitRefused, match="no unused approval"):
        submit(ledger, client, candidate)
    assert server.posts == []


def test_approval_covers_the_exact_explanation(ledger, client, server):
    candidate = ready(ledger, explanation="the approved text")
    with pytest.raises(SubmitRefused, match="no unused approval"):
        submit(ledger, client, candidate, explanation="different text")
    assert server.posts == []


def test_refuses_when_theorem_was_solved_meanwhile(ledger, client, server):
    candidate = ready(ledger)
    server.status = "Proved"
    with pytest.raises(SubmitRefused, match="no longer open"):
        submit(ledger, client, candidate)
    assert server.posts == []


def test_refuses_when_statement_differs_from_snapshot(ledger, client, server):
    candidate = ready(ledger)
    server.statement = "theorem toy (n : Nat) : n + 0 = n := by sorry"
    with pytest.raises(SubmitRefused, match="differs from the frozen snapshot"):
        submit(ledger, client, candidate)
    assert server.posts == []


# --- exactly once ----------------------------------------------------------------------


def test_happy_path_reaches_a_verdict(ledger, client, server):
    submission = submit(ledger, client, ready(ledger))
    assert len(server.posts) == 1
    assert ledger.submission(submission)["state"] == "sent"
    results = poll_pending(ledger, client)
    assert results[0]["outcome"] == "proved"
    assert ledger.submission(submission)["status"] == "ACCEPTED"
    assert poll_pending(ledger, client) == []


def test_intent_is_on_disk_before_the_post(ledger, client, server, tmp_path):
    candidate = ready(ledger)
    seen = {}
    original = server._post

    def spy(request):
        other = Ledger(tmp_path / "ledger.sqlite")
        seen["states"] = [r["state"] for r in other.submissions()]
        other.close()
        return original(request)

    server._post = spy
    submit(ledger, client, candidate)
    assert seen["states"] == ["intent"]


def test_approval_is_single_use(ledger, client, server):
    candidate = ready(ledger)
    submit(ledger, client, candidate)
    with pytest.raises(SubmitRefused, match="no unused approval"):
        submit(ledger, client, candidate)
    assert len(server.posts) == 1


def test_crash_after_the_server_stored_it_never_duplicates(ledger, client, server):
    candidate = ready(ledger)
    server.store_then_drop = True
    with pytest.raises(httpx.ReadTimeout):
        submit(ledger, client, candidate)
    assert ledger.submissions()[0]["state"] == "intent"

    # Even with a fresh approval, submit refuses until reconcile has settled it.
    server.store_then_drop = False
    ledger.approve(candidate, None, "tester")
    with pytest.raises(SubmitRefused, match="reconcile"):
        submit(ledger, client, candidate)
    assert len(server.posts) == 1

    results = reconcile(ledger, client)
    assert results == [{"submission": 1, "state": "sent", "remote_id": "sub-1"}]
    assert ledger.submissions()[0]["state"] == "sent"
    with pytest.raises(SubmitRefused, match="already exists"):
        submit(ledger, client, candidate)
    assert len(server.posts) == 1


def test_crash_before_the_server_got_it_allows_a_retry(ledger, client, server):
    candidate = ready(ledger)
    server.fail_post = httpx.ConnectError("no route")
    with pytest.raises(httpx.ConnectError):
        submit(ledger, client, candidate)
    assert reconcile(ledger, client) == [{"submission": 1, "state": "not_sent"}]

    server.fail_post = None
    ledger.approve(candidate, None, "tester")
    submit(ledger, client, candidate)
    assert len(server.posts) == 1


def test_definite_refusal_is_marked_not_sent(ledger, client, server):
    candidate = ready(ledger)
    server.fail_post = 429
    with pytest.raises(PlatformError):
        submit(ledger, client, candidate)
    assert ledger.submissions()[0]["state"] == "not_sent"


def test_server_error_stays_unknown(ledger, client, server):
    candidate = ready(ledger)
    server.fail_post = 503
    with pytest.raises(PlatformError):
        submit(ledger, client, candidate)
    assert ledger.submissions()[0]["state"] == "intent"


def test_reductions_record_their_obligations(ledger, client, server):
    obligations = ["Theorems.Thm_child"]
    candidate = ready(ledger, gate=passed_gate(PROOF, obligations))
    submission = submit(ledger, client, candidate)
    assert [r["module"] for r in ledger.obligations(submission)] == obligations
    assert poll_pending(ledger, client)[0]["outcome"] == "proved_via_reduction"


def test_key_is_only_sent_to_the_refresh_endpoint(server):
    seen = []

    def recorder(request):
        seen.append((request.url.path, request.read()))
        return server(request)

    client = PlatformClient("p2m_secret", transport=httpx.MockTransport(recorder))
    client.theorem("th-1")
    carrying = [path for path, body in seen if b"p2m_secret" in body]
    assert carrying == ["/api/v1/agent/refresh"]


def test_sha_helper_matches_hashlib():
    assert sha256("x") == hashlib.sha256(b"x").hexdigest()
    assert json.loads('{"ok": true}')["ok"] is True
