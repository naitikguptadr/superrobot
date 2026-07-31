"""Thin wrappers around DataRobot's own scaffold scripts.

The architecture's second core decision: we own the frontend (understanding
the source agent), DataRobot owns the backend (what a deployable agent looks
like). So the scaffold is produced by *their* scripts, called here, never
reimplemented.

That is not deference for its own sake. The first real deploy failed on a
missing `.datarobot/` directory -- a directory `select_framework.py` writes
as a matter of course. Hand-rolling that contract meant permanently chasing
a target DataRobot actively evolves; calling their scripts makes upstream
drift a submodule bump instead of a rewrite.

The three scripts, signatures confirmed by reading them rather than by
trusting a summary:

* `clone_template.py --target-dir DIR`
  Clones `datarobot-community/datarobot-agent-application`. The ref is
  pinned *inside the script* (currently tag 11.10.7) -- there is no flag to
  override it, and we deliberately do not add one.
* `select_framework.py --framework {langgraph,crewai,llamaindex,nat,base}
  --target-dir DIR`
  Writes `.datarobot/answers/agent-agent.yml`.
* `setup_template.py --llm-model MODEL --target-dir DIR`
  Generates `.env` via `dr dotenv setup` and initializes the Pulumi stack.

Everything runs through an injectable runner so tests never touch the
network, `git`, `dr`, or Pulumi.

These are synchronous. The three steps are strictly sequential -- you cannot
select a framework into a directory that has not been cloned -- so there is
no concurrency to win, and a plain blocking call keeps the failure mode
(non-zero exit, captured stderr) obvious.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Mirrors FRAMEWORKS in the vendored select_framework.py. A test asserts the
# two stay identical, so upstream drift fails in CI rather than at deploy.
FRAMEWORKS: tuple[str, ...] = ("langgraph", "crewai", "llamaindex", "nat", "base")

_VENDORED_SCRIPTS = (
    Path("vendor") / "datarobot-agent-skills" / "skills" / "datarobot-agent-assist" / "scripts"
)


class ScaffoldError(Exception):
    """A scaffold step could not run, or ran and failed.

    Always raised rather than returned: a caller that ignored a failed clone
    would go on to build against a directory that is not a DataRobot recipe.
    """


@dataclass(frozen=True)
class ScriptResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ScriptRunner(Protocol):
    def __call__(self, command: Sequence[str], *, cwd: Path) -> ScriptResult: ...


def _subprocess_runner(command: Sequence[str], *, cwd: Path) -> ScriptResult:
    completed = subprocess.run(  # noqa: S603 - argv is built here, never shell-interpolated
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return ScriptResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def skill_scripts_dir() -> Path:
    """Locate the vendored `datarobot-agent-assist/scripts` directory.

    Vendored as a git submodule, so a fresh clone without `--recursive` has
    the directory but not its contents. Say that plainly instead of failing
    later with a confusing "no such file".
    """
    root = Path(__file__).resolve().parents[2]
    scripts = root / _VENDORED_SCRIPTS
    if not scripts.is_dir():
        raise ScaffoldError(
            f"DataRobot scaffold scripts not found at {scripts}. The "
            "datarobot-agent-skills submodule is not checked out: run "
            "`git submodule update --init --recursive`."
        )
    return scripts


def _run(
    script: str,
    args: Sequence[str],
    target_dir: Path,
    *,
    runner: ScriptRunner | None,
    scripts_dir: Path | None,
) -> ScriptResult:
    resolved_target = Path(target_dir).resolve()
    if not resolved_target.is_dir():
        raise ScaffoldError(f"target directory does not exist: {resolved_target}")

    base = scripts_dir if scripts_dir is not None else skill_scripts_dir()
    script_path = base / script
    if not script_path.is_file():
        raise ScaffoldError(f"scaffold script not found: {script_path}")

    run = runner if runner is not None else _subprocess_runner
    command = [sys.executable, str(script_path), *args, "--target-dir", str(resolved_target)]
    result = run(command, cwd=resolved_target)

    if not result.ok:
        detail = (result.stderr or result.stdout).strip() or "no output"
        raise ScaffoldError(f"{script} failed (exit {result.returncode}): {detail}")
    return result


def clone_template(
    target_dir: Path,
    *,
    runner: ScriptRunner | None = None,
    scripts_dir: Path | None = None,
) -> ScriptResult:
    """Clone the pinned `datarobot-agent-application` recipe into `target_dir`."""
    return _run("clone_template.py", (), target_dir, runner=runner, scripts_dir=scripts_dir)


def select_framework(
    target_dir: Path,
    framework: str,
    *,
    runner: ScriptRunner | None = None,
    scripts_dir: Path | None = None,
) -> ScriptResult:
    """Record the agentic framework in `.datarobot/answers/agent-agent.yml`.

    The framework comes from the IR's orchestration topology, not from an
    import-name guess -- and an unsupported value is rejected here, before
    the script runs, so the caller gets a message naming what IS supported
    rather than argparse's.
    """
    if framework not in FRAMEWORKS:
        raise ScaffoldError(
            f"unsupported framework {framework!r}; DataRobot's recipe supports "
            f"{', '.join(FRAMEWORKS)}. Migrating a {framework} agent means "
            "recompiling it onto one of these, which is a decision for the IR, "
            "not a value to pass through."
        )
    return _run(
        "select_framework.py",
        ("--framework", framework),
        target_dir,
        runner=runner,
        scripts_dir=scripts_dir,
    )


def setup_template(
    target_dir: Path,
    llm_model: str,
    *,
    runner: ScriptRunner | None = None,
    scripts_dir: Path | None = None,
) -> ScriptResult:
    """Generate `.env` and initialize the Pulumi stack.

    `llm_model` must be a DataRobot LLM Gateway id (`provider/model`), which
    is not the same as the model name the dataflow probe read out of the
    source repo. Reconciling the two is the caller's job, via
    `list_llm_models.py`.
    """
    return _run(
        "setup_template.py",
        ("--llm-model", llm_model),
        target_dir,
        runner=runner,
        scripts_dir=scripts_dir,
    )
