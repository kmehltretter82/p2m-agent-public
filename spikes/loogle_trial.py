"""M7: is local Loogle practical on the Pi? Times the build, index and queries. Uses no model."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

from lean_lsp_mcp.loogle import LoogleManager

from p2m_agent.envs import env_for
from p2m_agent.worker import _tree_memory_kb

env = env_for("leanprover/lean4:v4.33.1", "0df444a360eaa60ab8c11dca51a86af692955474")
os.environ["PATH"] = f"{env.bin_dir}{os.pathsep}{os.environ['PATH']}"
peak = {"tree_kb": 0, "min_available_kb": 10**12}
done = threading.Event()


def sample() -> None:
    while not done.wait(2.0):
        peak["tree_kb"] = max(peak["tree_kb"], _tree_memory_kb(os.getpid())[0])
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                peak["min_available_kb"] = min(peak["min_available_kb"], int(line.split()[1]))


threading.Thread(target=sample, daemon=True).start()
result: dict = {}


async def main() -> None:
    manager = LoogleManager(project_path=env.root)
    started = time.monotonic()
    result["installed"] = manager.ensure_installed()
    result["build_seconds"] = round(time.monotonic() - started)
    result["build_peak_gb"] = round(peak["tree_kb"] / 1e6, 2)
    print(json.dumps(result), flush=True)
    if not result["installed"]:
        return
    started = time.monotonic()
    result["started"] = await manager.start()
    result["first_start_seconds"] = round(time.monotonic() - started)
    print(json.dumps(result), flush=True)
    if not result["started"]:
        return
    result["queries"] = []
    for query in ("Nat.gcd, Nat.gcd _ _ = _", "StrictConcaveOn", "Real.sqrt (_ * _)", "IsCompact, Continuous, sSup"):
        started = time.monotonic()
        hits = await manager.query(query, num_results=5)
        result["queries"].append({
            "query": query, "seconds": round(time.monotonic() - started, 2), "hits": len(hits),
            "first": (hits[0].get("name") if hits else None),
        })
    await manager.stop()
    started = time.monotonic()
    result["second_start_ok"] = await manager.start()
    result["warm_start_seconds"] = round(time.monotonic() - started)
    await manager.stop()


try:
    asyncio.run(main())
finally:
    done.set()
    result["peak_tree_gb"] = round(peak["tree_kb"] / 1e6, 2)
    result["min_available_gb"] = round(peak["min_available_kb"] / 1e6, 2)
    Path.home().joinpath("loogle-result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result), flush=True)
