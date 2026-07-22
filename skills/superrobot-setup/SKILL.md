---
name: superrobot-setup
description: Configure DataRobot auth, LLM Gateway, and platform capability probes for SuperRobot.
---

# SuperRobot Setup

Use when the user needs to connect SuperRobot to DataRobot.

## Steps

1. Ensure `dr` CLI is installed or guide install from DataRobot docs.
2. Run `superrobot setup` (prefer `dr auth login`, else API token + Platform endpoint).
3. Confirm `superrobot doctor` is ready (endpoint, auth, LLM Gateway).
4. Surface capability chips: Gateway, Agent App, Workload, Memory.

Never request Anthropic/OpenAI keys for the product path.
