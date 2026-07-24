# SuperRobot

DataRobot-native brownfield control plane. Migrate any existing Python agent to DataRobot, validate with Gap Analysis, deploy to Agent App or Workload API, and operate with receipts — all through the LLM Gateway.

This tree is the **from-scratch rebuild** on `rebuild/pi-datarobot` (orphan branch, no shared history with `main`). The previous Textual-era codebase lives on `feat/brownfield-pipeline-and-tui`, tagged `archive/pre-pi-rebuild` as of the cutover PR.

## Architecture

| Layer | Path | Role |
|---|---|---|
| Engine CLI | `superrobot/` (Python) | Setup, doctor, transform, deploy, receipts |
| Premium shell | `shell/` (Node / Pi customization) | Interactive branded UI, Gateway-wired Pi |
| Specs | `docs/specs/` | Spec-by-spec build contracts |

## Quick start (Spec 01)

```bash
git submodule update --init --recursive   # pulls in vendor/datarobot-agent-skills
uv sync --all-extras
uv run superrobot setup --endpoint https://app.datarobot.com --token "$DATAROBOT_API_TOKEN" --yes
uv run superrobot doctor
```

Interactive shell (Spec 02 bootstrap):

```bash
cd shell && npm install && npm run build
```

Then just run `superrobot` (no subcommand) from anywhere in the repo — it launches
the interactive shell directly. Existing subcommands (`scan`, `transform`, `deploy`,
etc.) are unaffected.

## Specs

1. [Setup and Gateway](docs/specs/01-setup-and-gateway.md)
2. [Premium Shell](docs/specs/02-premium-shell.md)
3. [Transform Engine](docs/specs/03-transform-engine.md)
4. [Agent App Deploy](docs/specs/04-agent-app-deploy.md)
5. [Workload API Deploy](docs/specs/05-workload-deploy.md)
6. [Memory API](docs/specs/06-memory-api.md)
7. [Gap Analysis](docs/specs/07-gap-analysis.md)
8. [Receipts](docs/specs/08-receipts.md)

See [docs/verification-matrix.md](docs/verification-matrix.md) for how each spec's
acceptance criteria maps to actual tests, and [docs/demo.md](docs/demo.md) /
`./scripts/demo.sh` for a runnable golden-path walkthrough.

```bash
uv run superrobot scan tests/fixtures/langchain_agent --json
uv run superrobot transform tests/fixtures/langchain_agent --json --skip-eval -o /tmp/sr-out
uv run superrobot validate /tmp/sr-out --json
uv run superrobot deploy /tmp/sr-out --target agent-app --json
uv run superrobot receipt operations --json
```

## Design rules

- Models: DataRobot LLM Gateway only
- Auth: `dr auth` and/or API token
- Secrets: never in receipts, git, or UI logs
- Platform APIs: Agent App, Workload, Memory when entitled
