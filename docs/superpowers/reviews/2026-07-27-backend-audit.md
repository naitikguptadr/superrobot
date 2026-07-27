# Backend Code Review — Findings Log (2026-07-27)

**Status: IDENTIFY-ONLY. Nothing here has been fixed.** This is a findings record for triage.

**Scope:** the Python backend, `superrobot/` — 6,059 LOC. The `superrobot/pipeline/graph/` package (~1,250 LOC) was intensively reviewed earlier in this session and is excluded except where it's the *reference* for a bug class found elsewhere.

**Method note on trust:** every finding below is tagged **[VERIFIED]** (I reproduced it and show the output) or **[INSPECTION]** (read from the code, not executed). I have not silently upgraded inference to fact. The `[INSPECTION]` items are real code paths but their user-visible impact is reasoned, not observed.

**Coverage caveat:** this was done single-threaded after a parallel subagent sweep failed on an org spend limit. It is a broad sweep plus deep dives on the highest-risk paths — not the exhaustive per-line pass originally planned. Areas explicitly **not** deeply covered are listed at the end so the gap is visible rather than implied-complete.

---

## Critical

### C1. `--json` emits non-JSON on every error path [VERIFIED]

**Where:** `superrobot/cli.py` — confirmed at `:337` (validate), `:639`/`:713` (auth checks); same shape at `:398`, `:403`, `:632`.

The error branches `console.print(...)` to **stdout** and return *before* reaching the `if json_out:` block, so `--json` yields Rich-rendered plain text instead of JSON.

```
$ superrobot validate /nonexistent/path --json     # exit=2
Not a directory /nonexistent/path
→ json.decoder.JSONDecodeError: Expecting value: line 1 column 1

$ superrobot memory ensure demo --config-dir /tmp/emptycfg --json   # exit=1
Not authenticated — run superrobot setup
→ json.decoder.JSONDecodeError: Expecting value: line 1 column 1
```

**Why it's Critical:** this is *the same bug class* already fixed once in this file. `cli.py:577-580` carries a comment explaining that stdout warnings corrupted `--json` and routes them to `console_err` — but only for deploy warnings. Every error path still has it. The Pi shell's `cli-bridge.ts` parses this stdout as JSON, so in the conversational shell these surface as a parse failure instead of "Not authenticated" — the user is told the wrong thing about why their command failed.

**Shape of the fix (not applied):** route pre-JSON error output to `console_err`, or emit `{"error": ...}` on stdout. Note `cli.py:768` already does the latter for empty receipts, so the correct pattern exists in-file and just isn't applied consistently.

---

## Important

### I1. Jinja has no `StrictUndefined` — a typo'd template variable silently generates blank output [VERIFIED]

**Where:** `superrobot/pipeline/config_generator.py:41-43`

```python
>>> t = env.from_string("value={{ definitely_missing_var }}|end")
>>> t.render()
'value=|end'          # silently empty, no error
```

**Why it matters:** these templates generate the Python/YAML/Dockerfile that gets **deployed to DataRobot**. If `config_generator` fails to pass a variable — or a template references a renamed one — the generated artifact is silently missing that value rather than failing the build. Given 15 templates and a hand-maintained context dict, this is a live drift risk, and the failure mode is a broken deployment discovered at runtime rather than an error at generate time.

### I2. `autoescape=select_autoescape()` is correct only by accident [VERIFIED]

**Where:** `superrobot/pipeline/config_generator.py:43`

`select_autoescape()` keys off file extension and enables escaping for `.html`/`.htm`/`.xml` only. Every template here is `.j2`, so it evaluates **False** — confirmed. That's the right outcome (HTML-escaping generated Python would corrupt it), but it's incidental: renaming a template to anything HTML-ish would silently start escaping and produce subtly broken code. The intent should be explicit (`autoescape=False` with a comment) rather than a coincidence of the extension check.

### I3. Raw exception text flows into on-disk receipts that claim to be "non-secret" [INSPECTION]

**Where:** `superrobot/pipeline/workload_deployer.py:133` and `:159` (`error_message=str(exc)`) → `cli.py:690` → `Receipt.error_message` → persisted to disk.

`superrobot/models/receipt.py:13` documents the model as a *"Non-secret record of a single `superrobot deploy` attempt."* Nothing enforces that. `str(exc)` on an httpx error can carry the full request URL and, depending on the exception, other request context. There is no redaction step between the exception and the persisted file.

**Honest scope:** I did **not** demonstrate a token landing in a receipt — that would need a live failing deploy. What I did establish is that the "non-secret" guarantee is *unenforced by construction*: arbitrary exception text is persisted unsanitized. Given the codebase handles `DATAROBOT_API_TOKEN` and provider keys, this deserves a redaction pass or an explicit sanitizer before it can be claimed non-secret.

### I4. Code rewriting fails silently, producing an unmigrated package [INSPECTION]

**Where:** `superrobot/pipeline/ast_migrate.py:174-176` and `:191-193`

```python
try:
    return ast.unparse(new_tree) + "\n", rewriter.rewrites
except Exception:
    return content, 0        # original content, zero rewrites reported
```

If `ast.unparse` raises, the function returns the **original unmigrated source** and reports `0` rewrites. The caller cannot distinguish "nothing needed rewriting" from "rewriting was attempted and failed." Imports stay nested, which is precisely what DRUM's flat-bundle requirement forbids — so the package deploys and then fails at runtime with an import error, with no warning at generate time.

Note the asymmetry: the `except SyntaxError` at the top of each function is legitimate (skip unparseable files), but this second catch silently discards a *successful parse that failed to re-emit* — a different and more alarming case.

### I5. Three divergent directory-exclusion lists [INSPECTION]

| Module | Excludes |
|---|---|
| `pipeline/scanner.py:17` | `.venv, node_modules, .superrobot, .git, __pycache__, tests, test, .tox, dist` |
| `pipeline/config_generator.py:61` | `.venv, node_modules, .superrobot, .git, __pycache__, tests` |
| `pipeline/graph/builder.py:26` | `__pycache__, venv, .venv, node_modules, .git` |

Concrete consequences:
- `config_generator` **migrates files from `test/`, `.tox/`, and `dist/`** that `scanner` never analyzed — shipping code into the generated package that no analysis stage ever saw.
- `graph/builder` does **not** exclude `.superrobot`, so re-scanning an already-transformed repo pulls the tool's own generated output into the call graph, which can skew framework detection and reachability.
- Only `graph/builder` excludes bare `venv` (no dot).

These should be one shared constant.

### I6. `git clone` has no timeout [INSPECTION]

**Where:** `superrobot/repo.py:42-51`

Every other subprocess and HTTP call in the codebase sets a timeout (`cli_wrapper` 30s, httpx clients 30s, probes 20s, evaluator 30s). The clone is the sole exception: an unresponsive host hangs `superrobot scan <url>` indefinitely with no output and no way to know why.

### I7. Cloned repos are never cleaned up [INSPECTION]

**Where:** `superrobot/repo.py:41` — `tempfile.mkdtemp(prefix="superrobot-clone-")`

The temp directory is created and returned; nothing removes it, on success or failure. Every `superrobot scan <github-url>` leaves a full repo clone in `/tmp` forever. `mkdtemp` permissions (0700) are correct, so this is disk leakage rather than an exposure issue.

### I8. Timed-out eval subprocesses are orphaned [INSPECTION]

**Where:** `superrobot/pipeline/evaluator.py:79-88`

```python
stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=...)
except TimeoutError:
    return DrCommandResult(returncode=124, ...)
```

`wait_for` abandons the wait but never kills `proc`. The evaluator runs **five** iterations; a hanging agent leaves five orphaned Python processes still executing user code — potentially still making LLM/network calls — after the command returns. Needs `proc.kill()` + reap in the timeout branch. (`dr/cli_wrapper.py:83-90` has the identical pattern.)

### I9. Evaluator executes an interpreter chosen from the scanned repo [INSPECTION]

**Where:** `superrobot/pipeline/evaluator.py:50-53`

```python
repo_venv = Path(cwd).resolve().parent / ".venv" / "bin" / "python"
if repo_venv.exists():
    return str(repo_venv)
```

For a repo the user imported but does not trust (the tool's whole premise is pointing it at *foreign* agent repos, including from a GitHub URL), a `.venv/bin/python` committed to that repo is executed directly. That is arbitrary code execution sourced from untrusted input, and it happens before any user confirmation.

Related, same file `:76`: the subprocess inherits `{**os.environ}`, handing the full environment — including `DATAROBOT_API_TOKEN` and every provider key — to that code.

**Framing:** `eval` legitimately must run the agent, so *some* execution is inherent. The issues are (a) the interpreter itself being repo-controlled rather than SuperRobot's own, and (b) unconditional full-environment inheritance where a filtered allowlist would do.

---

## Minor

### M1. `read_text()` without explicit encoding at 13 sites [INSPECTION]

`receipts.py:56,64` · `scanner.py:484,491` · `gap_analysis.py:71,86,177` · `workload_deployer.py:36,60` · `config_generator.py:57` · `dr/llm_gateway.py:96` · `setup/config.py:48,80`

Relies on the platform locale rather than UTF-8. Notable because the codebase *already knows better* — `scanner.py:131` and `:511` correctly use `encoding="utf-8", errors="replace"`, so this is drift within a single file, not an unconsidered default. `setup/config.py:48,80` is the sharpest instance: it reads the token/state file, so a locale mismatch surfaces as a confusing auth failure.

### M2. `scanner.py` dependency parsing lacks the robustness its own file applies elsewhere [INSPECTION]

**Where:** `scanner.py:484` (`req.read_text()`), `:491` (`pyproject.read_text()`)

Both lack the `encoding=` and `except OSError` guards that `:131` and `:511` in the same file correctly use. An unreadable or non-UTF-8 `requirements.txt` — e.g. a dangling symlink, the exact class of bug already found and fixed in `graph/builder.py` — raises out of `scan()` and fails the whole command.

### M3. Local-path check precedes URL parsing [INSPECTION]

**Where:** `superrobot/repo.py:32-34` — `if local.exists(): return local.resolve()` runs before `parse_github_url`. A local directory whose name happens to match a URL-ish string silently shadows the remote. Low impact, but the precedence is undocumented.

---

## Explicitly cleared (checked, found safe)

Recording what was examined and dismissed, so the flags above are interpretable:

- **No shell injection.** Every subprocess (`repo.py:42`, `evaluator.py:66`, `cli_wrapper.py:30,68`, `probes.py:27`) uses `asyncio.create_subprocess_exec` with an argv list. **No `shell=True` anywhere; no `os.system`.**
- **`repo.py` URL handling is sound.** `parse_github_url` doesn't pass user input through — it regex-captures `user`/`repo` and *reconstructs* `https://github.com/{user}/{repo}.git`. The result always starts with `https://`, so it can't be reinterpreted as a `git` option flag (the `--upload-pack` class of attack).
- **No unsafe deserialization.** `yaml.safe_load` is used (`workload_deployer.py:60`); no `pickle`, no `marshal`, no bare `yaml.load`. The one `__import__` (`evaluator.py:29`) is inside the intentional agent-runner and takes a module name the tool itself derived.
- **HTTP timeouts are present** on all httpx clients (30s / 20s probes) — `git clone` (I6) is the lone gap.
- **Receipt model has no field that structurally holds a credential** — `error_message` (I3) is the only free-text field and the only concern.
- **`os.execvp` in `cli.py:115`** passes an argv list and resolves `node` via `shutil.which` with error handling; no injection path found.

---

## Not covered — known gaps in this review

Stated plainly so this isn't mistaken for exhaustive:

- `dr/llm_gateway.py` (206 LOC), `dr/workload_client.py`, `dr/memory_client.py` — read only for timeout/secret patterns, **not** line-by-line for API-contract correctness or pagination.
- The **15 Jinja templates** — the environment config was tested (I1/I2), but individual template *output correctness* was not rendered and validated.
- `setup/` — swept for secrets and timeouts; `endpoints.py` normalization and `SetupState` schema-compat were not probed with edge-case inputs.
- `schema_inference.py`, `analyzer.py` LLM-response validation — not examined.
- **The known test-order flake** (`test_memory_ensure_blocked_without_auth`, passes alone / fails in full run) — not root-caused. Evidence points at cached capability-probe state leaking between tests; the exact global was not identified.
- No **fuzzing / property testing** of `scanner.py` detection against adversarial repo shapes.

## Suggested triage order

1. **C1** — verified, user-visible, breaks the conversational shell's error reporting, and the correct pattern already exists in the same file.
2. **I4, I1** — both silently produce broken deployed artifacts; failure surfaces late and confusingly.
3. **I3** — unenforced security guarantee; needs a redaction pass before the "non-secret" claim holds.
4. **I8, I9** — process/credential hygiene around executing untrusted code.
5. **I5, I6, I7** — correctness and resource-leak cleanups.
6. **M1–M3** — consistency drift.
