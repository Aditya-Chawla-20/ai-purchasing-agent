# Persistence Model

## 1. Storage rules

- SQLite is the MVP source of truth for mock operational data and application state.
- Foreign keys are enabled.
- IDs are UUID strings; external mock IDs are stored separately.
- Timestamps are ISO-8601 UTC values.
- Money uses integer minor units, never floating point.
- JSON is used only for immutable snapshots and flexible audit details, not for fields needed by constraints or joins.
- Schema changes use migrations; seed data is repeatable and environment-specific.

## 2. Tables

### Operational facts

```text
products(
  id PK, sku UNIQUE, name, unit_of_measure, unit_volume, active
)

nodes(
  id PK, code UNIQUE, name, active
)

inventory(
  product_id FK, node_id FK,
  on_hand, reserved, damaged, version, observed_at,
  PK(product_id, node_id)
)

forecasts(
  id PK, product_id FK, node_id FK,
  window_start, window_end, expected_demand, model_version, observed_at,
  UNIQUE(product_id, node_id, window_start, window_end, model_version)
)

suppliers(
  id PK, code UNIQUE, name, active, reliability_score_bps
)

supplier_products(
  supplier_id FK, product_id FK,
  unit_cost_minor, currency, minimum_order_quantity,
  lead_time_days, max_available_quantity, terms_version, observed_at,
  PK(supplier_id, product_id)
)

sales_observations(
  id PK, product_id FK, node_id FK,
  window_start, window_end, units_sold,
  source, source_version, observed_at,
  UNIQUE(product_id, node_id, window_start, window_end, source_version)
)

demand_signals(
  id PK, external_event_id UNIQUE, product_id FK, node_id FK,
  sales_observation_id FK, baseline_forecast_id FK, revised_forecast_id FK,
  actual_to_baseline_ratio_bps, threshold_bps,
  source_version, triggered_at, created_at
)

planning_policies(
  product_id FK, node_id FK,
  safety_stock, review_period_days,
  demand_spike_threshold_bps, minimum_sales_observation_hours,
  version, effective_from, observed_at,
  PK(product_id, node_id)
)

budgets(
  id PK, node_id FK, period_start, period_end,
  currency, allocated_minor, committed_minor, version, observed_at,
  UNIQUE(node_id, period_start, period_end, currency)
)

storage_capacity(
  node_id PK/FK, available_volume, version, observed_at
)

purchase_orders(
  id PK, external_id UNIQUE, supplier_id FK, node_id FK,
  status, currency, total_minor, expected_delivery_at,
  idempotency_key UNIQUE, created_at, updated_at, version
)

purchase_order_items(
  id PK, purchase_order_id FK, product_id FK,
  ordered_quantity, confirmed_quantity, received_quantity,
  unit_cost_minor,
  UNIQUE(purchase_order_id, product_id)
)
```

Eligible incoming quantity is derived from open PO items whose expected delivery falls inside the planning horizon and whose status is eligible. It is not duplicated in `inventory`.

### Agent and audit state

```text
recommendations(
  id PK, source, source_reference, product_id FK, node_id FK,
  preferred_supplier_id FK, recommended_quantity, created_at
)

purchasing_reviews(
  id PK, recommendation_id FK, status,
  policy_version, workflow_version, current_proposal_version,
  recovery_attempts, created_at, updated_at, completed_at
)

evidence_snapshots(
  id PK, review_id FK, sequence, snapshot_hash UNIQUE,
  payload_json, completeness_status, created_at,
  UNIQUE(review_id, sequence)
)

decisions(
  id PK, review_id FK, evidence_snapshot_id FK, version,
  decision_type, original_quantity, raw_need, proposed_quantity,
  confidence_label, reason_codes_json, calculation_json,
  explanation_json, created_at,
  UNIQUE(review_id, version)
)

sourcing_plans(
  id PK, review_id FK, decision_id FK, evidence_snapshot_id FK,
  version, product_id FK, node_id FK, currency,
  raw_need, total_quantity, total_cost_minor, need_by_at,
  allocation_policy_version, status, created_at,
  UNIQUE(review_id, version)
)

sourcing_plan_lines(
  id PK, sourcing_plan_id FK, supplier_id FK,
  sequence, quantity, unit_cost_minor, total_cost_minor,
  minimum_order_quantity, reliability_score_bps,
  expected_delivery_at, inclusion_reason_codes_json,
  idempotency_key UNIQUE,
  UNIQUE(sourcing_plan_id, supplier_id)
)

sourcing_option_assessments(
  id PK, sourcing_plan_id FK, supplier_id FK,
  eligible, exclusion_reason_codes_json, normalized_terms_json,
  UNIQUE(sourcing_plan_id, supplier_id)
)

purchase_proposals(
  id PK, review_id FK, decision_id FK, sourcing_plan_id FK,
  proposal_version, evidence_snapshot_hash, status, created_at,
  UNIQUE(review_id, proposal_version), UNIQUE(sourcing_plan_id)
)

approval_requests(
  id PK, review_id FK, decision_id FK, proposal_id FK, proposal_version,
  status, requested_at, decided_at, decided_by, comment,
  UNIQUE(review_id, proposal_version)
)

action_attempts(
  id PK, review_id FK, decision_id FK, sourcing_plan_line_id FK NULL, attempt_number,
  action_type, idempotency_key UNIQUE, request_json,
  response_json, status, error_code, started_at, completed_at
)

validation_results(
  id PK, review_id FK, action_attempt_id FK,
  status, comparisons_json, mismatch_codes_json, observed_at
)

audit_events(
  id INTEGER PK AUTOINCREMENT, review_id FK,
  event_type, actor_type, actor_id, correlation_id,
  payload_json, created_at
)

investigation_traces(
  id PK, review_id FK, scenario_type, provider, model,
  allowed_tools_json, mandatory_manifest_json,
  status, rounds_used, started_at, completed_at
)

agent_tool_calls(
  id PK, investigation_trace_id FK, round_number, sequence,
  tool_name, arguments_json, arguments_hash, result_ref,
  result_hash, provider, model, attempt, status, error_code,
  duration_ms, created_at,
  UNIQUE(investigation_trace_id, sequence)
)
```

LangGraph checkpoint tables are framework-owned and kept logically separate from domain tables.

## 3. Indexes

- `purchase_orders(status, expected_delivery_at)` for incoming supply.
- `purchase_order_items(product_id, purchase_order_id)` for SKU lookups.
- `forecasts(product_id, node_id, window_start, window_end)`.
- `purchasing_reviews(status, updated_at)` for the queue.
- `audit_events(review_id, id)` for ordered timelines.
- `sales_observations(product_id, node_id, window_end)` for recent demand evidence.
- `sourcing_plan_lines(sourcing_plan_id, sequence)` for stable execution order.
- `agent_tool_calls(investigation_trace_id, sequence)` for replayable tool traces.
- Unique indexes on all idempotency and external-reference fields.

## 4. Concurrency

Operational records carry a `version`. Pre-execution checks compare the evidence snapshot versions with fresh versions. A difference triggers recalculation. In a production database this maps to optimistic locking; in SQLite, a short transaction protects the mock create-and-commit operation.

## 5. Seed dataset contract

Seed data MUST include:

- one 800-unit recommendation that resolves to `MODIFY`;
- one case that resolves to `ACCEPT`;
- one case with no net need that resolves to `REJECT`;
- one stale or missing evidence case that resolves to `INVESTIGATE`;
- a 500-unit PO that receives a 250-unit supplier confirmation;
- at least one alternate supplier for the partial-fulfilment recovery case;
- enough supplier options to demonstrate one-supplier, split, ineligible, and constrained plans;
- baseline/revised forecasts and recent sales for demand-spike and sufficient-coverage cases.

Seeds are deterministic and resettable without deleting non-seed user data in normal application operation.
