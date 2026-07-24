"""Workload API deploy target — create/replace a containerized workload."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from superrobot.dr.workload_client import WorkloadApiError, WorkloadClient

_IMAGE_PLACEHOLDER = "REPLACE_WITH_IMAGE_URI"
CREDENTIAL_REFERENCE_PREFIX = "credential:"


class WorkloadPreflightError(Exception):
    """Raised when a workload deploy fails a safety check before any API call."""


@dataclass
class WorkloadDeployResult:
    """Result of a workload create/replace attempt."""

    success: bool
    action: Literal["created", "replaced"] | None
    workload_id: str | None
    error_message: str | None = None


def load_manifest(manifest_dir: str | Path, image_uri: str) -> dict[str, object]:
    """Read workload/workload.yaml and inject the built image URI."""
    path = Path(manifest_dir) / "workload" / "workload.yaml"
    if not path.is_file():
        raise WorkloadPreflightError(f"No workload.yaml found at {path}")
    rendered = path.read_text().replace(_IMAGE_PLACEHOLDER, image_uri)
    manifest = yaml.safe_load(rendered)
    if not isinstance(manifest, dict):
        raise WorkloadPreflightError(f"Invalid workload.yaml at {path}")
    return manifest


def load_manifest_from_artifact(manifest_dir: str | Path, artifact_id: str) -> dict[str, object]:
    """Read workload/workload.yaml but reference an existing artifact by id
    instead of an inline artifact.spec + imageUri.

    Needed for images built via Code-to-Workload (server-side build):
    those images live in DataRobot's own internal registry and are only
    schedulable when the workload references the artifact that was
    actually built -- creating a fresh artifact from a copied imageUri is
    rejected with "is not permitted on this cluster" (confirmed against a
    real staging environment). The `name` and `runtime` blocks are
    unchanged; only `artifact` is replaced with `artifactId`. Per the
    DataRobot Workload API, `runtime.containerGroups[].name` and
    `containers[].name` must match what the referenced artifact defines.
    """
    path = Path(manifest_dir) / "workload" / "workload.yaml"
    if not path.is_file():
        raise WorkloadPreflightError(f"No workload.yaml found at {path}")
    manifest = yaml.safe_load(path.read_text())
    if not isinstance(manifest, dict):
        raise WorkloadPreflightError(f"Invalid workload.yaml at {path}")
    manifest.pop("artifact", None)
    manifest["artifactId"] = artifact_id
    return manifest


def _min_replica_count(manifest: dict[str, object]) -> int:
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        return 0
    groups = runtime.get("containerGroups")
    if not isinstance(groups, list) or not groups:
        return 0
    counts = [int(g.get("replicaCount", 0)) for g in groups if isinstance(g, dict)]
    return min(counts) if counts else 0


def preflight_replace(manifest: dict[str, object]) -> None:
    """Block rolling-replace of a live workload below 2 replicas."""
    replicas = _min_replica_count(manifest)
    if replicas < 2:
        raise WorkloadPreflightError(
            f"Refusing rolling replace at {replicas} replica(s) — scale to >=2 before replacing"
        )


def preflight_secrets(secrets: dict[str, str] | None) -> None:
    """Block plaintext secret values — require a DR credential reference."""
    for key, value in (secrets or {}).items():
        if not value.startswith(CREDENTIAL_REFERENCE_PREFIX):
            raise WorkloadPreflightError(
                f"{key} is not a credential reference — use "
                f"'{CREDENTIAL_REFERENCE_PREFIX}<credential-id>', not a plaintext secret"
            )


async def deploy_workload(
    *,
    manifest_dir: str | Path,
    endpoint: str,
    token: str,
    image_uri: str | None = None,
    artifact_id: str | None = None,
    agent_name: str | None = None,
    secrets: dict[str, str] | None = None,
    client: WorkloadClient | None = None,
) -> WorkloadDeployResult:
    """Create or rolling-replace a Workload API deployment.

    Exactly one of `image_uri` (bring-your-own-image: a fresh artifact is
    created from workload.yaml's inline spec) or `artifact_id` (reference an
    already-built artifact -- required for Code-to-Workload/server-side
    builds) must be given.
    """
    if bool(image_uri) == bool(artifact_id):
        return WorkloadDeployResult(
            success=False,
            action=None,
            workload_id=None,
            error_message="Exactly one of image_uri or artifact_id is required",
        )

    try:
        preflight_secrets(secrets)
        manifest = (
            load_manifest(manifest_dir, image_uri)
            if image_uri
            else load_manifest_from_artifact(manifest_dir, artifact_id)  # type: ignore[arg-type]
        )
    except WorkloadPreflightError as exc:
        return WorkloadDeployResult(
            success=False, action=None, workload_id=None, error_message=str(exc)
        )

    name = agent_name or str(manifest.get("name", "")).strip()
    if not name:
        return WorkloadDeployResult(
            success=False,
            action=None,
            workload_id=None,
            error_message="Workload manifest has no name",
        )
    manifest["name"] = name

    workload_client = client or WorkloadClient(endpoint, token)
    action: Literal["created", "replaced"]
    try:
        existing = await workload_client.find_by_name(name)
        if existing:
            preflight_replace(manifest)
            result = await workload_client.replace(str(existing.get("id", "")), manifest)
            action = "replaced"
        else:
            result = await workload_client.create(manifest)
            action = "created"
    except (WorkloadPreflightError, WorkloadApiError) as exc:
        return WorkloadDeployResult(
            success=False, action=None, workload_id=None, error_message=str(exc)
        )

    return WorkloadDeployResult(success=True, action=action, workload_id=str(result.get("id", "")))
