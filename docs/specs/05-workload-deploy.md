# Spec 05 — Workload API Deploy Target

## Goal
First-class Workload API target: generate service adapter + Dockerfile + workload.yaml (already in transform), then deploy/operate via Workload client with preflight and 2-replica rolling-replace guard.

## Acceptance
- `superrobot deploy <dir> --target workload --image-uri <uri>` creates/updates a workload (mocked HTTP in unit tests).
- Preflight blocks single-replica replace and plaintext-secret inject.
- Capability probe: when Workload entitlement missing, CLI exits non-zero with clear message.
- Unit tests for client, preflight, deployer, rollout.
- Skill `superrobot-workload-deploy` documents flags.

## Non-goals
- Memory API (Spec 06).
- Gap Analysis skill (Spec 07).
- Receipts / attribution (Spec 08).
