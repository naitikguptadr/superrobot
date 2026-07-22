# Spec 02 — Premium SuperRobot Shell (Pi Customization)

## Goal
Deeply customize Pi as the SuperRobot interactive shell: branded theme, splash, stage rail, Gateway-only provider wiring, and `superrobot` binary entrypoint that is not recognizable as stock Pi.

## Acceptance
- `npx`/`npm` package exposes binary `superrobot`.
- Default model provider is DataRobot LLM Gateway (no direct vendor login in product path).
- Theme tokens and layout package live under `shell/` and override Pi defaults.
- Print/JSON mode available for Swarm/Gap embedding (`superrobot -p` or equivalent).
- Visual QA checklist documented in `docs/ui-qa.md`.

## Non-goals
- Complete graph canvas / receipt theater (Spec 05+).
- Engine transform logic (Python package).
