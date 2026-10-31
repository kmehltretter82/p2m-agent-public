"""Targets from local mirrors of platform theorems, with no API call.

Mirrors let the gate, the worker and the benchmark run on real statements offline. A mirror
carries no environment information, so the caller states the toolchain and revision, and a
statement that does not elaborate there simply fails the gate's `target_builds` check.
"""

from __future__ import annotations

import re
from pathlib import Path

from .gate import Target

_DECL = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+|protected\s+)*(?:theorem|lemma)\s+([^\s({:\[]+)"
)
_NAMESPACE = re.compile(r"^\s*namespace\s+(\S+)")
_END = re.compile(r"^\s*end\s+(\S+)\s*$")


def declared_theorems(source: str) -> list[str]:
    """Fully qualified theorem names, tracking `namespace` blocks. Ignores block comments."""
    names: list[str] = []
    scopes: list[str] = []
    depth = 0
    for line in source.splitlines():
        if depth or line.lstrip().startswith("/-"):
            depth += line.count("/-") - line.count("-/")
            continue
        namespace = _NAMESPACE.match(line)
        if namespace:
            scopes.append(namespace.group(1))
            continue
        end = _END.match(line)
        if end and scopes and scopes[-1] == end.group(1):
            scopes.pop()
            continue
        declaration = _DECL.match(line)
        if declaration:
            names.append(".".join([*scopes, declaration.group(1)]))
    return names


def target_from_mirror(root: Path, slug: str, mathlib_rev: str, toolchain: str) -> Target:
    path = root / "Theorems" / f"Thm_{slug}.lean"
    source = path.read_text()
    matches = [n for n in declared_theorems(source) if n.replace(".", "_") == slug]
    if not matches:
        raise ValueError(f"{path.name}: no theorem whose name maps to slug {slug}")
    # The mirror is preamble and statement together, so all of it is kept verbatim.
    return Target(
        theorem_name=matches[-1],
        formal_statement=source,
        preamble="",
        mathlib_rev=mathlib_rev,
        toolchain=toolchain,
    )
