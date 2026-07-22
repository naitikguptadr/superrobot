"""Deep AST-based source migration for DRUM bundles.

Goes beyond flatten-and-delegate: rewrites imports via AST, strips hardcoded
secret defaults from env lookups, and flags A2A gather / hardcoded prompts.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class MigrationReport:
    """What the deep migrator changed in one file."""

    flat_imports: int = 0
    env_rewrites: int = 0
    gather_warnings: int = 0
    prompt_extractions: int = 0
    notes: list[str] = field(default_factory=list)


# Provider / secret env vars that should come from DR runtime params on deploy
_RUNTIME_ENV_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "MISTRAL_API_KEY",
        "CO_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "OLLAMA_BASE_URL",
        "LITELLM_API_BASE",
        "PROMPT_TEMPLATE_ID",
        "DATAROBOT_ENDPOINT",
        "DATAROBOT_API_TOKEN",
    }
)

_PROMPT_ASSIGN_RE = re.compile(
    r"^(\s*)(SYSTEM_PROMPT|system_prompt|PROMPT|DEFAULT_PROMPT)\s*=\s*(['\"]{3}|['\"])",
    re.MULTILINE,
)


def _flat_for_module(module: str, flat_names: dict[str, str]) -> str | None:
    """Longest-prefix match of a dotted module against migrated flat names."""
    if module in flat_names:
        return flat_names[module]
    for dotted in sorted(flat_names, key=len, reverse=True):
        if module.startswith(dotted + "."):
            return flat_names[dotted]
    # last-segment match for relative imports like `.search` → search
    stem = module.rsplit(".", maxsplit=1)[-1]
    if stem in flat_names.values():
        return stem
    return None


class _ImportRewriter(ast.NodeTransformer):
    """Rewrite absolute/relative imports of migrated modules to flat names."""

    def __init__(self, flat_names: dict[str, str]) -> None:
        self.flat_names = flat_names
        self.rewrites = 0

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        self.generic_visit(node)
        if node.module:
            flat = _flat_for_module(node.module, self.flat_names)
            if flat and (flat != node.module or node.level):
                self.rewrites += 1
                return ast.ImportFrom(module=flat, names=node.names, level=0)
        elif node.level:
            # from . import search  →  from search import … (when search is flat)
            for alias in node.names:
                if alias.name in self.flat_names.values():
                    self.rewrites += 1
                    return ast.ImportFrom(module=alias.name, names=node.names, level=0)
                dotted_hit = self.flat_names.get(alias.name)
                if dotted_hit:
                    self.rewrites += 1
                    return ast.ImportFrom(module=dotted_hit, names=node.names, level=0)
        return node

    def visit_Import(self, node: ast.Import) -> ast.AST:
        self.generic_visit(node)
        new_names: list[ast.alias] = []
        changed = False
        for alias in node.names:
            flat = _flat_for_module(alias.name, self.flat_names)
            if flat and flat != alias.name:
                asname = alias.asname or alias.name.rsplit(".", maxsplit=1)[-1]
                new_names.append(ast.alias(name=flat, asname=asname if asname != flat else None))
                changed = True
                self.rewrites += 1
            else:
                new_names.append(alias)
        if changed:
            return ast.Import(names=new_names)
        return node


class _EnvRewriter(ast.NodeTransformer):
    """Strip hardcoded defaults from secret env lookups.

    os.getenv('OPENAI_API_KEY', 'sk-...') → os.getenv('OPENAI_API_KEY')
    """

    def __init__(self) -> None:
        self.rewrites = 0

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        key = _env_key_from_call(node)
        if key and key in _RUNTIME_ENV_NAMES and len(node.args) >= 2:
            self.rewrites += 1
            return ast.Call(func=node.func, args=[node.args[0]], keywords=[])
        return node


def _env_key_from_call(node: ast.Call) -> str | None:
    func = node.func
    is_getenv = isinstance(func, ast.Attribute) and func.attr in {"getenv", "get"}
    if not is_getenv or not node.args:
        return None
    arg0 = node.args[0]
    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
        return arg0.value
    return None


class _GatherDetector(ast.NodeVisitor):
    """Count asyncio.gather usages (A2A race risk on DR)."""

    def __init__(self) -> None:
        self.count = 0
        self.lines: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "gather"
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncio"
        ):
            self.count += 1
            self.lines.append(getattr(node, "lineno", 0))
        self.generic_visit(node)


def rewrite_imports_ast(content: str, flat_names: dict[str, str]) -> tuple[str, int]:
    """AST-rewrite imports of migrated modules to flat DRUM names."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content, 0
    rewriter = _ImportRewriter(flat_names)
    new_tree = rewriter.visit(tree)
    if rewriter.rewrites == 0:
        return content, 0
    ast.fix_missing_locations(new_tree)
    try:
        return ast.unparse(new_tree) + "\n", rewriter.rewrites
    except Exception:
        return content, 0


def rewrite_env_defaults(content: str) -> tuple[str, int]:
    """Strip hardcoded defaults from secret env lookups."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content, 0
    rewriter = _EnvRewriter()
    new_tree = rewriter.visit(tree)
    if rewriter.rewrites == 0:
        return content, 0
    ast.fix_missing_locations(new_tree)
    try:
        return ast.unparse(new_tree) + "\n", rewriter.rewrites
    except Exception:
        return content, 0


def detect_asyncio_gather(content: str) -> tuple[int, list[str]]:
    """Return gather count and warning notes."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return 0, []
    det = _GatherDetector()
    det.visit(tree)
    notes = [
        f"asyncio.gather at line {ln} — A2A calls must run sequentially on DR" for ln in det.lines
    ]
    return det.count, notes


def extract_hardcoded_prompts(content: str) -> tuple[str, int, list[str]]:
    """Flag hardcoded system prompts with an inline marker comment."""
    matches = list(_PROMPT_ASSIGN_RE.finditer(content))
    if not matches:
        return content, 0, []
    notes = [
        f"Hardcoded prompt '{m.group(2)}' — move to Prompt Management Registry" for m in matches
    ]
    marker = (
        "# SUPERROBOT: hardcoded prompt detected — prefer PromptTemplate "
        "(PROMPT_TEMPLATE_ID) over string constants\n"
    )
    if "SUPERROBOT: hardcoded prompt" in content:
        return content, len(matches), notes
    first = matches[0]
    insert_at = first.start()
    return content[:insert_at] + marker + content[insert_at:], len(matches), notes


def deep_migrate_source(
    content: str,
    flat_names: dict[str, str],
) -> tuple[str, MigrationReport]:
    """Run the full deep-migration pass on one Python source file."""
    report = MigrationReport()

    rewritten, n_imp = rewrite_imports_ast(content, flat_names)
    if n_imp:
        content = rewritten
        report.flat_imports = n_imp

    content, n_env = rewrite_env_defaults(content)
    report.env_rewrites = n_env

    gather_n, gather_notes = detect_asyncio_gather(content)
    report.gather_warnings = gather_n
    report.notes.extend(gather_notes)

    content, n_prompt, prompt_notes = extract_hardcoded_prompts(content)
    report.prompt_extractions = n_prompt
    report.notes.extend(prompt_notes)

    return content, report
