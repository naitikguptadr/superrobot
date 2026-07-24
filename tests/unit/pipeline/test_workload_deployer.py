"""Workload deploy target unit tests — preflight, deployer, rollout."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from superrobot.pipeline.workload_deployer import (
    WorkloadPreflightError,
    deploy_workload,
    load_manifest,
    load_manifest_from_artifact,
    preflight_replace,
    preflight_secrets,
)

_MANIFEST_YAML = """\
name: research-agent
artifact:
  spec:
    type: service
    containerGroups:
      - name: default
        containers:
          - name: main
            imageUri: REPLACE_WITH_IMAGE_URI
runtime:
  containerGroups:
    - name: default
      replicaCount: {replicas}
"""


def _write_manifest(tmp_path: Path, replicas: int = 2) -> Path:
    workload_dir = tmp_path / "workload"
    workload_dir.mkdir()
    (workload_dir / "workload.yaml").write_text(_MANIFEST_YAML.format(replicas=replicas))
    return tmp_path


class _FakeClient:
    def __init__(self, existing: dict[str, object] | None = None) -> None:
        self.existing = existing
        self.created: dict[str, object] | None = None
        self.replaced: tuple[str, dict[str, object]] | None = None

    async def find_by_name(self, name: str) -> dict[str, object] | None:
        return self.existing

    async def create(self, manifest: dict[str, object]) -> dict[str, object]:
        self.created = manifest
        return {"id": "w-new"}

    async def replace(self, workload_id: str, manifest: dict[str, object]) -> dict[str, object]:
        self.replaced = (workload_id, manifest)
        return {"id": workload_id}


def test_load_manifest_injects_image_uri(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    manifest = load_manifest(tmp_path, "registry.example.com/agent:1")
    containers = manifest["artifact"]["spec"]["containerGroups"][0]["containers"]  # type: ignore[index]
    assert containers[0]["imageUri"] == "registry.example.com/agent:1"


def test_load_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkloadPreflightError, match="No workload.yaml"):
        load_manifest(tmp_path, "registry.example.com/agent:1")


def test_load_manifest_from_artifact_replaces_inline_artifact_with_id(tmp_path: Path) -> None:
    """Code-to-Workload (server-side build) images live in DataRobot's own
    internal registry and are only schedulable when the workload references
    the artifact that was actually built, not a fresh artifact created from
    a copied imageUri (DR rejects that with a 'not permitted on this
    cluster' error -- confirmed against a real staging environment)."""
    _write_manifest(tmp_path)
    manifest = load_manifest_from_artifact(tmp_path, "artifact-abc123")
    assert manifest["artifactId"] == "artifact-abc123"
    assert "artifact" not in manifest
    assert manifest["name"] == "research-agent"
    assert manifest["runtime"]["containerGroups"][0]["name"] == "default"  # type: ignore[index]


def test_load_manifest_from_artifact_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkloadPreflightError, match="No workload.yaml"):
        load_manifest_from_artifact(tmp_path, "artifact-abc123")


def test_preflight_replace_blocks_single_replica() -> None:
    with pytest.raises(WorkloadPreflightError, match="Refusing rolling replace"):
        preflight_replace({"runtime": {"containerGroups": [{"replicaCount": 1}]}})


def test_preflight_replace_allows_two_or_more() -> None:
    preflight_replace({"runtime": {"containerGroups": [{"replicaCount": 2}]}})


def test_preflight_secrets_blocks_plaintext() -> None:
    with pytest.raises(WorkloadPreflightError, match="not a credential reference"):
        preflight_secrets({"API_KEY": "sk-plaintext-secret"})


def test_preflight_secrets_allows_credential_reference() -> None:
    preflight_secrets({"API_KEY": "credential:abc123"})


def test_deploy_workload_creates_when_absent(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    fake = _FakeClient(existing=None)
    result = asyncio.run(
        deploy_workload(
            manifest_dir=tmp_path,
            image_uri="registry.example.com/agent:1",
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is True
    assert result.action == "created"
    assert result.workload_id == "w-new"
    assert fake.created is not None


def test_deploy_workload_creates_from_existing_artifact_id(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    fake = _FakeClient(existing=None)
    result = asyncio.run(
        deploy_workload(
            manifest_dir=tmp_path,
            artifact_id="artifact-abc123",
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is True
    assert result.action == "created"
    assert fake.created is not None
    assert fake.created["artifactId"] == "artifact-abc123"
    assert "artifact" not in fake.created


def test_deploy_workload_requires_exactly_one_of_image_uri_or_artifact_id(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    fake = _FakeClient(existing=None)

    result = asyncio.run(
        deploy_workload(
            manifest_dir=tmp_path,
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is False
    assert result.error_message is not None
    assert "image_uri" in result.error_message or "artifact_id" in result.error_message

    both_result = asyncio.run(
        deploy_workload(
            manifest_dir=tmp_path,
            image_uri="registry.example.com/agent:1",
            artifact_id="artifact-abc123",
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert both_result.success is False


def test_deploy_workload_rolling_replace_when_present(tmp_path: Path) -> None:
    _write_manifest(tmp_path, replicas=2)
    fake = _FakeClient(existing={"id": "w-1", "name": "research-agent"})
    result = asyncio.run(
        deploy_workload(
            manifest_dir=tmp_path,
            image_uri="registry.example.com/agent:2",
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is True
    assert result.action == "replaced"
    assert fake.replaced is not None
    assert fake.replaced[0] == "w-1"


def test_deploy_workload_blocks_single_replica_replace(tmp_path: Path) -> None:
    _write_manifest(tmp_path, replicas=1)
    fake = _FakeClient(existing={"id": "w-1", "name": "research-agent"})
    result = asyncio.run(
        deploy_workload(
            manifest_dir=tmp_path,
            image_uri="registry.example.com/agent:2",
            endpoint="https://app.datarobot.com",
            token="tok",
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is False
    assert result.error_message is not None
    assert "Refusing rolling replace" in result.error_message
    assert fake.replaced is None


def test_deploy_workload_blocks_plaintext_secret_before_any_call(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    fake = _FakeClient(existing=None)
    result = asyncio.run(
        deploy_workload(
            manifest_dir=tmp_path,
            image_uri="registry.example.com/agent:1",
            endpoint="https://app.datarobot.com",
            token="tok",
            secrets={"API_KEY": "sk-plaintext"},
            client=fake,  # type: ignore[arg-type]
        )
    )
    assert result.success is False
    assert result.error_message is not None
    assert "credential reference" in result.error_message
    assert fake.created is None


def test_deploy_workload_without_explicit_client_still_runs_preflight(tmp_path: Path) -> None:
    """No client injected — deploy_workload builds a real WorkloadClient, but
    the plaintext-secret preflight fires before any network call."""
    _write_manifest(tmp_path)
    result = asyncio.run(
        deploy_workload(
            manifest_dir=tmp_path,
            image_uri="registry.example.com/agent:1",
            endpoint="https://app.datarobot.com",
            token="tok",
            secrets={"API_KEY": "sk-plaintext"},
        )
    )
    assert result.success is False
