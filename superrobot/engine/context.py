"""Transform pipeline context — carries state across stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from superrobot.models.analysis_result import AnalysisResult
from superrobot.models.eval_result import EvalSummary
from superrobot.models.scan_result import ScanResult
from superrobot.pipeline.deployer import DeployResult


@dataclass
class TransformContext:
    """Mutable state for a brownfield transform run."""

    repo_path: str
    output_dir: Path
    scan: ScanResult | None = None
    analysis: AnalysisResult | None = None
    files: dict[str, str] = field(default_factory=dict)
    eval_summary: EvalSummary | None = None
    deploy_result: DeployResult | None = None

    @property
    def entry_info(self) -> tuple[str, str, list[str]] | None:
        from superrobot.models.agent_config import parse_signature_params
        from superrobot.pipeline.config_generator import flat_module_name

        if not self.scan or not self.scan.entry_points:
            return None
        ep = self.scan.primary_entry or self.scan.entry_points[0]
        module = flat_module_name(self.scan.repo_path, ep.file)
        return (module, ep.function, parse_signature_params(ep.signature))
