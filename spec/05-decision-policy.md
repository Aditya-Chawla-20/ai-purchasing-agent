# Deterministic Decision and Constraint Policy

## 1. Inputs

All inputs are typed, validated, and captured in one evidence snapshot:

- original recommended quantity;
- on-hand, reserved, and damaged inventory;
- eligible outstanding PO quantity within the planning horizon;
- demand forecast for lead time plus review period;
- safety stock;
- supplier MOQ, lead time, availability, unit cost, and terms version;
- uncommitted purchasing budget;
- available storage volume and product unit volume;
- evidence freshness metadata.
- all eligible supplier options when one supplier cannot satisfy the justified need;
- for demand-change reviews, recent sales plus baseline and revised forecasts.

If required input is unavailable, invalid, stale, or materially conflicting, the outcome is `INVESTIGATE`; calculations MAY be displayed as incomplete but MUST NOT create a proposal.

## 2. Calculation order

All divisions use floor unless explicitly stated. Intermediate and final quantities are integers.

```text
usable_on_hand       = max(0, on_hand - reserved - damaged)
eligible_incoming    = sum(open, unreceived PO quantity arriving from now through planning horizon)
inventory_position   = usable_on_hand + eligible_incoming
target_stock         = forecast_demand + safety_stock
raw_need             = max(0, target_stock - inventory_position)

budget_units         = available_budget_minor // unit_cost_minor
storage_units        = available_storage_volume // product_unit_volume
supplier_units       = max_available_quantity, when provided; otherwise unbounded
hard_cap             = min(raw_need, budget_units, storage_units, supplier_units)
```

### MOQ rule

1. If `raw_need == 0`, feasible quantity is `0`.
2. If `0 < hard_cap < MOQ`, feasible quantity is `0`; purchasing from this supplier is infeasible.
3. Otherwise, round `raw_need` up to the nearest MOQ multiple only when the rounded quantity passes budget, storage, and supplier caps.
4. If rounded-up quantity fails a cap, use the largest MOQ multiple not exceeding `hard_cap`.
5. If no positive multiple fits, feasible quantity is `0`.

This policy never violates a hard cap merely to satisfy MOQ.

An overdue or delayed open PO is not eligible incoming supply. If `reserved + damaged > on_hand`,
inventory is conflicting evidence and the policy returns `INVESTIGATE`; it must not silently treat
the balance as zero usable stock.

## 3. Decision classification

Evaluate in this priority order:

1. Evidence deficient -> `INVESTIGATE`, no proposal.
2. `raw_need == 0` -> `REJECT`, quantity `0`.
3. `feasible_quantity == 0` while `raw_need > 0` -> `INVESTIGATE`; reason identifies the blocking constraint.
4. `feasible_quantity == original_quantity` -> `ACCEPT`.
5. Positive feasible quantity different from original -> `MODIFY`.

`REJECT` means the recommendation is unnecessary, not merely infeasible. An actual need blocked by constraints is `INVESTIGATE` so a buyer can change supplier, budget, storage, or timing.

## 4. Constraint catalog

| Code | Severity | Pass condition |
|---|---|---|
| `EVIDENCE_COMPLETE` | hard | all required evidence valid and fresh |
| `POSITIVE_UNIT_COST` | hard | unit cost greater than zero |
| `INTEGER_QUANTITY` | hard | proposed quantity is a whole unit |
| `MOQ_SATISFIED` | hard | quantity is zero or valid MOQ multiple and at least MOQ |
| `WITHIN_BUDGET` | hard | total cost not above available budget |
| `WITHIN_STORAGE` | hard | required volume not above available storage |
| `WITHIN_SUPPLIER_AVAILABILITY` | hard | quantity not above known available supply |
| `NEED_JUSTIFIED` | hard | positive quantity is supported by calculated need |
| `LARGE_VARIANCE` | advisory | proposed quantity is within configured variance from original |
| `LOW_SUPPLIER_RELIABILITY` | advisory | reliability meets configured threshold |

Advisory failures affect approval rationale and confidence; they do not silently change quantities.

## 5. Confidence label

Confidence is categorical, not a fabricated probability:

- `HIGH`: all required evidence fresh, no advisory failures, and no material forecast anomaly.
- `MEDIUM`: all hard checks pass but at least one advisory concern exists.
- `LOW`: reserved for `INVESTIGATE`; no automatic action is allowed.

## 6. Explanation contract

The explanation output contains:

```json
{
  "summary": "Modify the order from 800 to 450 units.",
  "important_factors": [
    {"reason_code": "OPEN_PO_REDUCES_NEED", "evidence_refs": ["evidence-id"]}
  ],
  "constraint_summary": "All hard constraints pass for 450 units.",
  "uncertainties": [],
  "next_action": "Buyer approval is required before creating the purchase order."
}
```

The model cannot change `decision_type`, any numeric value, reason code, or approval requirement. Output schema validation rejects extra fields. A deterministic renderer uses the same contract when the model fails.

## 7. Pre-execution policy

Immediately before a write:

1. refresh inventory, eligible open POs, budget, capacity, and supplier availability;
2. compare source versions with the approved snapshot;
3. rerun the calculation and constraints;
4. if the proposed action is unchanged, execute;
5. if any action field changes, create a new decision/proposal version and request approval again;
6. if the need disappears, complete without creating a PO and record `PRE_EXECUTION_FACTS_CHANGED`.

## 8. Multi-supplier allocation policy

Allocation runs only after the single-supplier policy cannot cover a positive justified need or a recovery/demand-change review explicitly requests alternate sourcing.

1. Determine one common `need_by_at` from the approved planning horizon.
2. Exclude inactive suppliers, stale terms, currency mismatches, zero availability, arrivals after `need_by_at`, and suppliers for which no MOQ-compliant line fits remaining hard limits.
3. Search all eligible supplier MOQ lots up to `raw_need + maximum_eligible_moq - 1`. Prune candidates that are worse at the same supplied quantity on reliability, cost, PO count, and delivery.
4. Rank complete plans lexicographically: cover the largest amount of raw need; maximize reliability weighted over required units; minimize total cost; minimize line count; minimize latest delivery time; then use ordered supplier IDs for stable replay. The reliability numerator is `sum(effective_line_quantity * reliability_score_bps)`, where the final line's effective quantity excludes MOQ overbuy, divided by raw need.
5. Enforce budget and storage across the plan and availability/MOQ per line.
6. Only the final line may round the plan above raw need, by at most that supplier's MOQ minus one. Overbuy does not improve the reliability score.
7. If full coverage is infeasible, return the best safe partial plan as advisory evidence but produce `INVESTIGATE`; do not request approval for a knowingly incomplete plan.

Reliability scores are stored decimal values compared without binary floating-point arithmetic. Allocation is a deterministic domain service, not a model decision.

## 9. Demand-change policy

The default anomaly threshold is actual sales velocity at least `1.5` times baseline velocity over an observation window of at least 24 hours. Compare the exact rational rates using integer cross-multiplication and persist the derived ratio as basis points; do not use binary floating point. The threshold and policy version are stored with the review.

1. Reject non-positive, stale, differently scoped, or non-overlapping sales/forecast windows as conflicting evidence. A zero baseline with positive actual sales also requires investigation because a finite ratio is undefined.
2. Require a revised forecast version newer than the baseline and covering the planning horizon.
3. If the threshold is not met, record the signal as unconfirmed and make no purchasing change.
4. If met, calculate target stock from the revised forecast and recompute inventory position with eligible incoming supply.
5. If coverage is sufficient, complete without action.
6. If a positive gap remains, create a supplemental single- or multi-supplier plan. Existing issued POs remain unchanged.
7. Missing or contradictory evidence produces `INVESTIGATE`; constraints are applied exactly as in Scenario 1.
