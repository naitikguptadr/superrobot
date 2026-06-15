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


def test_parse_templates_list_dr_v02_format() -> None:
    """Real dr v0.2.x output: INFO header + ID/Name lines."""
    stdout = (
        "INFO  Fetching templates from: https://staging.datarobot.com/api/v2/applicationTemplates/?limit=100\n"
        "ID: 69090966c601dbd8c8514516\tName: Agentic Starter\n"
        "ID: 67a0f7338be36c535d4dcaa0\tName: Talk to My Data Agent\n"
    )
    templates = parse_templates_list(stdout)
    assert len(templates) == 2
    assert templates[0].name == "Agentic Starter"
    assert templates[0].template_id == "69090966c601dbd8c8514516"
    assert templates[1].name == "Talk to My Data Agent"
