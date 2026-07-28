"""Receipt store — persist/list/diagnose non-secret deploy receipts."""

from __future__ import annotations

import re
from pathlib import Path

from superrobot.models.receipt import Receipt
from superrobot.setup.config import config_dir

# Receipt ids are generated as `uuid4().hex[:12]`, so a strict alphanumeric
# form is the whole legitimate space. Validating against it keeps a
# user-supplied id from escaping the receipts directory.
_RECEIPT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_DIAGNOSTICS: list[tuple[str, str]] = [
    (
        "refusing rolling replace",
        "Scale the live workload to >=2 replicas, then retry with `receipt replace`.",
    ),
    (
        "not a credential reference",
        "Pass --secret KEY=credential:<id> — plaintext secret values are rejected "
        "before any network call.",
    ),
    (
        "pulumi",
        "Pulumi failures delete deployment logs (BUZZOK-30076) — prefer a manual UI "
        "deploy if you need to preserve logs.",
    ),
    (
        "no superrobot generated package found",
        "Directory doesn't look like a generated package — run `superrobot generate` "
        "or `transform` first.",
    ),
    (
        "not entitled",
        "Run `superrobot doctor` to re-probe capabilities — the account may be "
        "missing this entitlement.",
    ),
    ("not authenticated", "Run `superrobot setup`."),
]


def _receipts_dir(root: str | Path | None = None) -> Path:
    return config_dir(root) / "receipts"


def _validated_receipt_id(receipt_id: str) -> str:
    """Reject an id that could escape the receipts directory.

    `receipt show|diagnose|replace` take this straight from the command line
    and it used to be interpolated into a path unchecked, so
    `receipt show ../../secret` read an arbitrary JSON file and a crafted
    `Receipt.id` wrote outside the store.
    """
    if not _RECEIPT_ID_RE.match(receipt_id):
        msg = (
            f"Invalid receipt id {receipt_id!r} — expected 1-64 characters of "
            "letters, digits, dash or underscore."
        )
        raise ValueError(msg)
    return receipt_id


def save_receipt(receipt: Receipt, root: str | Path | None = None) -> Path:
    """Persist a receipt as JSON. No secrets are ever included on the model."""
    receipt_id = _validated_receipt_id(receipt.id)
    directory = _receipts_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{receipt_id}.json"
    destination.write_text(receipt.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return destination


def load_receipt(receipt_id: str, root: str | Path | None = None) -> Receipt | None:
    path = _receipts_dir(root) / f"{_validated_receipt_id(receipt_id)}.json"
    if not path.is_file():
        return None
    return Receipt.model_validate_json(path.read_text(encoding="utf-8"))


def list_receipts(root: str | Path | None = None, *, target: str | None = None) -> list[Receipt]:
    """All receipts, newest first, optionally filtered by target."""
    directory = _receipts_dir(root)
    if not directory.is_dir():
        return []
    receipts = [Receipt.model_validate_json(p.read_text()) for p in directory.glob("*.json")]
    if target:
        receipts = [r for r in receipts if r.target == target]
    return sorted(receipts, key=lambda r: r.created_at, reverse=True)


def latest_receipt(root: str | Path | None = None, *, target: str | None = None) -> Receipt | None:
    receipts = list_receipts(root, target=target)
    return receipts[0] if receipts else None


def diagnose(receipt: Receipt) -> str:
    """One-line, pattern-matched fix suggestion for a failed receipt."""
    if receipt.success:
        return "No failure to diagnose — this receipt recorded a successful operation."
    text = (receipt.error_message or "").lower()
    for needle, fix in _DIAGNOSTICS:
        if needle in text:
            return fix
    if receipt.action == "blocked":
        return (
            "Blocked by Gap Analysis — run `superrobot validate <dir>` for full "
            "findings, fix them, or pass --waive."
        )
    return "No known failure pattern matched — review error_message directly."
