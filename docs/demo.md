# Golden-path demo

```bash
./scripts/demo.sh
```

Runs the full brownfield pipeline against `tests/fixtures/langchain_agent`, an
isolated `--config-dir` (never touches `~/.config/superrobot/`), and no real
DataRobot credentials:

1. `scan` — static analysis, detects framework/entry point/env vars.
2. `analyze` — deterministic fallback analysis (no Gateway credentials configured
   in the demo, so it falls back rather than calling the LLM Gateway — same code
   path `superrobot analyze` always uses when credentials are absent).
3. `transform` — full Scan → Analyze → Generate (eval skipped for speed), writes a
   generated Agent App + Workload package to a temp dir.
4. `validate` — Gap Analysis against the generated package; the fixture is clean
   (no findings).
5. `deploy --target agent-app --waive` — this is expected to fail cleanly with
   `"dr: command not found — is dr on PATH?"` unless the `dr` CLI happens to be
   installed. That's intentional: it demonstrates the graceful-failure path (see
   `superrobot/dr/cli_wrapper.py`) and that a receipt gets written even when the
   deploy attempt fails outright, not just on a clean success/failure response.
6. `receipt operations` — shows the receipt from step 5.

To see a real deploy or a Workload API call, run `superrobot setup` with real
DataRobot credentials first, then use `superrobot deploy --target agent-app` (needs
`dr` on PATH) or `--target workload --image-uri <uri>` (needs a built/pushed image)
directly instead of this demo script.

See [verification-matrix.md](verification-matrix.md) for how each spec's acceptance
criteria maps to actual tests.
