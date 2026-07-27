"""Enrich a scanner-produced ScanResult using the whole-repo call graph.

Deliberately an *enrichment* layer, not a replacement for
`superrobot.pipeline.scanner`. ScanResult has 15 fields; a call graph is
genuinely better at three of them (which entry point is real, how much to
trust the framework detection, and whether an import is actually reachable
at runtime). The other twelve -- dependencies parsed from requirements.txt,
env vars matched by regex, tools found by decorator, LLM clients found by
constructor name, secret-pattern risk flags, file counts -- gain nothing
from reachability analysis, so they are passed through untouched.

Two invariants make this safe to run unconditionally:

1. Conservative: enrichment may raise confidence (when the graph *proves* a
   framework is reachable from a real entry point) and may add findings, but
   never lowers a score below what the scanner reported. Cutover therefore
   cannot regress existing behavior.
2. Total: any failure to build or query the graph degrades to returning the
   scanner's own result unchanged, so a repo the graph can't handle still
   scans successfully. `enrich_scan_result` never raises.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from superrobot.models.scan_result import EntryPoint, ScanResult
from superrobot.pipeline.graph.builder import (
    GraphBuildTimeout,
    build_repo_graph,
    strip_collision_suffix,
)
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.framework_detect import detect_framework

logger = logging.getLogger(__name__)

# Wall-clock budget for the whole graph build during `superrobot scan`.
#
# `scan` was near-instant before the graph engine and is the command users
# run interactively and repeatedly, so this is chosen as a *latency
# contract* -- "scan is never more than ~5s slower than it used to be" --
# rather than as an attempt to always finish the graph.
#
# Measured on this repo: ~2.2s for 50 files, ~4.4s for 150. Cost scales with
# the number of in-repo call sites, so a 500-file repo would run well over
# 10s and a pathological one far worse. 5s therefore keeps enrichment for
# the small-to-medium agent repos this tool targets and deliberately gives
# it up beyond roughly that size, instead of making everyone wait.
#
# Giving it up is cheap and safe: exceeding the budget discards the graph
# entirely (see `GraphBuildTimeout` -- never a truncated one) and returns
# the scanner's own result, so the cost of a trip is losing an optional
# confidence bump and entry-point reordering, never a wrong answer.
#
# `validate` deliberately trades the other way; see
# `superrobot.pipeline.gap_analysis.GAP_ANALYSIS_GRAPH_BUDGET_SECONDS`.
ENRICHMENT_BUDGET_SECONDS = 5.0


def enrich_scan_result(base: ScanResult, repo_path: str | Path) -> ScanResult:
    """Return `base` improved with graph-derived signal.

    Returns `base` unchanged if the repo cannot be graphed. Never raises.
    """
    try:
        repo_graph = build_repo_graph(
            Path(repo_path), deadline=time.monotonic() + ENRICHMENT_BUDGET_SECONDS
        )
        entry_point = resolve_entry_point(repo_graph)
        detection = detect_framework(repo_graph, entry_point)
    except GraphBuildTimeout:
        # Distinguished from the generic failure below purely so the log
        # says which one happened: "too slow on this repo" is a tuning
        # signal, "the graph blew up" is a bug report.
        logger.debug(
            "graph enrichment timed out for %s after its %.1fs budget; "
            "returning the unenriched scan result",
            repo_path,
            ENRICHMENT_BUDGET_SECONDS,
        )
        return base
    except Exception as exc:  # noqa: BLE001 - defensive, see module docstring
        logger.debug("graph enrichment skipped for %s: %s", repo_path, exc)
        return base

    enriched = base.model_copy(deep=True)

    # (1) Confidence: only ever raised, and only when the graph independently
    # agrees with the scanner about which framework this is. A disagreement
    # means the graph is looking at something the scanner didn't conclude, so
    # it must not be used to inflate certainty.
    if detection.framework == base.detected_framework:
        enriched.confidence = max(base.confidence, detection.confidence)

    # (2) Entry points: promote the graph-resolved entry point to first, since
    # it was traced through real call edges rather than ranked by name alone.
    # Reorders only -- never drops a candidate the scanner found.
    # Reorders `enriched`'s own deep-copied EntryPoints, not `base`'s, so the
    # returned result never shares mutable objects with the caller's input.
    if entry_point is not None:
        enriched.entry_points = _promote_entry_point(enriched.entry_points, entry_point)

    return enriched


def _promote_entry_point(entry_points: list[EntryPoint], entry_point: str) -> list[EntryPoint]:
    """Move the scanner-discovered EntryPoint matching `entry_point` to the
    front of the list, leaving every other candidate in place.
    """
    local_name = strip_collision_suffix(entry_point).rsplit(".", 1)[-1]
    match = next((ep for ep in entry_points if ep.function == local_name), None)
    if match is None:
        return list(entry_points)
    return [match] + [ep for ep in entry_points if ep is not match]
