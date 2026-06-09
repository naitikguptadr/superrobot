"""Live mode — attach to locally running agent."""

from __future__ import annotations

import json
from dataclasses import dataclass

from superrobot.dr.cli_wrapper import DrCliWrapper


@dataclass
class LiveRunResult:
    """Result of a live agent test run."""

    success: bool
    output: str
    stderr: str
    active_nodes: list[str]


async def run_live_query(
    query: str,
    cwd: str | None = None,
    cli: DrCliWrapper | None = None,
) -> LiveRunResult:
    """Send a test query via dr run dev and return execution path hints."""
    wrapper = cli or DrCliWrapper()
    payload = json.dumps({"query": query})
    result = await wrapper.run_dev(payload, cwd=cwd)
    active_nodes = ["input", "llm_call", "output"] if result.ok else ["input"]
    return LiveRunResult(
        success=result.ok,
        output=result.stdout,
        stderr=result.stderr,
        active_nodes=active_nodes,
    )
