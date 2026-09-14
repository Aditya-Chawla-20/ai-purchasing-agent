# Testing and Evaluation Strategy

## 1. Evaluation philosophy

Business correctness is evaluated deterministically. Agent quality is evaluated separately through required-information coverage, allowed tool usage, grounded explanation, and recovery behavior. A model judge is optional and is never the only correctness oracle.

Scenario 1 may safely modify a recommendation to a constrained quantity and must expose `raw_need`, `proposed_quantity`, and the remaining `unresolved_quantity`. Supplier-shortfall and demand-recovery plans are stricter: incomplete recovery coverage is advisory only, produces `INVESTIGATE`/`NEEDS_ATTENTION`, and cannot be approved or executed.

## 2. Test layers

### Unit tests

- inventory position and target-stock formulas;
- budget, storage, supplier-availability, and MOQ boundaries;
- decision precedence;
- evidence freshness and conflict detection;
- state-transition guards;
- validation field comparisons;
- idempotency request equality;
- explanation schema and fallback renderer;
- multi-supplier allocation objective, dominance pruning, stable tie-breaks, and final-line MOQ overbuy;
- demand velocity threshold, version ordering, and scope/window consistency;
- tool-call scope, phase, round, uniqueness, and attempt guards.

Use table-driven tests and a frozen clock. Property tests SHOULD verify that feasible quantity is never negative and never violates hard caps.

### Integration tests

- repositories and migrations against temporary SQLite;
- each tool envelope and error mapping;
- workflow checkpoint, interrupt, reload, and resume;
- pre-execution evidence change forces new proposal version;
- create response loss followed by idempotent retry;
- supplier confirmation triggers recovery once;
- bounded tool-loop provider failover and mandatory evidence completion;
- multi-line action idempotency and aggregate validation;
- approved action provider exhaustion, durable wait, refresh, and retry;
- demand signal starts one deduplicated demand-change review.

### API contract tests

- success and problem response schemas;
- authorization by role;
- stale approval returns `409`;
- repeated mutation with same key is stable;
- timeline pagination ordering;
- execution-retry state/version authorization;
- mock demand/custom scenario endpoints are unavailable outside development/test;
- custom scenarios cannot overwrite seeded or unrelated records.

### Frontend tests

- state-specific rendering;
- facts/calculations/AI text are labeled distinctly;
- approval confirmation contains exact proposal values;
- double submission is prevented;
- stale proposal refresh behavior;
- critical keyboard and accessible-name checks;
- supplier comparison/allocation and per-line validation rendering;
- demand coverage chart has numeric/table equivalents;
- custom scenario validation and execution-retry behavior.

### End-to-end/evaluation tests

Run the workflow with fake tools and deterministic explanation by default. A small optional model-enabled suite checks schema compliance and groundedness but must not make CI dependent on a paid key.

## 3. Required scenario matrix

| ID | Setup | Expected decision/action | Critical assertions |
|---|---|---|---|
| `EV-001` | 800 recommended; calculated feasible need 800 | `ACCEPT`; approval; PO 800 | all evidence read, constraints pass, validation passes |
| `EV-002` | 800 recommended; open PO/storage/budget reduce feasible quantity to 450 | `MODIFY` to 450 | exact reasons present; never executes 800 |
| `EV-003` | inventory position meets target | `REJECT`; no PO | reason `NO_NET_REQUIREMENT`; completes without approval |
| `EV-004` | forecast missing or inventory stale | `INVESTIGATE`; no PO | deficient source named; no fabricated default |
| `EV-005` | raw need positive but no MOQ multiple fits budget | `INVESTIGATE`; no PO | budget and MOQ failure recorded |
| `EV-006` | buyer rejects valid proposal | rejected terminal state | no write call; comment audited |
| `EV-007` | inventory changes after approval | recalculate and reapprove | obsolete proposal never executes |
| `EV-008` | create response lost then retried | one PO only | same idempotency key; replay indicated |
| `EV-009` | intended 500, observed supplier confirmation 250, remaining need persists | new proposal or escalation | coverage recomputed; no blind order for 250 |
| `EV-010` | created PO has wrong quantity | failed validation and recovery/escalation | never displays success |
| `EV-011` | reserved plus damaged exceeds on hand | `INVESTIGATE`; no PO | inventory marked conflicting |
| `EV-012` | overdue open PO | incoming excluded | need is calculated without late supply |
| `EV-013` | supplier cost or MOQ changes after review | new approval required | original proposal never executes |
| `EV-014` | model omits budget and storage reads | manifest adds them before decision | conclusive snapshot contains every required fact |
| `EV-015` | invalid/duplicate/out-of-scope model calls | reject calls; bounded retry or attention | no unauthorized adapter call occurs |
| `EV-016` | Gemini tool loop unavailable; Groq agent succeeds | investigation continues on Groq agent model | same deterministic decision and complete audit trace |
| `EV-017` | all action-tool providers fail after approval | `AWAITING_EXECUTION_RETRY`; no PO | approval retained; retry refreshes evidence |
| `EV-018` | two suppliers required to cover shortfall | approved multi-line plan and two validated POs | reliability-first objective; exact per-line idempotency |
| `EV-019` | cheaper supplier is less reliable but both plans cover need | higher-reliability plan wins | cost is applied only after coverage and reliability |
| `EV-020` | only partial allocation is feasible | `INVESTIGATE`; no approval | advisory partial plan shown; no knowingly incomplete write |
| `EV-021` | one of several plan-line writes fails | `NEEDS_ATTENTION` | successful PO retained and never duplicated |
| `EV-022` | actual velocity >= 1.5x and revised demand exceeds coverage | supplemental sourcing plan | existing PO unchanged; new plan approved and validated |
| `EV-023` | demand spike but current/incoming supply covers target | complete with no action | sales/forecast evidence retained; no new PO |
| `EV-024` | stale sales or conflicting forecast scopes/versions | `INVESTIGATE` | deficient evidence named; no purchasing change |
| `EV-025` | custom scenario submitted in development | normal workflow executes expected outcome | persisted isolated facts; no parallel calculation path |

## 4. Per-run evaluation record

Each evaluation captures:

```json
{
  "scenario_id":"EV-002",
  "expected":{
    "decision":"MODIFY",
    "quantity":450,
    "required_tools":["get_inventory","get_demand_forecast"],
    "forbidden_tools":["create_purchase_order_before_approval"],
    "validation":"PASSED"
  },
  "actual":{},
  "checks":[
    {"name":"decision_correct","passed":true},
    {"name":"constraints_respected","passed":true},
    {"name":"result_validated","passed":true}
  ]
}
```

## 5. Grounded explanation checks

The explanation passes only when:

- every numeric value equals a field in the decision package;
- every important factor contains a known reason code and valid evidence reference;
- no unsupported supplier, budget, demand, or inventory claim appears;
- the next action matches workflow state;
- schema validation succeeds.

A deterministic parser can check numbers/references. Human rubric review covers clarity and concision. DeepEval MAY later measure tool correctness; RAGAS is not required because the MVP has no RAG pipeline.

## 6. Traceability matrix

| Requirement | Primary evidence |
|---|---|
| `FR-001`-`FR-005` | API/tool contract tests, `EV-004` |
| `FR-006`-`FR-009` | bounded-agent tests, `EV-014`-`EV-016` |
| `FR-010`-`FR-015` | domain unit tests, `EV-001`-`EV-005` |
| `FR-016`-`FR-018` | allocation/demand tests, `EV-018`-`EV-024` |
| `FR-020`-`FR-025` | workflow/API integration tests, `EV-006`-`EV-008` |
| `FR-026`-`FR-029` | guarded action and retry tests, `EV-017` |
| `FR-030`-`FR-034` | validation/recovery tests, `EV-009`-`EV-010` |
| `FR-035`-`FR-036` | multi-line validation tests, `EV-018`-`EV-021` |
| `FR-040`-`FR-046` | frontend, custom-scenario, environment, and audit reconstruction tests |
| `NFR-001`-`NFR-011` | CI suite, dependency checks, redaction/idempotency and provider-normalization tests |

## 7. Release gate

The expanded build is releasable when all required scenario tests pass, no hard-constraint or duplicate-action defect is open, migrations work on a clean database, frontend build succeeds, and the setup/demo runbook has been executed from a clean checkout.

The default test configuration sets `LLM_ENABLED=false`; it must not contact configured provider
keys from a local `.env`. Provider adapter tests may opt in explicitly with mocked clients.

Optional live tests use explicit markers and never run in the default suite. They verify Gemini tool declarations and Groq `GROQ_AGENT_MODEL` function calls independently from explanation-model tests. NVIDIA remains excluded from live tool tests until enabled as a declared tool-capable model.
