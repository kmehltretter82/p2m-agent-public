"""The `p2m` command line. Prints JSON, so both people and scripts can read it."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from . import gate as gate_module
from .config import paths
from .envs import env_for, ensure_env
from .ledger import Ledger
from .mirror import target_from_mirror
from .worker import DEFAULT_EFFORT, DEFAULT_MODEL, AttemptConfig, Limits, run_attempt


def _print(value) -> None:
    print(json.dumps(value, indent=2, default=str))


def _ledger() -> Ledger:
    return Ledger(paths().ledger)


def _client():
    from .platform import PlatformClient, load_api_key

    path = Path(os.environ.get("P2M_CREDENTIALS", paths().credentials)).expanduser()
    if not path.exists():
        raise SystemExit(f"no Prove2Me credentials at {path}; see README.md")
    return PlatformClient(load_api_key(path))


def _mirror_roots(args) -> list[Path]:
    return [Path(p).expanduser() for p in (args.mirror or [])]


def cmd_env(args) -> None:
    seed = Path(args.seed).expanduser() if args.seed else None
    _print(ensure_env(env_for(args.toolchain, args.mathlib_rev), seed))


def cmd_target_add(args) -> None:
    from .platform import target_from_theorem

    client = _client()
    target = target_from_theorem(client.theorem(args.theorem_id), client.environments())
    _print({"target": _ledger().add_target(target, "platform"), "name": target.theorem_name})


def cmd_target_import(args) -> None:
    target = target_from_mirror(Path(args.root).expanduser(), args.slug, args.mathlib_rev, args.toolchain)
    _print({"target": _ledger().add_target(target, "mirror"), "name": target.theorem_name})


def cmd_target_list(args) -> None:
    _print([
        {k: row[k] for k in ("id", "theorem_name", "theorem_id", "mathlib_rev", "source")}
        for row in _ledger().targets()
    ])


def cmd_check(args) -> None:
    ledger = _ledger()
    target = ledger.target(args.target)
    source = Path(args.file).read_text()
    env = env_for(target.toolchain, target.mathlib_rev)
    result = gate_module.check(
        source, target, env, mode=args.mode, mirror_roots=_mirror_roots(args),
        cache_dir=env.root / ".gate-cache",
    )
    candidate = ledger.add_candidate(args.target, source)
    ledger.record_gate(candidate, result)
    _print({"candidate": candidate, **result.to_json()})
    sys.exit(0 if result.passed else 1)


def cmd_attempt_run(args) -> None:
    ledger = _ledger()
    target = ledger.target(args.target)
    config = AttemptConfig(
        target_id=args.target,
        env=env_for(target.toolchain, target.mathlib_rev),
        mirror_roots=_mirror_roots(args),
        model=args.model,
        effort=args.effort,
        limits=Limits(args.max_turns, args.max_budget, args.wall_clock, args.max_rounds),
        allow_web=args.web,
        label=args.label,
        briefing=Path(args.briefing).read_text() if args.briefing else "",
    )
    _print(asyncio.run(run_attempt(ledger, config)).__dict__)


def cmd_attempt_resume(args) -> None:
    ledger = _ledger()
    row = ledger.attempt(args.attempt)
    target = ledger.target(row["target_id"])
    config = AttemptConfig(
        target_id=row["target_id"],
        env=env_for(target.toolchain, target.mathlib_rev),
        mirror_roots=_mirror_roots(args),
        model=row["model"] or DEFAULT_MODEL,
        effort=row["effort"] or DEFAULT_EFFORT,
        limits=Limits(args.max_turns, args.max_budget, args.wall_clock, args.max_rounds),
        label=row["label"],
        resume_attempt=args.attempt,
    )
    _print(asyncio.run(run_attempt(ledger, config)).__dict__)


def cmd_approve(args) -> None:
    from .approval import approval_summary

    ledger = _ledger()
    explanation = Path(args.explanation).read_text() if args.explanation else None
    summary = approval_summary(ledger, args.candidate, args.mode, explanation)
    if not args.yes:
        _print(summary)
        print("\nNothing approved. Re-run with --yes --by <your name> to approve this exact file.")
        return
    if not args.by:
        raise SystemExit("--yes needs --by <your name>, which is recorded with the approval")
    _print({"approval": ledger.approve(args.candidate, explanation, args.by), **summary})


def cmd_submit(args) -> None:
    from .submit import submit

    explanation = Path(args.explanation).read_text() if args.explanation else None
    _print({"submission": submit(_ledger(), _client(), args.candidate, args.mode, explanation)})


def cmd_poll(args) -> None:
    from .submit import poll_pending

    _print(poll_pending(_ledger(), _client()))


def cmd_reconcile(args) -> None:
    from .submit import reconcile

    _print(reconcile(_ledger(), _client()))


def cmd_report(args) -> None:
    from .report import report

    _print(report(_ledger(), args.label))


def cmd_benchmark(args) -> None:
    from .benchmark import run_benchmark

    while True:
        result = asyncio.run(run_benchmark(_ledger(), Path(args.plan), args.arm, args.only))
        _print(result)
        if result["status"] != "paused" or not args.wait:
            return
        # A reset time can lie days ahead. Sleep until then, or an hour if none was reported.
        until = result.get("resets_at") or time.time() + 3600
        time.sleep(max(60.0, until - time.time() + 60.0))


def cmd_frontier(args) -> None:
    from .scout import frontier

    _print(frontier(_client(), args.mission))


def cmd_dossier(args) -> None:
    from .scout import dossier

    text = dossier(_client(), args.theorem, mission_id=args.mission, milestone_id=args.milestone)
    if args.output:
        Path(args.output).write_text(text)
        _print({"written": args.output, "characters": len(text)})
    else:
        print(text)


def cmd_sync(args) -> None:
    from .reductions import sync_obligations

    _print(sync_obligations(_ledger(), _client()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="p2m", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def mirror_option(p):
        p.add_argument("--mirror", action="append", help="directory of local platform mirrors")

    p = commands.add_parser("env", help="create or repair a pinned Lean environment")
    p.add_argument("toolchain")
    p.add_argument("mathlib_rev")
    p.add_argument("--seed", help="existing project to clone .lake/packages from (read only)")
    p.set_defaults(run=cmd_env)

    target = commands.add_parser("target", help="freeze a target").add_subparsers(required=True)
    p = target.add_parser("add", help="freeze a platform theorem by id")
    p.add_argument("theorem_id")
    p.set_defaults(run=cmd_target_add)
    p = target.add_parser("import", help="freeze a theorem from a local mirror")
    p.add_argument("root")
    p.add_argument("slug")
    p.add_argument("mathlib_rev")
    p.add_argument("toolchain")
    p.set_defaults(run=cmd_target_import)
    p = target.add_parser("list")
    p.set_defaults(run=cmd_target_list)

    p = commands.add_parser("check", help="run the gate; exits non-zero on failure")
    p.add_argument("file")
    p.add_argument("--target", type=int, required=True)
    p.add_argument("--mode", choices=["prove", "disprove"], default="prove")
    mirror_option(p)
    p.set_defaults(run=cmd_check)

    attempt = commands.add_parser("attempt", help="run the worker").add_subparsers(required=True)

    def limits(p):
        p.add_argument("--max-turns", type=int, default=150)
        p.add_argument("--max-budget", type=float, default=5.0)
        p.add_argument("--wall-clock", type=int, default=3600)
        p.add_argument("--max-rounds", type=int, default=12)
        mirror_option(p)

    p = attempt.add_parser("run")
    p.add_argument("target", type=int)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--effort", default=DEFAULT_EFFORT)
    p.add_argument("--web", action="store_true")
    p.add_argument("--label", default="")
    p.add_argument("--briefing", help="file with a scouting dossier")
    limits(p)
    p.set_defaults(run=cmd_attempt_run)
    p = attempt.add_parser("resume")
    p.add_argument("attempt", type=int)
    limits(p)
    p.set_defaults(run=cmd_attempt_resume)

    p = commands.add_parser("approve", help="show, then record your approval of one exact file")
    p.add_argument("candidate", type=int)
    p.add_argument("--mode", choices=["prove", "disprove"], default="prove")
    p.add_argument("--explanation")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--by")
    p.set_defaults(run=cmd_approve)

    p = commands.add_parser("submit", help="send an approved candidate to the platform (public)")
    p.add_argument("candidate", type=int)
    p.add_argument("--mode", choices=["prove", "disprove"], default="prove")
    p.add_argument("--explanation")
    p.set_defaults(run=cmd_submit)

    p = commands.add_parser("poll", help="fetch verdicts for pending submissions")
    p.set_defaults(run=cmd_poll)
    p = commands.add_parser("reconcile", help="settle submissions with an unknown outcome")
    p.set_defaults(run=cmd_reconcile)
    p = commands.add_parser("report", help="summarize attempts from the ledger")
    p.add_argument("--label")
    p.set_defaults(run=cmd_report)

    p = commands.add_parser("benchmark", help="run a frozen benchmark plan")
    p.add_argument("plan")
    p.add_argument("--arm", choices=["baseline", "agent"], action="append")
    p.add_argument("--only", action="append", help="limit to these slugs")
    p.add_argument("--wait", action="store_true", help="sleep through plan limits, then continue")
    p.set_defaults(run=cmd_benchmark)

    p = commands.add_parser("frontier", help="a mission's milestones and open leaves")
    p.add_argument("mission")
    p.set_defaults(run=cmd_frontier)
    p = commands.add_parser("dossier", help="collect what the platform knows about a theorem")
    p.add_argument("theorem")
    p.add_argument("--mission")
    p.add_argument("--milestone")
    p.add_argument("--output")
    p.set_defaults(run=cmd_dossier)
    p = commands.add_parser("sync", help="update reduction obligations from the platform")
    p.set_defaults(run=cmd_sync)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
