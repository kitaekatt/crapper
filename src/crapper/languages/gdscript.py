"""GDScript functions, methods, and cyclomatic complexity.

Decision points follow the same structural rule as the other languages:
`if`, `elif`, `for`, `while`, each `match` arm, a conditional expression
(`a if c else b`), and each `and` / `or` (or `&&` / `||`). Every named `func`
is an entry, including `static func` and functions in inner `class` blocks.
A lambda stays inside the function that holds it. A function's namespace is
the dotted module path, plus the enclosing inner classes: `module.Inner`.
"""

from crapper.languages.treesitter import (
    child_of_type,
    complexity,
    descendants,
    end_line,
    node_text,
    parse,
    start_line,
)
from crapper.languages.typescript import _strip_root_prefix, _strip_src_dir
from crapper.model import Function

_DECISIONS = {
    "if_statement",
    "elif_clause",
    "for_statement",
    "while_statement",
    "pattern_section",
    "conditional_expression",
}
_LOGIC = {"and", "or", "&&", "||"}


def _is_decision(node) -> bool:
    if node.type in _DECISIONS:
        return True
    return node.type == "binary_operator" and any(
        child.type in _LOGIC for child in node.children
    )


def _skip(node) -> bool:
    return node.type == "class_definition"


def _module_namespace(path: str, source_root: str | None) -> str:
    relative = _strip_src_dir(_strip_root_prefix(path.replace("\\", "/"), source_root))
    if relative.endswith(".gd"):
        relative = relative[: -len(".gd")]
    return relative.replace("/", ".")


def _class_names(data: bytes, node) -> list[str]:
    names: list[str] = []
    current = node.parent
    while current is not None:
        if current.type == "class_definition":
            ident = child_of_type(current, "name")
            if ident is not None:
                names.append(node_text(data, ident))
        current = current.parent
    names.reverse()
    return names


def functions_in_source(
    source: str, path: str, source_root: str | None = None
) -> list[Function]:
    data, tree = parse(source, "gdscript")
    module = _module_namespace(path, source_root)
    found: list[Function] = []
    for node in descendants(tree.root_node):
        if node.type != "function_definition":
            continue
        ident = child_of_type(node, "name")
        if ident is None:
            continue
        namespace = ".".join(filter(None, [module, *_class_names(data, node)]))
        found.append(
            Function(
                name=node_text(data, ident),
                namespace=namespace,
                complexity=complexity(node, _is_decision, _skip),
                start_line=start_line(node),
                end_line=end_line(node),
                path=path,
                language="gdscript",
            )
        )
    return found
