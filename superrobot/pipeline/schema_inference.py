"""Infer agent input/output schemas from entry-point AST."""

from __future__ import annotations

import ast
from pathlib import Path

from superrobot.models.scan_result import EntryPoint

_TYPE_MAP = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "dict": "dict",
    "list": "list",
    "Any": "any",
}


def infer_schemas(
    repo_path: str | Path,
    entry: EntryPoint | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Best-effort schema inference from type hints and return literals."""
    if entry is None:
        return {"query": "str"}, {"response": "str"}

    root = Path(repo_path).resolve()
    py_file = root / entry.file
    if not py_file.exists():
        return _fallback_from_signature(entry)

    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return _fallback_from_signature(entry)

    func = _find_function(tree, entry.function)
    if func is None:
        return _fallback_from_signature(entry)

    input_schema = _input_from_function(func)
    output_schema = _output_from_function(func)
    if not input_schema:
        input_schema = _fallback_from_signature(entry)[0]
    if not output_schema:
        output_schema = {"response": "str"}
    return input_schema, output_schema


def _fallback_from_signature(entry: EntryPoint) -> tuple[dict[str, str], dict[str, str]]:
    from superrobot.models.agent_config import parse_signature_params

    params = parse_signature_params(entry.signature)
    if not params:
        return {"query": "str"}, {"response": "str"}
    if len(params) == 1:
        return {params[0]: "str"}, {"response": "str"}
    return {name: "str" for name in params}, {"response": "str"}


def _find_function(
    tree: ast.AST,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _input_from_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    schema: dict[str, str] = {}
    for arg in func.args.args:
        if arg.arg in ("self", "cls"):
            continue
        schema[arg.arg] = _annotation_to_type(arg.annotation)
    for arg in func.args.kwonlyargs:
        schema[arg.arg] = _annotation_to_type(arg.annotation)
    return schema


def _annotation_to_type(node: ast.expr | None) -> str:
    if node is None:
        return "str"
    if isinstance(node, ast.Name):
        return _TYPE_MAP.get(node.id, node.id.lower())
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Subscript):
        base = _annotation_to_type(node.value)
        return f"list[{base}]" if base != "str" else "list"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _annotation_to_type(node.left)
        right = _annotation_to_type(node.right)
        return left if right in ("None", "none") else f"{left}|{right}"
    if isinstance(node, ast.Attribute):
        return node.attr.lower()
    return "str"


def _output_from_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    if func.returns:
        hinted = _annotation_to_type(func.returns)
        if hinted == "dict":
            keys = _dict_keys_from_returns(func)
            if keys:
                return {key: "str" for key in keys}
        if hinted not in ("str", "any"):
            return {"response": hinted}

    keys = _dict_keys_from_returns(func)
    if keys:
        return {key: "str" for key in keys}
    return {"response": "str"}


def _dict_keys_from_returns(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    keys: list[str] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        keys.extend(_extract_dict_keys(node.value))
    return list(dict.fromkeys(keys))


def _extract_dict_keys(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Dict):
        result: list[str] = []
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                result.append(key.value)
        return result
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        return []
    return []
