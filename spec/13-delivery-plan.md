# Step-by-step Delivery Plan

## 1. Execution rule

Build one walking skeleton from recommendation to validated action before adding model wording or visual polish. Every step ends with a runnable verification and a small coherent commit.

## 2. Phases

### Step 0 - repository and quality baseline (30 minutes)

Deliver:

- backend/frontend skeletons;
- dependency manifests and lockfiles;
- `.env.example`, `.gitignore`, formatter/linter/test commands;
- root README with exact setup commands.

Verify: both apps start; backend health endpoint and empty frontend page load.

Suggested commit: `chore: scaffold purchasing agent applications`

### Step 1 - domain policy first (60 minutes)

Deliver:

- domain types, formulas, MOQ/cap rules, decisions, reason codes;
- table-driven unit tests for `EV-001` through `EV-005`.

Verify: unit tests reproduce exact expected quantities without database, graph, or LLM.

Suggested commit: `feat: add deterministic purchasing decision policy`

### Step 2 - persistence, migrations, and seed data (45 minutes)

Deliver:

- minimal tables from the data spec;
- repositories and repeatable seed command;
- seeded 800-unit and failure-path cases.

Verify: migrate a new SQLite file, seed it, and query each required evidence type.

Suggested commit: `feat: add purchasing data store and demo fixtures`

### Step 3 - controlled read tools (45 minutes)

Deliver:

- normalized tool envelopes, freshness checks, fake/mock adapters;
- contract tests for success, not found, stale, and timeout.

Verify: collect one complete evidence snapshot and one deficient snapshot.

Suggested commit: `feat: add typed purchasing evidence tools`

### Step 4 - durable LangGraph review (75 minutes)

Deliver:

- typed workflow state and graph nodes through approval interrupt;
- SQLite checkpointing, lifecycle persistence, audit events;
- interrupt/reload/resume integration test.

Verify: start `EV-002`, reach `AWAITING_APPROVAL`, restart/reload, and resume the same run.

Suggested commit: `feat: orchestrate durable recommendation reviews`

### Step 5 - safe execution and feedback validation (60 minutes)

Deliver:

- pre-execution refresh;
- approval/version guards;
- idempotent mock PO create, read-back, validator, bounded retry.

Verify: `EV-007`, `EV-008`, and `EV-010`; prove exactly one PO exists after retry.

Suggested commit: `feat: execute and validate purchase orders safely`

### Step 6 - API and buyer workspace (75 minutes)

Deliver:

- endpoints from API spec;
- recommendation list and review page;
- evidence, calculation, constraint, approval, action, validation, and timeline UI.

Verify: browser completes `EV-002`; refresh during approval and after completion retains state.

Suggested commit: `feat: add buyer review and approval workspace`

### Step 7 - explanation adapter (30 minutes)

Deliver:

- closed structured prompt, provider adapter, strict output validation;
- deterministic fallback and optional model configuration.

Verify: valid model output, malformed model output, and unavailable provider all preserve the same decision.

Suggested commit: `feat: add grounded decision explanations`

### Step 8 - partial fulfilment recovery (45 minutes, P1)

Deliver:

- deduplicated supplier confirmation endpoint/event;
- coverage recomputation, alternate supplier proposal or escalation;
- `EV-009` end to end.

Verify: a 500-to-250 confirmation never blindly orders the remaining 250.

Suggested commit: `feat: handle partial supplier fulfilment feedback`

### Step 9 - submission hardening (30 minutes)

Deliver:

- complete README, architecture diagram, test/evaluation results;
- screenshots or short demo recording if desired;
- secret scan and clean-checkout verification.

Verify: a fresh clone can follow README, run tests, and execute demo.

Suggested commit: `docs: finalize assignment setup and evaluation`

### Step 10 - bounded agent investigation

Deliver the phase-scoped tool registry, provider-neutral call DTOs, Gemini/Groq-agent adapters, mandatory evidence manifests, loop limits, sanitized traces, and fake-provider tests. Preserve the existing deterministic collector behind test fixtures only where needed for compatibility.

Verify `EV-014` through `EV-016`; the same snapshot still produces the same decision regardless of model call order.

Suggested commit: `feat: add bounded purchasing tool investigation`

### Step 11 - multi-supplier sourcing plans

Deliver normalized plan/line persistence, deterministic lexicographic allocation, supplier inclusion/exclusion evidence, aggregate approval, and per-line idempotent execution/read-back validation.

Verify `EV-018` through `EV-021`, including one failed line without duplicate successful POs.

Suggested commit: `feat: allocate and validate multi-supplier plans`

### Step 12 - approved action tool and retry

Deliver the proposal-ID-only action tool, post-approval tool exposure, capability-aware provider failover, durable `AWAITING_EXECUTION_RETRY`, and authorized refresh-before-retry API/UI.

Verify forged/stale IDs are denied and `EV-017` creates no PO until a provider invokes the valid tool.

Suggested commit: `feat: guard model-invoked approved actions`

### Step 13 - demand-change workflow

Deliver sales/demand-signal migrations and tools, spike policy, Scenario 3 graph entry, supplemental sourcing, demand coverage visual, and repeatable `demand-spike` demo.

Verify `EV-022` through `EV-024`; existing issued POs remain unchanged.

Suggested commit: `feat: respond to demand forecast changes`

### Step 14 - custom evaluator scenarios

Deliver the development/test-only scenario API and compact builder using the same repositories and workflow, plus environment and isolation tests.

Verify `EV-025`, all four decision outcomes, and rejection of the endpoint outside development/test.

Suggested commit: `feat: add custom purchasing scenario lab`

## 3. Scope fallback

If time becomes constrained during the expansion, retain the current Scenario 1/2 baseline and implement Step 10 before breadth. Do not sacrifice idempotency, approval, deterministic policy/allocation, post-action validation, or evaluation to add Scenario 3 or custom inputs.

## 4. Implementation checklist

- [ ] Spec IDs referenced in test names or docstrings.
- [ ] No framework types inside domain policy.
- [ ] No LLM/network required for default test suite.
- [ ] The model can call only the proposal-ID action tool and only after approval.
- [ ] Investigation bounds and mandatory evidence manifests are enforced.
- [ ] Multi-line plans satisfy global and per-supplier constraints.
- [ ] Every write preceded by approval and fresh validation.
- [ ] Every success followed by observed-result validation.
- [ ] Audit events contain no secrets or hidden reasoning.
- [ ] Seed cases and UI labels match the specs.
- [ ] Git history remains intact and secrets are absent.
