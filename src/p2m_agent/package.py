"""Inline a multi-module proof development into the single file the platform accepts.

Generalized from `prove2me_mfotl/scripts/package_solution.py`, which hardcoded one project's
module prefix. Modules under `local_prefixes` are inlined in dependency order; every other
import is kept, once.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_IMPORT = re.compile(r"\s*import\s+([^\s]+)\s*")


class PackagingError(ValueError):
    pass


def package(root: Path, source: Path, local_prefixes: Iterable[str]) -> str:
    prefixes = tuple(p.rstrip(".") + "." for p in local_prefixes)
    imports: list[str] = []
    bodies: list[str] = []
    visited: set[Path] = set()
    active: list[Path] = []

    def visit(path: Path) -> None:
        path = path.resolve()
        if path in active:
            chain = " -> ".join(p.name for p in [*active, path])
            raise PackagingError(f"circular local import: {chain}")
        if path in visited:
            return
        if not path.is_file():
            raise PackagingError(f"local module file not found: {path}")
        active.append(path)
        body: list[str] = []
        for line in path.read_text().splitlines():
            match = _IMPORT.fullmatch(line)
            if not match:
                body.append(line)
                continue
            module = match.group(1)
            if module.startswith(prefixes):
                visit(root / (module.replace(".", "/") + ".lean"))
            elif module not in imports:
                imports.append(module)
        active.pop()
        visited.add(path)
        bodies.append("\n".join(body).strip())

    visit(source)
    header = "\n".join(f"import {module}" for module in imports)
    return header + "\n\n" + "\n\n".join(b for b in bodies if b) + "\n"
