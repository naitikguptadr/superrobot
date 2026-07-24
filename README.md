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

## FAQ

**How does it figure out what's in a repo?**
The `scan` stage is pure static analysis — no LLM call. It reads imports,
`requirements.txt`/`pyproject.toml`, and any `workflow.yaml`, in that priority
order, to detect the framework (LangChain, LlamaIndex, CrewAI, LangGraph,
raw async, etc.), find entry points, and pull environment variables via
`os.getenv`/dotenv usage. Each detection carries a confidence score; low
confidence means the shell will ask you to confirm before generating
anything, rather than silently guessing.

**Does it modify my original repo?**
No. `transform`/`generate` write into a separate output directory you choose
(`--output-dir` / `-o`). Your source repo is only ever read.

**How does the "prebuilt container image" thing work for Workload deploys?**
Workload API deploys need a container image somewhere DataRobot can pull
from. Two ways to get one:
- **Bring your own** — build and push it yourself (Docker, any registry DR
  can reach), then pass `--image-uri <uri>`.
- **Let DataRobot build it** — Code-to-Workload (C2W): DataRobot builds the
  image server-side from your source and hosts it in its own internal
  registry. You get an artifact id back, and deploy with `--artifact-id <id>`
  instead. This is what the vendored `datarobot-workload-api` skill's
  `code-to-workload.md` reference walks through — create an artifact, sync
  your code, trigger a build, wait for it to reach `COMPLETED` (not just
  `BUILT` — that distinction matters, see the skill doc), then deploy.
  **Why two separate flags and not one:** an image built via C2W lives in
  DataRobot's own registry and is only schedulable when the workload
  references the artifact that was actually built. Passing that same image
  URI to `--image-uri` creates a *new* artifact from scratch and DataRobot
  rejects it — confirmed against a real environment.

**Why does it ask for confirmation before deploying?**
Deploys are the one step that touches real infrastructure. The shell always
shows a confirm dialog first, naming the specific risks: Agent App builds can
take 15–20 minutes even for a Python-only change (a known platform issue,
BUZZOK-30076), and a failed Pulumi run deletes its own deployment logs. You
decide, not the agent.

**What happens to secrets?**
Never written to receipts, generated files, git, or logs. Workload secrets
must be passed as `KEY=credential:<id>` (a reference to a DataRobot-managed
credential) — a plaintext value is rejected before any API call is made.

**What's a receipt?**
A non-secret audit record written after every deploy attempt — blocked,
failed, or successful — under `~/.config/superrobot/receipts/`. It captures
attribution (model used), the Gap Analysis summary, and enough context
(`superrobot receipt diagnose <id>`) to pattern-match common failure modes.
`superrobot receipt replace <id>` re-runs a past attempt through the normal
Gap Analysis gate, not auto-waived.

**What does Gap Analysis actually check?**
The same platform rules DataRobot's own tooling encodes: flat imports (DRUM
flattens the deployed bundle, so `from agent.agent.x import y` works locally
but fails in production), runtime parameters present in all three required
locations, and that generated dependency changes are additive-only (removing
a package breaks Playground/Deployment parity). Findings are `blocking` or
`warning` — blocking stops deploy unless you explicitly pass `--waive`.

**Does Agent App deploy work today?**
The deploy call itself does, but `superrobot generate` doesn't yet produce
the `.datarobot/` + `Taskfile.yaml` scaffold that `dr task run deploy`
requires (the same structure `dr templates setup` produces for a real
template). Workload API is the fully-working target right now; see
[Deploying](#deploying) above.

**What are the vendored DataRobot skills for?**
`vendor/datarobot-agent-skills/` is DataRobot's own official skill set
(Workload API, model deployment, monitoring, explainability, and more),
pulled in as a submodule so the shell has deep, accurate platform knowledge
out of the box — not just what this project's own docs teach it. Run `git
submodule update --init --recursive` after cloning to pull it in.

**Why does it use the DataRobot LLM Gateway instead of my own OpenAI/Anthropic key?**
So the whole flow — scanning, generating, and the conversational shell
itself — runs on infrastructure your DataRobot account already has access
to, with no separate vendor API key to manage. `superrobot setup` is the only
place credentials are entered.
