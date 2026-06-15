"""Setup constants tests."""

from superrobot.setup.constants import (
    ENDPOINT_PRESETS,
    SETUP_STEPS,
    endpoint_label,
    normalize_endpoint,
)


def test_staging_endpoint_preset() -> None:
    assert ENDPOINT_PRESETS["staging"] == "https://staging.datarobot.com"


def test_production_endpoint_preset() -> None:
    assert ENDPOINT_PRESETS["production"] == "https://app.datarobot.com"


def test_environment_step_precedes_auth_step() -> None:
    # Auth must come after Environment so dr auth login targets the chosen URL
    assert SETUP_STEPS.index("Environment") < SETUP_STEPS.index("Auth")


def test_normalize_endpoint_strips_trailing_slash() -> None:
    assert normalize_endpoint("https://staging.datarobot.com/") == "https://staging.datarobot.com"


def test_normalize_endpoint_strips_api_v2_suffix() -> None:
    assert normalize_endpoint("https://app.datarobot.com/api/v2") == "https://app.datarobot.com"
    assert normalize_endpoint("https://app.datarobot.com/api/v2/") == "https://app.datarobot.com"


def test_normalize_endpoint_strips_whitespace() -> None:
    assert normalize_endpoint("  https://app.datarobot.com  ") == "https://app.datarobot.com"


def test_api_endpoint_appends_api_v2() -> None:
    from superrobot.setup.constants import api_endpoint

    # dr CLI / DR SDK require the /api/v2 form in DATAROBOT_ENDPOINT
    assert api_endpoint("https://staging.datarobot.com") == "https://staging.datarobot.com/api/v2"
    assert api_endpoint("https://staging.datarobot.com/api/v2/") == (
        "https://staging.datarobot.com/api/v2"
    )


def test_endpoint_label() -> None:
    assert endpoint_label("https://staging.datarobot.com/api/v2") == "staging"
    assert endpoint_label("https://app.datarobot.com") == "production"
    assert endpoint_label("https://my-dr.example.com") == "custom"
