"""M6 live test: a private reduction from SKETCH_ACCEPTED to proved, through the real paths.

Hand-written toys, no model. `make-public` is never called and both toys are deprecated after.
Closing them counts as two first solves on the account, as calibration found.
"""
from __future__ import annotations

import json
import tempfile
import time
import uuid
from pathlib import Path

from p2m_agent.config import paths
from p2m_agent.envs import env_for
from p2m_agent.gate import check
from p2m_agent.ledger import Ledger
from p2m_agent.platform import PlatformClient, load_api_key, target_from_theorem
from p2m_agent.reductions import publish_child, sync_obligations, write_mirror
from p2m_agent.report import report
from p2m_agent.submit import poll_pending, submit
from p2m_agent.worker import gate_wrap

client = PlatformClient(load_api_key(paths().credentials))
profile = lambda: {k: client._get("/me").get(k) for k in ("num_solved_prob", "num_submitted_prob", "trust")}
before = profile()
tag = uuid.uuid4().hex[:8]
child, parent = f"p2m_agent_red_{tag}_child", f"p2m_agent_red_{tag}_parent"
note = "Private toy statement used only to test the reduction path of a local tool."
child_statement = f"theorem {child} (n : Nat) : 0 + n = n := by sorry"
child_id = publish_child(client, child, "Private reduction toy, child", child_statement, note)
parent_id = publish_child(
    client, parent, "Private reduction toy, parent",
    f"theorem {parent} (n : Nat) : 0 + (0 + n) = n := by sorry", note,
)
result = {"tag": tag, "profile_before": before}
try:
    target = target_from_theorem(client.theorem(parent_id), client.environments())
    env = env_for(target.toolchain, target.mathlib_rev)
    source = f"import Theorems.Thm_{child}\ntheorem solution (n : Nat) : 0 + (0 + n) = n := by\n  rw [{child}, {child}]\n"
    work = Path(tempfile.mkdtemp(dir=paths().home, prefix="m6-"))
    write_mirror(work / "mirrors", child, "", child_statement)
    gate = check(source, target, env, mirror_roots=[work / "mirrors"], wrap=gate_wrap(env, work), workdir=work / "g")
    result["gate"] = {"passed": gate.passed, "obligations": gate.obligations, "axioms": gate.axioms,
                      "failed": [(c.name, c.detail[:200]) for c in gate.failures()]}
    print(json.dumps(result["gate"]), flush=True)
    assert gate.passed

    ledger = Ledger(paths().ledger)
    target_row = ledger.add_target(target, "platform-private")
    candidate = ledger.add_candidate(target_row, source)
    ledger.record_gate(candidate, gate)
    ledger.approve(candidate, None, "M6 private toy, no public effect")
    submission = submit(ledger, client, candidate)

    def settle(wanted):
        for _ in range(60):
            poll_pending(ledger, client)
            if ledger.submission(submission)["status"] in wanted:
                return
            time.sleep(3)

    settle({"SKETCH_ACCEPTED", "ACCEPTED", "CE", "WA", "SORRY", "FAILED", "ERROR"})
    row = ledger.submission(submission)
    result["first_verdict"] = {"status": row["status"], "outcome": row["outcome"], "error": row["error_message"]}
    result["while_open"] = {
        "report": report(ledger)["submissions"], "sync": sync_obligations(ledger, client),
        "parent_status": client.theorem(parent_id).status,
    }
    print(json.dumps(result["first_verdict"]), json.dumps(result["while_open"]), flush=True)
    assert row["status"] == "SKETCH_ACCEPTED"

    remote = client.verify(child_id, "theorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\n", "prove")
    for _ in range(60):
        verdict = client.poll(remote)
        if verdict.status != "PENDING":
            break
        time.sleep(3)
    result["child_verdict"] = verdict.status

    for _ in range(40):
        result["after_child"] = {"sync": sync_obligations(ledger, client), "parent_status": client.theorem(parent_id).status}
        row = ledger.submission(submission)
        if row["outcome"] != "reduction_open" and result["after_child"]["parent_status"] == "Proved":
            break
        time.sleep(3)
    result["final"] = {"status": row["status"], "outcome": row["outcome"],
                       "obligations": [dict(o)["status"] for o in ledger.obligations(submission)],
                       "report": report(ledger)["submissions"]}
    print(json.dumps(result["child_verdict"]), json.dumps(result["after_child"]), json.dumps(result["final"]), flush=True)
finally:
    for theorem_id in (child_id, parent_id):
        client.set_theorem_deprecated(theorem_id, True)
    result["profile_after"] = profile()
    Path(paths().home / "m6-result.json").write_text(json.dumps(result, indent=1, default=str))
    print("profile", before, "->", result["profile_after"], flush=True)
