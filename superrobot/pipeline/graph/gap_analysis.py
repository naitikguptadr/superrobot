"""Graph-native gap analysis checks. Reuses GapFinding from the canonical
superrobot.models.gap_result module so results compose with today's
GapReport/gap_analysis.py flow unchanged -- this adds one new check, it
does not replace the existing file-scan-based rules (flat-imports,
endpoint-usage, pyproject-removal, runtime-param), which stay as they are
for now.
"""

from __future__ import annotations

from superrobot.models.gap_result import GapFinding
from superrobot.pipeline.graph.builder import RepoGraph
from superrobot.pipeline.graph.framework_detect import detect_framework


def check_unreachable_frameworks(
    repo_graph: RepoGraph, entry_point: str | None
) -> list[GapFinding]:
    """Flag framework imports present in the repo but not reachable from
    the resolved entry point, as a non-blocking warning.
    """
    result = detect_framework(repo_graph, entry_point)
    return [
        GapFinding(rule="unreachable-framework-import", severity="warning", message=warning)
        for warning in result.unreachable_warnings
    ]
