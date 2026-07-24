"""Tool registration sync check — every @tool in tools/ must appear in get_tools().

# See docs: "Tools" — tool types, @tool decorator, tool registration

These tests catch two classes of silent mistakes that are easy to introduce
when adding or refactoring tools:

1. **Missing registration** — a developer adds a new ``@tool`` function to a
   module in ``src/yeaboi/tools/`` but forgets to add it to the ``return``
   list in ``get_tools()``. The function exists, is importable, but is never
   bound to the LLM and can therefore never be called by the agent.

2. **Duplicate registration** — a tool is listed twice in ``get_tools()``,
   causing the LLM to see two identical tool schemas. This is harmless in most
   LLM implementations but can confuse tool-dispatch logic and wastes tokens.

Discovery strategy — AST scanning (no import)
----------------------------------------------
The scanner reads each ``.py`` source file in ``src/yeaboi/tools/``
(excluding ``__init__.py``) and walks the AST to find ``FunctionDef`` nodes
whose decorator list contains ``@tool`` or ``@tool(...)`` (decorator-factory
form). This approach has two advantages over reflection-based discovery:

- No side effects — the tool modules are not executed; no real API clients
  are constructed, no environment variables are consumed.
- Import-order independence — the scan is purely structural and will not
  fail if module-level code in a tool file raises at import time.

The ``@tool`` decorator is recognised in three syntactic forms:
  ``@tool``             → ast.Name(id='tool')
  ``@langchain.tool``   → ast.Attribute(attr='tool')
  ``@tool(...)``        → ast.Call(func=ast.Name(id='tool'))
"""

import ast
import pathlib

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TOOLS_SRC = pathlib.Path(__file__).parent.parent.parent.parent / "src" / "yeaboi" / "tools"


def _is_tool_decorator(node: ast.expr) -> bool:
    """Return True if *node* is a ``@tool`` decorator in any of its three forms."""
    # @tool  (bare name)
    if isinstance(node, ast.Name):
        return node.id == "tool"
    # @module.tool  (attribute access, e.g. @langchain_core.tools.tool)
    if isinstance(node, ast.Attribute):
        return node.attr == "tool"
    # @tool(...)  (decorator factory called with arguments)
    if isinstance(node, ast.Call):
        return _is_tool_decorator(node.func)
    return False


def _scan_module_for_tools(path: pathlib.Path) -> list[str]:
    """Return the names of all ``@tool``-decorated functions in *path* via AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and any(_is_tool_decorator(d) for d in node.decorator_list)
    ]


def _discover_all_tools() -> dict[str, str]:
    """Scan every non-__init__ module in the tools package.

    Returns a dict mapping ``tool_function_name → source_file_stem`` so that
    assertion failure messages can point the developer to the right file.
    """
    discovered: dict[str, str] = {}
    for path in sorted(_TOOLS_SRC.glob("*.py")):
        if path.name == "__init__.py":
            continue
        for name in _scan_module_for_tools(path):
            discovered[name] = path.stem
    return discovered


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolRegistrySync:
    """Ensure get_tools() stays in sync with @tool-decorated functions in tools/.

    # See docs: "Tools" — tool registration pattern
    #
    # The contract: every @tool function in a tools module must appear in
    # get_tools(). This is not enforced by Python or LangChain — it relies
    # entirely on the developer remembering to add the import and list entry
    # in __init__.py. These tests make that contract machine-checkable.
    """

    def test_every_decorated_tool_is_registered(self):
        """All @tool functions discovered by AST scan appear in get_tools()."""
        from yeaboi.tools import get_tools

        registered_names = {t.name for t in get_tools()}
        discovered = _discover_all_tools()

        missing = {
            f"{name} (in tools/{module}.py)" for name, module in discovered.items() if name not in registered_names
        }
        assert not missing, (
            "The following @tool functions are defined but not registered in get_tools():\n"
            + "\n".join(f"  - {item}" for item in sorted(missing))
        )

    def test_no_tool_registered_twice(self):
        """get_tools() contains no duplicate tool names."""
        from yeaboi.tools import get_tools

        tools = get_tools()
        names = [t.name for t in tools]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for name in names:
            if name in seen:
                duplicates.add(name)
            seen.add(name)

        assert not duplicates, "The following tools are registered more than once in get_tools():\n" + "\n".join(
            f"  - {name}" for name in sorted(duplicates)
        )

    def test_no_extra_tools_registered_outside_modules(self):
        """get_tools() contains no names absent from the tools package source files.

        Catches the inverse mistake: a tool name added to the return list in
        get_tools() that references a function imported from *outside* the tools
        package (or that no longer exists after a rename).
        """
        from yeaboi.tools import get_tools

        registered_names = {t.name for t in get_tools()}
        discovered_names = set(_discover_all_tools().keys())

        phantom = registered_names - discovered_names
        assert not phantom, (
            "The following names are registered in get_tools() but not found as "
            "@tool functions in src/yeaboi/tools/:\n" + "\n".join(f"  - {name}" for name in sorted(phantom))
        )

    def test_tool_count_matches_between_scan_and_registry(self):
        """The number of @tool functions equals the number of entries in get_tools().

        A quick sanity check: if the two sets are equal in size AND
        test_every_decorated_tool_is_registered / test_no_extra_tools_registered
        both pass, the registry is an exact mirror of the source files.
        """
        from yeaboi.tools import get_tools

        registered_count = len(get_tools())
        discovered_count = len(_discover_all_tools())

        assert registered_count == discovered_count, (
            f"Tool count mismatch: {discovered_count} @tool functions in source files "
            f"but {registered_count} entries in get_tools()."
        )
