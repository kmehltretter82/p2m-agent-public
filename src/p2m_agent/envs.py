"""Pinned Lean environments: one directory per (toolchain, Mathlib revision).

Each environment holds its own `.lake/packages`. Lean refuses to load build files stamped
by a different Lean commit (`incompatible header`), so `verify` checks every stamp rather
than trusting that a build which once worked still matches.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import paths

LAKEFILE = """\
import Lake
open Lake DSL

package «prove2me» where
  leanOptions := #[⟨`autoImplicit, false⟩]

require mathlib from git
  "https://github.com/leanprover-community/mathlib4.git" @
  "{rev}"

lean_lib «Definitions» where
lean_lib «Theorems» where
@[default_target]
lean_lib «Solutions» where
"""

SMOKE = "import Mathlib.Tactic.Linarith\nexample (a b : Int) (h : a < b) : a ≤ b := by linarith\n"

#: Above this, local building means the cache is wrong, and would take hours to finish.
MAX_LOCAL_REBUILD = 50

_COMMIT = re.compile(rb"[0-9a-f]{40}")
_VERSION_COMMIT = re.compile(r"commit ([0-9a-f]{40})")


class EnvError(RuntimeError):
    pass


def munge_toolchain(toolchain: str) -> str:
    """`leanprover/lean4:v4.33.1` -> the directory name elan installs it under."""
    return toolchain.replace("/", "--").replace(":", "---")


@dataclass(frozen=True)
class LeanEnv:
    root: Path
    toolchain: str
    mathlib_rev: str

    @property
    def bin_dir(self) -> Path:
        return paths().toolchains / munge_toolchain(self.toolchain) / "bin"

    @property
    def lean(self) -> Path:
        return self.bin_dir / "lean"

    @property
    def lake(self) -> Path:
        return self.bin_dir / "lake"

    def package_libs(self) -> list[Path]:
        return sorted(self.root.glob(".lake/packages/*/.lake/build/lib/lean"))

    def lean_commit(self) -> str:
        out = subprocess.run(
            [str(self.lean), "--version"], capture_output=True, text=True, check=True
        ).stdout
        match = _VERSION_COMMIT.search(out)
        if not match:
            raise EnvError(f"could not read the Lean commit from: {out!r}")
        return match.group(1)


def env_for(toolchain: str, mathlib_rev: str, root: Path | None = None) -> LeanEnv:
    return LeanEnv(root or paths().envs / mathlib_rev[:12], toolchain, mathlib_rev)


def olean_commit(path: Path) -> str | None:
    with path.open("rb") as handle:
        match = _COMMIT.search(handle.read(256))
    return match.group(0).decode() if match else None


def stale_oleans(env: LeanEnv) -> list[Path]:
    """Build files whose stamped Lean commit differs from this environment's toolchain."""
    expected = env.lean_commit()
    stale: list[Path] = []
    for lib in env.package_libs():
        stale.extend(p for p in lib.rglob("*.olean") if olean_commit(p) != expected)
    return stale


def missing_modules(env: LeanEnv) -> list[str]:
    """Mathlib source files, relative to its root, that have no build file."""
    mathlib = env.root / ".lake" / "packages" / "mathlib"
    build = mathlib / ".lake" / "build" / "lib" / "lean"
    return sorted(
        str(source.relative_to(mathlib))
        for source in (mathlib / "Mathlib").rglob("*.lean")
        if not (build / source.relative_to(mathlib).with_suffix(".olean")).exists()
    )


def _build_few(env: LeanEnv, missing: list[str], timeout: int) -> int:
    """Build a handful of modules locally, refusing anything close to a Mathlib rebuild."""
    modules = [m.removesuffix(".lean").replace("/", ".") for m in missing]
    dry_run = subprocess.run(
        [str(env.lake), "build", "--no-build", *modules],
        cwd=env.root, capture_output=True, text=True, timeout=timeout,
    )
    needed = re.findall(r"Building (\S+)", dry_run.stdout + dry_run.stderr)
    if len(needed) > MAX_LOCAL_REBUILD:
        raise EnvError(
            f"refusing to build {len(needed)} modules locally (limit {MAX_LOCAL_REBUILD}); "
            "the Mathlib cache is probably missing or mismatched"
        )
    _run([str(env.lake), "build", *modules], env.root, timeout)
    return len(needed)


def _run(cmd: list[str], cwd: Path, timeout: int) -> None:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        tail = (result.stdout + result.stderr)[-1500:]
        raise EnvError(f"{' '.join(cmd)} failed in {cwd}:\n{tail}")


def _clone_tree(source: Path, destination: Path) -> None:
    """Copy a tree, sharing disk blocks where the filesystem can (APFS, btrfs, xfs)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = ["-cR"] if sys.platform == "darwin" else ["-R", "--reflink=auto"]
    subprocess.run(["cp", *flags, str(source), str(destination)], check=True)


def ensure_env(env: LeanEnv, seed: Path | None = None, timeout: int = 3600) -> dict:
    """Create or repair an environment and prove it works. Returns a report.

    `seed` is another project to clone `.lake/packages` from. It is only ever read.
    """
    root = env.root
    root.mkdir(parents=True, exist_ok=True)
    for name in ("Definitions", "Theorems", "Solutions"):
        (root / name).mkdir(exist_ok=True)
    (root / "lean-toolchain").write_text(env.toolchain + "\n")
    (root / "lakefile.lean").write_text(LAKEFILE.format(rev=env.mathlib_rev))
    if not env.lean.exists():
        _run(["elan", "toolchain", "install", env.toolchain], root, timeout)

    packages = root / ".lake" / "packages"
    if not packages.exists():
        if seed is not None:
            _clone_tree(seed / ".lake" / "packages", packages)
            shutil.copy2(seed / "lake-manifest.json", root / "lake-manifest.json")
        else:
            _run([str(env.lake), "update"], root, timeout)

    head = subprocess.run(
        ["git", "-C", str(packages / "mathlib"), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if head != env.mathlib_rev:
        raise EnvError(f"mathlib is at {head}, expected {env.mathlib_rev}")

    stale = stale_oleans(env)
    missing = missing_modules(env)
    repaired = {"stale": len(stale), "missing": len(missing)}
    if stale:
        _run([str(env.lake), "exe", "cache", "get"], root, timeout)
        stale = stale_oleans(env)
        missing = missing_modules(env)
    if missing:
        # `cache get` can report "already decompressed" while files are absent, so force it.
        from_root = [f".lake/packages/mathlib/{module}" for module in missing]
        _run([str(env.lake), "exe", "cache", "get!", *from_root], root, timeout)
        missing = missing_modules(env)
        if missing:
            # The upstream cache can lack a few modules at a given revision.
            repaired["built_locally"] = _build_few(env, missing, timeout)
            missing = missing_modules(env)
        stale = stale_oleans(env)
    if stale:
        sample = ", ".join(str(p.relative_to(packages)) for p in stale[:5])
        raise EnvError(f"{len(stale)} build files still have the wrong Lean stamp: {sample}")
    if missing:
        raise EnvError(f"{len(missing)} Mathlib modules have no build file: {missing[:5]}")

    return {
        "root": str(root),
        "toolchain": env.toolchain,
        "mathlib_rev": env.mathlib_rev,
        "repaired": repaired,
        "smoke": smoke_test(env),
    }


def lean_path(env: LeanEnv, *extra: Path) -> str:
    return ":".join(str(p) for p in [*extra, *env.package_libs()])


def smoke_test(env: LeanEnv, timeout: int = 900) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        file = Path(tmp) / "Smoke.lean"
        file.write_text(SMOKE)
        result = subprocess.run(
            [str(env.lean), str(file)],
            capture_output=True, text=True, timeout=timeout,
            env={"LEAN_PATH": lean_path(env), "PATH": str(env.bin_dir)},
        )
    if result.returncode != 0:
        raise EnvError(f"smoke test failed:\n{(result.stdout + result.stderr)[-1500:]}")
    return {"ok": True}


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Create or repair a pinned Lean environment")
    parser.add_argument("toolchain")
    parser.add_argument("mathlib_rev")
    parser.add_argument("--seed", type=Path)
    args = parser.parse_args()
    print(json.dumps(ensure_env(env_for(args.toolchain, args.mathlib_rev), args.seed), indent=2))
