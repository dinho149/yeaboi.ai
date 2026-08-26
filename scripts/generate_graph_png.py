"""Generate a PNG visualisation of the current LangGraph agent graph.

# See README: "Agentic Blueprint Reference" — Agent Graph
#
# LangGraph's compiled graphs expose .get_graph() which returns a DrawableGraph.
# DrawableGraph has .draw_mermaid_png() which calls the Mermaid.ink API to render
# a PNG from the graph topology. This requires no additional dependencies — it
# uses an HTTP request to mermaid.ink under the hood.
#
# Usage:
#   make graph
#   # or directly:
#   uv run python scripts/generate_graph_png.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ is not a package, and these modules are also loaded by path in tests,
# where sys.path[0] is not this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _sibling_repos import site_root  # noqa: E402

from yeaboi.agent.graph import create_graph  # noqa: E402


def main() -> None:
    """Build the agent graph and save a PNG visualisation into the website."""
    graph = create_graph()

    # get_graph() returns a DrawableGraph — a lightweight representation of
    # the graph topology (nodes + edges) that can be rendered in various formats.
    # draw_mermaid_png() converts it to Mermaid markup, sends it to the
    # mermaid.ink rendering API, and returns raw PNG bytes.
    # See README: "Agentic Blueprint Reference" — graph visualisation
    png_bytes = graph.get_graph().draw_mermaid_png()

    # Drawn from this repo's code, served by the website: written into a
    # yeaboi-site checkout. See scripts/_site_repo.py.
    output_path = site_root() / "graph.png"
    output_path.write_bytes(png_bytes)

    print(f"Graph PNG saved to {output_path}")


if __name__ == "__main__":
    main()
