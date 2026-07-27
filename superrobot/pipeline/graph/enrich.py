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
from pathlib import Path

from superrobot.models.scan_result import EntryPoint, ScanResult
from superrobot.pipeline.graph.builder import build_repo_graph, strip_collision_suffix
from superrobot.pipeline.graph.entry_points import resolve_entry_point
from superrobot.pipeline.graph.framework_detect import detect_framework

logger = logging.getLogger(__name__)


def enrich_scan_result(base: ScanResult, repo_path: str | Path) -> ScanResult:
    """Return `base` improved with graph-derived signal.

    Returns `base` unchanged if the repo cannot be graphed. Never raises.
    """
    try:
        repo_graph = build_repo_graph(Path(repo_path))
        entry_point = resolve_entry_point(repo_graph)
        detection = detect_framework(repo_graph, entry_point)
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
