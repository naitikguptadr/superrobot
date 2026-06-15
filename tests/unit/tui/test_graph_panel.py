"""Graph panel unit tests."""

from superrobot.tui.graph_panel import GraphPanel, _compute_depth, _render_dag, _topological_layers


def test_topological_layers_linear() -> None:
    nodes = [
        {"id": "a", "label": "A"},
        {"id": "b", "label": "B"},
        {"id": "c", "label": "C"},
    ]
    edges = [("a", "b"), ("b", "c")]
    layers = _topological_layers(nodes, edges)
    assert len(layers) == 3


def test_compute_depth() -> None:
    nodes = [{"id": "input", "label": "Input"}, {"id": "output", "label": "Output"}]
    edges = [("input", "output")]
    assert _compute_depth(nodes, edges) == 2


def test_render_dag_empty() -> None:
    assert "Scanning will populate" in _render_dag([], [])


def test_render_dag_with_nodes() -> None:
    nodes = [{"id": "input", "label": "Input", "type": "input"}]
    rendered = _render_dag(nodes, [])
    assert "Input" in rendered


def test_graph_panel_set_graph() -> None:
    panel = GraphPanel()
    nodes = [
        {"id": "input", "label": "Input", "type": "input"},
        {"id": "llm", "label": "LLM", "type": "llm_call"},
        {"id": "output", "label": "Output", "type": "output"},
    ]
    panel.set_graph(nodes)
    assert len(panel.nodes) == 3
    assert panel.stats.startswith("nodes: 3")
