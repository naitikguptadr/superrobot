"""Brownfield transform engine — orchestrates Scan → Deploy."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from superrobot.engine.context import TransformContext
from superrobot.models.analysis_result import AnalysisResult
from superrobot.models.eval_result import EvalSummary
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.analyzer import analyze
from superrobot.pipeline.config_generator import (
    generate_config,
    render_files,
    write_generated_files,
)
from superrobot.pipeline.deployer import DeployResult, deploy
from superrobot.pipeline.evaluator import run_eval
from superrobot.pipeline.scanner import scan
from superrobot.repo import clone_repository

StageCallback = Callable[[str, str], None]


class TransformEngine:
    """Single orchestrator for all brownfield pipeline stages."""

    def __init__(self, on_stage: StageCallback | None = None) -> None:
        self._on_stage = on_stage or (lambda _stage, _detail: None)

    def _emit(self, stage: str, detail: str = "") -> None:
        self._on_stage(stage, detail)

    async def resolve_source(self, source: str) -> str:
        path = Path(source)
        if path.exists():
            return str(path.resolve())
        if source.startswith("http") or "github.com" in source:
            self._emit("clone", source)
            cloned = await clone_repository(source)
            return str(cloned)
        msg = f"Path not found: {source}"
        raise FileNotFoundError(msg)

    def run_scan(self, repo_path: str) -> ScanResult:
        self._emit("scan", repo_path)
        return scan(repo_path)

    async def run_analyze(self, scan_result: ScanResult) -> AnalysisResult:
        self._emit("analyze", scan_result.detected_framework)
        return await analyze(scan_result)

    def run_generate(
        self,
        scan_result: ScanResult,
        analysis: AnalysisResult,
        output_dir: str | Path,
    ) -> tuple[Path, dict[str, str]]:
        self._emit("generate", str(output_dir))
        config = generate_config(scan_result, analysis)
        files = render_files(config)
        out = write_generated_files(files, output_dir)
        return out, files

    async def run_eval(
        self,
        analysis: AnalysisResult,
        cwd: str | Path,
        entry: tuple[str, str, list[str]] | None = None,
    ) -> EvalSummary:
        self._emit("eval", str(cwd))
        return await run_eval(analysis, cwd=str(cwd), entry=entry)

    async def run_deploy(self, cwd: str | Path, *, has_ui: bool = False) -> DeployResult:
        self._emit("deploy", str(cwd))
        return await deploy(cwd=str(cwd), has_ui=has_ui)

    async def transform(
        self,
        source: str,
        *,
        output_dir: str | Path | None = None,
        skip_eval: bool = False,
        skip_deploy: bool = True,
        skip_clone: bool = False,
        framework: str | None = None,
    ) -> TransformContext:
        """Run the full brownfield pipeline and return accumulated context."""
        if skip_clone and Path(source).exists():
            repo_path = source
        else:
            repo_path = await self.resolve_source(source)
        out = Path(output_dir or Path(repo_path) / ".superrobot")

        ctx = TransformContext(repo_path=repo_path, output_dir=out)
        ctx.scan = self.run_scan(repo_path)
        ctx.analysis = await self.run_analyze(ctx.scan)
        if framework and ctx.analysis is not None:
            from superrobot.models.analysis_result import DrFramework

            ctx.analysis.dr_framework = DrFramework(framework)
            ctx.analysis.confidence = max(ctx.analysis.confidence, 0.7)
            ctx.analysis.notes = f"Framework forced via --framework={framework}. " + (
                ctx.analysis.notes or ""
            )
        written, ctx.files = self.run_generate(ctx.scan, ctx.analysis, out)

        if not skip_eval:
            ctx.eval_summary = await self.run_eval(ctx.analysis, written, entry=ctx.entry_info)

        if not skip_deploy:
            has_ui = any("ui/" in k for k in ctx.files)
            ctx.deploy_result = await self.run_deploy(written, has_ui=has_ui)

        return ctx
