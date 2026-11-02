"""Do the worker's Lean tools really answer under the sandbox? Calls them as the worker would."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from p2m_agent.envs import env_for
from p2m_agent.worker import lean_mcp_server

env = env_for("leanprover/lean4:v4.33.1", "0df444a360eaa60ab8c11dca51a86af692955474")
project = Path(sys.argv[1])
source = sorted((project / "Solutions").glob("*.lean"))[0]
server = lean_mcp_server(project, env)


def text(result) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)[:300].replace("\n", " ")


async def main() -> None:
    params = StdioServerParameters(command=server["command"], args=server["args"], env=server["env"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            relative = str(source.relative_to(project))
            print("diagnostics:", text(await session.call_tool("lean_diagnostic_messages", {"file_path": relative})))
            print("goal:", text(await session.call_tool("lean_goal", {"file_path": relative, "line": 3})))
            print("search:", text(await session.call_tool("lean_local_search", {"query": "Nat.gcd_eq_left"})))


asyncio.run(main())
