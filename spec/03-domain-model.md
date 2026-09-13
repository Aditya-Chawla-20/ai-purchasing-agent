# Domain Model and Invariants

## 1. Ubiquitous language

| Term | Definition |
|---|---|
| Recommendation | External suggestion to buy a quantity; it is input, not truth |
| Review | One durable investigation and decision lifecycle |
| Evidence | Versioned fact retrieved from an authoritative source |
| Inventory position | On hand plus eligible incoming quantity minus reserved quantity |
| Planning horizon | Supplier lead time plus configurable review period |
| Target stock | Forecast demand during the horizon plus safety stock |
| Raw need | Non-negative target stock minus inventory position |
| Feasible quantity | Quantity remaining after MOQ, budget, and storage rules |
| Proposal | Versioned, approval-ready action derived from a decision snapshot |
| Sourcing plan | Immutable proposal containing one or more supplier allocations for one product and node |
| Investigation | Bounded sequence of typed tool selections and normalized observations |
| Demand signal | Versioned notice that actual sales velocity may have diverged from baseline demand |
| Action | Controlled attempt to create or change a PO |
| Validation | Comparison between intended action and observed system result |
| Escalation | Terminal human-attention outcome when automation cannot proceed safely |

## 2. Core aggregates

### PurchasingReview

Owns lifecycle state, immutable input, evidence snapshot references, current decision, proposal version, approval, action references, validation, and recovery count.

Allowed states:

```text
CREATED
INVESTIGATING
COLLECTING_EVIDENCE
EVALUATING
AWAITING_APPROVAL
AWAITING_EXECUTION_RETRY
EXECUTING
VALIDATING
REPLANNING
COMPLETED
REJECTED_BY_BUYER
NEEDS_ATTENTION
FAILED
```

Terminal states are `COMPLETED`, `REJECTED_BY_BUYER`, `NEEDS_ATTENTION`, and `FAILED`.

### PurchaseProposal

Contains one `SourcingPlan`, evidence snapshot hash, decision version, and approval requirement. Once presented, it is immutable. Recalculation creates a new proposal and plan version.

### SourcingPlan

Owns one or more `SourcingPlanLine` values for the same product, node, currency, evidence snapshot, and need-by time. Each line records supplier, quantity, MOQ, unit cost, reliability, expected delivery, inclusion rationale, and stable idempotency key. Rejected supplier options are retained with exclusion reason codes.

### PurchaseOrder

Represents the observed order in the mock system of record. It is never constructed from model prose.

## 3. Value objects

- `Quantity`: integer greater than or equal to zero; unit is `EA` in MVP.
- `Money`: integer `amount_minor` plus ISO currency code; arithmetic across currencies is forbidden.
- `TimeWindow`: inclusive start and exclusive end in UTC.
- `EvidenceRef`: evidence type, record ID, source, version, observed-at timestamp.
- `ConstraintResult`: code, severity, passed, limit, observed value, explanatory data.
- `Decision`: type, original quantity, proposed quantity, reason codes, policy version.
- `ValidationResult`: status, compared fields, mismatches, observed-at timestamp.
- `AgentToolCall`: round, tool name, sanitized arguments, result reference/hash, provider/model, timing, attempt, and outcome.
- `InvestigationTrace`: scenario, allowed tools, mandatory evidence manifest, bounded calls, and completion status.
- `SalesObservation`: product, node, interval, units sold, source version, and observed-at timestamp.
- `DemandSignal`: baseline and actual velocity, ratio, source reference, evidence version, and trigger time.
- `SourcingPlanLine`: supplier allocation plus commercial, reliability in integer basis points, delivery, constraint, and validation references.

## 4. Invariants

- `INV-001` Quantities are whole, non-negative units.
- `INV-002` Total cost equals quantity multiplied by unit cost using integer minor units.
- `INV-003` A conclusive decision uses exactly one immutable evidence snapshot.
- `INV-004` Evidence outside its freshness window cannot support a conclusive decision.
- `INV-005` Feasible quantity cannot exceed raw need unless MOQ rounding is explicitly applied and the rounded quantity still passes all hard limits.
- `INV-006` An approved proposal cannot be edited; a changed proposal requires new approval.
- `INV-007` Execution uses the current proposal version and matching snapshot hash.
- `INV-008` One idempotency key maps to at most one purchase order.
- `INV-009` A review cannot be completed until action validation passes, unless its decision requires no action.
- `INV-010` A partial fulfilment cannot automatically create a replacement PO until remaining demand and incoming supply are recomputed.
- `INV-011` Model-selected tools cannot expand beyond the product, node, supplier candidates, and phase authorized by the review.
- `INV-012` A proposal's total quantity and cost equal the sums of its immutable sourcing-plan lines.
- `INV-013` Each plan line satisfies its supplier MOQ, availability, currency, required-by time, and per-line cost; the plan satisfies global budget and storage limits.
- `INV-014` Only the final line may round total quantity above raw need, by at most that line's MOQ minus one.
- `INV-015` One plan-line idempotency key maps to at most one purchase order, and a completed plan has one passed validation per line.
- `INV-016` The action tool accepts only the current approved proposal ID; every PO field is resolved from persisted state.
- `INV-017` A demand-change decision requires fresh, consistent recent-sales, baseline-forecast, and revised-forecast evidence.
- `INV-018` An issued PO is never mutated implicitly by a demand-change review; additional need is supplemental.

## 5. State transition rules

| From | Event | To | Guard |
|---|---|---|---|
| `CREATED` | investigation started | `INVESTIGATING` | valid input; provider absence is handled as an investigation failure |
| `INVESTIGATING` | selected reads complete | `COLLECTING_EVIDENCE` | bounds respected |
| `INVESTIGATING` | bounds/provider exhausted | `NEEDS_ATTENTION` | no conclusive snapshot created |
| `COLLECTING_EVIDENCE` | evidence complete | `EVALUATING` | required items valid |
| `COLLECTING_EVIDENCE` | evidence deficient | `NEEDS_ATTENTION` | retry exhausted or source conflict |
| `EVALUATING` | actionable proposal | `AWAITING_APPROVAL` | proposal passes hard constraints |
| `EVALUATING` | no action required | `COMPLETED` | `REJECT` with no write |
| `EVALUATING` | uncertainty | `NEEDS_ATTENTION` | `INVESTIGATE` |
| `AWAITING_APPROVAL` | buyer rejects | `REJECTED_BY_BUYER` | proposal version matches |
| `AWAITING_APPROVAL` | buyer approves | `EXECUTING` | authorization and snapshot valid |
| `AWAITING_EXECUTION_RETRY` | authorized retry | `EXECUTING` | refreshed proposal unchanged and provider invokes approved tool |
| `AWAITING_EXECUTION_RETRY` | refreshed proposal changed | `AWAITING_APPROVAL` | prior approval superseded |
| `EXECUTING` | action observed | `VALIDATING` | result includes PO ID |
| `EXECUTING` | action-tool providers exhausted | `AWAITING_EXECUTION_RETRY` | immutable approval retained; no write occurred |
| `EXECUTING` | transient failure | `EXECUTING` | retry budget remains |
| `EXECUTING` | permanent failure | `NEEDS_ATTENTION` | error classified |
| `VALIDATING` | valid | `COMPLETED` | all critical fields match |
| `VALIDATING` | recoverable mismatch | `REPLANNING` | recovery budget remains |
| `VALIDATING` | unsafe/unrecoverable | `NEEDS_ATTENTION` | mismatch or budget exhausted |
| `REPLANNING` | new proposal | `AWAITING_APPROVAL` | new version created |

Illegal transitions return a conflict error and append an audit event; they never mutate state.

## 6. Reason code catalog

Minimum supported codes:

```text
NEED_MATCHES_ORIGINAL
NEED_LOWER_THAN_ORIGINAL
NO_NET_REQUIREMENT
MOQ_ROUNDING_APPLIED
BELOW_MOQ_NO_FEASIBLE_ORDER
BUDGET_LIMITED
STORAGE_LIMITED
OPEN_PO_REDUCES_NEED
LEAD_TIME_EXTENDS_HORIZON
MISSING_REQUIRED_EVIDENCE
STALE_REQUIRED_EVIDENCE
CONFLICTING_EVIDENCE
SUPPLIER_PARTIAL_FULFILMENT
ALTERNATE_SUPPLIER_REQUIRED
MULTI_SUPPLIER_PLAN_REQUIRED
SUPPLIER_EXCLUDED_STALE_TERMS
SUPPLIER_EXCLUDED_CURRENCY
SUPPLIER_EXCLUDED_LATE_ARRIVAL
SUPPLIER_EXCLUDED_MOQ
DEMAND_SPIKE_DETECTED
DEMAND_EVIDENCE_CONFLICT
SUPPLEMENTAL_PURCHASE_REQUIRED
INVESTIGATION_BOUNDS_EXHAUSTED
ACTION_TOOL_PROVIDER_UNAVAILABLE
PRE_EXECUTION_FACTS_CHANGED
ACTION_RESULT_MISMATCH
RECOVERY_LIMIT_REACHED
```
