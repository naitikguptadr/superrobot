"""Textual App class — layout and key bindings."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.widgets import Footer, Header, Static

from superrobot import __version__
from superrobot.models.analysis_result import AnalysisResult
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.analyzer import analyze
from superrobot.pipeline.config_generator import generate_config, render_files
from superrobot.pipeline.deployer import deploy
from superrobot.pipeline.evaluator import run_eval
from superrobot.pipeline.scanner import build_graph_nodes, scan
from superrobot.pipeline.ui_generator import generate_ui_component
from superrobot.tui.config_panel import ConfigPanel
from superrobot.tui.copilot_panel import CopilotPanel
from superrobot.tui.eval_panel import EvalPanel
from superrobot.tui.graph_panel import GraphPanel
from superrobot.tui.pipeline_panel import PipelinePanel
from superrobot.tui.ui_builder_modal import UIBuilderModal


class SuperRobotApp(App[None]):
    """Main SuperRobot TUI application."""

    CSS_PATH = "tui/app.css"
    TITLE = "SuperRobot"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "continue_pipeline", "Continue"),
        Binding("e", "edit_config", "Edit"),
        Binding("r", "re_analyze", "Re-analyze"),
        Binding("u", "build_ui", "Build UI"),
        Binding("s", "skip_eval", "Skip Eval"),
    ]

    def __init__(
        self,
        repo_path: str | None = None,
        mode: str = "import",
        skip_eval: bool = False,
        output_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.repo_path = repo_path
        self.mode = mode
        self.skip_eval = skip_eval
        self.output_dir = output_dir or (str(Path(repo_path) / ".superrobot") if repo_path else ".")
        self._scan_result: ScanResult | None = None
        self._analysis_result: AnalysisResult | None = None
        self._generated_files: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        repo_label = self.repo_path or f"mode: {self.mode}"
        yield Header()
        yield Static(f"SUPERROBOT v{__version__}  |  {repo_label}", id="header-bar")
        with Grid(id="main-grid"):
            yield PipelinePanel(id="pipeline-panel")
            yield GraphPanel(id="graph-panel")
            yield CopilotPanel(id="copilot-panel")
            yield ConfigPanel(id="config-panel")
        yield EvalPanel(id="eval-panel")
        yield Footer()

    def on_mount(self) -> None:
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(0, "active")
        if self.mode == "import" and self.repo_path:
            self.run_scan()

    @work(thread=True)
    def run_scan(self) -> None:
        if not self.repo_path:
            return
        result = scan(self.repo_path)
        self.call_from_thread(self._on_scan_complete, result)

    def _on_scan_complete(self, result: ScanResult) -> None:
        self._scan_result = result
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(0, "done")
        pipeline.advance()

        graph = self.query_one(GraphPanel)
        nodes = build_graph_nodes(self.repo_path or ".")
        graph.set_graph(nodes)

        copilot = self.query_one(CopilotPanel)
        copilot.log_message(
            f"Scan complete: {result.detected_framework} (confidence {result.confidence:.2f})"
        )
        if result.risk_flags:
            copilot.log_message(f"Risks: {', '.join(f.value for f in result.risk_flags)}")

        self.run_analyze()

    @work(exclusive=True)
    async def run_analyze(self) -> None:
        if not self._scan_result:
            return
        result = await analyze(self._scan_result)
        self._on_analyze_complete(result)

    def _on_analyze_complete(self, result: AnalysisResult) -> None:
        self._analysis_result = result
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(1, "done")

        copilot = self.query_one(CopilotPanel)
        copilot.log_message(f"Purpose: {result.agent_purpose}")
        copilot.log_message(f"DR framework: {result.dr_framework.value}")

        if result.confidence < 0.6:
            copilot.log_message("[warning]Low confidence — confirm framework before generating[/]")

    def action_continue_pipeline(self) -> None:
        pipeline = self.query_one(PipelinePanel)
        step = pipeline.current_step
        if step == 1:
            self.run_generate()
        elif step == 2:
            pipeline.advance()
        elif step == 3:
            self.push_screen(UIBuilderModal(), self._on_ui_builder_result)
        elif step == 4:
            if self.skip_eval:
                pipeline.set_step_status(4, "done")
                pipeline.advance()
            else:
                self.run_eval_stage()
        elif step == 5:
            self.run_deploy_stage()

    @work(thread=True)
    def run_generate(self) -> None:
        if not self._scan_result or not self._analysis_result:
            return
        config = generate_config(self._scan_result, self._analysis_result)
        files = render_files(config)
        self.call_from_thread(self._on_generate_complete, files)

    def _on_generate_complete(self, files: dict[str, str]) -> None:
        self._generated_files = files
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(2, "done")
        pipeline.advance()

        config_panel = self.query_one(ConfigPanel)
        config_panel.set_files(files)

        copilot = self.query_one(CopilotPanel)
        copilot.log_message(f"Generated {len(files)} config files")

    def _on_ui_builder_result(self, description: str | None) -> None:
        pipeline = self.query_one(PipelinePanel)
        if description and self._analysis_result:
            self.run_ui_generate(description)
        else:
            pipeline.set_step_status(3, "done")
            pipeline.advance()

    @work(exclusive=True)
    async def run_ui_generate(self, description: str) -> None:
        if not self._analysis_result:
            return
        try:
            tsx = await generate_ui_component(description, self._analysis_result)
            self._on_ui_generate_complete(tsx)
        except Exception as exc:
            copilot = self.query_one(CopilotPanel)
            copilot.log_message(f"[error]UI generation failed: {exc}[/]")

    def _on_ui_generate_complete(self, tsx: str) -> None:
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(3, "done")
        pipeline.advance()
        copilot = self.query_one(CopilotPanel)
        copilot.log_message("dr-ui component generated")
        self._generated_files["ui/component.tsx"] = tsx

        graph = self.query_one(GraphPanel)
        nodes = list(graph.nodes)
        nodes.append({"id": "ui_component", "label": "UI", "type": "ui"})
        graph.set_graph(nodes)

    @work(exclusive=True)
    async def run_eval_stage(self) -> None:
        if not self._analysis_result:
            return
        summary = await run_eval(self._analysis_result, cwd=self.output_dir)
        self._on_eval_complete(summary)

    def _on_eval_complete(self, summary: object) -> None:
        from superrobot.models.eval_result import EvalSummary

        assert isinstance(summary, EvalSummary)
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(4, "done")
        pipeline.advance()

        eval_panel = self.query_one(EvalPanel)
        eval_panel.set_results(summary)

        copilot = self.query_one(CopilotPanel)
        copilot.log_message(f"Eval: {summary.passed}/{summary.total} passed")

    @work(exclusive=True)
    async def run_deploy_stage(self) -> None:
        has_ui = "ui/component.tsx" in self._generated_files
        result = await deploy(cwd=self.output_dir, has_ui=has_ui)
        self._on_deploy_complete(result)

    def _on_deploy_complete(self, result: object) -> None:
        from superrobot.pipeline.deployer import DeployResult

        assert isinstance(result, DeployResult)
        pipeline = self.query_one(PipelinePanel)
        if result.success:
            pipeline.set_step_status(5, "done")
            self.query_one(CopilotPanel).log_message("[green]Deploy succeeded[/]")
        else:
            pipeline.set_step_status(5, "failed")
            copilot = self.query_one(CopilotPanel)
            copilot.log_message(f"[red]Deploy failed: {result.error_message}[/]")
            for warning in result.warnings or []:
                copilot.log_message(f"[yellow]{warning}[/]")

    def action_re_analyze(self) -> None:
        if self._scan_result:
            self.run_analyze()

    def action_edit_config(self) -> None:
        self.query_one(CopilotPanel).log_message("Press [enter] after editing in config panel")

    def action_skip_eval(self) -> None:
        self.skip_eval = True
        self.query_one(CopilotPanel).log_message("Eval skipped")

    def action_build_ui(self) -> None:
        self.push_screen(UIBuilderModal(), self._on_ui_builder_result)
