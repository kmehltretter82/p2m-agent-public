"""Positive controls: your real, accepted-style solutions against real Mathlib.

These need a built environment and the local `prove2me_workspace`, so they skip elsewhere.
Each one also has a mutated twin, to show the gate is not simply passing everything.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from p2m_agent.envs import env_for
from p2m_agent.gate import check
from p2m_agent.mirror import declared_theorems, target_from_mirror

REV = "0df444a360eaa60ab8c11dca51a86af692955474"
TOOLCHAIN = "leanprover/lean4:v4.33.1"
WORKSPACE = Path(
    os.environ.get("P2M_MIRROR_ROOT", "~/codex/collaboration/prove2me_workspace")
).expanduser()

SLUGS = [
    "PathsTreesFlowers_Invariance_lemma_4_2",  # 33 lines, a reduction with one child
    "SeymourMFMC_Binary_parity_decomposition",  # 149 lines, direct
    "MonoReset_IntervalBFS_lemma_4",  # 238 lines, direct
]

mathlib = pytest.mark.mathlib


@pytest.fixture(scope="module")
def env():
    environment = env_for(TOOLCHAIN, REV)
    if not environment.package_libs() or not WORKSPACE.is_dir():
        pytest.skip("needs the Mathlib environment and the local workspace mirrors")
    return environment


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("gate-cache")


@mathlib
@pytest.mark.parametrize("slug", SLUGS)
def test_real_solution_passes(env, cache, slug):
    target = target_from_mirror(WORKSPACE, slug, REV, TOOLCHAIN)
    source = (WORKSPACE / "Solutions" / f"Sol_{slug}.lean").read_text()
    result = check(source, target, env, mirror_roots=[WORKSPACE], cache_dir=cache, timeout=1500)
    assert result.passed, [(c.name, c.detail[:400]) for c in result.failures()]
    print(slug, result.compile_seconds, result.axioms, result.obligations)


@mathlib
def test_mutated_real_solution_fails(env, cache):
    slug = "MonoReset_IntervalBFS_lemma_4"
    target = target_from_mirror(WORKSPACE, slug, REV, TOOLCHAIN)
    source = (WORKSPACE / "Solutions" / f"Sol_{slug}.lean").read_text()
    # Keep everything except the statement of `solution`, which gains a false premise.
    mutated, count = re.subn(r"theorem solution\b", "theorem solution (cheat : False)", source, count=1)
    assert count == 1
    result = check(mutated, target, env, mirror_roots=[WORKSPACE], cache_dir=cache, timeout=1500)
    assert not result.passed
    assert "statement_matches" in {c.name for c in result.failures()} or "compiles" in {
        c.name for c in result.failures()
    }


def test_declared_theorems_tracks_namespaces():
    source = """
/- theorem commented (x : Nat) : True := trivial -/
namespace A.B
@[simp] theorem one : True := trivial
namespace C
private theorem two : True := trivial
end C
theorem three : True := trivial
end A.B
lemma four : True := trivial
"""
    assert declared_theorems(source) == ["A.B.one", "A.B.C.two", "A.B.three", "four"]
