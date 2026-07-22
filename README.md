# SuperRobot

DataRobot-native brownfield control plane. Migrate any existing Python agent to DataRobot, validate with Gap Analysis, deploy to Agent App or Workload API, and operate with receipts — all through the LLM Gateway.

This tree is the **from-scratch rebuild** on `rebuild/pi-datarobot` (orphan worktree). The previous Textual-era codebase remains on the archive branch/tag `archive/pre-pi-rebuild`.

## Architecture

| Layer | Path | Role |
|---|---|---|
| Engine CLI | `superrobot/` (Python) | Setup, doctor, transform, deploy, receipts |
| Premium shell | `shell/` (Node / Pi customization) | Interactive branded UI, Gateway-wired Pi |
| Specs | `docs/specs/` | Spec-by-spec build contracts |

## Quick start (Spec 01)

```bash
uv sync --all-extras
uv run superrobot setup --endpoint https://app.datarobot.com --token "$DATAROBOT_API_TOKEN" --yes
uv run superrobot doctor
```

Interactive shell (Spec 02 bootstrap):

```bash
cd shell && npm install && npm run build && npm start
```

## Specs

1. [Setup and Gateway](docs/specs/01-setup-and-gateway.md)
2. [Premium Shell](docs/specs/02-premium-shell.md)
3. [Transform Engine](docs/specs/03-transform-engine.md)
4. [Agent App Deploy](docs/specs/04-agent-app-deploy.md) ← current

```bash
uv run superrobot scan tests/fixtures/langchain_agent --json
uv run superrobot transform tests/fixtures/langchain_agent --json --skip-eval -o /tmp/sr-out
uv run superrobot deploy /tmp/sr-out --target agent-app --json
```

1. [Setup + Gateway](docs/specs/01-setup-and-gateway.md)
2. [Premium shell](docs/specs/02-premium-shell.md)

## Design rules

- Models: DataRobot LLM Gateway only
- Auth: `dr auth` and/or API token
- Secrets: never in receipts, git, or UI logs
- Platform APIs: Agent App, Workload, Memory when entitled
