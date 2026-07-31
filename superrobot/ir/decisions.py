"""Human decisions that unblock a migration, on the record.

The coverage ledger blocks whenever carrying a behavior over would require
a guess. That is the point -- but a tool that can only ever block is not a
tool. Something has to let a human say "this one is fine, and here is why",
without that turning into a way to make gaps disappear quietly.

This module is that seam. A decisions file is YAML the human (or the
harness agent, on the human's instruction) fills in:

```yaml
model: azure/gpt-4o                     # DataRobot LLM Gateway id
system_prompt: |
  You are a research assistant...
examples:
  - Find recent papers on LLM hallucination
facts:
  - fact: "llm_call:main.py:7:Agent"
    disposition: migrated
    reason: the crew uses the org default, which we are pinning to gpt-4o
    model: gpt-4o
acknowledged_residue:
  - no LLM call site was found in this repo
```

Four rules keep this from becoming a rubber stamp:

1. **A decision cannot invent a fact.** Naming a fact id the probes did not
   produce is an error, not a no-op. A decisions file that has drifted out
   of sync with the code fails loudly rather than silently under-applying.
2. **Every decision needs a reason**, including `migrated`. Overriding a
   block is exactly where the justification matters most, and the ledger
   only demands reasons for deferrals.
3. **A decision cannot promote a fact to a state the probes disagree with
   silently** -- the reason is written into the ledger prefixed as a human
   decision, so `report()` distinguishes what we derived from what someone
   asserted.
4. **Acknowledging residue downgrades it, never deletes it.** A blocking
   residue becomes a warning that still appears in the report, carrying who
   said it was acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from superrobot.ir.model import Disposition, Frontend

_DECIDABLE = (Disposition.MIGRATED, Disposition.DEFERRED)

DEFAULT_DECISIONS_FILENAME = "superrobot-decisions.yaml"


class DecisionError(Exception):
    """The decisions file is malformed, or asks for something that would
    let a gap escape accounting.
    """


@dataclass(frozen=True)
class FactDecision:
    fact: str
    disposition: Disposition
    reason: str
    model: str | None = None
    """For an `llm_call` fact whose model the probes could not resolve: the
    model the migrated agent should actually use."""

    def ledger_reason(self) -> str:
        return f"human decision: {self.reason}"


@dataclass
class Decisions:
    model: str | None = None
    system_prompt: str | None = None
    examples: list[str] = field(default_factory=list)
    target_framework: str | None = None
    frontend: Frontend = field(default_factory=Frontend)
    facts: dict[str, FactDecision] = field(default_factory=dict)
    acknowledged_residue: list[str] = field(default_factory=list)

    def for_fact(self, fact_id: str) -> FactDecision | None:
        return self.facts.get(fact_id)

    def acknowledges(self, residue_description: str) -> bool:
        return residue_description in self.acknowledged_residue


def _require_reason(raw: object, fact: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise DecisionError(
            f"decision for {fact!r} has no reason. Overriding a block without "
            "saying why is how a gap disappears quietly -- that is the one "
            "thing this file exists to prevent."
        )
    return raw.strip()


def _parse_disposition(raw: object, fact: str) -> Disposition:
    try:
        disposition = Disposition(str(raw))
    except ValueError:
        raise DecisionError(
            f"decision for {fact!r} has disposition {raw!r}; expected one of "
            f"{', '.join(d.value for d in _DECIDABLE)}"
        ) from None
    if disposition not in _DECIDABLE:
        raise DecisionError(
            f"decision for {fact!r} sets {disposition.value}, which a human "
            "decision cannot do. A decision resolves a block; it does not "
            "create one."
        )
    return disposition


def parse_decisions(text: str) -> Decisions:
    """Parse a decisions document. Raises `DecisionError` on anything that
    would weaken the accounting.
    """
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise DecisionError(f"decisions file is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise DecisionError("decisions file must be a YAML mapping at the top level")

    decisions = Decisions(
        model=raw.get("model"),
        system_prompt=raw.get("system_prompt"),
        examples=list(raw.get("examples") or []),
        target_framework=raw.get("target_framework"),
        acknowledged_residue=list(raw.get("acknowledged_residue") or []),
    )

    frontend_raw = raw.get("frontend")
    if isinstance(frontend_raw, dict):
        decisions.frontend = Frontend(**frontend_raw)

    for entry in raw.get("facts") or []:
        if not isinstance(entry, dict) or not entry.get("fact"):
            raise DecisionError(f"each entry under `facts` needs a `fact` id; got {entry!r}")
        fact = str(entry["fact"])
        if fact in decisions.facts:
            raise DecisionError(f"fact {fact!r} is decided twice; the second would win silently")
        decisions.facts[fact] = FactDecision(
            fact=fact,
            disposition=_parse_disposition(entry.get("disposition"), fact),
            reason=_require_reason(entry.get("reason"), fact),
            model=entry.get("model"),
        )

    return decisions


def load_decisions(path: Path | None) -> Decisions:
    """Load a decisions file, or return empty decisions when `path` is None.

    A path that was given but does not exist is an error: silently
    proceeding with no decisions would produce a blocked run whose cause
    (a typo in the path) is invisible.
    """
    if path is None:
        return Decisions()
    path = Path(path)
    if not path.is_file():
        raise DecisionError(f"decisions file not found: {path}")
    return parse_decisions(path.read_text())


def render_decisions_template(
    blocking: list[tuple[str, str, str]],
    *,
    needs_model: bool,
    needs_system_prompt: bool,
    suggested_framework: str,
) -> str:
    """Render a starter decisions file listing every blocker awaiting a call.

    `blocking` is `(fact_id, kind, reason)` per blocked fact. The template
    is deliberately unfilled: every entry is commented out, so an
    unattended run cannot accidentally acknowledge anything.
    """
    lines = [
        "# SuperRobot migration decisions.",
        "#",
        "# Every entry below is a place the analysis stopped rather than guess.",
        "# Uncomment and answer the ones you want to resolve; leave the rest and",
        "# the migration will keep blocking on them, which is the safe default.",
        "",
    ]

    if needs_model:
        lines += [
            "# The DataRobot LLM Gateway model id for the migrated agent.",
            "# Run list_llm_models.py to see what your account actually has.",
            "# model: azure/gpt-4o",
            "",
        ]
    if needs_system_prompt:
        lines += [
            "# agent_spec.md requires a system prompt, and no probe extracts one.",
            "# system_prompt: |",
            "#   You are ...",
            "",
        ]

    lines += [
        "# Example inputs. rehearsal.py replays these to simulate the agent",
        "# before any code is written.",
        "# examples:",
        "#   - ...",
        "",
        f"# Recipe framework derived from the detected topology: {suggested_framework}",
        f"# target_framework: {suggested_framework}",
        "",
    ]

    if not blocking:
        lines += ["# No blocking facts. Nothing here needs your decision.", "facts: []", ""]
        return "\n".join(lines)

    lines += [f"# {len(blocking)} blocking fact(s) awaiting a decision.", "facts:"]
    for fact_id, kind, reason in blocking:
        lines += [
            "",
            f"  # [{kind}] {reason}",
            f'  # - fact: "{fact_id}"',
            "  #   disposition: migrated   # or: deferred",
            "  #   reason: ",
        ]
        if kind == "llm_call":
            lines.append("  #   model:                  # the model the migrated agent should use")
    lines += [
        "",
        "# Residue is a known gap in the analysis itself rather than in one fact.",
        "# Acknowledging one downgrades it from blocking to a warning -- it still",
        "# appears in every report.",
        "# acknowledged_residue:",
        "#   - ...",
        "",
    ]
    return "\n".join(lines)
