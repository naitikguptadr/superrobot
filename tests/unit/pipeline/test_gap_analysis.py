"""Gap Analysis unit tests."""

from __future__ import annotations

from pathlib import Path

from superrobot.pipeline.gap_analysis import run_gap_analysis

_CLEAN_CUSTOM_PY = """\
_RUNTIME_PARAM_KEYS = [
    "API_KEY",
]
"""

_CLEAN_INFRA_PY = """\
api_key_param = None  # references API_KEY
"""

_CLEAN_ENV_TEMPLATE = """\
API_KEY=
PROMPT_TEMPLATE_ID=
DATAROBOT_ENDPOINT=
"""


def _write_package(
    tmp_path: Path,
    *,
    custom_py: str = _CLEAN_CUSTOM_PY,
    infra_py: str = _CLEAN_INFRA_PY,
    env_template: str = _CLEAN_ENV_TEMPLATE,
    pyproject: str = '[project]\ndependencies = ["foo"]\n',
    extra_agent_file: tuple[str, str] | None = None,
) -> Path:
    agent_dir = tmp_path / "agent" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "custom.py").write_text(custom_py)
    infra_dir = tmp_path / "infra" / "infra"
    infra_dir.mkdir(parents=True)
    (infra_dir / "agent.py").write_text(infra_py)
    (tmp_path / ".env.template").write_text(env_template)
    (tmp_path / "pyproject.toml").write_text(pyproject)
    if extra_agent_file:
        name, content = extra_agent_file
        (agent_dir / name).write_text(content)
    return tmp_path


def test_run_gap_analysis_on_empty_dir_is_blocking(tmp_path: Path) -> None:
    report = run_gap_analysis(tmp_path)
    assert len(report.blocking) == 1
    assert report.blocking[0].rule == "not-a-package"


def test_run_gap_analysis_clean_package_has_no_findings(tmp_path: Path) -> None:
    _write_package(tmp_path)
    report = run_gap_analysis(tmp_path)
    assert report.findings == []


def test_run_gap_analysis_flags_nested_import_as_blocking(tmp_path: Path) -> None:
    _write_package(tmp_path, custom_py=_CLEAN_CUSTOM_PY + "\nfrom agent.agent.helpers import x\n")
    report = run_gap_analysis(tmp_path)
    assert any(f.rule == "flat-imports" for f in report.blocking)


def test_run_gap_analysis_flags_endpoint_confusion_as_warning(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        extra_agent_file=(
            "legacy.py",
            'DATAROBOT_PREDICTION_API_URL = "x"\nclient = dr.Client()\n',
        ),
    )
    report = run_gap_analysis(tmp_path)
    assert any(f.rule == "endpoint-usage" for f in report.warnings)
    assert not any(f.rule == "endpoint-usage" for f in report.blocking)


def test_run_gap_analysis_flags_missing_runtime_param_as_warning(tmp_path: Path) -> None:
    _write_package(
        tmp_path,
        custom_py="_RUNTIME_PARAM_KEYS = []\n",
        infra_py="# no reference\n",
        env_template="API_KEY=\n",
    )
    report = run_gap_analysis(tmp_path)
    messages = [f.message for f in report.warnings]
    files = [f.file for f in report.warnings]
    assert any("API_KEY" in m for m in messages)
    assert "agent/agent/custom.py" in files
    assert "infra/infra/agent.py" in files


def test_run_gap_analysis_pyproject_removal_needs_source(tmp_path: Path) -> None:
    original_repo = tmp_path / "original"
    original_repo.mkdir()
    (original_repo / "pyproject.toml").write_text('[project]\ndependencies = ["foo", "bar"]\n')

    package_dir = tmp_path / "out"
    _write_package(package_dir, pyproject='[project]\ndependencies = ["foo"]\n')

    without_source = run_gap_analysis(package_dir)
    assert not any(f.rule == "pyproject-removal" for f in without_source.findings)

    with_source = run_gap_analysis(package_dir, source_repo=original_repo)
    assert any(f.rule == "pyproject-removal" for f in with_source.blocking)
