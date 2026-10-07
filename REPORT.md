# Design report

## 1. Architecture

One process, synchronous, four parts behind one seam.

```
goal ──> Discovery agent (LLM) ──trace──> Compiler ──> Capability (YAML)
              │                                             │
              ▼                                             ▼
        ┌──────────── Surface: observe / present / read / act ────────────┐
        │   policy allowlist · risk gate · control lease · redaction      │
        └──────────── WebSurface (Playwright)   [DesktopSurface] ─────────┘
              ▲                                             ▲
        Operator (human) <── intervention ──  Replay engine (no LLM) ──> RunResult
```

- **Accessibility tree, not DOM selectors or pixels.** Targets are recorded as role, name and row caption. The target app has no ids and unlabeled inputs, so this is what survives, and desktop apps expose the same representation. A page with poor semantics yields a poor tree, so each target carries a fallback.
- **The model never sees or emits real inputs.** It types `{{member_id}}` and the surface substitutes; screens are shown to it with the same placeholders. Parameterisation falls out for free and credentials never enter a prompt. Sign-on is done by the app profile.
- **The model is confined to `discover` and `probe`.** `replay.py` does not import the LLM module.
- **Verify before saving.** A discovery is "successful" only on the model's word, so the artifact is replayed at once, with the example input and with a different one. The second run catches targets that encode the example's data.
- **Simple over scalable.** No queues or services. The model is one config value per role, default `claude-opus-5-5`: discovery is rare and its mistakes are replayed forever.

## 2. Artifact schema

Three layers, so what is true of a product is stated once (`cua/schema.py`):

| Layer | Holds | Authored by |
|---|---|---|
| **App profile** (per product) | Sign-on, global recoveries and outcomes, hard-failure states, policy | A person, once |
| **Capability** (per task) | Params, outputs, named targets, steps, success target, outcomes, status, provenance | Discovery, then a reviewer |
| **Tenant binding** (per institution) | Base URL, secret sources, target overrides by name | A person, per tenant |

- **A contract first.** `params` and `outputs` are typed with a sensitivity class; a caller need not read the steps.
- **Targets are a named table.** Steps, outcomes and tenant overrides refer to a name, so a tenant can replace one control without touching the flow.
- **A target is an ordered list of locators, each of which must match exactly one control.** The primary is an accessibility locator (`role`, `name`, optional `row` caption); the fallback anchors on visible text. A positional `nth` is a last resort and is flagged for review.
- **Checkpoints are implicit and total.** A step is complete when the next step's target is present (`success` after the last). Every step is verified, with no separate assertion list to go stale.
- **Outcomes belong to the step they can follow**, so "No member on file" is only looked for after the search.
- **`status: draft | approved` and `provenance`** make it reviewable. The file holds no transcript and no data values.

## 3. Determinism & error handling

Three rules give determinism: no model in the loop; a target must resolve uniquely (zero or several matches is an error, never "take the first"); all waiting is condition-based.

After every step the engine waits on a race of known states, in this order:

| Seen | Class | Response |
|---|---|---|
| A profile `failure` state (error page) | Hard failure | Stop: `APP_ERROR` |
| A step or profile `outcome` | Business outcome | Stop, return its name and message |
| A `recovery` condition (sign-on, notice) | Recoverable | Bounded action, then resume or restart |
| The next target | Progress | Continue |
| None by the deadline | Hard failure | `CHECKPOINT_TIMEOUT` |

Slow loads are absorbed by the wait and logged. Failures carry `kind`, `step`, `expected`, `observed`, `retryable`, a masked screenshot and a redacted accessibility snapshot. Bad parameters and missing approval are refused in a preflight, before a browser opens. A value that does not parse as its declared type is `OUTPUT_INVALID`.

**`side_effects: none | committed | unknown`** is on every result. An irreversible step sets it to `unknown` when clicked and `committed` only when the next checkpoint proves it. Until then the engine will not restart the flow (a session expiry at commit becomes `UNSAFE_RESTART`), never offers the operator "retry", and returns `needs_human`. This stops a timeout becoming a double posting.

UI drift is secondary: if the fallback locator matches where the primary failed, the run continues with a `drift:` warning.

## 4. Heterogeneity & multi-tenant

**Surface seam.** The agent, compiler and replay engine depend only on the `Surface` protocol and role/name targets; `web.py` alone imports Playwright.

- **Legacy web** is implemented: frames by name, captions through table rows.
- **Desktop** would be a second `Surface` over UI Automation or the macOS accessibility API, which expose the same tree. Schema and engine are unchanged.
- **No usable tree** (Citrix, canvas): the locator list has room for an image anchor. Not built.

**Multi-tenant reuse.** A capability is keyed to a product and version range, never a tenant; a binding supplies only the differences. Demonstrated: the `lakeside` variant relabels three controls, the unmodified artifact fails cleanly at the first one, and a three-entry override makes it succeed.

**Drift detection.** Built: `--dry-run` resolves a flow up to its first irreversible step and is safe to schedule per tenant; fallback warnings and unused overrides are logged. Not built: a fleet view of these signals, and promoting an override shared by many tenants into the base artifact.

## 5. Escalation & handoff

- **Detecting "stuck".** Discovery: the agent asks, three failed actions in a row, the same call three times, or the step limit. Replay: unresolvable or ambiguous target, checkpoint timeout, application error, exhausted recovery, unsafe restart. An irreversible action in discovery raises an approval request.
- **Routing.** An `Intervention` record carries the capability, step, reason, expected state, screenshot, snapshot and the options offered.
- **Control model.** The session has a lease owned by `automation` or `human`. `Surface.act` refuses unless automation holds it; human input is recorded only while the human holds it. Every transfer is logged.
- **Same live session.** The operator works in the same browser context. An injected listener records their clicks and changes, never typed values.
- **Handing back.** The operator answers `retry`, `done` or `abort`. After `done` the engine re-runs the checkpoint race before continuing. Handoffs are capped, and an unanswered request times out to `needs_human`.

Minimal by design: the operator answers in a terminal or a small web console fed by a file queue, and works in the automation's own browser window on the same machine. Production would park the run, stream the browser to a remote operator and resume by run id; the lease, the record and the queue seam carry over.

## 6. Safety

- **One choke point.** Policy is enforced in `Surface.act` and at the network layer, so discovery and replay cannot differ. Off-allowlist requests are aborted, including a human's.
- **Policy outranks the artifact.** Risk is classified at run time from the control; a step mislabelled `safe` is still refused (tested).
- **Irreversible actions** need human approval in discovery, and in replay both `status: approved` and the caller's `confirm`. They are never retried.
- **Data handling.** Secrets come from the environment by name. Logs, snapshots and saved results are redacted of parameter values, sensitive outputs and SSN-shaped strings; only the caller receives real outputs. Sensitive controls are masked in screenshots.
- **Prompt injection.** Screen text is untrusted; the allowlist and approval gate sit outside the model.

Limits: risk rules are name patterns, so an irreversible control with an innocuous caption is missed until a rule is added. Undeclared PII on a page can appear in a failure screenshot and is visible to the model during discovery. The lease is cooperative: it does not physically stop a person typing while automation runs.

## 7. Cuts

- Desktop surface and image-anchor locators (seam only).
- Remote co-browsing for the operator; asynchronous, resumable runs.
- Pruning exploratory detours from a trace; verification and review cover it.
- LLM-assisted recovery on replay, an agent-facing catalog, code generation.
- Table extraction by column header; only caption/value pairs are supported.

Rough edges: a fixed 150 ms pause after each click; parameter patterns inferred from one example; human steps during discovery are evidence only, not compiled into the artifact.

Next, in order: a fleet drift view built on `--dry-run`; resumable runs with a real operator surface; a tool-calling catalog over `capabilities/`; a desktop `Surface`.
