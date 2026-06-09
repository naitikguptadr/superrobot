"""Repository cloning for brownfield import."""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

_GITHUB_RE = re.compile(r"github\.com[:/](?P<user>[^/]+)/(?P<repo>[^/.]+)")


def parse_github_url(url: str) -> str | None:
    """Normalize a GitHub URL to https clone URL."""
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    match = _GITHUB_RE.search(url)
    if match:
        user, repo = match.group("user"), match.group("repo")
        return f"https://github.com/{user}/{repo}.git"
    parsed = urlparse(url)
    if parsed.netloc == "github.com" and parsed.path.count("/") >= 2:
        parts = [p for p in parsed.path.split("/") if p]
        return f"https://github.com/{parts[0]}/{parts[1]}.git"
    return None


async def clone_repository(source: str, dest: Path | None = None) -> Path:
    """Clone a GitHub URL or return local path if it exists."""
    local = Path(source)
    if local.exists():
        return local.resolve()

    clone_url = parse_github_url(source)
    if not clone_url:
        msg = f"Cannot resolve source as local path or GitHub URL: {source}"
        raise FileNotFoundError(msg)

    target = dest or Path(tempfile.mkdtemp(prefix="superrobot-clone-"))
    proc = await asyncio.create_subprocess_exec(
        "git",
        "clone",
        "--depth",
        "1",
        clone_url,
        str(target),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        msg = f"git clone failed: {stderr_b.decode().strip()}"
        raise RuntimeError(msg)
    return target.resolve()
