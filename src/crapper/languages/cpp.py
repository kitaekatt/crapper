"""C++ functions, methods, lambdas, and cyclomatic complexity.

Decision points follow the same structural rule as Java: `if` (so `else if`
counts once), `for`, range-for, `while`, `do`, each `case` and `default`
label, `catch`, `?:`, `goto`, and each `&&` / `||` (or `and` / `or`).
Entries are free functions, methods defined in a class body or out of line
(`A::f`), constructors, destructors, operators, and lambdas bound to a name
(`auto f = [](...) {...};`, also as a class member). A named lambda is its own
entry and is not also charged to the function that holds it. A method is named
`Class::method`, with template arguments dropped from the qualifier. The
:namespace is the dotted module path alone, built as for Python and TypeScript
(`kernel/src/routeEdge.cpp` is `routeEdge`). Enclosing C++ namespaces prefix
the name: `woodkernel::Class::method`, `a::b::freeFn`.
`.h` is read as C++: the grammar accepts the C subset that headers use.
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
    "for_statement",
    "for_range_loop",
    "while_statement",
    "do_statement",
    "case_statement",
    "catch_clause",
    "conditional_expression",
    "goto_statement",
}
_LOGIC = {"&&", "||", "and", "or"}
_TYPE_SCOPES = {"class_specifier", "struct_specifier", "union_specifier"}
_SUFFIXES = (".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h")


def _is_decision(node) -> bool:
    if node.type in _DECISIONS:
        return True
    return node.type == "binary_expression" and any(
        child.type in _LOGIC for child in node.children
    )


def _skip(node) -> bool:
    return node.type in _TYPE_SCOPES or _is_named_lambda(node)


def _is_named_lambda(node) -> bool:
    if node.type != "lambda_expression" or node.parent is None:
        return False
    return node.parent.type in {"init_declarator", "field_declaration"}


def _module_namespace(path: str, source_root: str | None) -> str:
    relative = _strip_src_dir(_strip_root_prefix(path.replace("\\", "/"), source_root))
    for suffix in _SUFFIXES:
        if relative.endswith(suffix):
            relative = relative[: -len(suffix)]
            break
    return relative.replace("/", ".")


def _enclosing_names(data: bytes, node, kinds) -> list[str]:
    names: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in kinds:
            ident = child_of_type(
                current, "namespace_identifier", "type_identifier"
            )
            if ident is not None:
                names.append(node_text(data, ident))
        current = current.parent
    names.reverse()
    return names


def _qualified(data: bytes, node) -> str:
    """`A::f` as written, without template arguments (`Box<T>::f` is `Box::f`)."""

    if node.type == "template_type":
        return _qualified(data, node.children[0])
    if node.type == "qualified_identifier":
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
        tail = _qualified(data, name)
        if scope is None:
            return tail
        return f"{_qualified(data, scope)}::{tail}"
    return node_text(data, node)


def _declarator_name(data: bytes, node) -> str | None:
    declarator = node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")
    if declarator is None:
        return None
    name = declarator.child_by_field_name("declarator")
    if name is None:
        return None
    return _qualified(data, name)


def _lambda_variable(data: bytes, lam) -> tuple[str, object] | None:
    parent = lam.parent
    if parent.type == "init_declarator":
        ident = parent.child_by_field_name("declarator")
        if ident is None or ident.type != "identifier":
            return None
        return node_text(data, ident), parent.parent
    ident = child_of_type(parent, "field_identifier")
    if ident is None:
        return None
    return node_text(data, ident), parent


def _entry(node, name, namespace, body, path, data_node=None) -> Function:
    anchor = data_node or node
    return Function(
        name=name,
        namespace=namespace,
        complexity=complexity(body, _is_decision, _skip),
        start_line=start_line(anchor),
        end_line=end_line(anchor),
        path=path,
        language="cpp",
    )


def functions_in_source(
    source: str, path: str, source_root: str | None = None
) -> list[Function]:
    data, tree = parse(source, "cpp")
    module = _module_namespace(path, source_root)
    found: list[Function] = []
    for node in descendants(tree.root_node):
        if node.type == "function_definition":
            body = child_of_type(node, "compound_statement")
            name = _declarator_name(data, node)
            holder = node
        elif _is_named_lambda(node):
            variable = _lambda_variable(data, node)
            body = child_of_type(node, "compound_statement")
            if variable is None:
                continue
            name, holder = variable
        else:
            continue
        if body is None or not name:
            continue
        types = _enclosing_names(data, node, _TYPE_SCOPES)
        spaces = _enclosing_names(data, node, {"namespace_definition"})
        member = node.type == "function_definition" or node.parent.type == "field_declaration"
        if member and "::" not in name:
            name = "::".join([*types, name])
        name = "::".join([*spaces, name])
        found.append(_entry(node, name, module, body, path, holder))
    return found
