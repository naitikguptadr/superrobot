"""Template browser parsing tests."""

from superrobot.tui.template_browser import parse_templates_list


def test_parse_templates_list_multiline() -> None:
    stdout = (
        "langgraph-agent    LangGraph starter    langgraph\n"
        "crewai-agent    CrewAI starter    crewai\n"
    )
    templates = parse_templates_list(stdout)
    assert len(templates) >= 2
    assert templates[0].name == "langgraph-agent"


def test_parse_templates_fallback() -> None:
    templates = parse_templates_list("my-template\n")
    assert len(templates) == 1
    assert templates[0].name == "my-template"
