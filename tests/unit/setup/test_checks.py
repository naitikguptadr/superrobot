"""Setup checks unit tests."""

from superrobot.setup.checks import PrerequisiteStatus, SetupCheckResult, check_prerequisites


def test_check_prerequisites_returns_all_binaries() -> None:
    prereqs = check_prerequisites()
    assert len(prereqs) >= 6
    assert all(isinstance(p, PrerequisiteStatus) for p in prereqs)


def test_setup_check_result_not_ready_by_default() -> None:
    result = SetupCheckResult()
    assert not result.is_ready


def test_setup_check_result_ready_when_all_ok() -> None:
    result = SetupCheckResult(
        prerequisites=[PrerequisiteStatus("dr", True)],
        auth_ok=True,
        endpoint_set=True,
        token_set=True,
        gateway_ok=True,
    )
    assert result.is_ready
