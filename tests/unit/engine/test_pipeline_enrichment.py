"""The engine's scan stage must return graph-enriched results."""

from __future__ import annotations

from pathlib import Path

from superrobot.engine.pipeline import TransformEngine


def test_run_scan_returns_graph_enriched_result(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def process():\n    return 1\n\ndef run_agent():\n    return process()\n"
    )

    result = TransformEngine().run_scan(str(tmp_path))

    # The graph traces run_agent as the real entry point; without enrichment
    # the scanner's name-ranking alone decides the order.
    assert result.primary_entry is not None
    assert result.primary_entry.function == "run_agent"


def test_run_scan_promotes_the_entry_point_only_the_graph_can_find(
    tmp_path: Path,
) -> None:
    """The discriminating cutover test.

    `run_agent` tops scanner.py's own ranking, so the test above passes with
    or without enrichment. Here the scanner ranks `process` first and only
    the graph -- by tracing the `__main__` guard -- knows that `main` is what
    actually runs. This test fails on the un-enriched passthrough.
    """
    (tmp_path / "app.py").write_text(
        "def process():\n"
        "    return 1\n\n\n"
        "def main():\n"
        "    return process()\n\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n"
    )

    result = TransformEngine().run_scan(str(tmp_path))

    assert result.primary_entry is not None
    assert result.primary_entry.function == "main"


def test_run_scan_still_succeeds_on_a_repo_the_graph_cannot_parse(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text("def broken(:\n    pass\n")

    result = TransformEngine().run_scan(str(tmp_path))

    assert result is not None
