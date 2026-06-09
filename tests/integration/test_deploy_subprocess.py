"""Integration tests — require real dr CLI."""

import pytest

from superrobot.pipeline.deployer import deploy


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deploy_subprocess() -> None:
    result = await deploy(cwd=".")
    assert result.warnings is not None
