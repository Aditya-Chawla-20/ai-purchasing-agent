# Functional and Non-functional Requirements

## 1. Functional requirements

### Intake and evidence

- `FR-001` The system MUST accept a recommendation containing product, node, supplier, quantity, and source reference.
- `FR-002` The system MUST create a durable run with a unique run ID and immutable input snapshot.
- `FR-003` The system MUST collect inventory, forecast, open PO, supplier terms, budget, and storage evidence.
- `FR-004` Each evidence item MUST contain source, observed-at time, effective interval where relevant, and freshness status.
- `FR-005` Missing, stale, invalid, or conflicting required evidence MUST be recorded explicitly; it MUST NOT be silently defaulted.
- `FR-006` The model MUST choose investigation calls only from the typed tools exposed for the current workflow phase and review scope.
- `FR-007` A mandatory evidence manifest MUST add any omitted safety-critical reads before a conclusive decision.
- `FR-008` Model selection MUST stop after three rounds or ten unique model-selected calls. The workflow MUST then complete the mandatory manifest; unresolved required evidence or provider failure produces `NEEDS_ATTENTION`. Manifest-added safety reads do not consume the model-selection cap, and one failed read receives at most two attempts.
- `FR-009` Every tool call MUST retain sanitized arguments, result hash, provider/model, duration, and outcome without storing private chain-of-thought.

### Decision

- `FR-010` Normal Python code MUST calculate inventory position, target stock, raw need, and feasible quantity.
- `FR-011` Hard constraints MUST include MOQ, budget, storage capacity, non-negative integer quantity, and evidence completeness.
- `FR-012` The decision MUST be one of `ACCEPT`, `MODIFY`, `REJECT`, or `INVESTIGATE`.
- `FR-013` A decision MUST include reason codes and evidence references sufficient to reproduce it.
- `FR-014` The LLM MAY produce a buyer-facing explanation only from the decision package supplied to it.
- `FR-015` Failure or unavailability of the explanation model MUST NOT change deterministic calculations and MUST display a template explanation.
- `FR-016` Multi-supplier allocation MUST maximize justified coverage, then reliability, then minimize cost, PO count, and delivery time while enforcing all hard limits.
- `FR-017` A demand-change review MUST compare versioned recent-sales evidence with the baseline and revised forecast before changing the purchasing plan.
- `FR-018` An issued PO MUST NOT be silently modified because demand changed; additional justified need MUST use a supplemental proposal.

### Approval and execution

- `FR-020` The policy engine MUST determine whether approval is required; the MVP policy requires approval for every PO write.
- `FR-021` A run awaiting approval MUST persist and resume using the same run/thread identity.
- `FR-022` Only a pending proposal with a valid decision snapshot MAY be approved or rejected.
- `FR-023` Execution MUST re-fetch volatile evidence and rerun constraints immediately before creating a PO.
- `FR-024` Every write MUST use an idempotency key derived from run ID and action version.
- `FR-025` Rejecting an approval MUST terminate the proposal without creating a PO.
- `FR-026` The only model-callable write MUST be `execute_approved_proposal(proposal_id)`, exposed only after approval; the server MUST reconstruct every action field from the immutable proposal.
- `FR-027` One sourcing-plan approval MUST cover an exact plan version and evidence hash; any changed line MUST require renewed approval.
- `FR-028` If every tool-capable provider fails after approval, the review MUST enter `AWAITING_EXECUTION_RETRY` without writing.
- `FR-029` Retrying execution MUST refresh evidence, retain idempotency keys, and supersede approval when the proposal changes.

### Validation and recovery

- `FR-030` The system MUST read the created PO back from the system of record.
- `FR-031` The validator MUST compare intended and observed supplier, node, product, quantity, cost, status, and idempotency key.
- `FR-032` A mismatch MUST produce a failed validation event and MUST NOT be presented as success.
- `FR-033` A supplier partial-fulfilment event MUST recompute remaining coverage before proposing another action.
- `FR-034` Recovery MUST be bounded; after the configured attempt limit, the system MUST escalate.
- `FR-035` Every sourcing-plan line MUST be written and validated independently; the review MUST complete only when every line passes.
- `FR-036` Partial multi-PO execution MUST retain successful lines, MUST NOT duplicate them, and MUST end in `NEEDS_ATTENTION`.

### Buyer experience and audit

- `FR-040` The UI MUST show input, evidence, calculations, constraints, recommendation, approval controls, action result, and validation result.
- `FR-041` The UI MUST distinguish facts, calculated values, model-authored explanation, and buyer actions.
- `FR-042` Every state transition and external call MUST create an append-only audit event with secrets removed.
- `FR-043` A buyer MUST be able to reload a run and see its current state and timeline.
- `FR-044` The UI MUST show selected tools, mandatory evidence completion, supplier inclusion/exclusion reasons, allocations, and per-PO validation.
- `FR-045` Development/test users MUST be able to create a validated custom scenario that runs through the production-shaped workflow.
- `FR-046` Development/test users MUST be able to trigger a versioned demand signal; production environments MUST reject mock and custom-scenario endpoints.

## 2. Non-functional requirements

- `NFR-001 Correctness`: business-rule functions MUST be deterministic, pure where practical, and covered by boundary tests.
- `NFR-002 Safety`: the LLM MUST have no raw database or unrestricted HTTP capability; its sole write surface accepts only an opaque approved proposal ID.
- `NFR-003 Reliability`: write retries MUST be safe and must not create duplicate purchase orders.
- `NFR-004 Explainability`: every reason displayed MUST map to evidence or a named rule.
- `NFR-005 Maintainability`: API, workflow, domain policy, repositories, and integrations MUST be separate modules with inward-pointing dependencies.
- `NFR-006 Portability`: the core demo MUST run locally using SQLite and mock integrations.
- `NFR-007 Security`: secrets MUST come from environment variables, never source control or audit payloads.
- `NFR-008 Performance`: independent evidence reads SHOULD run concurrently and have per-call timeouts.
- `NFR-009 Accessibility`: UI controls MUST be keyboard accessible, labeled, and not rely on color alone.
- `NFR-010 Testability`: deterministic policy and workflow safety tests MUST run without an LLM or network; model tool-selection behavior is covered by fake provider turns and optional live contract tests.
- `NFR-011 Provider portability`: provider-specific messages and function-call responses MUST be normalized behind one capability-aware adapter contract.

## 3. Global acceptance criteria

1. A conclusive decision cannot be created if a required fact is absent or stale.
2. A hard constraint cannot be overridden by explanation text or buyer input.
3. Approval of an obsolete snapshot causes re-evaluation, not blind execution.
4. Repeating an execute request with the same idempotency key returns the original PO.
5. Success is shown only after post-action validation passes.
6. A test can reconstruct each decision from stored input, evidence, policy version, and calculation output.
7. A model cannot change supplier, quantity, price, currency, node, or idempotency key through the action tool.
8. A multi-supplier review cannot complete while one plan line lacks passed read-back validation.
9. A demand-spike plan cannot be conclusive without fresh, mutually consistent sales and forecast evidence.
