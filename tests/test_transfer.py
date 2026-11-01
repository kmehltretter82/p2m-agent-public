from __future__ import annotations

import pytest

from p2m_agent.config import Paths
from p2m_agent.gate import Target
from p2m_agent.ledger import Ledger
from p2m_agent.transfer import export_state, import_state


def test_state_moves_and_paths_are_rewritten(tmp_path):
    source = Paths(tmp_path / "mac")
    ledger = Ledger(source.ledger)
    target = ledger.add_target(Target("toy", "theorem toy : True := by sorry", "", "r", "t"), "local")
    attempt = ledger.start_attempt(target, "prove", "m", "high", workdir=str(source.attempts / "1"))
    work = source.attempts / str(attempt)
    (work / "project" / ".lake").mkdir(parents=True)
    (work / "project" / ".lake" / "big.olean").write_text("x")
    (work / "events.jsonl").write_text("{}\n")
    ledger.close()

    archive = tmp_path / "state.tar.gz"
    export_state(source, archive)

    destination = Paths(tmp_path / "pi")
    assert import_state(destination, archive)["attempts"] == 1
    moved = Ledger(destination.ledger)
    assert moved.attempt(attempt)["workdir"] == str(destination.attempts / "1")
    assert moved.target(target).theorem_name == "toy"
    assert (destination.attempts / "1" / "events.jsonl").exists()
    assert not (destination.attempts / "1" / "project" / ".lake").exists()


def test_import_refuses_to_overwrite_a_ledger(tmp_path):
    source = Paths(tmp_path / "mac")
    Ledger(source.ledger).close()
    archive = tmp_path / "state.tar.gz"
    export_state(source, archive)
    with pytest.raises(FileExistsError):
        import_state(source, archive)
