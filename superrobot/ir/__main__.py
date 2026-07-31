"""JSON transport for the Pi harness.

This is not a user-facing CLI and must not grow into one. The harness is
the only interface; this module exists so TypeScript can call Python
without either side parsing prose.

Two rules the harness depends on:

* **stdout is always a single JSON object**, including on failure. A
  previous bug (audit C21) printed human text on error paths, so the shell
  reported a JSON parse failure instead of the actual problem.
* **A refusal is not an error.** When the projection declines because the
  coverage ledger is not clean, the payload carries `"refusal": true` and
  the blocking facts. The harness agent must surface those to a human
  rather than retrying around them -- retrying around a deliberate block
  defeats the entire architecture.

Diagnostics go to stderr, where they cannot corrupt the payload.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from superrobot.dr import scaffold
from superrobot.ir.agent_spec import ProjectionError, migration_ir_to_agent_spec
from superrobot.ir.decisions import (
    DEFAULT_DECISIONS_FILENAME,
    DecisionError,
    load_decisions,
    render_decisions_template,
)
from superrobot.ir.extract import Extraction, extract_migration_ir


def _emit(payload: dict[str, Any]) -> int:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


def _fail(exc: Exception, *, refusal: bool = False, blocking: list[str] | None = None) -> int:
    payload: dict[str, Any] = {
        "error": str(exc),
        "kind": type(exc).__name__,
        "refusal": refusal,
    }
    if blocking:
        payload["blocking"] = blocking
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1


def _blocking_lines(extraction: Extraction) -> list[str]:
    lines = [
        f"{entry.fact.id} ({entry.fact.kind}) at {entry.fact.file}:{entry.fact.line}"
        f" -- {entry.reason}"
        for entry in (extraction.ir.coverage.entries if extraction.ir.coverage else [])
        if entry.disposition.value == "blocking"
    ]
    lines += [
        f"residue: {r.description} -- {r.reason}"
        for r in extraction.ir.residue
        if r.severity.value == "blocking"
    ]
    return lines


def _repo_path(raw: str) -> Path:
    """The source repo, or an error naming the path.

    Probing a directory that does not exist finds nothing, and finding
    nothing is a state the extractor already has a meaning for: "the probes
    missed something". A typo'd path would therefore be reported as a
    suspicious-looking agent rather than as a typo. Same reasoning as
    `load_decisions` refusing a missing decisions file.
    """
    repo = Path(raw)
    if not repo.is_dir():
        raise FileNotFoundError(f"source repo not found: {repo}")
    return repo


def _extract(args: argparse.Namespace) -> Extraction:
    repo = _repo_path(args.repo)
    decisions = load_decisions(Path(args.decisions) if args.decisions else None)
    return extract_migration_ir(repo, decisions=decisions)


def _cmd_extract(args: argparse.Namespace) -> int:
    extraction = _extract(args)
    return _emit(
        {
            "ir": extraction.ir.model_dump(mode="json"),
            "coverage": {
                "clean": extraction.is_clean(),
                "blocking": _blocking_lines(extraction),
                "unaccounted": [f.id for f in extraction.ledger.unaccounted()],
                "report": extraction.ledger.report(),
            },
            "targetFramework": extraction.ir.target_framework,
        }
    )


def _cmd_report(args: argparse.Namespace) -> int:
    extraction = _extract(args)
    report = extraction.ledger.report()

    warnings = [r for r in extraction.ir.residue if r.severity.value != "blocking"]
    blocking = [r for r in extraction.ir.residue if r.severity.value == "blocking"]
    if blocking:
        report += "\n\nBLOCKING RESIDUE -- gaps in the analysis itself:\n"
        report += "\n".join(f"  {r.description}: {r.reason}" for r in blocking)
    if warnings:
        report += "\n\nKNOWN LIMITS (not blocking):\n"
        report += "\n".join(f"  {r.description}: {r.reason}" for r in warnings)

    return _emit({"report": report, "clean": extraction.is_clean()})


def _cmd_decisions_template(args: argparse.Namespace) -> int:
    extraction = extract_migration_ir(_repo_path(args.repo))
    coverage = extraction.ir.coverage
    blocking = [
        (entry.fact.id, entry.fact.kind, entry.reason or "")
        for entry in (coverage.entries if coverage else [])
        if entry.disposition.value == "blocking"
    ]
    yaml_text = render_decisions_template(
        blocking,
        needs_model=any(c.model is None for c in extraction.ir.llm_calls)
        or not extraction.ir.llm_calls,
        needs_system_prompt=not extraction.ir.system_prompt,
        suggested_framework=extraction.ir.target_framework,
    )
    return _emit(
        {
            "yaml": yaml_text,
            "path": str(Path(args.repo) / DEFAULT_DECISIONS_FILENAME),
            "blockingCount": len(blocking),
        }
    )


def _cmd_spec(args: argparse.Namespace) -> int:
    extraction = _extract(args)
    try:
        return _emit({"agentSpec": migration_ir_to_agent_spec(extraction.ir)})
    except ProjectionError as exc:
        return _fail(exc, refusal=True, blocking=_blocking_lines(extraction))


def _cmd_scaffold(args: argparse.Namespace) -> int:
    target = Path(args.target_dir)
    target.mkdir(parents=True, exist_ok=True)
    steps: list[dict[str, Any]] = []

    scaffold.clone_template(target)
    steps.append({"script": "clone_template.py", "ok": True})
    scaffold.select_framework(target, args.framework)
    steps.append({"script": "select_framework.py", "ok": True})
    scaffold.setup_template(target, args.llm_model)
    steps.append({"script": "setup_template.py", "ok": True})

    return _emit(
        {
            "targetDir": str(target.resolve()),
            "framework": args.framework,
            "steps": steps,
            "hasDatarobotDir": (target / ".datarobot").is_dir(),
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m superrobot.ir", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (("extract", _cmd_extract), ("report", _cmd_report), ("spec", _cmd_spec)):
        cmd = sub.add_parser(name)
        cmd.add_argument("repo")
        cmd.add_argument("--decisions", default=None)
        cmd.set_defaults(handler=handler)

    template = sub.add_parser("decisions-template")
    template.add_argument("repo")
    template.set_defaults(handler=_cmd_decisions_template)

    scaffold_cmd = sub.add_parser("scaffold")
    scaffold_cmd.add_argument("target_dir")
    scaffold_cmd.add_argument("--framework", required=True, choices=scaffold.FRAMEWORKS)
    scaffold_cmd.add_argument("--llm-model", required=True, dest="llm_model")
    scaffold_cmd.set_defaults(handler=_cmd_scaffold)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result: int = args.handler(args)
        return result
    except (DecisionError, scaffold.ScaffoldError, ProjectionError) as exc:
        return _fail(exc)
    except Exception as exc:  # noqa: BLE001 - stdout must stay parseable JSON
        return _fail(exc)


if __name__ == "__main__":
    sys.exit(main())
