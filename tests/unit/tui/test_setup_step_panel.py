"""Setup step panel unit tests."""

from superrobot.tui.setup_step_panel import SetupStepPanel


def test_setup_step_panel_advance() -> None:
    panel = SetupStepPanel()
    panel.advance()
    assert panel.current_step == 1
    assert panel.step_statuses.get(0) == "done"


def test_setup_step_panel_fail_current() -> None:
    panel = SetupStepPanel()
    panel.fail_current()
    assert panel.step_statuses.get(0) == "failed"


def test_pipeline_panel_complete_step_ahead_of_current() -> None:
    """Regression: Generate completed while current_step was still Analyze."""
    from superrobot.tui.pipeline_panel import PipelinePanel

    panel = PipelinePanel()
    panel.current_step = 1
    panel.complete_step(2)
    assert panel.step_statuses[0] == "done"
    assert panel.step_statuses[1] == "done"
    assert panel.step_statuses[2] == "done"
    assert panel.step_statuses[3] == "active"
    assert panel.current_step == 3
