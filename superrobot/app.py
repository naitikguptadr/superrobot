"""Textual App class — layout and key bindings."""

from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.widgets import Footer, Static

from superrobot import __version__
from superrobot.dr.cli_wrapper import DrCliWrapper
from superrobot.dr.copilot import stream_copilot
from superrobot.models.analysis_result import AnalysisResult, DrFramework
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.analyzer import analyze
from superrobot.pipeline.config_generator import (
    apply_fix,
    generate_config,
    render_files,
    write_generated_files,
)
from superrobot.pipeline.deployer import deploy
from superrobot.pipeline.evaluator import run_eval
from superrobot.pipeline.scanner import build_graph, scan
from superrobot.pipeline.ui_generator import generate_ui_component
from superrobot.post_deploy import (
    apply_readme_badge,
    generate_readme_badge_diff,
    get_git_remote_url,
)
from superrobot.tui.config_panel import ConfigPanel
from superrobot.tui.copilot_panel import CopilotPanel
from superrobot.tui.eval_panel import EvalPanel
from superrobot.tui.graph_panel import GraphPanel
from superrobot.tui.greenfield_wizard import GreenfieldSpec, GreenfieldWizard
from superrobot.tui.pipeline_panel import PipelinePanel
from superrobot.tui.readme_badge_modal import ReadmeBadgeModal
from superrobot.tui.template_browser import DrTemplate, TemplateBrowser, parse_templates_list
from superrobot.tui.ui_builder_modal import UIBuilderModal

_APP_CSS = Path(__file__).parent / "tui" / "app.css"


class SuperRobotApp(App[None]):
    """Main SuperRobot TUI application."""

    CSS_PATH = str(_APP_CSS)
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
        Binding("o", "open_ui_preview", "UI Preview", show=False),
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
        self._ui_preview_path: Path | None = None
        self._action_text = ""
        self._action_working = False
        self._action_frame = 0
        self._cli = DrCliWrapper()

    def compose(self) -> ComposeResult:
        import os

        from superrobot.setup.constants import endpoint_label

        repo_label = self.repo_path or f"mode: {self.mode}"
        env_name = endpoint_label(os.environ.get("DATAROBOT_ENDPOINT", "")).upper()
        model = os.environ.get("SUPERROBOT_MODEL", "")
        yield Static(
            f"[b] SUPERROBOT[/b] [dim]v{__version__}[/]"
            f"   {repo_label}"
            f"   [dim]·[/]   [b]{env_name}[/] [dim]{model}[/]",
            id="header-bar",
        )
        yield Static("", id="action-bar")
        with Grid(id="main-grid"):
            yield PipelinePanel(id="pipeline-panel")
            yield GraphPanel(id="graph-panel")
            yield CopilotPanel(id="copilot-panel")
            yield ConfigPanel(id="config-panel")
        yield EvalPanel(id="eval-panel")
        yield Footer()

    def set_action(self, text: str, *, working: bool = False) -> None:
        """Update the always-visible 'what happens next' bar."""
        self._action_text = text
        self._action_working = working
        self.query_one(PipelinePanel).working = working
        self._render_action_bar()

    def _render_action_bar(self) -> None:
        from superrobot.tui.pipeline_panel import SPINNER_FRAMES

        bar = self.query_one("#action-bar", Static)
        if self._action_working:
            frame = SPINNER_FRAMES[self._action_frame % len(SPINNER_FRAMES)]
            bar.update(f" [bold $accent]{frame}[/] {self._action_text}")
            bar.set_classes("action-working")
        else:
            bar.update(f" [b]▶ NEXT[/]  {self._action_text}")
            bar.set_classes("action-ready")

    def _animate_action_bar(self) -> None:
        if self._action_working:
            self._action_frame += 1
            self._render_action_bar()

    def on_mount(self) -> None:
        self.set_interval(0.12, self._animate_action_bar)
        pipeline = self.query_one(PipelinePanel)
        if self.mode == "import" and self.repo_path:
            from superrobot.tui.splash_screen import SplashScreen

            splash = SplashScreen(repo_label=Path(self.repo_path).name)
            self.push_screen(splash)
            self.set_timer(2.2, lambda: self._dismiss_splash(splash))
            pipeline.set_step_status(0, "active")
            self.set_action("Scanning repository — no LLM, takes about a second…", working=True)
            self.run_scan()
        elif self.mode == "greenfield":
            self._configure_greenfield_pipeline()
            self.set_action("Answer the three wizard questions", working=True)
            self.push_screen(GreenfieldWizard(), self._on_greenfield_spec)
        elif self.mode == "template":
            self._configure_template_pipeline()
            self.set_action("Pick a template from the list", working=True)
            self.run_template_list()

    def _dismiss_splash(self, splash: object) -> None:
        from superrobot.tui.splash_screen import SplashScreen

        if isinstance(splash, SplashScreen) and splash.is_current:
            splash.dismiss(None)

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
        pipeline.set_step_note(0, f"{result.detected_framework} · {result.confidence:.2f}")
        pipeline.complete_step(0)
        self.set_action("Analyzing with the DataRobot LLM Gateway…", working=True)

        graph = self.query_one(GraphPanel)
        nodes, edges = build_graph(self.repo_path or ".")
        graph.set_graph(nodes, edges)

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

        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_note(1, f"→ {result.dr_framework.value} · {result.confidence:.2f}")
        self.set_action("Press [b]enter[/] to generate the DR config files")

        copilot = self.query_one(CopilotPanel)
        copilot.log_message(f"Purpose: {result.agent_purpose}")
        copilot.log_message(f"DR framework: {result.dr_framework.value}")

        if result.confidence < 0.6:
            copilot.log_message("[yellow]Low confidence — confirm framework before generating[/]")

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
        # async worker runs on the app thread — call directly
        self._show_template_browser(templates)

    def _show_template_browser(self, templates: list[DrTemplate]) -> None:
        self.push_screen(TemplateBrowser(templates), self._on_template_selected)

    def _on_template_selected(self, template: DrTemplate | None) -> None:
        if not template:
            self.exit()
            return
        self.run_template_clone(template.template_id or template.name)

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
            copilot.log_message(f"[red]Clone failed: {result.stderr or result.stdout}[/]")
            copilot.log_message(
                "[yellow]Your dr CLI version may not support `templates clone` — "
                "run `dr templates setup` in a terminal instead.[/]"
            )

    def action_continue_pipeline(self) -> None:
        pipeline = self.query_one(PipelinePanel)
        step = pipeline.current_step
        if step == 1:
            self.set_action("Generating DR config files…", working=True)
            self.run_generate()
        elif step == 2:
            pipeline.advance()
        elif step == 3:
            self.set_action("Describe a component, or press escape to skip", working=True)
            self.push_screen(UIBuilderModal(), self._on_ui_builder_result)
        elif step == 4:
            if self.skip_eval:
                pipeline.complete_step(4)
                self._show_deploy_action()
            else:
                self.set_action("Running the 5-shot eval via dr run dev…", working=True)
                self.run_eval_stage()
        elif step == 5:
            self.set_action("Deploying via dr task run deploy — takes 15–20 min…", working=True)
            self.run_deploy_stage()

    def _show_deploy_action(self) -> None:
        self.set_action(
            "Press [b]enter[/] to deploy to DataRobot (15–20 min) — or [b]q[/] to quit "
            "with the generated files kept on disk"
        )

    @work(thread=True)
    def run_generate(self) -> None:
        if not self._scan_result or not self._analysis_result:
            return
        config = generate_config(self._scan_result, self._analysis_result)
        files = render_files(config)
        self.call_from_thread(self._on_generate_complete, files)

    def _on_generate_complete(self, files: dict[str, str]) -> None:
        self._generated_files = files
        write_generated_files(files, self.output_dir)
        pipeline = self.query_one(PipelinePanel)
        pipeline.complete_step(2)

        pipeline.set_step_note(2, f"{len(files)} files written")
        self.set_action(
            "Press [b]enter[/] to describe a dr-ui component — escape in the dialog skips it"
        )

        config_panel = self.query_one(ConfigPanel)
        config_panel.set_files(files)

        copilot = self.query_one(CopilotPanel)
        copilot.log_message(f"Generated {len(files)} config files → {self.output_dir}")
        self.run_copilot("generate", {"files": list(files.keys())})

    def _on_ui_builder_result(self, description: str | None) -> None:
        pipeline = self.query_one(PipelinePanel)
        if description and self._analysis_result:
            self.set_action("Generating dr-ui component…", working=True)
            self.run_ui_generate(description)
        else:
            pipeline.set_step_note(3, "skipped")
            pipeline.complete_step(3)
            self._show_eval_action()

    def _show_eval_action(self) -> None:
        self.set_action("Press [b]enter[/] to run the 5-shot pre-deploy eval — [b]s[/] skips it")

    @work(exclusive=True)
    async def run_ui_generate(self, description: str) -> None:
        if not self._analysis_result:
            return
        try:
            tsx = await generate_ui_component(description, self._analysis_result)
            self._on_ui_generate_complete(tsx)
        except Exception as exc:
            self.query_one(CopilotPanel).log_message(f"[red]UI generation failed: {exc}[/]")
            self._show_eval_action()

    def _on_ui_generate_complete(self, tsx: str) -> None:
        from superrobot.pipeline.ui_preview import write_preview

        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_note(3, "component.tsx")
        pipeline.complete_step(3)
        self._generated_files["ui/component.tsx"] = tsx
        write_generated_files(self._generated_files, self.output_dir)
        self._ui_preview_path = write_preview(tsx, self.output_dir)

        self.query_one(ConfigPanel).set_files(self._generated_files)
        self.set_action(
            "Component generated — press [b]o[/] to open the live preview in your browser, "
            "[b]enter[/] to continue to eval"
        )
        copilot = self.query_one(CopilotPanel)
        copilot.log_message("dr-ui component generated → component.tsx tab")
        copilot.log_message(f"[dim]Live preview: {self._ui_preview_path}[/]")

        graph = self.query_one(GraphPanel)
        nodes = list(graph.nodes)
        nodes.append({"id": "ui_component", "label": "UI", "type": "ui"})
        edges = list(graph.edges) + [("output", "ui_component")]
        graph.set_graph(nodes, edges)

    def action_open_ui_preview(self) -> None:
        if not self._ui_preview_path:
            self.notify("No dr-ui component generated yet — press u first", severity="warning")
            return
        import webbrowser

        webbrowser.open(f"file://{self._ui_preview_path.resolve()}")
        self.query_one(CopilotPanel).log_message("[green]Opened live preview in browser[/]")

    def _entry_info(self) -> tuple[str, str, list[str]] | None:
        """(flat_module, function, params) of the migrated entry point, if known."""
        if not self._scan_result or not self._scan_result.entry_points:
            return None
        from superrobot.models.agent_config import parse_signature_params
        from superrobot.pipeline.config_generator import flat_module_name

        ep = self._scan_result.entry_points[0]
        module = flat_module_name(self._scan_result.repo_path, ep.file)
        return (module, ep.function, parse_signature_params(ep.signature))

    @work(exclusive=True)
    async def run_eval_stage(self) -> None:
        if not self._analysis_result:
            return
        summary = await run_eval(
            self._analysis_result, cwd=self.output_dir, entry=self._entry_info()
        )
        self._on_eval_complete(summary)

    def _on_eval_complete(self, summary: object) -> None:
        from superrobot.models.eval_result import EvalSummary

        assert isinstance(summary, EvalSummary)
        pipeline = self.query_one(PipelinePanel)
        pipeline.set_step_note(4, f"{summary.passed}/{summary.total} passed")
        pipeline.complete_step(4)
        self._show_deploy_action()

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
            pipeline.set_step_note(5, "live")
            pipeline.set_step_status(5, "done")
            self.set_action("Deployed — press [b]q[/] to exit, or [b]y[/] to add a README badge")
            copilot.log_message("[green]Deploy succeeded[/]")
            self._offer_readme_badge()
        else:
            pipeline.set_step_status(5, "failed")
            self.set_action("Deploy failed — see copilot for the error, [b]enter[/] retries")
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
        copilot.log_stage(stage)
        copilot.begin_stream(f"Copilot ({stage})…")
        parts: list[str] = []
        try:
            async for chunk in stream_copilot(stage, context):
                parts.append(chunk)
                copilot.log_stream_chunk(chunk)
        except Exception as exc:
            copilot.end_stream()
            copilot.log_message(f"[dim]Copilot unavailable: {exc}[/]")
            return
        copilot.end_stream()
        self._last_copilot_suggestion = "".join(parts)

    def action_apply_copilot_fix(self) -> None:
        copilot = self.query_one(CopilotPanel)
        if not self._last_copilot_suggestion or "[FIX]:" not in self._last_copilot_suggestion:
            copilot.log_message("[yellow]No [FIX]: suggestion to apply[/]")
            return
        if not self._scan_result or not self._analysis_result:
            return
        config = generate_config(self._scan_result, self._analysis_result)
        files = apply_fix(self._last_copilot_suggestion, config)
        changed = sorted(name for name in files if files[name] != self._generated_files.get(name))
        if not changed:
            copilot.log_message(
                "[yellow]This suggestion targets your source repo — nothing to change in "
                "the generated config. Apply it manually in your agent code.[/]"
            )
            self.notify("Suggestion is manual — see copilot panel", severity="warning")
            return
        self._generated_files = files
        write_generated_files(files, self.output_dir)
        self.query_one(ConfigPanel).set_files(files)
        copilot.log_message(f"[green]✓ Fix applied — regenerated: {', '.join(changed)}[/]")
        self.notify(f"Fix applied to {len(changed)} file(s)")

    def action_re_analyze(self) -> None:
        if self._scan_result:
            self.run_analyze()

    def action_edit_config(self) -> None:
        """Focus the visible config tab's editor — edits happen in place."""
        if not self._generated_files:
            self.notify("Nothing to edit yet — run Generate first", severity="warning")
            return
        self.query_one(ConfigPanel).focus_active_editor()

    def on_config_panel_saved(self, message: ConfigPanel.Saved) -> None:
        """Persist in-pane config edits to disk."""
        self._generated_files.update(message.files)
        write_generated_files(message.files, self.output_dir)
        self.notify(f"Saved {len(message.files)} file(s) to {self.output_dir}")
        self.query_one(CopilotPanel).log_message(
            f"[green]✓ Config saved — {', '.join(sorted(message.files))}[/]"
        )

    def action_skip_eval(self) -> None:
        self.skip_eval = True
        pipeline = self.query_one(PipelinePanel)
        if pipeline.current_step == 4:
            pipeline.set_step_note(4, "skipped")
            pipeline.complete_step(4)
            self._show_deploy_action()
        self.query_one(CopilotPanel).log_message("Eval skipped")

    def action_build_ui(self) -> None:
        self.push_screen(UIBuilderModal(), self._on_ui_builder_result)
