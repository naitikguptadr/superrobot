"""dr-ui React component generation via LLM — Stage 4."""

from __future__ import annotations

import json
import re
from pathlib import Path

from superrobot.dr.llm_gateway import LLMGateway, has_llm_credentials
from superrobot.models.analysis_result import AnalysisResult

CATALOG_PATH = Path(__file__).parent.parent / "dr" / "drui_catalog.json"
TOKENS_PATH = Path(__file__).parent.parent / "dr" / "drui_tokens.json"


async def generate_ui_component(
    description: str,
    analysis: AnalysisResult,
    existing_components: list[str] | None = None,
    gateway: LLMGateway | None = None,
) -> str:
    """Generate a @dr-ui React component from natural language description."""
    if gateway is None and not has_llm_credentials():
        return _stub_ui_component(description, analysis)

    gw = gateway or LLMGateway()
    if not gw.available:
        return _stub_ui_component(description, analysis)
    catalog = CATALOG_PATH.read_text()
    tokens = TOKENS_PATH.read_text()

    user_content = (
        f"<DR_UI_COMPONENT_CATALOG>\n{catalog}\n</DR_UI_COMPONENT_CATALOG>\n"
        f"<DR_DESIGN_TOKENS>\n{tokens}\n</DR_DESIGN_TOKENS>\n"
        f"<AGENT_INPUT_SCHEMA>\n{json.dumps(analysis.input_schema)}\n</AGENT_INPUT_SCHEMA>\n"
        f"<AGENT_OUTPUT_SCHEMA>\n{json.dumps(analysis.output_schema)}\n</AGENT_OUTPUT_SCHEMA>\n"
        f"<EXISTING_COMPONENTS>\n{json.dumps(existing_components or [])}\n</EXISTING_COMPONENTS>\n"
        f"User: {description}"
    )

    tsx = await gw.call_text("ui_generate", user_content)
    tsx = _strip_markdown_fences(tsx)

    if not _validate_tsx(tsx):
        retry_content = f"{user_content}\n\nParse error: invalid TSX syntax. Return valid JSX only."
        tsx = await gw.call_text("ui_generate", retry_content)
        tsx = _strip_markdown_fences(tsx)

    return tsx


def _stub_ui_component(description: str, analysis: AnalysisResult) -> str:
    """Minimal dr-ui placeholder when LLM is unavailable."""
    inputs = ", ".join(analysis.input_schema.keys()) or "query"
    return f"""export default function GeneratedPanel() {{
  return (
  <Card title="{description[:60]}">
    <TextInput label="{inputs}" />
    <Text>Output: response</Text>
  </Card>
  );
}}
"""


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _validate_tsx(tsx: str) -> bool:
    """Basic TSX validation — checks for export and JSX tags."""
    if not tsx:
        return False
    if not re.search(r"export\s+(default\s+)?function", tsx):
        return False
    return "<" in tsx and ">" in tsx
