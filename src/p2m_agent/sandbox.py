"""Wrap Lean processes so that code run at compile time cannot reach the user's secrets.

Lean executes code while elaborating (`#eval`, `run_cmd`, macros), so a candidate or a mirrored
file can do anything the current user can. The home directory is denied by default and only
named paths are opened. This covers Lean and `lean-lsp-mcp`; the SDK's own sandbox covers Bash
only. One interface, two backends: `sandbox-exec` on macOS, `bwrap` on Linux.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


class SandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Policy:
    read: tuple[Path, ...] = field(default_factory=tuple)
    write: tuple[Path, ...] = field(default_factory=tuple)
    network: bool = False


def _paths(paths: Iterable[Path]) -> list[str]:
    return sorted({str(Path(p).expanduser().resolve()) for p in paths})


def _quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(policy: Policy, home: Path) -> str:
    home_path = str(home.resolve())
    readable = _paths([*policy.read, *policy.write])
    writable = _paths(policy.write)
    lines = [
        "(version 1)",
        "(allow default)",
        f"(deny file-read* (subpath {_quote(home_path)}))",
        f"(deny file-write* (subpath {_quote(home_path)}))",
    ]
    # Later rules win. Metadata must stay readable: path resolution stats every parent.
    lines.append("(allow file-read-metadata)")
    lines += [f"(allow file-read* (subpath {_quote(p)}))" for p in readable]
    lines += [f"(allow file-write* (subpath {_quote(p)}))" for p in writable]
    if not policy.network:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def bwrap_arguments(policy: Policy, home: Path) -> list[str]:
    arguments = ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    arguments += ["--tmpfs", str(home.resolve())]
    for path in _paths(policy.read):
        arguments += ["--ro-bind", path, path]
    for path in _paths(policy.write):
        arguments += ["--bind", path, path]
    if not policy.network:
        arguments.append("--unshare-net")
    return [*arguments, "--die-with-parent"]


def wrap(policy: Policy, command: list[str], home: Path | None = None) -> list[str]:
    home = home or Path.home()
    if sys.platform == "darwin":
        tool = shutil.which("sandbox-exec") or "/usr/bin/sandbox-exec"
        if not Path(tool).exists():
            raise SandboxUnavailable("sandbox-exec not found")
        return [tool, "-p", macos_profile(policy, home), *command]
    if sys.platform.startswith("linux"):
        tool = shutil.which("bwrap")
        if not tool:
            raise SandboxUnavailable("bubblewrap is not installed (apt install bubblewrap)")
        return [tool, *bwrap_arguments(policy, home), *command]
    raise SandboxUnavailable(f"no sandbox backend for {sys.platform}")
