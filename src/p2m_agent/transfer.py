"""Move the agent's state between machines: the ledger and its attempts.

Environments can be rebuilt with `p2m env`, and secrets must be set up on each machine by a
person, so neither travels. Attempts record absolute paths, so import rewrites them.
"""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

from .config import Paths


def export_state(paths: Paths, archive: Path) -> dict:
    # A checkpoint folds the write-ahead log in, so the single file is complete.
    connection = sqlite3.connect(paths.ledger)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(paths.ledger, arcname="ledger.sqlite")
        if paths.attempts.is_dir():
            tar.add(
                paths.attempts, arcname="attempts",
                # Project trees link into Mathlib and hold only reproducible build output.
                filter=lambda info: None if "/.lake" in info.name else info,
            )
    return {"archive": str(archive), "bytes": archive.stat().st_size}


def import_state(paths: Paths, archive: Path) -> dict:
    if paths.ledger.exists():
        raise FileExistsError(f"{paths.ledger} already exists; refusing to overwrite it")
    paths.home.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(paths.home, filter="data")
    connection = sqlite3.connect(paths.ledger)
    with connection:
        rows = connection.execute("SELECT id FROM attempts").fetchall()
        for (attempt_id,) in rows:
            connection.execute(
                "UPDATE attempts SET workdir = ? WHERE id = ?",
                (str(paths.attempts / str(attempt_id)), attempt_id),
            )
    connection.close()
    return {"attempts": len(rows), "home": str(paths.home)}
