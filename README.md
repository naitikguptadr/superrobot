# SuperRobot

DataRobot-native brownfield control plane. Bring any existing Python agent to
DataRobot — migrate, validate with Gap Analysis, deploy to Agent App or the
Workload API, and operate with receipts — through a conversational shell wired
to the DataRobot LLM Gateway.

Just run `superrobot`. Talk to it naturally — "import this repo and deploy
it" — and it drives the real pipeline (scan → transform → validate → deploy →
receipts) as live tool calls, with a stage-rail widget tracking progress and a
confirm gate before anything that touches production.

## Architecture

| Layer | Path | Role |
|---|---|---|
| Engine CLI | `superrobot/` (Python) | Setup, doctor, scan/transform/validate/deploy, receipts |
| Premium shell | `shell/` (Node / Pi customization) | Conversational UI, pipeline tools, Gateway-wired Pi |
| DataRobot skills | `vendor/datarobot-agent-skills/` (submodule) | Official DataRobot agent skills, available out of the box |
| Specs | `docs/specs/` | Spec-by-spec build contracts |

## Quick start

```bash
git submodule update --init --recursive   # pulls in vendor/datarobot-agent-skills
uv sync --all-extras
uv run superrobot setup --endpoint https://app.datarobot.com --token "$DATAROBOT_API_TOKEN" --yes
uv run superrobot doctor
```

Build the interactive shell once:

```bash
cd shell && npm install && npm run build
```

Then just run `superrobot` (no subcommand) from anywhere in the repo — it launches
the interactive shell directly, like `opencode`/`claude` do. Existing subcommands
(`scan`, `transform`, `deploy`, etc.) still work unchanged for scripting/CI.

To make the global `superrobot` command track this checkout (recommended for
local development):

```bash
uv tool install --editable . --force
```

## Deploying

Two targets:

- **Agent App** (`--target agent-app`) — the native DR deployment path via
  `dr task run deploy`. Requires the target directory to already be a
  DR-template-scaffolded project (a `Taskfile.yaml` + `.datarobot/` metadata,
  the same structure `dr templates setup` produces) — `superrobot generate`
  does not yet emit this scaffold, so this target isn't usable on a bare
  generated package today.
- **Workload API** (`--target workload`) — a direct REST deploy, no Taskfile
  needed. Provide the image either way:
  - `--image-uri <uri>` — bring your own image, already pushed to a registry
    DataRobot can pull from.
  - `--artifact-id <id>` — deploy from an artifact DataRobot already built for
    you (e.g. via Code-to-Workload / server-side build — see the vendored
    `datarobot-workload-api` skill). Required for C2W images: they live in
    DataRobot's own internal registry and aren't schedulable under a freshly
    created artifact.

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
uv run superrobot deploy /tmp/sr-out --target workload --image-uri <built-image-uri> --json
uv run superrobot receipt operations --json
```

Or, conversationally, from inside `superrobot`:

```
Import tests/fixtures/langchain_agent, transform it, validate it, and deploy it to Workload using artifact id <id>.
```

## Design rules

- Models: DataRobot LLM Gateway only
- Auth: `dr auth` and/or API token
- Secrets: never in receipts, git, or UI logs
- Platform APIs: Agent App, Workload, Memory when entitled
