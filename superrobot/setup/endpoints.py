"""Platform endpoint normalization — Platform API only."""

from __future__ import annotations


class EndpointError(ValueError):
    """Raised when a URL is not a valid DataRobot Platform endpoint."""


_PREDICTION_MARKERS = (
    "prediction",
    "pred.",
    "/pred/",
    "datarobot.com/pred",
)


def normalize_endpoint(url: str) -> str:
    """Return bare https://host form without /api/v2 or trailing slash."""
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        raise EndpointError("Endpoint URL is required")
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _PREDICTION_MARKERS):
        raise EndpointError(
            "DATAROBOT_ENDPOINT must be the Platform API URL, not the Prediction API"
        )
    if cleaned.endswith("/api/v2"):
        cleaned = cleaned[: -len("/api/v2")]
    if not cleaned.startswith("https://") and not cleaned.startswith("http://"):
        cleaned = f"https://{cleaned}"
    return cleaned.rstrip("/")


def api_endpoint(url: str) -> str:
    """Canonical API form expected by dr CLI and SDK: https://host/api/v2."""
    return f"{normalize_endpoint(url)}/api/v2"


def gateway_base_url(url: str) -> str:
    """LLM Gateway OpenAI-compatible base URL."""
    return f"{api_endpoint(url)}/genai/llmgw"


ENDPOINT_PRESETS: dict[str, str] = {
    "production": "https://app.datarobot.com",
    "staging": "https://staging.datarobot.com",
}
