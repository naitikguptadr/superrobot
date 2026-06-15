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


ARROW_FRAMES = ("─▶", "╌▶", "┄▶", "╌▶")


class GraphPanel(Static):
    """Renders agent DAG with topological layout."""

    nodes: reactive[list[dict[str, str]]] = reactive([])
    edges: reactive[list[tuple[str, str]]] = reactive([])
    stats: reactive[str] = reactive("nodes: 0  edges: 0  depth: 0")

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._frame = 0

    def compose(self) -> ComposeResult:
        yield Static(id="graph-content")
        yield Static(id="graph-stats")

    def on_mount(self) -> None:
        self.border_title = "AGENT GRAPH"
        self.set_interval(0.25, self._tick)
        self._refresh()

    def _tick(self) -> None:
        if self.nodes:
            self._frame = (self._frame + 1) % len(ARROW_FRAMES)
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

    def watch_stats(self, _value: str) -> None:
        self._refresh()

    def _refresh(self) -> None:
        if not self.is_mounted:
            return
        arrow = ARROW_FRAMES[self._frame]
        rendered = _render_dag(self.nodes, self.edges, arrow=arrow)
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


_TYPE_STYLES = {
    "input": "$secondary",
    "output": "$secondary",
    "llm_call": "$accent",
    "tool": "$warning",
    "router": "$primary",
    "memory_read": "$success",
    "memory_write": "$success",
    "ui": "$success",
}

_MAX_LABEL = 24


def _render_dag(
    nodes: list[dict[str, str]],
    edges: list[tuple[str, str]],
    arrow: str = "─▶",
) -> str:
    """Render the DAG horizontally: layers are columns flowing left → right."""
    if not nodes:
        return "[dim]Scanning will populate the agent graph…[/]"

    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    type_map = {n["id"]: n.get("type", "llm_call") for n in nodes}
    layers = _topological_layers(nodes, edges)
    rendered_ids = {nid for layer in layers for nid in layer}
    # cycles or disconnected nodes still deserve a box
    leftovers = [n["id"] for n in nodes if n["id"] not in rendered_ids]
    layers.extend([nid] for nid in leftovers)
    if not layers:
        layers = [[n["id"]] for n in nodes]

    def box_lines(nid: str) -> list[str]:
        label = label_map.get(nid, nid)[:_MAX_LABEL]
        style = _TYPE_STYLES.get(type_map.get(nid, ""), "$accent")
        width = len(label)
        return [
            f"[{style}]╭{'─' * (width + 2)}╮[/]",
            f"[{style}]│ {label} │[/]",
            f"[{style}]╰{'─' * (width + 2)}╯[/]",
        ]

    def plain_width(nid: str) -> int:
        return len(label_map.get(nid, nid)[:_MAX_LABEL]) + 4

    # Build each column: nodes in a layer stack vertically inside the column.
    columns: list[list[str]] = []
    column_widths: list[int] = []
    for layer in layers:
        col_width = max(plain_width(nid) for nid in layer)
        col_lines: list[str] = []
        for i, nid in enumerate(layer):
            pad = col_width - plain_width(nid)
            col_lines.extend(line + " " * pad for line in box_lines(nid))
            if i < len(layer) - 1:
                col_lines.append(" " * col_width)
        columns.append(col_lines)
        column_widths.append(col_width)

    height = max(len(col) for col in columns)
    # vertically centre every column
    for idx, col in enumerate(columns):
        missing = height - len(col)
        top = missing // 2
        blank = " " * column_widths[idx]
        col[:0] = [blank] * top
        col.extend([blank] * (missing - top))

    arrow_row = height // 2  # connector sits at the vertical centre
    lines = []
    for row in range(height):
        parts = []
        for idx, col in enumerate(columns):
            cell = col[row] if row < len(col) else " " * column_widths[idx]
            parts.append(cell)
            if idx < len(columns) - 1:
                parts.append(f"[$accent]{arrow}[/]" if row == arrow_row else "  ")
        lines.append("".join(parts))

    return "\n".join(lines)
