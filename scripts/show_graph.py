"""Print the agent graph as a Mermaid diagram (paste it into Markdown; GitHub draws it).

    uv run python scripts/show_graph.py

No database, LLM or .env needed: it only builds the graph and describes its nodes and edges.
"""

from querymind.agent.graph import build_graph


def main() -> None:
    print(build_graph().get_graph().draw_mermaid())


if __name__ == "__main__":
    main()
