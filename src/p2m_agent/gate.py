"""The local gate: deterministic checks a candidate must pass before anyone submits it.

No model is involved. The gate runs Lean that the worker wrote, so it catches honest
mistakes rather than determined attacks; the server's verdict remains the authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path
from typing import Literal

from .envs import LeanEnv, lean_path

STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
#: The server's documented compile limit is about 300s; keep a margin. Calibrated in M2.
TIME_WARN_SECONDS = 120.0

_IMPORT_LINE = re.compile(r"^\s*(?:public\s+|meta\s+)*import\s+(?:all\s+)?([^\s]+)\s*$")
_SORRY_WARNING = re.compile(r"declaration uses [`']?sorry")

Mode = Literal["prove", "disprove"]
#: Wraps a Lean command line, for example in a sandbox. The identity by default.
Wrap = Callable[[list[str]], list[str]]


@dataclass(frozen=True)
class Target:
    """The immutable statement being proved: stored as given and never edited."""

    theorem_name: str
    formal_statement: str
    preamble: str
    mathlib_rev: str
    toolchain: str
    theorem_id: str | None = None

    @property
    def slug(self) -> str:
        return self.theorem_name.replace(".", "_")

    @property
    def module(self) -> str:
        return f"Theorems.Thm_{self.slug}"

    @property
    def solution_module(self) -> str:
        return f"Solutions.Sol_{self.slug}"

    def mirror_source(self) -> str:
        return f"{self.preamble.strip()}\n\n{self.formal_statement.strip()}\n"

    def sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [self.theorem_name, self.formal_statement, self.preamble,
                 self.mathlib_rev, self.toolchain]
            ).encode()
        ).hexdigest()


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    #: A warning is reported but does not fail the gate.
    warning: bool = False


@dataclass
class GateResult:
    passed: bool
    mode: str
    file_sha256: str
    checks: list[Check]
    axioms: list[str] = field(default_factory=list)
    #: Imported platform theorems. Non-empty means a reduction: these remain obligations.
    obligations: list[str] = field(default_factory=list)
    compile_seconds: float | None = None
    diagnostics: list[dict] = field(default_factory=list)
    lean_commit: str | None = None
    mathlib_rev: str | None = None

    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.warning]

    def to_json(self) -> dict:
        return asdict(self)


def parse_imports(source: str) -> list[str]:
    """Imports from the file header. Stops at the first line of real code."""
    imports: list[str] = []
    in_block_comment = 0
    for raw in source.splitlines():
        line = raw.strip()
        if in_block_comment or line.startswith("/-"):
            in_block_comment += line.count("/-") - line.count("-/")
            continue
        if not line or line.startswith("--"):
            continue
        match = _IMPORT_LINE.match(raw)
        if not match:
            break
        imports.append(match.group(1))
    return imports


def static_checks(source: str, target: Target, mode: Mode) -> list[Check]:
    """Checks that need no Lean at all, so they fail fast and cost nothing."""
    imports = parse_imports(source)
    checks = [
        Check(
            "does_not_import_target",
            target.module not in imports,
            f"imports its own target {target.module}" if target.module in imports else "",
        )
    ]
    if mode == "disprove":
        platform = [m for m in imports if m.startswith("Theorems.")]
        checks.append(
            Check(
                "disproof_imports_no_theorems",
                not platform,
                f"disproofs may not import platform theorems: {platform}" if platform else "",
            )
        )
    if "Mathlib" in imports:
        checks.append(
            Check(
                "targeted_imports",
                True,
                "whole `import Mathlib` is slow and risks the server's compile timeout",
                warning=True,
            )
        )
    return checks


class _Builder:
    """Compiles a candidate and the mirrored platform modules it imports."""

    def __init__(
        self,
        env: LeanEnv,
        workdir: Path,
        mirror_roots: Sequence[Path],
        cache_dir: Path | None,
        wrap: Wrap,
        timeout: int,
    ):
        self.env = env
        self.source_root = workdir / "src"
        self.build_root = workdir / "build"
        self.mirror_roots = list(mirror_roots)
        self.cache_dir = cache_dir
        self.wrap = wrap
        self.timeout = timeout
        self.keys: dict[str, str] = {}

    def lean_env(self) -> dict[str, str]:
        return {
            "LEAN_PATH": lean_path(self.env, self.build_root),
            "PATH": str(self.env.bin_dir),
        }

    def compile(self, module: str, source: str, commit: str) -> tuple[bool, list[dict]]:
        relative = Path(*module.split("."))
        src = self.source_root / relative.with_suffix(".lean")
        olean = self.build_root / relative.with_suffix(".olean")
        src.parent.mkdir(parents=True, exist_ok=True)
        olean.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(source)

        key = self._key(source, commit)
        self.keys[module] = key
        cached = self.cache_dir / f"{key}.olean" if self.cache_dir else None
        if cached and cached.exists():
            shutil.copy2(cached, olean)
            return True, []

        command = self.wrap([
            str(self.env.lean), f"--root={self.source_root}", "--json",
            "-D", "autoImplicit=false", "-o", str(olean), str(src),
        ])
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=self.timeout, env=self.lean_env()
        )
        messages = _parse_messages(result.stdout)
        ok = result.returncode == 0 and olean.exists()
        if not ok and not messages:
            messages = [{"severity": "error", "data": (result.stdout + result.stderr)[-1500:]}]
        if ok and cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(olean, cached)
        return ok, messages

    def _key(self, source: str, commit: str) -> str:
        parents = sorted(self.keys.get(m, m) for m in parse_imports(source))
        digest = hashlib.sha256()
        digest.update("\0".join([commit, self.env.mathlib_rev, source, *parents]).encode())
        return digest.hexdigest()

    def find_mirror(self, module: str) -> str | None:
        relative = Path(*module.split(".")).with_suffix(".lean")
        for root in self.mirror_roots:
            path = root / relative
            if path.is_file():
                return path.read_text()
        return None

    def compile_mirrors(self, source: str, commit: str, seen: set[str]) -> str | None:
        """Compile platform modules imported by `source`. Returns an error or None."""
        for module in parse_imports(source):
            if not module.startswith(("Theorems.", "Definitions.")) or module in seen:
                continue
            seen.add(module)
            mirrored = self.find_mirror(module)
            if mirrored is None:
                return f"unknown import {module}: no local mirror of this platform module"
            problem = self.compile_mirrors(mirrored, commit, seen)
            if problem:
                return problem
            ok, messages = self.compile(module, mirrored, commit)
            if not ok:
                return f"mirror {module} does not compile: {_first_error(messages)}"
        return None


def _parse_messages(stdout: str) -> list[dict]:
    messages = []
    for line in stdout.splitlines():
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def _first_error(messages: list[dict]) -> str:
    for message in messages:
        if message.get("severity") == "error":
            return str(message.get("data", ""))[:500]
    return "unknown error"


def _diagnostics(messages: list[dict]) -> list[dict]:
    """Keep only what a person or model needs, not Lean's whole output."""
    kept = []
    for message in messages:
        if message.get("severity") == "information":
            continue
        kept.append(
            {
                "severity": message.get("severity"),
                "line": (message.get("pos") or {}).get("line"),
                "column": (message.get("pos") or {}).get("column"),
                "message": str(message.get("data", ""))[:2000],
            }
        )
    return kept


def _gate_script() -> str:
    return str(resources.files("p2m_agent") / "lean" / "GateCheck.lean")


def _judge(report: dict, target: Target, mode: Mode, imports: list[str]) -> tuple[list[Check], list[str]]:
    alone = report.get("alone", {})
    compared = report.get("compared", {})
    checks: list[Check] = []
    obligations = [m for m in imports if m.startswith("Theorems.")]

    if "error" in alone:
        checks.append(Check("inspect", False, str(alone["error"])))
        return checks, []

    found = bool(alone.get("found"))
    lookalikes = alone.get("lookalikes", [])
    hint = f" (found {lookalikes}: it must not be inside a namespace)" if lookalikes else ""
    checks.append(Check("solution_exists", found, "" if found else f"no top-level `solution`{hint}"))
    if not found:
        return checks, []

    kind = alone.get("kind")
    checks.append(Check("solution_is_theorem", kind == "theorem", f"`solution` is a {kind}"))
    declared = alone.get("declared_in", "")
    in_module = target.solution_module in declared
    checks.append(Check("solution_is_local", in_module, f"declared in {declared}"))
    checks.append(
        Check(
            "target_not_in_dependencies",
            not alone.get("imports_target"),
            "the target reaches the candidate through an import" if alone.get("imports_target") else "",
        )
    )
    own_sorry = alone.get("own_sorry", [])
    checks.append(Check("no_own_sorry", not own_sorry, f"uses sorry: {own_sorry}" if own_sorry else ""))
    own_axioms = alone.get("own_axioms", [])
    checks.append(Check("no_new_axioms", not own_axioms, f"declares: {own_axioms}" if own_axioms else ""))

    # Mirrored platform theorems are `by sorry` by design, so sorryAx is expected only when
    # the candidate imports one. `no_own_sorry` above covers the candidate's own code.
    allowed = set(STANDARD_AXIOMS)
    if obligations:
        allowed.add("sorryAx")
    extra = sorted(set(alone.get("axioms", [])) - allowed)
    checks.append(Check("axioms_allowed", not extra, f"disallowed axioms: {extra}" if extra else ""))

    match = compared.get("match")
    if "error" in compared:
        checks.append(Check("statement_matches", False, str(compared["error"])))
    elif match == "structural":
        checks.append(Check("statement_matches", True))
    elif match == "defeq":
        checks.append(
            Check(
                "statement_matches",
                True,
                "definitionally equal but not structurally identical: the server may answer WA",
                warning=True,
            )
        )
    else:
        expected = "the negation of the whole target" if mode == "disprove" else "the target"
        detail = compared.get("reason") or (
            f"does not state {expected}.\n"
            f"expected: {compared.get('expected')}\nactual:   {compared.get('actual')}"
        )
        checks.append(Check("statement_matches", False, detail))
    if match in ("structural", "defeq") and not compared.get("binder_info_same", True):
        checks.append(
            Check(
                "binders_identical",
                True,
                "implicit or explicit binders differ from the target; the platform asks for the same binders",
                warning=True,
            )
        )
    return checks, obligations


def check(
    candidate: str,
    target: Target,
    env: LeanEnv,
    mode: Mode = "prove",
    mirror_roots: Sequence[Path] = (),
    cache_dir: Path | None = None,
    wrap: Wrap = lambda command: command,
    timeout: int = 900,
    workdir: Path | None = None,
) -> GateResult:
    """Run every check on `candidate`, the exact text that would be submitted."""
    digest = hashlib.sha256(candidate.encode()).hexdigest()
    commit = env.lean_commit()
    result = GateResult(
        passed=False, mode=mode, file_sha256=digest, checks=static_checks(candidate, target, mode),
        lean_commit=commit, mathlib_rev=env.mathlib_rev,
    )
    if result.failures():
        return result

    holder = None
    if workdir is None:
        holder = tempfile.TemporaryDirectory(prefix="p2m-gate-")
        workdir = Path(holder.name)
    try:
        builder = _Builder(env, workdir, mirror_roots, cache_dir, wrap, timeout)

        problem = builder.compile_mirrors(target.mirror_source(), commit, set())
        if not problem:
            ok, messages = builder.compile(target.module, target.mirror_source(), commit)
            if not ok:
                problem = f"the target itself does not compile: {_first_error(messages)}"
        if problem:
            result.checks.append(Check("target_builds", False, problem))
            return result

        problem = builder.compile_mirrors(candidate, commit, {target.module})
        if problem:
            result.checks.append(Check("imports_resolve", False, problem))
            return result

        started = time.monotonic()
        try:
            ok, messages = builder.compile(target.solution_module, candidate, commit)
        except subprocess.TimeoutExpired:
            result.checks.append(Check("compiles", False, f"no result within {timeout}s"))
            return result
        result.compile_seconds = round(time.monotonic() - started, 2)
        result.diagnostics = _diagnostics(messages)
        result.checks.append(Check("compiles", ok, "" if ok else _first_error(messages)))
        if not ok:
            return result

        warned = [m for m in messages if _SORRY_WARNING.search(str(m.get("data", "")))]
        result.checks.append(
            Check(
                "no_sorry_warning",
                not warned,
                f"{len(warned)} declaration(s) use sorry" if warned else "",
            )
        )
        if result.compile_seconds > TIME_WARN_SECONDS:
            result.checks.append(
                Check(
                    "compile_time",
                    True,
                    f"{result.compile_seconds}s locally; the server limit is about 300s",
                    warning=True,
                )
            )

        script = _gate_script()
        command = wrap([
            str(env.lean), "--run", script, mode, target.theorem_name,
            target.module, target.solution_module,
        ])
        run = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, env=builder.lean_env()
        )
        try:
            report = json.loads(run.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            detail = (run.stdout + run.stderr)[-1500:]
            result.checks.append(Check("inspect", False, f"checker gave no result: {detail}"))
            return result

        judged, obligations = _judge(report, target, mode, parse_imports(candidate))
        result.checks.extend(judged)
        result.axioms = report.get("alone", {}).get("axioms", [])
        result.obligations = obligations
        result.passed = not result.failures()
        return result
    finally:
        if holder:
            holder.cleanup()
