# SuperRobot

**Bring any agent to DataRobot without rebuilding it from scratch.**

SuperRobot is a TUI-powered CLI for migrating existing Python agents (LangChain, LlamaIndex, CrewAI, raw async) to DataRobot. It scans your repo, maps it to the closest DR agent framework, generates compliant deployment config, and walks you through eval and deploy.

## Prerequisites

- `dr` CLI (authenticated via `dr auth login`)
- `uv`, `task`, `pulumi`, `node`, `npm`
- `DATAROBOT_ENDPOINT` and `DATAROBOT_API_TOKEN`

## Quick Start

```bash
uv sync --all-extras
task qa
superrobot import ./path/to/your-agent
```

## Commands

| Command | Description |
|---|---|
| `superrobot import <path>` | Brownfield: Scan → Analyze → Generate → UI → Eval → Deploy |
| `superrobot new` | Greenfield wizard |
| `superrobot template` | Browse DR templates |
| `superrobot scan <path>` | Stage 1 only (JSON output) |
| `superrobot generate <path>` | Stages 1-3 (writes files) |

## Development

```bash
task qa          # lint + format-check + mypy + unit tests
task test-all    # includes integration tests (needs DR credentials)
```

See [AGENTS.md](AGENTS.md) for full architecture and DR platform rules.
