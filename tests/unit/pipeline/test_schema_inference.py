"""Schema inference unit tests."""

from superrobot.models.scan_result import EntryPoint
from superrobot.pipeline.schema_inference import infer_schemas


def test_infer_schemas_from_annotations(tmp_path) -> None:
    (tmp_path / "main.py").write_text(
        "async def run_agent(query: str, max_sources: int = 3) -> dict[str, str]:\n"
        "    return {'response': query, 'sources': []}\n"
    )
    entry = EntryPoint(
        file="main.py",
        function="run_agent",
        signature="async def run_agent(query, max_sources)",
    )
    input_schema, output_schema = infer_schemas(tmp_path, entry)
    assert input_schema == {"query": "str", "max_sources": "int"}
    assert "response" in output_schema


def test_infer_schemas_from_return_literal(tmp_path) -> None:
    (tmp_path / "main.py").write_text(
        "async def run_agent(query):\n    return {'answer': query, 'confidence': 0.9}\n"
    )
    entry = EntryPoint(file="main.py", function="run_agent", signature="async def run_agent(query)")
    _, output_schema = infer_schemas(tmp_path, entry)
    assert output_schema == {"answer": "str", "confidence": "str"}
