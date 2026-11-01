"""The tool policy and sandbox, including canary tests. No model is involved."""

from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from p2m_agent.envs import LeanEnv
from p2m_agent.gate import GateResult, Check, Target, check
from p2m_agent.sandbox import Policy, bwrap_arguments, macos_profile, wrap
from p2m_agent.worker import ToolPolicy, evaluate_tool_call, feedback_prompt, platform_closure

TOOLCHAIN = "leanprover/lean4:v4.33.1"
SANDBOX = sys.platform == "darwin" or shutil.which("bwrap") is not None


@pytest.fixture
def policy(tmp_path) -> ToolPolicy:
    project = tmp_path / "project"
    (project / "Solutions").mkdir(parents=True)
    (project / "Theorems").mkdir()
    candidate = project / "Solutions" / "Sol_toy.lean"
    candidate.write_text("")
    (project / "Theorems" / "Thm_toy.lean").write_text("")
    mathlib = tmp_path / "packages"
    mathlib.mkdir()
    return ToolPolicy(project=project, candidate=candidate, readable=(mathlib,))


@pytest.mark.parametrize("tool", ["Edit", "Write"])
def test_only_the_candidate_is_editable(policy, tool):
    assert evaluate_tool_call(tool, {"file_path": str(policy.candidate)}, policy) is None
    assert evaluate_tool_call(tool, {"file_path": "Solutions/Sol_toy.lean"}, policy) is None
    # The statement must never change, whatever a model is told or decides.
    assert evaluate_tool_call(tool, {"file_path": "Theorems/Thm_toy.lean"}, policy)
    assert evaluate_tool_call(tool, {"file_path": "lakefile.lean"}, policy)
    assert evaluate_tool_call(tool, {"file_path": "/etc/passwd"}, policy)
    assert evaluate_tool_call(tool, {"file_path": "Solutions/../Theorems/Thm_toy.lean"}, policy)


def test_symlink_cannot_escape_to_edit(policy, tmp_path):
    outside = tmp_path / "outside.lean"
    outside.write_text("")
    link = policy.project / "Solutions" / "link.lean"
    link.symlink_to(outside)
    assert evaluate_tool_call("Write", {"file_path": str(link)}, policy)


def test_read_limits(policy, tmp_path):
    assert evaluate_tool_call("Read", {"file_path": "Theorems/Thm_toy.lean"}, policy) is None
    assert evaluate_tool_call("Read", {"file_path": str(tmp_path / "packages" / "x")}, policy) is None
    assert evaluate_tool_call("Read", {"file_path": str(Path.home() / ".ssh" / "id_rsa")}, policy)
    assert evaluate_tool_call("Read", {"file_path": "../secrets.json"}, policy)


def test_read_through_a_symlink_is_judged_by_its_destination(policy, tmp_path):
    secret = tmp_path / "credentials.json"
    secret.write_text("{}")
    link = policy.project / "Theorems" / "innocent.lean"
    link.symlink_to(secret)
    assert evaluate_tool_call("Read", {"file_path": str(link)}, policy)


def test_search_limits(policy):
    assert evaluate_tool_call("Grep", {"pattern": "x"}, policy) is None
    assert evaluate_tool_call("Grep", {"pattern": "x", "path": "Theorems"}, policy) is None
    assert evaluate_tool_call("Grep", {"pattern": "x", "path": "/Users"}, policy)
    assert evaluate_tool_call("Grep", {"pattern": "x", "glob": "../../**"}, policy)
    assert evaluate_tool_call("Glob", {"pattern": "**/*.lean"}, policy) is None
    assert evaluate_tool_call("Glob", {"pattern": "/Users/**"}, policy)
    assert evaluate_tool_call("Glob", {"pattern": "../**"}, policy)


@pytest.mark.parametrize(
    "tool",
    ["Bash", "Task", "Agent", "NotebookEdit", "mcp__claude_ai_Claude_Docs__read", "mcp__other__x", "WebFetch"],
)
def test_everything_else_is_denied(policy, tool):
    assert evaluate_tool_call(tool, {}, policy)


def test_lean_tools_are_allowed_except_builds(policy):
    assert evaluate_tool_call("mcp__lean__lean_goal", {}, policy) is None
    assert evaluate_tool_call("mcp__control__report_status", {}, policy) is None
    assert evaluate_tool_call("mcp__lean__lean_build", {}, policy)
    assert evaluate_tool_call("mcp__lean__lean_run_code", {}, policy)


def test_web_tools_block_the_platform_when_enabled(policy):
    web = ToolPolicy(policy.project, policy.candidate, allow_web=True)
    assert evaluate_tool_call("WebFetch", {"url": "https://arxiv.org/abs/1"}, web) is None
    assert evaluate_tool_call("WebFetch", {"url": "https://prove2.me/theorems/x"}, web)
    assert evaluate_tool_call("WebFetch", {"url": "https://www.prove2.me/x"}, web)
    assert evaluate_tool_call("WebSearch", {"query": "lemma"}, web) is None


def test_feedback_is_trimmed_to_what_matters():
    result = GateResult(
        passed=False, mode="prove", file_sha256="x",
        checks=[Check("compiles", False, "boom"), Check("other", True)],
        diagnostics=[
            {"severity": "warning", "line": 1, "column": 0, "message": "unused"},
            *[{"severity": "error", "line": i, "column": 0, "message": "E" * 5000} for i in range(20)],
        ],
    )
    text = feedback_prompt(result)
    assert "boom" in text and "unused" not in text
    assert text.count("- line") == 8
    assert len(text) < 12_000


def test_closure_contains_only_what_is_reachable(tmp_path):
    (tmp_path / "Theorems").mkdir()
    (tmp_path / "Definitions").mkdir()
    (tmp_path / "Definitions" / "Def_a.lean").write_text("def a := 1\n")
    (tmp_path / "Theorems" / "Thm_b.lean").write_text("import Definitions.Def_a\n")
    (tmp_path / "Theorems" / "Thm_unrelated.lean").write_text("")
    (tmp_path / "Solutions").mkdir()
    (tmp_path / "Solutions" / "Sol_target.lean").write_text("HIDDEN")
    closure = platform_closure("import Theorems.Thm_b\nimport Mathlib.Tactic\n", [tmp_path])
    assert set(closure) == {"Theorems.Thm_b", "Definitions.Def_a"}


# --- sandbox ---------------------------------------------------------------------------


def test_macos_profile_opens_only_named_paths(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    profile = macos_profile(Policy(read=(home / "r",), write=(home / "w",)), home)
    assert f'(deny file-read* (subpath "{home.resolve()}"))' in profile
    assert f'(allow file-read* (subpath "{(home / "r").resolve()}"))' in profile
    assert f'(allow file-write* (subpath "{(home / "w").resolve()}"))' in profile
    assert "(deny network*)" in profile
    assert "(deny network*)" not in macos_profile(Policy(network=True), home)


def test_bwrap_arguments_hide_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    arguments = bwrap_arguments(Policy(read=(home / "r",), write=(home / "w",)), home)
    text = " ".join(arguments)
    assert f"--tmpfs {home.resolve()}" in text
    assert f"--ro-bind {(home / 'r').resolve()}" in text
    assert f"--bind {(home / 'w').resolve()}" in text
    assert "--unshare-net" in arguments


@pytest.fixture
def lean_env(tmp_path) -> LeanEnv:
    env = LeanEnv(tmp_path / "env", TOOLCHAIN, "none")
    if not env.lean.exists():
        pytest.skip("needs the Lean toolchain")
    return env


@pytest.fixture
def canary():
    """A fake secret under the real home directory, which is what the sandbox denies."""
    directory = Path.home() / ".local" / "share" / "p2m-agent" / "secrets"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"canary-{uuid.uuid4().hex}"
    token = f"CANARY-{uuid.uuid4().hex}"
    path.write_text(token)
    yield path, token
    path.unlink()


def _eval_read(path: Path) -> str:
    return f'#eval IO.FS.readFile "{path}"\ntheorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\n'


@pytest.mark.lean
@pytest.mark.skipif(not SANDBOX, reason="needs sandbox-exec or bwrap")
def test_control_lean_reads_the_canary_without_the_sandbox(lean_env, canary, tmp_path):
    path, token = canary
    source = tmp_path / "Control.lean"
    source.write_text(_eval_read(path))
    run = subprocess.run([str(lean_env.lean), str(source)], capture_output=True, text=True)
    assert token in run.stdout, "the control must succeed, or the sandbox test proves nothing"


@pytest.mark.lean
@pytest.mark.skipif(not SANDBOX, reason="needs sandbox-exec or bwrap")
def test_sandboxed_lean_cannot_read_the_canary(lean_env, canary, tmp_path):
    path, token = canary
    workdir = Path.home() / ".local" / "share" / "p2m-agent" / "test-work" / uuid.uuid4().hex
    workdir.mkdir(parents=True)
    try:
        source = workdir / "Attack.lean"
        source.write_text(_eval_read(path))
        policy = Policy(read=(lean_env.bin_dir.parent,), write=(workdir,))
        run = subprocess.run(
            wrap(policy, [str(lean_env.lean), str(source)]), capture_output=True, text=True
        )
        assert token not in run.stdout + run.stderr
        assert "error" in (run.stdout + run.stderr).lower()
    finally:
        subprocess.run(["rm", "-rf", str(workdir)])


@pytest.mark.lean
@pytest.mark.skipif(not SANDBOX, reason="needs sandbox-exec or bwrap")
def test_sandboxed_lean_cannot_write_outside_or_use_the_network(lean_env, tmp_path):
    workdir = Path.home() / ".local" / "share" / "p2m-agent" / "test-work" / uuid.uuid4().hex
    workdir.mkdir(parents=True)
    outside = Path.home() / ".local" / "share" / "p2m-agent" / f"escape-{uuid.uuid4().hex}"
    try:
        source = workdir / "Attack.lean"
        source.write_text(
            f'#eval IO.FS.writeFile "{outside}" "x"\n'
            '#eval do\n'
            '  let child ← IO.Process.output { cmd := "/usr/bin/curl", args := #["-sS", "-m", "5", "https://prove2.me/api/v1/health"] }\n'
            '  IO.println child.stdout\n'
        )
        policy = Policy(read=(lean_env.bin_dir.parent,), write=(workdir,))
        run = subprocess.run(
            wrap(policy, [str(lean_env.lean), str(source)]), capture_output=True, text=True
        )
        assert not outside.exists()
        assert "prove2me-api" not in run.stdout
    finally:
        subprocess.run(["rm", "-rf", str(workdir)])
        outside.unlink(missing_ok=True)


@pytest.mark.lean
@pytest.mark.skipif(not SANDBOX, reason="needs sandbox-exec or bwrap")
def test_gate_works_under_the_sandbox_and_stops_a_stealing_candidate(lean_env, canary):
    from p2m_agent.worker import gate_wrap

    path, token = canary
    work = Path.home() / ".local" / "share" / "p2m-agent" / "test-work" / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        target = Target("toy", "theorem toy (n : Nat) : 0 + n = n := by sorry", "", "none", TOOLCHAIN)
        honest = check(
            "theorem solution (n : Nat) : 0 + n = n := Nat.zero_add n\n", target, lean_env,
            wrap=gate_wrap(lean_env, work), workdir=work / "a",
        )
        assert honest.passed, honest.failures()
        stealing = check(
            _eval_read(path), target, lean_env,
            wrap=gate_wrap(lean_env, work), workdir=work / "b",
        )
        assert token not in str(stealing.to_json())
    finally:
        subprocess.run(["rm", "-rf", str(work)])
