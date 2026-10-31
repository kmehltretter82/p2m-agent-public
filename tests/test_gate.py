"""Positive and negative controls for the gate. Core Lean only, so these run in seconds."""

from __future__ import annotations

from pathlib import Path

import pytest

from p2m_agent.config import paths
from p2m_agent.envs import LeanEnv
from p2m_agent.gate import Target, check, parse_imports, static_checks

TOOLCHAIN = "leanprover/lean4:v4.33.1"

pytestmark = pytest.mark.lean


@pytest.fixture(scope="module")
def env(tmp_path_factory) -> LeanEnv:
    bare = LeanEnv(tmp_path_factory.mktemp("env"), TOOLCHAIN, "none")
    if not bare.lean.exists():
        pytest.skip(f"{TOOLCHAIN} is not installed under {paths().toolchains}")
    return bare


def target(statement: str, name: str = "toy") -> Target:
    return Target(name, statement, "", "none", TOOLCHAIN)


ZERO_ADD = target("theorem toy (n : Nat) : 0 + n = n := by sorry")
ZERO_ADD_PROOF = "theorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\n"


def failed(result) -> set[str]:
    return {c.name for c in result.failures()}


def warned(result) -> set[str]:
    return {c.name for c in result.warnings()}


# --- positive controls -----------------------------------------------------------------


def test_real_proof_passes(env):
    result = check(ZERO_ADD_PROOF, ZERO_ADD, env)
    assert result.passed, result.failures()
    assert not result.warnings()
    assert set(result.axioms) <= {"propext", "Classical.choice", "Quot.sound"}
    assert result.compile_seconds is not None


def test_renamed_binder_passes(env):
    candidate = "theorem solution (m : Nat) : 0 + m = m := Nat.zero_add m\n"
    assert check(candidate, ZERO_ADD, env).passed


def test_renamed_universe_passes(env):
    t = target("theorem toy.{u} (α : Sort u) (a : α) : a = a := by sorry")
    candidate = "theorem solution.{v} (β : Sort v) (b : β) : b = b := rfl\n"
    assert check(candidate, t, env).passed


def test_helpers_are_allowed(env):
    candidate = (
        "theorem helper (n : Nat) : 0 + n = n := Nat.zero_add n\n"
        "theorem solution (n : Nat) : 0 + n = n := helper n\n"
    )
    assert check(candidate, ZERO_ADD, env).passed


def test_whole_disproof_passes(env):
    t = target("theorem toy (n : Nat) : n + 1 = 0 := by sorry")
    candidate = (
        "theorem solution : ¬ (∀ n : Nat, n + 1 = 0) := "
        "fun h => Nat.succ_ne_zero 0 (h 0)\n"
    )
    assert check(candidate, t, env, mode="disprove").passed


def test_namespaced_target_passes(env):
    t = target("namespace Foo.Bar\ntheorem toy (n : Nat) : 0 + n = n := by sorry\nend Foo.Bar", "Foo.Bar.toy")
    assert check(ZERO_ADD_PROOF, t, env).passed


# --- warnings: accepted, but flagged for calibration against the server ----------------


def test_defeq_only_statement_warns(env):
    t = target("theorem toy (n : Nat) : n + 0 = n := by sorry")
    result = check("theorem solution (n : Nat) : n = n := rfl\n", t, env)
    assert result.passed
    assert "statement_matches" in warned(result)


def test_binder_info_change_warns(env):
    candidate = "theorem solution {n : Nat} : 0 + n = n := Nat.zero_add n\n"
    result = check(candidate, ZERO_ADD, env)
    assert result.passed
    assert "binders_identical" in warned(result)


def test_whole_mathlib_import_warns():
    source = "import Mathlib\ntheorem solution : True := trivial\n"
    checks = static_checks(source, ZERO_ADD, "prove")
    assert any(c.name == "targeted_imports" and c.warning for c in checks)


# --- negative controls: each must fail, for the right reason ---------------------------


@pytest.mark.parametrize(
    ("label", "statement", "candidate", "mode", "reason"),
    [
        (
            "namespaced solution",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "namespace Foo\ntheorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\nend Foo\n",
            "prove",
            "solution_exists",
        ),
        (
            "private solution",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "private theorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\n",
            "prove",
            "solution_exists",
        ),
        (
            "wrong name",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "theorem toy2 (n : Nat) : 0 + n = n := Nat.zero_add n\n",
            "prove",
            "solution_exists",
        ),
        (
            "def rather than theorem",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "def solution (n : Nat) : 0 + n = n := Nat.zero_add n\n",
            "prove",
            "solution_is_theorem",
        ),
        (
            "extra hypothesis weakens the statement",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "theorem solution (n : Nat) (_ : n > 5) : 0 + n = n := Nat.zero_add n\n",
            "prove",
            "statement_matches",
        ),
        (
            "stronger hypothesis than the target's",
            "theorem toy (n : Nat) (h : n > 5) : n ≠ 0 := by sorry",
            "theorem solution (n : Nat) (h : n > 6) : n ≠ 0 := by omega\n",
            "prove",
            "statement_matches",
        ),
        (
            "changed binder type",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "theorem solution (n : Int) : 0 + n = n := Int.zero_add n\n",
            "prove",
            "statement_matches",
        ),
        (
            "local instance changes what the same text means",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "local instance : Add Nat := ⟨fun _ b => b⟩\n"
            "theorem solution (n : Nat) : 0 + n = n := rfl\n",
            "prove",
            "statement_matches",
        ),
        (
            "local notation changes what the same text means",
            "theorem toy (n m : Nat) : max n m = max m n := by sorry",
            'local notation "max" => Nat.add\n'
            "theorem solution (n m : Nat) : max n m = max m n := Nat.add_comm n m\n",
            "prove",
            "statement_matches",
        ),
        (
            "sorry in the proof",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "theorem solution (n : Nat) : 0 + n = n := by sorry\n",
            "prove",
            "no_own_sorry",
        ),
        (
            "sorry hidden in a helper",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "theorem helper (n : Nat) : 0 + n = n := sorry\n"
            "theorem solution (n : Nat) : 0 + n = n := helper n\n",
            "prove",
            "no_own_sorry",
        ),
        (
            "new axiom",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "axiom cheat (n : Nat) : 0 + n = n\n"
            "theorem solution (n : Nat) : 0 + n = n := cheat n\n",
            "prove",
            "no_new_axioms",
        ),
        (
            "native_decide brings a compiler axiom",
            "theorem toy : 2 ^ 10 = 1024 := by sorry",
            "theorem solution : 2 ^ 10 = 1024 := by native_decide\n",
            "prove",
            "axioms_allowed",
        ),
        (
            "compile error",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "theorem solution (n : Nat) : 0 + n = n := no_such_lemma n\n",
            "prove",
            "compiles",
        ),
        (
            "auto-bound implicit is an error, as on the server",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "theorem solution (x : α) (n : Nat) : 0 + n = n := Nat.zero_add n\n",
            "prove",
            "compiles",
        ),
        (
            "imports its own target",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "import Theorems.Thm_toy\ntheorem solution (n : Nat) : 0 + n = n := toy n\n",
            "prove",
            "does_not_import_target",
        ),
        (
            "disproof that negates only the conclusion",
            "theorem toy (n : Nat) : n + 1 = 0 := by sorry",
            "theorem solution (n : Nat) : ¬ (n + 1 = 0) := Nat.succ_ne_zero n\n",
            "disprove",
            "statement_matches",
        ),
        (
            "proves the target while claiming to disprove it",
            "theorem toy (n : Nat) : 0 + n = n := by sorry",
            "theorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\n",
            "disprove",
            "statement_matches",
        ),
    ],
)
def test_negative_control(env, label, statement, candidate, mode, reason):
    result = check(candidate, target(statement), env, mode=mode)
    assert not result.passed, f"{label}: the gate accepted it"
    assert reason in failed(result), f"{label}: failed {failed(result)}, wanted {reason}"


def test_disproof_may_not_import_theorems():
    source = "import Theorems.Thm_other\ntheorem solution : True := trivial\n"
    checks = static_checks(source, ZERO_ADD, "disprove")
    assert any(c.name == "disproof_imports_no_theorems" and not c.ok for c in checks)


# --- reductions: mirrored imports ------------------------------------------------------


def write_mirror(root: Path, module: str, source: str) -> None:
    path = root / Path(*module.split(".")).with_suffix(".lean")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)


def test_reduction_is_flagged_with_its_obligations(env, tmp_path):
    write_mirror(tmp_path, "Theorems.Thm_child", "theorem child (n : Nat) : 0 + n = n := by sorry\n")
    candidate = "import Theorems.Thm_child\ntheorem solution (n : Nat) : 0 + n = n := child n\n"
    result = check(candidate, ZERO_ADD, env, mirror_roots=[tmp_path])
    assert result.passed, result.failures()
    assert result.obligations == ["Theorems.Thm_child"]
    assert "sorryAx" in result.axioms


def test_reduction_still_rejects_its_own_sorry(env, tmp_path):
    write_mirror(tmp_path, "Theorems.Thm_child", "theorem child (n : Nat) : 0 + n = n := by sorry\n")
    candidate = "import Theorems.Thm_child\ntheorem solution (n : Nat) : 0 + n = n := by sorry\n"
    result = check(candidate, ZERO_ADD, env, mirror_roots=[tmp_path])
    assert not result.passed
    assert "no_own_sorry" in failed(result)


def test_target_reached_through_a_mirror_is_rejected(env, tmp_path):
    write_mirror(tmp_path, "Theorems.Thm_toy", "theorem toy (n : Nat) : 0 + n = n := by sorry\n")
    write_mirror(tmp_path, "Theorems.Thm_sneaky", "import Theorems.Thm_toy\ntheorem sneaky (n : Nat) : 0 + n = n := toy n\n")
    candidate = "import Theorems.Thm_sneaky\ntheorem solution (n : Nat) : 0 + n = n := sneaky n\n"
    result = check(candidate, ZERO_ADD, env, mirror_roots=[tmp_path])
    assert not result.passed
    assert "target_not_in_dependencies" in failed(result)


def test_unknown_platform_import_is_reported(env):
    candidate = "import Theorems.Thm_missing\ntheorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\n"
    result = check(candidate, ZERO_ADD, env)
    assert "imports_resolve" in failed(result)


def test_parse_imports_stops_at_code():
    source = "-- note\n/- block\nimport Fake\n-/\nimport A.B\n\nimport C\nopen D\nimport E\n"
    assert parse_imports(source) == ["A.B", "C"]
