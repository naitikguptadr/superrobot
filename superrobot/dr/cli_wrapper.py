"""All subprocess calls to the `dr` binary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class DrCommandResult:
    """Captured result from a dr CLI invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class DrCliWrapper:
    """Async wrapper around dr CLI subprocess calls."""

    def __init__(self, dr_binary: str = "dr") -> None:
        self._dr = dr_binary

    async def _run(self, *args: str, cwd: str | None = None) -> DrCommandResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._dr,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except FileNotFoundError:
            return DrCommandResult(
                returncode=127, stdout="", stderr=f"{self._dr}: command not found — is dr on PATH?"
            )
        stdout_b, stderr_b = await proc.communicate()
        return DrCommandResult(
            returncode=proc.returncode or 0,
            stdout=stdout_b.decode(),
            stderr=stderr_b.decode(),
        )

    async def auth_check(self) -> bool:
        result = await self._run("auth", "check")
        return result.ok

    async def templates_list(self) -> DrCommandResult:
        return await self._run("templates", "list")

    async def templates_clone(self, template: str, dest: str) -> DrCommandResult:
        return await self._run("templates", "clone", template, dest)

    async def component_add_agent(self, cwd: str | None = None) -> DrCommandResult:
        return await self._run("component", "add", "agent", cwd=cwd)

    async def run_dev(
        self,
        input_json: str,
        cwd: str | None = None,
        timeout: float = 30.0,
    ) -> DrCommandResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._dr,
                "run",
                "dev",
                "--input",
                input_json,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except FileNotFoundError:
            return DrCommandResult(
                returncode=127, stdout="", stderr=f"{self._dr}: command not found — is dr on PATH?"
            )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return DrCommandResult(
                returncode=proc.returncode or 0,
                stdout=stdout_b.decode(),
                stderr=stderr_b.decode(),
            )
        except TimeoutError:
            return DrCommandResult(returncode=124, stdout="", stderr="timeout")

    async def task_run_deploy(self, cwd: str | None = None) -> DrCommandResult:
        return await self._run("task", "run", "deploy", cwd=cwd)
