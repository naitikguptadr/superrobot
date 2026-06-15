"""Setup wizard constants."""

from __future__ import annotations

ENDPOINT_PRESETS: dict[str, str] = {
    "production": "https://app.datarobot.com",
    "staging": "https://staging.datarobot.com",
}

DEFAULT_MODEL = "azure/gpt-5-5-2026-04-23"

# Environment must come before Auth so `dr auth login <url>` targets the
# selected DataRobot environment (production / staging / custom).
SETUP_STEPS = ["Welcome", "Tools", "Environment", "Auth", "Verify", "Complete"]


def normalize_endpoint(url: str) -> str:
    """Normalize a DataRobot Platform URL to a bare https://host form.

    Users routinely paste the SDK-style URL ending in /api/v2 or a URL with a
    trailing slash; both break LLM Gateway base-URL construction.
    """
    url = url.strip().rstrip("/")
    if url.endswith("/api/v2"):
        url = url[: -len("/api/v2")]
    return url


def api_endpoint(url: str) -> str:
    """Canonical API form: https://host/api/v2.

    This is what the dr CLI and DataRobot SDK expect in DATAROBOT_ENDPOINT —
    exporting the bare host makes `dr auth check` reject a valid token.
    """
    return f"{normalize_endpoint(url)}/api/v2"


def endpoint_label(url: str) -> str:
    """Human-readable environment name for a Platform URL."""
    normalized = normalize_endpoint(url)
    for name, preset in ENDPOINT_PRESETS.items():
        if normalized == preset:
            return name
    return "custom"
