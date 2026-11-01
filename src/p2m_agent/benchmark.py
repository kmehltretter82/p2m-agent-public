"""M4: the agent against plain Claude Code, on frozen targets with the same limits.

Both arms run on the same Agent SDK and the same stripped settings, so the harness gets no
credit for flags anyone can set. Nothing is ever submitted. Answers stay hidden because each
attempt's project holds only the target's own dependencies, and web tools are off in both arms.
A plan is safe to re-run: finished attempts are skipped and paused ones resume.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

from . import gate as gate_module
from .config import paths
from .envs import LeanEnv, env_for
from .ledger import Ledger
from .mirror import target_from_mirror
from .worker import (
    AttemptConfig,
    Limits,
    ToolPolicy,
    _consume,
    evaluate_tool_call,
    gate_wrap,
    prepare_project,
    run_attempt,
)

BASELINE_TOOLS = ["Read", "Edit", "Write", "Grep", "Glob", "Bash"]
BASELINE_PROMPT = """\
Prove the Lean 4 theorem `{name}` for the Prove2Me platform, by writing `{candidate}`.

The platform's own instructions are in `SKILL.md` and `references/prove.md`. Do not submit
anything: there is no network access to the platform here. Build with
`lake build {module}` until it succeeds. The target's file is `{target_file}`:

```lean
{statement}```
"""


@dataclass
class Plan:
    name: str
    toolchain: str
    mathlib_rev: str
    mirror_root: Path
    targets: list[str]
    skill_root: Path | None = None
    model: str = "claude-opus-5-5"
    effort: str = "high"
    budget_usd: float = 3.0
    max_turns: int = 120
    wall_clock_seconds: int = 2400
    arms: list[str] = field(default_factory=lambda: ["agent", "baseline"])

    @classmethod
    def load(cls, path: Path) -> Plan:
        data = json.loads(path.read_text())
        data["mirror_root"] = Path(data["mirror_root"]).expanduser()
        if data.get("skill_root"):
            data["skill_root"] = Path(data["skill_root"]).expanduser()
        return cls(**data)

    def limits(self) -> Limits:
        return Limits(self.max_turns, self.budget_usd, self.wall_clock_seconds, max_gate_rounds=8)


def baseline_denial(name: str, arguments: dict, policy: ToolPolicy) -> str | None:
    """The baseline may build and edit inside its project, and nothing else."""
    if name == "Bash":
        command = str(arguments.get("command", "")).strip()
        allowed = ("lake build", "lake env lean", "lean ")
        forbidden = any(token in command for token in (";", "&&", "||", "|", "`", "$(", ">", "<"))
        if not command.startswith(allowed) or forbidden:
            return "only `lake build` and `lean` commands are available here"
        return None
    if name in ("Edit", "Write"):
        path = Path(str(arguments.get("file_path", ""))).expanduser()
        path = (path if path.is_absolute() else policy.project / path).resolve()
        if path != policy.candidate.resolve():
            return "you may only edit the solution file"
        return None
    if name in ("Read", "Grep", "Glob"):
        return evaluate_tool_call(name, arguments, policy)
    return f"{name} is not available here"


async def run_baseline(
    ledger: Ledger, target_id: int, plan: Plan, env: LeanEnv, label: str, resume_attempt: int | None
) -> tuple[str, int | None]:
    target = ledger.target(target_id)
    session = None
    if resume_attempt is not None:
        attempt_id = resume_attempt
        row = ledger.attempt(attempt_id)
        session = row["session_id"]
        workdir = Path(row["workdir"])
        ledger.set_attempt_state(attempt_id, "running")
    else:
        attempt_id = ledger.start_attempt(target_id, "prove", plan.model, plan.effort, label=label)
        workdir = paths().attempts / str(attempt_id)
        workdir.mkdir(parents=True, exist_ok=True)
        with ledger._tx() as db:
            db.execute("UPDATE attempts SET workdir = ? WHERE id = ?", (str(workdir), attempt_id))

    project, candidate = prepare_project(workdir, target, env, [plan.mirror_root])
    if plan.skill_root:
        shutil.copy2(plan.skill_root / "SKILL.md", project / "SKILL.md")
        (project / "references").mkdir(exist_ok=True)
        shutil.copy2(plan.skill_root / "references" / "prove.md", project / "references" / "prove.md")
    log_path = workdir / "events.jsonl"

    def log(event: dict) -> None:
        with log_path.open("a") as handle:
            handle.write(json.dumps(event, default=str) + "\n")

    policy = ToolPolicy(
        project=project, candidate=candidate,
        readable=((env.root / ".lake" / "packages").resolve(), env.bin_dir.parent),
    )

    async def hook(hook_input, tool_use_id, context):
        reason = baseline_denial(hook_input.get("tool_name", ""), hook_input.get("tool_input") or {}, policy)
        if reason is None:
            return {}
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    options = ClaudeAgentOptions(
        model=plan.model, effort=plan.effort,
        system_prompt={"type": "preset", "preset": "claude_code"},
        tools=BASELINE_TOOLS,
        allowed_tools=["Read", "Edit", "Write", "Grep", "Glob", "Bash(lake:*)", "Bash(lean:*)"],
        permission_mode="dontAsk", setting_sources=[], strict_mcp_config=True,
        hooks={"PreToolUse": [HookMatcher(hooks=[hook])]},
        cwd=str(project), max_turns=plan.max_turns, max_budget_usd=plan.budget_usd,
        env={"PATH": f"{env.bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"},
        resume=session,
    )
    prompt = (
        "Your previous session was interrupted. Check your file and carry on." if session else
        BASELINE_PROMPT.format(
            name=target.theorem_name, candidate=candidate.relative_to(project),
            module=target.solution_module,
            target_file=Path(*target.module.split(".")).with_suffix(".lean"),
            statement=target.mirror_source(),
        )
    )
    limits = plan.limits()
    try:
        async with asyncio.timeout(limits.wall_clock_seconds):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                run = await _consume(client, ledger, attempt_id, log, limits)
    except TimeoutError:
        run = None

    if run and (run.limit_rejected or run.soft_limit):
        ledger.set_attempt_state(attempt_id, "paused")
        return "limit_wait", run.resets_at

    # The baseline has no gate loop: its result is judged once, by the same gate.
    source = candidate.read_text()
    outcome, note = "stuck", "no file was written"
    if source.strip():
        mode = "disprove" if "¬" in source and "theorem solution :" in source else "prove"
        gate_dir = workdir / "gate"
        shutil.rmtree(gate_dir, ignore_errors=True)
        gate_dir.mkdir(parents=True)
        candidate_id = ledger.add_candidate(target_id, source, attempt_id)
        result = await asyncio.to_thread(
            gate_module.check, source, target, env, mode=mode, mirror_roots=[project],
            cache_dir=env.root / ".gate-cache", wrap=gate_wrap(env, workdir), workdir=gate_dir,
        )
        ledger.record_gate(candidate_id, result, attempt_id)
        if result.passed:
            outcome, note = ("disproved_locally" if mode == "disprove" else "proved_locally"), ""
        else:
            outcome, note = "stuck", "gate: " + ", ".join(c.name for c in result.failures())
    if run is None:
        note = f"wall-clock limit; {note}"
        outcome = outcome if outcome.endswith("_locally") else "budget_exhausted"
    elif run.subtype in ("error_max_budget_usd", "error_max_turns") and not outcome.endswith("_locally"):
        outcome, note = "budget_exhausted", run.subtype
    ledger.finish_attempt(attempt_id, outcome, note)
    return outcome, None


async def run_benchmark(
    ledger: Ledger, plan_path: Path, arms: list[str] | None = None, only: list[str] | None = None
) -> dict:
    plan = Plan.load(plan_path)
    env = env_for(plan.toolchain, plan.mathlib_rev)
    selected = [s for s in plan.targets if not only or s in only]
    ran: list[dict] = []
    # Alternate which arm goes first, so neither always runs with a warmer file cache.
    for index, slug in enumerate(selected):
        order = list(arms or plan.arms)
        if index % 2:
            order.reverse()
        for arm in order:
            label = f"{plan.name}:{arm}:{slug}"
            existing = ledger.attempts(label)
            if existing and existing[-1]["state"] == "finished":
                continue
            resume = existing[-1]["id"] if existing else None
            target = target_from_mirror(plan.mirror_root, slug, plan.mathlib_rev, plan.toolchain)
            target_id = ledger.add_target(target, "mirror")
            print(f"[{label}] starting", file=sys.stderr, flush=True)
            if arm == "agent":
                result = await run_attempt(ledger, AttemptConfig(
                    target_id=target_id, env=env, mirror_roots=[plan.mirror_root], model=plan.model,
                    effort=plan.effort, limits=plan.limits(), label=label, resume_attempt=resume,
                ))
                outcome, resets_at = result.outcome, result.resets_at
            else:
                outcome, resets_at = await run_baseline(ledger, target_id, plan, env, label, resume)
            ran.append({"label": label, "outcome": outcome})
            print(f"[{label}] {outcome}", file=sys.stderr, flush=True)
            if outcome == "limit_wait":
                return {"status": "paused", "resets_at": resets_at, "ran": ran}
    return {"status": "complete", "ran": ran}
