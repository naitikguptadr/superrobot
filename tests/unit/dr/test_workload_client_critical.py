"""Regression tests for two critical WorkloadClient defects (audit C2, C7).

Both are silent-wrong-result bugs: the user is told the deploy succeeded
while the platform is left serving the old image, or a second workload is
created behind their back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from superrobot.dr.workload_client import WorkloadApiError, WorkloadClient
from superrobot.pipeline.workload_deployer import deploy_workload

_ENDPOINT = "https://app.datarobot.com/api/v2"
_TOKEN = "tok"


class _RecordingTransport:
    """Captures calls and replays scripted responses."""

    def __init__(self, responses: list[tuple[int, object]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, object]] = []

    async def __call__(
        self, method: str, url: str, headers: dict[str, str], payload: object | None
    ) -> tuple[int, object]:
        self.calls.append((method, url, payload))
        return self._responses.pop(0) if self._responses else (200, {})


class TestReplaceUsesTheRollingReplacementEndpoint:
    """C2 — replace() sent PATCH /workloads/{id}/, which DataRobot's own spec
    documents as "Metadata only — no restart". Every deploy after the first
    reported success while the live workload kept serving the old image.
    """

    def test_replace_posts_to_the_replacement_endpoint(self) -> None:
        transport = _RecordingTransport([(202, {"id": "w1"})])
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=transport)

        asyncio.run(client.replace("w1", {"name": "agent", "artifactId": "art-2"}))

        method, url, payload = transport.calls[0]
        assert method == "POST", "PATCH /workloads/{id}/ is metadata-only and does not swap images"
        assert url.endswith("/workloads/w1/replacement/")
        assert isinstance(payload, dict)
        assert payload["artifactId"] == "art-2"
        assert payload["strategy"] == "rolling"

    def test_replace_without_an_artifact_id_fails_loudly(self) -> None:
        """A bring-your-own-image manifest carries an inline artifact spec,
        not an id. The replacement endpoint cannot consume that, so this must
        raise rather than silently leave the old image serving.
        """
        transport = _RecordingTransport([])
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=transport)

        with pytest.raises(WorkloadApiError) as excinfo:
            asyncio.run(client.replace("w1", {"name": "agent", "artifact": {"spec": {}}}))

        assert "artifact" in str(excinfo.value).lower()
        assert not transport.calls, "must not issue a request it knows cannot work"

    def test_replace_surfaces_an_api_error(self) -> None:
        transport = _RecordingTransport([(400, {"detail": "status mismatch"})])
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=transport)

        with pytest.raises(WorkloadApiError):
            asyncio.run(client.replace("w1", {"artifactId": "art-2"}))


class TestLookupDistinguishesMissingFromFailed:
    """C7 — any non-200 collapsed to "does not exist", so a 401/500 on the
    lookup silently routed a replace into a create: a duplicate workload, and
    the preflight replica guard never ran.
    """

    def test_a_404_means_the_workload_really_is_absent(self) -> None:
        transport = _RecordingTransport([(404, {"detail": "not found"})])
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=transport)

        assert asyncio.run(client.find_by_name("agent")) is None

    def test_an_empty_result_set_means_absent(self) -> None:
        transport = _RecordingTransport([(200, {"data": []})])
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=transport)

        assert asyncio.run(client.find_by_name("agent")) is None

    @pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503])
    def test_a_transient_or_auth_failure_raises_instead_of_reporting_absent(
        self, status: int
    ) -> None:
        transport = _RecordingTransport([(status, {"detail": "boom"})])
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=transport)

        with pytest.raises(WorkloadApiError):
            asyncio.run(client.find_by_name("agent"))

    def test_the_name_is_url_encoded(self) -> None:
        """Names derive from repo/directory names; an unencoded `&` injected a
        second query parameter and matched the wrong resource.
        """
        transport = _RecordingTransport([(200, {"data": []})])
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=transport)

        asyncio.run(client.find_by_name("my agent&limit=1"))

        _, url, _ = transport.calls[0]
        assert " " not in url
        assert "&limit=1" not in url


_MANIFEST = """\
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
      replicaCount: 2
"""


class TestRedeployEndToEnd:
    """What the user actually experiences, through `deploy_workload`."""

    @staticmethod
    def _manifest_dir(tmp_path: Path) -> Path:
        (tmp_path / "workload").mkdir()
        (tmp_path / "workload" / "workload.yaml").write_text(_MANIFEST)
        return tmp_path

    @staticmethod
    async def _transport(
        method: str, url: str, headers: dict[str, str], payload: object | None
    ) -> tuple[int, object]:
        if method == "GET":
            return 200, {"data": [{"id": "w-1", "name": "research-agent"}]}
        return 202, {"id": "w-1"}

    def test_artifact_id_redeploy_actually_rolls(self, tmp_path: Path) -> None:
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=self._transport)

        result = asyncio.run(
            deploy_workload(
                manifest_dir=self._manifest_dir(tmp_path),
                endpoint=_ENDPOINT,
                token=_TOKEN,
                artifact_id="art-2",
                client=client,
            )
        )

        assert result.success is True
        assert result.action == "replaced"

    def test_byo_image_redeploy_fails_loudly_instead_of_claiming_success(
        self, tmp_path: Path
    ) -> None:
        """The C2 payoff: this used to report success while the platform kept
        serving the old image. An actionable failure is strictly better.
        """
        client = WorkloadClient(_ENDPOINT, _TOKEN, transport=self._transport)

        result = asyncio.run(
            deploy_workload(
                manifest_dir=self._manifest_dir(tmp_path),
                endpoint=_ENDPOINT,
                token=_TOKEN,
                image_uri="reg/img:2",
                client=client,
            )
        )

        assert result.success is False
        assert "--artifact-id" in (result.error_message or "")
