# SuperRobot Demo — Voiceover Script

Cue-based, not timestamped — read each block as that moment appears on screen.
Keep it conversational; pause between blocks rather than rushing.

---

## 1. Cold open — before anything is typed

> "This is SuperRobot — it takes an existing Python agent, one you didn't build
> for DataRobot, and gets it running on the platform without a rewrite. No
> separate CLI to learn. You just talk to it."

*(banner + capability chips on screen: Gateway, Agent App, Workload, Memory)*

> "Those dots at the bottom are live — Gateway, Agent App, Workload, Memory.
> They're telling you exactly what this account is entitled to before you do
> anything."

---

## 2. Typing the import prompt

*(user types: "Import the agent repo at tests/fixtures/langchain_agent,
transform it, and validate it.")*

> "I'm not running a command. I'm just describing what I want. SuperRobot
> figures out the rest."

---

## 3. Scan runs — rail widget appears

*(pipeline box appears, Scan turns active with a spinner, then a green check)*

> "First it scans the repo — no LLM call yet, pure static analysis. It's
> reading the actual code: what framework, what entry point, what environment
> variables it needs. That box on the left is tracking every stage live."

---

## 4. Transform runs

*(Transform goes active → done, file count shown)*

> "Now it generates a DataRobot-compliant package from what it just learned —
> flat imports, runtime parameters wired in three places, everything the
> platform actually requires. That's normally the part a developer spends a
> day getting right by hand."

---

## 5. Validate runs

*(Validate goes active → done, "clean" shown)*

> "Before anything touches production, it runs Gap Analysis — the same
> platform rules DataRobot itself enforces. Clean here means zero blocking
> issues."

---

## 6. Typing the deploy prompt

*(user types the deploy request, referencing the artifact id)*

> "Now the interesting part. I'm asking it to deploy — to Workload API,
> using a container image that was already built."

---

## 7. Confirm dialog appears

*(boxed confirm dialog with warnings)*

> "It doesn't just deploy. It stops and asks. And it tells you exactly what
> you're signing up for — build time, and a real platform gotcha about logs
> getting deleted on a failed Pulumi run. You decide, not the agent."

*(click Yes / confirm)*

---

## 8. Deploy resolves — success

*(deploy row turns green, receipt written)*

> "And that's a real deployment — not a simulation. A moment ago I checked
> the DataRobot console directly: status running, two live replicas."

*(optional: cut to console/UI screenshot showing status: running)*

---

## 9. Close

> "Every step of this — the scan, the generated package, the deploy, even a
> platform bug we found and fixed along the way — happened against a real
> DataRobot environment tonight. That's SuperRobot: bring any agent to
> DataRobot, no rebuild required."

*(end card: `superrobot` / "No rebuild required.")*

---

## Optional B-roll lines (if you cut in anything extra)

- On the slide deck architecture slide: *"Two layers talking to each other —
  a Python engine that does the real migration work, and a Pi-based shell
  that makes it conversational."*
- On the vendored DataRobot skills: *"It also ships with DataRobot's own
  official skills built in, so it already knows things like the Workload API
  in detail — not just what we taught it."*
- If you show the bug-fix moment: *"This wasn't scripted — the first deploy
  attempt actually failed, we found why live, fixed it, and reran it for
  real."*
