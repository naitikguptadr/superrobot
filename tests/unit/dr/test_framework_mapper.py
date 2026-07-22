"""Framework mapper unit tests."""

from superrobot.dr.framework_mapper import map_framework
from superrobot.models.analysis_result import DrFramework


def test_map_crewai_high_confidence() -> None:
    framework, confidence = map_framework("crewai")
    assert framework == DrFramework.CREWAI
    assert confidence >= 0.9


def test_map_langchain_with_state_graph() -> None:
    framework, confidence = map_framework("langchain", has_state_graph=True)
    assert framework == DrFramework.LANGGRAPH
    assert confidence >= 0.8


def test_map_workflow_yaml_nat() -> None:
    framework, confidence = map_framework("unknown", has_workflow_yaml=True)
    assert framework == DrFramework.NAT
    assert confidence >= 0.9


def test_map_unknown_low_confidence() -> None:
    framework, confidence = map_framework("unknown")
    assert framework == DrFramework.LANGGRAPH
    assert confidence <= 0.4


def test_map_autogen_falls_back_to_langgraph_medium() -> None:
    framework, confidence = map_framework("autogen")
    assert framework == DrFramework.LANGGRAPH
    assert 0.4 <= confidence < 0.6  # triggers TUI confirm


def test_map_semantic_kernel_falls_back() -> None:
    framework, confidence = map_framework("semantic_kernel")
    assert framework == DrFramework.LANGGRAPH
    assert confidence < 0.6


def test_map_haystack_falls_back() -> None:
    framework, confidence = map_framework("haystack")
    assert framework == DrFramework.LANGGRAPH
    assert confidence <= 0.55
