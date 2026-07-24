"""Deploy receipt — attribution and audit trail for a single deploy attempt."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ReceiptTarget = Literal["agent-app", "workload"]
ReceiptAction = Literal["blocked", "deployed", "created", "replaced", "failed"]


class Receipt(BaseModel):
    """Non-secret record of a single `superrobot deploy` attempt."""

    id: str
    created_at: str
    target: ReceiptTarget
    action: ReceiptAction
    success: bool
    model: str
    gap_summary: dict[str, int] = Field(default_factory=dict)
    waived_findings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    replaces: str | None = None
    manifest_dir: str | None = None
    image_uri: str | None = None
    artifact_id: str | None = None
    has_ui: bool = False
