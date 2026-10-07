# cua: record once, replay many

A small, working version of a computer-use integration layer for legacy back-office apps that have no API.

- **Discover:** an LLM drives the live application once to reach a goal.
- **Compile:** the run becomes a typed, versioned, parameterised capability file.
- **Replay:** that file runs deterministically, with new inputs and no model, and returns a structured result.
- **Escalate:** when automation cannot safely continue, a human takes over the same live session and hands it back.

The design write-up is in [REPORT.md](REPORT.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync
uv run playwright install chromium
```

Configuration is all environment variables. Copy `.env.example` to `.env` and fill it in; the CLI and `scripts/demo.sh` read `.env` automatically, and it is gitignored.

| Variable | Needed for | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `discover`, `probe` | Not needed for `replay` or the tests |
| `HARBOR_USERNAME`, `HARBOR_PASSWORD` | every run against the mock app | Mock credentials: `teller01` / `harbor-demo-pw` |
| `CUA_MODEL_DISCOVERY`, `CUA_MODEL_PROBE`, `CUA_MODEL` | optional | Default `claude-opus-5-5`; needs a Claude Opus 5.x or Sonnet 5.x model |

## Demo path

Start the target app, a mock credit-union back office. It looks like a normal product, but its markup is deliberately hostile (framesets, nested layout tables, no ids or classes, unlabeled fields); the styling is one stylesheet of element selectors laid over that structure:

```bash
uv run python -m mockapp.server            # http://127.0.0.1:8765
export HARBOR_USERNAME=teller01 HARBOR_PASSWORD=harbor-demo-pw
export ANTHROPIC_API_KEY=...
```

1. Run the agent on a goal. It writes `capabilities/member.read_savings_balance.yaml` and then verifies the artifact by replaying it twice.

```bash
uv run python -m cua discover \
  --name member.read_savings_balance \
  --goal "Look up member {{member_id}} and read their current share savings balance." \
  --param member_id=10042 --verify-param member_id=10077
```

2. Replay the artifact with a different input. No model is involved.

```bash
uv run python -m cua replay capabilities/member.read_savings_balance.yaml --param member_id=10077
```

3. Teach it a business outcome, then see it reported as one.

```bash
uv run python -m cua probe  capabilities/member.read_savings_balance.yaml --param member_id=99999
uv run python -m cua replay capabilities/member.read_savings_balance.yaml --param member_id=88888
```

4. Inject a runtime failure and hand the stuck session to a human. Use `--headed` to work in the browser window, then type `retry`, `done` or `abort` in the terminal.

```bash
curl 'http://127.0.0.1:8765/__fault?error=1'
uv run python -m cua replay capabilities/member.read_savings_balance.yaml --param member_id=10042 \
  --operator console --headed
```

`scripts/demo.sh` runs the whole story (both capabilities, every outcome class, the second tenant) and regenerates `capabilities/` and `evidence/`.

Other faults: `notice=1`, `expire=1`, `slow=2500`, `flat=1` (layout drift), `lost=1` (response lost after a commit). `curl .../__reset` restores the app.

### Without live services

The tests need no API key and no running server. They start the mock app themselves and replace the model with a scripted stand-in that reads the same screen text and picks refs from it, so the agent loop, compiler, replay engine, guardrails and handoff all run for real.

```bash
uv run pytest
```

`replay` never needs an API key, so the checked-in capabilities can be replayed with only the mock app running.

## Web console

A small React console shows what the system did: the saved capabilities, every recorded run as a readable timeline with its result and failure screenshot, and a live operator view for the human handoff.

```bash
npm --prefix console install && npm --prefix console run build   # once
uv run python -m cua console                                     # http://127.0.0.1:8770
```

To take over a stuck run yourself: start the mock app, open **Human in the loop**, and press **Start a stuck run**. A browser window opens on an error page and the request appears in the console. Click **Member Lookup** in that window, then **Retry the step** in the console; the run verifies the screen and finishes. Any replay started with `--operator web` sends its requests to the same console.

The console only reads `capabilities/`, `evidence/` and `runs/`, and answers handoff requests through the `runs/_pending` queue. It never drives the application.

## Evidence

`evidence/` holds the output of one full `scripts/demo.sh` run: the two saved artifacts, and one folder per step. Each run folder has `events.jsonl` (what happened and why), `result.json` (redacted), and on failure a screenshot, an accessibility snapshot and the intervention request. Discovery and probe used `claude-opus-5-5` against the live mock app.

| Folder | Shows |
|---|---|
| `01-discovery-read-balance` | Real LLM discovery, plus the two verification replays |
| `02-probe-not-found` | Model names the "no member on file" screen `MEMBER_NOT_FOUND` |
| `03-replay-success` | Deterministic replay with a different member |
| `04-replay-not-found`, `05-replay-permission-denied` | Business outcomes, not failures |
| `06-replay-invalid-param` | Bad input refused in preflight |
| `07-replay-recovered-*` | System notice dismissed; session expiry re-signed-on and restarted |
| `08-replay-app-error` | Hard failure with screenshot and snapshot |
| `09-replay-human-handoff` | Stuck run handed to an operator, their action recorded, run resumed |
| `10-replay-layout-drift` | Primary locator fails, fallback matches, drift warning |
| `11-replay-second-tenant` | Same artifact on the relabelled `lakeside` tenant via overrides |
| `12-discovery-open-sub-account` | Real LLM discovery with an approval request for the irreversible click |
| `13-probe-validation-error` | Model names the below-minimum deposit screen as a business outcome |
| `14-replay-irreversible-refused` | Draft artifact refused |
| `15-replay-irreversible-committed` | Approved and confirmed: `side_effects: committed` |
| `16-replay-commit-unknown` | Response lost after commit: `needs_human`, `side_effects: unknown`, not retried |
| `17-replay-slow-load` | A slow page absorbed by the checkpoint wait and logged |
| `18-replay-validation-outcome` | The probed outcome on replay: deposit below minimum, nothing committed |
| `19-replay-dry-run` | Irreversible capability resolved up to the commit, then stopped |
| `20-replay-expiry-at-commit` | Session expires as the commit is clicked: `UNSAFE_RESTART`, flow not re-run |
| `21-replay-second-tenant-irreversible` | The irreversible capability on the `lakeside` tenant |
| `22-discovery-policy-blocked` | Real LLM run with a goal outside the allowlist: the click is blocked, the model gives up, no artifact is saved |

Two things in that run were not done by a person: the operator in step 09 is played by `scripts/handoff_demo.py`, and the approval in step 12 was piped to the console prompt. To take over a session yourself, use step 4 of the demo path above.

## Commands

| Command | What it does |
|---|---|
| `discover` | LLM-driven run; writes a draft capability and verifies it by replay |
| `probe` | Replays with a bad input (dry run) and asks the model to name the resulting outcome |
| `approve` | Marks a verified capability approved, which irreversible steps require |
| `replay` | Deterministic execution; prints the result as JSON. Exit code 0 success or business outcome, 1 failed, 2 needs human |
| `console` | Web console for capabilities, evidence and the operator handoff |

Useful `replay` flags: `--confirm` (caller's consent to irreversible steps), `--dry-run` (stop before the first irreversible step), `--tenant lakeside`, `--operator console` or `--operator web`.

## Layout

```
cua/schema.py     capability artifact, app profile, tenant binding, result contract
cua/surface.py    the surface seam, and target derivation from an accessibility tree
cua/web.py        Playwright surface: perception, unique target resolution, policy choke point
cua/replay.py     deterministic replay engine and error taxonomy
cua/agent.py      discovery loop and outcome probe (the only model callers)
cua/compiler.py   trace -> capability
cua/handoff.py    control lease, intervention requests, operator seam
cua/console.py    web console backend (read-only views, operator queue)
console/          React web console
cua/policy.py     allowlist and risk classification
cua/redact.py     redaction;  cua/evidence.py  run logs
apps/             app profile per vendor product (login, global recoveries, policy)
tenants/          per-institution binding (URL, secret sources, target overrides)
capabilities/     saved artifacts
mockapp/          the target application and its fault injection
evidence/         artifacts and logs from real runs
```

## What is mocked

- **Target app:** local and fake; all member data is invented.
- **Operator console:** minimal. It is a terminal prompt (`--operator console`) or the web console (`--operator web`), and the human works in the automation's own browser window on the same machine; there is no remote co-browsing. The control lease, the intervention request, the capture of human actions and the resume logic are real.
- **Desktop surface:** not built. `cua/surface.py` is the seam it would implement.
