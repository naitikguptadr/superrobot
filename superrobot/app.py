"""Textual App class — layout and key bindings."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.widgets import Footer, Header, Static

from superrobot import __version__
from superrobot.dr.cli_wrapper import DrCliWrapper
from superrobot.dr.copilot import stream_copilot
from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.analyzer import analyze
from superrobot.pipeline.config_generator import apply_fix, generate_config, render_files
from superrobot.pipeline.deployer import deploy
from superrobot.pipeline.evaluator import run_eval
from superrobot.pipeline.scanner import build_graph_nodes, scan
from superrobot.pipeline.ui_generator import generate_ui_component
from superrobot.post_deploy import (
    apply_readme_badge,
    generate_readme_badge_diff,
    get_git_remote_url,
)
from superrobot.tui.config_edit_modal import ConfigEditModal
from superrobot.tui.config_panel import CONFIG_TABS, ConfigPanel
from superrobot.tui.copilot_panel import CopilotPanel
from superrobot.tui.eval_panel import EvalPanel
from superrobot.tui.graph_panel import GraphPanel
from superrobot.tui.greenfield_wizard import GreenfieldSpec, GreenfieldWizard
from superrobot.tui.pipeline_panel import PipelinePanel
from superrobot.tui.readme_badge_modal import ReadmeBadgeModal
from superrobot.tui.template_browser import DrTemplate, TemplateBrowser, parse_templates_list
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
        Binding("a", "apply_copilot_fix", "Apply Fix"),
        Binding("y", "confirm_badge", "Badge"),
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
        self._last_copilot_suggestion: str = ""
        self._pending_badge: tuple[str, str] | None = None
        self._active_config_tab = CONFIG_TABS[0]
        self._cli = DrCliWrapper()

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
        if self.mode == "import" and self.repo_path:
            pipeline.set_step_status(0, "active")
            self.run_scan()
        elif self.mode == "greenfield":
            self._configure_greenfield_pipeline()
            self.push_screen(GreenfieldWizard(), self._on_greenfield_spec)
        elif self.mode == "template":
            self._configure_template_pipeline()
            self.run_template_list()

    def _configure_greenfield_pipeline(self) -> None:
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(0, "done")
        pipeline.set_step_status(1, "done")
        pipeline.current_step = 2

    def _configure_template_pipeline(self) -> None:
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(0, "done")
        pipeline.set_step_status(1, "done")
        pipeline.current_step = 2

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

        self.run_copilot("scan", result.model_dump())
        self.run_analyze()

    @work(exclusive=True)
    async def run_analyze(self) -> None:
        if self.mode == "greenfield" and self._analysis_result:
            return
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

        self.run_copilot("analyze", result.model_dump())

    def _on_greenfield_spec(self, spec: GreenfieldSpec | None) -> None:
        if not spec:
            self.exit()
            return
        self._analysis_result = AnalysisResult(
            agent_purpose=spec.agent_purpose,
            dr_framework=DrFramework(spec.framework),
            input_schema={"query": "str"},
            output_schema={"response": "str"},
            suggested_ui_components=["TextInput", "Card"],
            confidence=1.0,
        )
        self._scan_result = ScanResult(
            detected_framework=spec.framework,
            dependencies=spec.tools,
            confidence=1.0,
            repo_path=str(Path.cwd()),
        )
        copilot = self.query_one(CopilotPanel)
        copilot.log_message(f"Greenfield: {spec.agent_purpose}")
        self.run_scaffold_agent()

    @work(exclusive=True)
    async def run_scaffold_agent(self) -> None:
        result = await self._cli.component_add_agent(cwd=self.output_dir)
        copilot = self.query_one(CopilotPanel)
        if result.ok:
            copilot.log_message("[green]Agent scaffolded via dr component add[/]")
        else:
            copilot.log_message(f"[yellow]Scaffold: {result.stderr or result.stdout}[/]")
        self.run_generate()

    @work(exclusive=True)
    async def run_template_list(self) -> None:
        result = await self._cli.templates_list()
        templates = parse_templates_list(result.stdout)
        self.call_from_thread(self._show_template_browser, templates)

    def _show_template_browser(self, templates: list[DrTemplate]) -> None:
        self.push_screen(TemplateBrowser(templates), self._on_template_selected)

    def _on_template_selected(self, template: DrTemplate | None) -> None:
        if not template:
            self.exit()
            return
        self.run_template_clone(template.name)

    @work(exclusive=True)
    async def run_template_clone(self, template_name: str) -> None:
        dest = str(Path(self.output_dir).resolve())
        Path(dest).mkdir(parents=True, exist_ok=True)
        result = await self._cli.templates_clone(template_name, dest)
        copilot = self.query_one(CopilotPanel)
        if result.ok:
            self.repo_path = dest
            copilot.log_message(f"[green]Cloned template: {template_name}[/]")
            self.run_scan()
        else:
            copilot.log_message(f"[red]Clone failed: {result.stderr}[/]")

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
        self.run_copilot("generate", {"files": list(files.keys())})

    def _on_ui_builder_result(self, description: str | None) -> None:
        if description and self._analysis_result:
            self.run_ui_generate(description)
        else:
            pipeline = self.query_one(PipelinePanel)
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
            self.query_one(CopilotPanel).log_message(f"[error]UI generation failed: {exc}[/]")

    def _on_ui_generate_complete(self, tsx: str) -> None:
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_status(3, "done")
        pipeline.advance()
        self.query_one(CopilotPanel).log_message("dr-ui component generated")
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

        self.query_one(EvalPanel).set_results(summary)
        copilot = self.query_one(CopilotPanel)
        copilot.log_message(f"Eval: {summary.passed}/{summary.total} passed")
        self.run_copilot("evaluate", summary.model_dump())

    @work(exclusive=True)
    async def run_deploy_stage(self) -> None:
        has_ui = "ui/component.tsx" in self._generated_files
        copilot = self.query_one(CopilotPanel)
        result = await deploy(cwd=self.output_dir, has_ui=has_ui)
        for warning in result.warnings or []:
            copilot.log_message(f"[yellow]{warning}[/]")
        self._on_deploy_complete(result)

    def _on_deploy_complete(self, result: object) -> None:
        from superrobot.pipeline.deployer import DeployResult

        assert isinstance(result, DeployResult)
        pipeline = self.query_one(PipelinePanel)
        copilot = self.query_one(CopilotPanel)
        if result.success:
            pipeline.set_step_status(5, "done")
            copilot.log_message("[green]Deploy succeeded[/]")
            self._offer_readme_badge()
        else:
            pipeline.set_step_status(5, "failed")
            copilot.log_message(f"[red]Deploy failed: {result.error_message}[/]")
            self.run_copilot("deploy", {"error": result.error_message, "stderr": result.stderr})

    def _offer_readme_badge(self) -> None:
        repo = Path(self.repo_path or self.output_dir)
        readme = repo / "README.md"
        env_vars = self._scan_result.env_vars if self._scan_result else []
        remote = get_git_remote_url(repo) or ""
        original, proposed = generate_readme_badge_diff(readme, env_vars, remote)
        if original != proposed:
            self._pending_badge = (str(readme), proposed)
            preview = f"--- README.md\n+++ README.md\n{proposed[-500:]}"
            self.push_screen(ReadmeBadgeModal(preview))

    def action_confirm_badge(self) -> None:
        if self._pending_badge:
            path, proposed = self._pending_badge
            apply_readme_badge(Path(path), proposed)
            self.query_one(CopilotPanel).log_message("[green]README badge written[/]")
            self._pending_badge = None

    @work(exclusive=True)
    async def run_copilot(self, stage: str, context: dict[str, object]) -> None:
        copilot = self.query_one(CopilotPanel)
        copilot.log_message(f"[dim]Copilot ({stage})...[/]")
        parts: list[str] = []
        try:
            async for chunk in stream_copilot(stage, context):
                parts.append(chunk)
                copilot.log_stream_chunk(chunk)
        except Exception as exc:
            copilot.log_message(f"[dim]Copilot unavailable: {exc}[/]")
            return
        self._last_copilot_suggestion = "".join(parts)

    def action_apply_copilot_fix(self) -> None:
        if not self._last_copilot_suggestion or "[FIX]:" not in self._last_copilot_suggestion:
            self.query_one(CopilotPanel).log_message("[yellow]No [FIX]: suggestion to apply[/]")
            return
        if not self._scan_result or not self._analysis_result:
            return
        config = generate_config(self._scan_result, self._analysis_result)
        files = apply_fix(self._last_copilot_suggestion, config)
        self._generated_files = files
        self.query_one(ConfigPanel).set_files(files)
        self.query_one(CopilotPanel).log_message("[green]Fix applied — review config panel[/]")

    def action_re_analyze(self) -> None:
        if self._scan_result:
            self.run_analyze()

    def action_edit_config(self) -> None:
        if not self._generated_files:
            return
        tab = self._active_config_tab
        key_map = {
            "workflow.yaml": "agent/agent/workflow.yaml",
            "myagent.py": "agent/agent/myagent.py",
            "pyproject.toml": "pyproject.toml",
            ".env.template": ".env.template",
        }
        file_key = key_map.get(tab, "")
        content = self._generated_files.get(file_key, "")
        if not content:
            return
        self.push_screen(ConfigEditModal(tab, content), self._on_config_edited)

    def _on_config_edited(self, new_content: str | None) -> None:
        if new_content is None:
            return
        key_map = {
            "workflow.yaml": "agent/agent/workflow.yaml",
            "myagent.py": "agent/agent/myagent.py",
            "pyproject.toml": "pyproject.toml",
            ".env.template": ".env.template",
        }
        file_key = key_map.get(self._active_config_tab, "")
        if file_key:
            self._generated_files[file_key] = new_content
            self.query_one(ConfigPanel).set_files(self._generated_files)

    def action_skip_eval(self) -> None:
        self.skip_eval = True
        self.query_one(CopilotPanel).log_message("Eval skipped")

    def action_build_ui(self) -> None:
        self.push_screen(UIBuilderModal(), self._on_ui_builder_result)
