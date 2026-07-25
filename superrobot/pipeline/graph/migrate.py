"""Format-preserving import rewriting via libcst, replacing
ast_migrate.py's ast.NodeTransformer + ast.unparse() approach.

libcst round-trips the concrete syntax tree exactly -- comments and
whitespace survive a rewrite, unlike ast.unparse() which regenerates
source from the abstract tree and can reformat it. Verified: a leading
comment above a rewritten import statement is preserved unchanged.

Scope (this module is NOT a full replacement for ast_migrate.py yet):
handles only exact-match, absolute `from <module> import X` statements
where `<module>` is a literal key in `flat_names`. Does NOT rewrite
`import x.y` statements, relative imports (left untouched -- see
_ImportRewriter.leave_ImportFrom), or nested-submodule imports needing
prefix matching (e.g. `from tools.search.util import x` will not match
a `flat_names` key of `tools.search`). Callers requiring that coverage
should still use ast_migrate.py's `rewrite_imports_ast()` until this
module reaches parity.
"""

from __future__ import annotations

import libcst as cst


class _ImportRewriter(cst.CSTTransformer):
    """Rewrite `from <nested.module> import X` to `from <flat> import X`
    when nested.module is a key in flat_names.
    """

    def __init__(self, flat_names: dict[str, str]) -> None:
        self.flat_names = flat_names
        self.rewrites = 0

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated_node.relative:
            # Relative imports (e.g. `from .search import x`) are out of
            # scope here: get_full_name_for_node() below only returns the
            # bare tail after the leading dots, so a flat_names key that
            # happens to match the tail (a realistic collision) would
            # otherwise cause us to silently rewrite a sibling-relative
            # import into an absolute import of an unrelated module.
            return updated_node
        if updated_node.module is None:
            return updated_node
        mod_name = cst.helpers.get_full_name_for_node(updated_node.module)
        if mod_name in self.flat_names:
            self.rewrites += 1
            new_module = cst.parse_expression(self.flat_names[mod_name])
            return updated_node.with_changes(module=new_module, relative=[])
        return updated_node


def rewrite_imports_libcst(content: str, flat_names: dict[str, str]) -> tuple[str, int]:
    """Rewrite nested imports of migrated modules to flat names.

    Returns (new_source, rewrite_count). Format/comments are preserved
    exactly except for the rewritten import lines themselves.
    """
    try:
        tree = cst.parse_module(content)
    except cst.ParserSyntaxError:
        # Malformed/non-standard source shouldn't crash a whole-repo
        # migration sweep; fall back to leaving the file untouched, same
        # as ast_migrate.py's SyntaxError fallback.
        return content, 0
    transformer = _ImportRewriter(flat_names)
    new_tree = tree.visit(transformer)
    return new_tree.code, transformer.rewrites
