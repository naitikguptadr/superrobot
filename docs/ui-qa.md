# SuperRobot UI visual QA

Checked against the real `@mariozechner/pi-coding-agent` theme/extension API
(`shell/node_modules/@mariozechner/pi-coding-agent/docs/{themes,extensions}.md`) —
earlier versions of this checklist assumed a theme/env-var surface Pi doesn't
actually have (no background-color token, no typography tokens, no motion tokens,
no `PI_THEME`/`OPENAI_*`/`SUPERROBOT_*` env-var config surface). The wiring in
`shell/src/cli.ts` + `shell/extensions/superrobot.ts` was rewritten against the
real API and verified end-to-end (build, typecheck, and a live `pi --print` run
that reached the real DataRobot Gateway endpoint — see `HANDOFF.md`).

1. First viewport is brand-first (SuperRobot + DataRobot signal), not generic chat
   chrome. **Done** — `cli.ts` prints the SuperRobot banner to stderr before handing
   off to Pi.
2. Removing the word "SuperRobot" still does not look like stock Pi. **Partial** —
   banner + capability-chip footer are SuperRobot-specific; Pi's own chat chrome
   underneath is unmodified (no such hook exists beyond theme colors/system prompt).
3. Accent color uses `shell/theme/superrobot.theme.json`'s teal accent token, not
   purple glow or default gray terminal. **Done**, but scoped correctly this time:
   Pi's theme schema (51 required tokens, see `docs/themes.md`) has **no background
   color token at all** — it colors foreground/accent/border/syntax, not the
   terminal background. "Deep navy background" was never achievable through Pi's
   theme system; dropped from this checklist rather than claimed.
4. ~~Expressive typography (theme stack)~~ — **removed**. Pi's theme schema has no
   typography/font tokens; terminal font is controlled by the user's terminal
   emulator, not by Pi or SuperRobot.
5. ~~Splash / stage-pulse / deploy-ignite motion~~ — **not implemented**. Pi exposes
   `ctx.ui.setWorkingIndicator()` (a real per-frame spinner hook) but nothing tied to
   SuperRobot's own pipeline stages (scan/analyze/generate/deploy run in the separate
   Python CLI, outside the Pi session). Would need a concrete design, not a checklist
   item to silently claim.
6. Capability chips (Gateway / Workload / Memory / Agent App) are visible after
   setup. **Done** — `shell/extensions/superrobot.ts`'s `session_start` handler reads
   `~/.config/superrobot/setup.json` directly and calls `ctx.ui.setStatus()`, a real,
   persistent footer status line (verified structurally; the footer's actual visual
   position/appearance still needs a live interactive terminal to eyeball).
7. No card soup / pill cluster / emoji decoration. Still true — chip line uses plain
   `●`/`○` glyphs, not emoji.

## Known limitation

Items 1, 2, and 6's actual on-screen appearance need a human at a live interactive
terminal — there is no tool available in this environment that attaches to and
renders an interactive TTY session the way a browser-pane tool renders a web page.
What *is* verified without one: `npm run build`, `npm run typecheck` (including
`shell/extensions/tsconfig.json`), and a live non-interactive `pi --print` run
confirming the extension loads, the DataRobot Gateway provider registers, and
`pi.setModel()` selects it (the run reached the real Gateway endpoint and got back
an expected 401 for a placeholder token — proof the request path is wired
correctly end-to-end, short of a real credential).
