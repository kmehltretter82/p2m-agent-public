"""Where the agent keeps its state.

Everything is resolved from one configurable home directory, so moving to another
machine is a matter of moving that directory (see PLAN.md, portability rules).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Attempts must not run under this tree: a parent CLAUDE.md there carries unrelated
#: agent instructions (see PLAN.md, "What checking IDEA.md changed").
DEFAULT_HOME = "~/.local/share/p2m-agent"


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def envs(self) -> Path:
        return self.home / "envs"

    @property
    def attempts(self) -> Path:
        return self.home / "attempts"

    @property
    def ledger(self) -> Path:
        return self.home / "ledger.sqlite"

    @property
    def credentials(self) -> Path:
        return self.home / "secrets" / "credentials.json"

    @property
    def toolchains(self) -> Path:
        return Path(os.environ.get("ELAN_HOME", "~/.elan")).expanduser() / "toolchains"


def paths() -> Paths:
    return Paths(Path(os.environ.get("P2M_HOME", DEFAULT_HOME)).expanduser())
