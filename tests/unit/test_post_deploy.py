"""Post-deploy README badge tests."""

from pathlib import Path

from superrobot.post_deploy import generate_readme_badge_diff


def test_generate_readme_badge_appends_section(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# My Agent\n")
    original, proposed = generate_readme_badge_diff(
        readme,
        ["OPENAI_API_KEY"],
        "https://github.com/user/agent",
    )
    assert original == "# My Agent\n"
    assert "## Deploy" in proposed
    assert "OPENAI_API_KEY" in proposed


def test_generate_readme_badge_replaces_existing(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Agent\n\n## Deploy\nold content\n")
    _, proposed = generate_readme_badge_diff(readme, [], "")
    assert "deploy-badge.svg" in proposed
