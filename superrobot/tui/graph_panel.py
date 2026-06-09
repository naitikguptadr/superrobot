"""DAG renderer using Unicode box-drawing."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widgets import Static


class GraphNode:
    """A node in the agent execution graph."""

    def __init__(self, node_id: str, label: str, node_type: str = "llm_call") -> None:
        self.id = node_id
        self.label = label
        self.node_type = node_type


class GraphPanel(Static):
    """Renders agent DAG with topological layout."""

    nodes: reactive[list[dict[str, str]]] = reactive([])
    edges: reactive[list[tuple[str, str]]] = reactive([])
    stats: reactive[str] = reactive("nodes: 0  edges: 0  depth: 0")

    def compose(self) -> ComposeResult:
        yield Static(id="graph-content")
        yield Static(id="graph-stats")

    def on_mount(self) -> None:
        self._refresh()

    def set_graph(
        self,
        nodes: list[dict[str, str]],
        edges: list[tuple[str, str]] | None = None,
    ) -> None:
        self.nodes = nodes
        if edges is not None:
            self.edges = edges
        elif len(nodes) > 1:
            self.edges = [(nodes[i]["id"], nodes[i + 1]["id"]) for i in range(len(nodes) - 1)]
        else:
            self.edges = []
        depth = _compute_depth(nodes, self.edges)
        self.stats = f"nodes: {len(nodes)}  edges: {len(self.edges)}  depth: {depth}"

    def watch_nodes(self, _value: list[dict[str, str]]) -> None:
        self._refresh()

    def watch_edges(self, _value: list[tuple[str, str]]) -> None:
        self._refresh()

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        rendered = _render_dag(self.nodes, self.edges)
        self.query_one("#graph-content", Static).update(rendered)
        self.query_one("#graph-stats", Static).update(self.stats)


def _compute_depth(nodes: list[dict[str, str]], edges: list[tuple[str, str]]) -> int:
    if not nodes:
        return 0
    layers = _topological_layers(nodes, edges)
    return len(layers)


def _topological_layers(
    nodes: list[dict[str, str]],
    edges: list[tuple[str, str]],
) -> list[list[str]]:
    ids = [n["id"] for n in nodes]
    in_degree = dict.fromkeys(ids, 0)
    adj: dict[str, list[str]] = {nid: [] for nid in ids}
    for src, dst in edges:
        if src in adj and dst in in_degree:
            adj[src].append(dst)
            in_degree[dst] += 1

    layers: list[list[str]] = []
    current = [nid for nid, deg in in_degree.items() if deg == 0]
    while current:
        layers.append(current)
        next_layer: list[str] = []
        for nid in current:
            for neighbor in adj.get(nid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_layer.append(neighbor)
        current = next_layer
    return layers


def _render_dag(nodes: list[dict[str, str]], edges: list[tuple[str, str]]) -> str:
    if not nodes:
        return "[dim]No graph data[/]"

    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    layers = _topological_layers(nodes, edges)

    lines: list[str] = []
    for layer in layers:
        boxes = [f"┌{'─' * 10}┐" for _ in layer]
        labels = [f"│{label_map.get(nid, nid)[:10]:^10}│" for nid in layer]
        bottoms = [f"└{'─' * 10}┘" for _ in layer]

        if len(layer) == 1:
            lines.extend([boxes[0], labels[0], bottoms[0], "     │", "     ▼"])
        else:
            row = "    ".join(f"{labels[i]}" for i in range(len(layer)))
            lines.append(row)
            lines.append("     │" + " " * 20)
            lines.append("     ▼")

    return "\n".join(lines)
