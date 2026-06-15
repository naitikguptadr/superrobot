"""LLM Gateway credential handling."""

from __future__ import annotations

import pytest

from superrobot.dr.llm_gateway import LLMGateway, has_llm_credentials


def test_has_llm_credentials_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATAROBOT_API_TOKEN", raising=False)
    monkeypatch.delenv("DATAROBOT_ENDPOINT", raising=False)
    assert has_llm_credentials() is False
    gw = LLMGateway()
    assert gw.available is False


def test_has_llm_credentials_true_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAROBOT_API_TOKEN", "token")
    monkeypatch.setenv("DATAROBOT_ENDPOINT", "https://staging.datarobot.com")
    assert has_llm_credentials() is True
    gw = LLMGateway()
    assert gw.available is True
