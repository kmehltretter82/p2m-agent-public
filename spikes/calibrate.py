"""M2 calibration: does the local gate agree with the real server?

Private toy theorems only, and `make-public` is never called. All accept-cases share one
theorem, because `num_solved_prob` and `trust` count at most once per theorem. Toys are
deprecated at the end. Writes docs/calibration.json.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import httpx

from p2m_agent.config import paths
from p2m_agent.envs import env_for
from p2m_agent.gate import check
from p2m_agent.ledger import Ledger
from p2m_agent.platform import PlatformClient, load_api_key, target_from_theorem
from p2m_agent.submit import poll_pending, reconcile, submit


class CrashAfterPost(PlatformClient):
    """Sends the POST for real, then behaves as if the connection dropped."""

    def verify(self, *args, **kwargs):
        super().verify(*args, **kwargs)
        raise httpx.ReadTimeout("simulated crash after the server stored it")


client = PlatformClient(load_api_key(paths().credentials))
tag = uuid.uuid4().hex[:8]
stats = lambda: {k: client._get("/me").get(k) for k in ("num_solved_prob", "num_submitted_prob", "trust")}
before = stats()


def publish(suffix: str, statement_tail: str):
    name = f"p2m_agent_cal_{tag}_{suffix}"
    job = client.submit_problem(
        name, f"Private calibration toy {suffix}",
        f"theorem {name} {statement_tail} := by sorry",
        "Private toy statement used only to calibrate a local checker against the server.",
        private=True,
    )
    for _ in range(100):
        state = client.publish_job(job)
        if state["status"] in ("PUBLISHED", "FAILED", "ERROR"):
            break
        time.sleep(3)
    if state["status"] != "PUBLISHED":
        raise SystemExit(f"publish failed: {state.get('status')} {state.get('error_message')}")
    return name, state["theorem_id"]


A, A_id = publish("a", "(n : Nat) : n + 0 = n")
B, B_id = publish("b", "(n : Nat) : 0 + n = n")
C, C_id = publish("c", "(n : Nat) : n + 1 = n")
environments = client.environments()
targets = {i: target_from_theorem(client.theorem(i), environments) for i in (A_id, B_id, C_id)}
env = env_for(targets[A_id].toolchain, targets[A_id].mathlib_rev)
GOOD = "theorem solution (n : Nat) : n + 0 = n := rfl\n"

REJECT_FIRST = [
    ("compile error", A_id, "prove", "theorem solution (n : Nat) : n + 0 = n := no_such_lemma n\n"),
    ("extra hypothesis", A_id, "prove", "theorem solution (n : Nat) (_ : n > 5) : n + 0 = n := rfl\n"),
    ("changed binder type", A_id, "prove", "theorem solution (n : Int) : n + 0 = n := Int.add_zero n\n"),
    ("sorry", A_id, "prove", "theorem solution (n : Nat) : n + 0 = n := by sorry\n"),
    ("sorry in a helper", A_id, "prove", "theorem h (n : Nat) : n + 0 = n := sorry\ntheorem solution (n : Nat) : n + 0 = n := h n\n"),
    ("imports its target", A_id, "prove", f"import Theorems.Thm_{A}\ntheorem solution (n : Nat) : n + 0 = n := {A} n\n"),
    ("new axiom", A_id, "prove", "axiom cheat (n : Nat) : n + 0 = n\ntheorem solution (n : Nat) : n + 0 = n := cheat n\n"),
    ("native_decide", A_id, "prove", "theorem solution (n : Nat) : n + 0 = n := by\n  have _h : 2 ^ 10 = 1024 := by native_decide\n  exact (fun _ => rfl) _h\n"),
    ("namespaced solution", A_id, "prove", f"namespace Foo\n{GOOD}end Foo\n"),
    ("local instance trick", B_id, "prove", "local instance : Add Nat := ⟨fun _ b => b⟩\ntheorem solution (n : Nat) : 0 + n = n := rfl\n"),
    ("disproof negating only the conclusion", C_id, "disprove", "theorem solution (n : Nat) : ¬ (n + 1 = n) := by omega\n"),
]
ACCEPT_AFTER = [
    ("def instead of theorem", A_id, "prove", "def solution (n : Nat) : n + 0 = n := rfl\n"),
    ("renamed binder", A_id, "prove", "theorem solution (m : Nat) : m + 0 = m := rfl\n"),
    ("implicit binder instead of explicit", A_id, "prove", "theorem solution {n : Nat} : n + 0 = n := rfl\n"),
    ("definitionally equal statement", A_id, "prove", "theorem solution (n : Nat) : n = n := rfl\n"),
]


def wait(remote: str) -> tuple[str, str]:
    for _ in range(100):
        verdict = client.poll(remote)
        if verdict.status != "PENDING":
            return verdict.status, (verdict.error_message or "")[:300]
        time.sleep(3)
    return "TIMEOUT", ""


rows = []


def run_case(label: str, theorem_id: str, mode: str, source: str) -> None:
    result = check(source, targets[theorem_id], env, mode=mode)
    remote = client.verify(theorem_id, source, mode)
    status, error = wait(remote)
    rows.append({
        "case": label, "gate": "pass" if result.passed else "fail",
        "gate_failed": [c.name for c in result.failures()],
        "gate_warned": [c.name for c in result.warnings()],
        "server": status, "server_error": error,
    })
    print(json.dumps(rows[-1], ensure_ascii=False)[:300], flush=True)


for case in REJECT_FIRST:
    run_case(*case)

# The real submit path, with a crash between the POST and recording its result.
ledger = Ledger(paths().ledger)
target_id = ledger.add_target(targets[A_id], "platform-private")
candidate = ledger.add_candidate(target_id, GOOD)
ledger.record_gate(candidate, check(GOOD, targets[A_id], env))
ledger.approve(candidate, None, "calibration on a private toy")
crashing = CrashAfterPost(load_api_key(paths().credentials))
try:
    submit(ledger, crashing, candidate)
    raise SystemExit("expected a simulated crash")
except httpx.ReadTimeout:
    pass
states = {"after_crash": ledger.submissions()[-1]["state"]}
states["reconcile"] = reconcile(ledger, client)
for _ in range(60):
    results = poll_pending(ledger, client)
    if results and results[-1]["status"] != "PENDING":
        states["verdict"] = results[-1]
        break
    time.sleep(3)
states["posts_for_this_file"] = sum(
    1 for s in client.my_submissions() if s.theorem_id == A_id
    and client.submission_source(s.id) == GOOD
)
print(json.dumps(states, default=str)[:600], flush=True)
rows.append({"case": "plain proof through submit() with a crash", "gate": "pass", "gate_failed": [],
             "gate_warned": [], "server": states.get("verdict", {}).get("status"), "server_error": ""})

for case in ACCEPT_AFTER:
    run_case(*case)

for theorem_id in (A_id, B_id, C_id):
    client.set_theorem_deprecated(theorem_id, True)
after = stats()
Path("docs/calibration.json").write_text(json.dumps(
    {"tag": tag, "profile_before": before, "profile_after": after, "crash_recovery": states, "cases": rows},
    indent=2, ensure_ascii=False, default=str))
print("profile", before, "->", after)
