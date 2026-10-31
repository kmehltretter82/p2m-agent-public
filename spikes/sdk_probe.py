"""M0 spike: how does the Agent SDK behave on a plan login?

Answers three questions from PLAN.md with tiny prompts:
  - what do `total_cost_usd` and `RateLimitEvent` report?
  - which tools does a stripped session really have?
  - does a session resume after its process dies abruptly?

Usage: sdk_probe.py first   -> prints a session id, then dies without cleanup
       sdk_probe.py resume <session id>
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
)

MODEL = "claude-opus-5-5"


def options(resume: str | None = None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=MODEL,
        system_prompt="You are a terse assistant.",
        tools=["Read"],
        setting_sources=[],
        max_turns=2,
        max_budget_usd=0.50,
        resume=resume,
    )


def report(message: object) -> None:
    if isinstance(message, SystemMessage) and message.subtype == "init":
        data = message.data
        print("init tools:", data.get("tools"))
        print("init mcp_servers:", data.get("mcp_servers"))
        print("init apiKeySource:", data.get("apiKeySource"), "| model:", data.get("model"))
    elif isinstance(message, RateLimitEvent):
        info = message.rate_limit_info
        print(
            "rate_limit:",
            json.dumps(
                {
                    "status": info.status,
                    "type": info.rate_limit_type,
                    "utilization": info.utilization,
                    "resets_at": info.resets_at,
                    "overage": info.overage_status,
                    "raw_keys": sorted(info.raw),
                }
            ),
        )
    elif isinstance(message, AssistantMessage):
        for block in message.content:
            text = getattr(block, "text", None)
            if text:
                print("assistant:", text)
    elif isinstance(message, ResultMessage):
        print(
            "result:",
            json.dumps(
                {
                    "subtype": message.subtype,
                    "session_id": message.session_id,
                    "turns": message.num_turns,
                    "cost_usd": message.total_cost_usd,
                    "usage": message.usage,
                    "terminal_reason": message.terminal_reason,
                },
                default=str,
            ),
        )


async def run(prompt: str, resume: str | None, die_after: bool) -> None:
    async with ClaudeSDKClient(options=options(resume)) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            report(message)
            if die_after and isinstance(message, ResultMessage):
                print("dying now, without cleanup")
                sys.stdout.flush()
                os._exit(9)


def main() -> None:
    if sys.argv[1] == "first":
        asyncio.run(run("Remember the word PERIDOT. Reply with only: OK", None, die_after=True))
    else:
        asyncio.run(run("Which word did I ask you to remember?", sys.argv[2], die_after=False))


if __name__ == "__main__":
    main()
