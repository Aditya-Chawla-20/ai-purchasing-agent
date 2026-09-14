# Agent Tool Contracts

## 1. Tool boundary

Tools are application-owned typed functions. Tool names and schemas are stable contracts; adapters may use SQLite, mocks, or later external services. During investigation the model receives only phase-appropriate read tools. After approval it receives only `execute_approved_proposal`.

Every call receives `ToolContext`:

```json
{
  "review_id": "uuid",
  "correlation_id": "uuid",
  "actor": {"type": "SYSTEM", "id": "workflow"},
  "deadline_ms": 2000
}
```

Every result uses this envelope:

```json
{
  "ok": true,
  "data": {},
  "source": "mock-erp",
  "source_version": "17",
  "observed_at": "2026-09-12T10:00:00Z",
  "warnings": []
}
```

Errors use stable codes: `NOT_FOUND`, `INVALID_ARGUMENT`, `TIMEOUT`, `UNAVAILABLE`, `CONFLICT`, `UNAUTHORIZED`, `POLICY_DENIED`, and `INTERNAL`. Tool errors never contain secrets or stack traces in API responses.

The tool dispatcher rejects calls outside the review's product, node, candidate-supplier set, time horizon, or current phase. It also rejects unknown arguments and coerces no unsafe values. Model selection is limited to three rounds and ten unique tool-and-normalized-argument pairs. Manifest-added safety reads do not consume that selection cap but remain audited and retry-bounded. Each failed read receives at most two total attempts.

### Mandatory evidence manifests

| Scenario | Required successful tools before a conclusive decision |
|---|---|
| Recommendation review | `get_inventory`, `get_demand_forecast`, `list_open_purchase_orders`, `get_supplier_terms`, `get_budget`, `get_storage_capacity`, `get_planning_policy` |
| Supplier shortfall | recommendation manifest plus `get_purchase_order` and `list_supplier_options` |
| Demand change | recommendation manifest plus `get_recent_sales` and `list_supplier_options` |

For demand change, `get_demand_forecast` must succeed separately for the signal's baseline and revised forecast IDs. The model may choose ordering, concurrency groups, and additional relevant reads. The workflow invokes any missing mandatory tool itself before snapshot creation. A required failed read produces `INVESTIGATE` after retry exhaustion.

## 2. Read tools

### `get_inventory`

Input: `product_id`, `node_id`.  
Output: `on_hand`, `reserved`, `damaged`, `version`, `observed_at`.

### `get_demand_forecast`

Input: `product_id`, `node_id`, `window_start`, `window_end`, and optional `forecast_id`.  
Output: `expected_demand`, `model_version`, interval, `observed_at`.

The tool must not extrapolate beyond stored forecast coverage. Recommendation reviews omit `forecast_id` and receive the current applicable version. Demand-change reviews call it for the signal's exact baseline and revised forecast IDs.

### `list_open_purchase_orders`

Input: `product_id`, `node_id`, `arrival_before`.  
Output: purchase-order items with ordered, confirmed, received, outstanding, status, and expected delivery.

Only the policy engine determines which records are eligible incoming supply.

### `get_supplier_terms`

Input: `supplier_id`, `product_id`.  
Output: MOQ, lead time days, unit cost, currency, max available quantity, reliability score, terms version.

### `list_supplier_options`

Input: `product_id`.  
Output: every active supplier ID and normalized terms for the product. Used for recovery, demand changes, and explicit alternate sourcing. Eligibility and allocation remain deterministic.

### `get_recent_sales`

Input: `product_id`, `node_id`, `window_start`, `window_end`.  
Output: exact stored sales observations, total units, normalized units-per-hour velocity, source versions, and observed-at timestamps.

The tool requires an interval of at least 24 hours, never extrapolates missing time, and never fabricates a demand forecast from sales.

### `get_budget`

Input: `node_id`, `as_of`, `currency`.  
Output: allocated, committed, available amount, period, version.

### `get_storage_capacity`

Input: `node_id`.  
Output: available volume and version. Product unit volume comes from the product record.

### `get_planning_policy`

Input: `product_id`, `node_id`.  
Output: safety stock, review-period days, demand-spike threshold basis points, minimum sales-observation hours, policy version, and effective interval.

### `get_purchase_order`

Input: `purchase_order_id`.  
Output: normalized header and item fields required by validation.

## 3. Deterministic service tools

### `calculate_purchase_decision`

Input: validated evidence snapshot ID and policy version.  
Output: calculations, constraint results, decision, proposed quantity, reason codes, confidence label.

This is a normal domain service wrapped as a tool for trace consistency. Its result is authoritative over model output.

### `allocate_sourcing_plan`

Input: validated evidence snapshot ID, raw need, need-by time, and allocation-policy version.  
Output: immutable included plan lines, rejected supplier assessments, total quantity/cost/volume, objective scores, constraints, and reason codes.

The allocator follows the lexicographic policy in the decision specification. The model may request allocation but cannot supply allocations or change its result.

### `validate_purchase_order`

Input: proposal version, action attempt ID, observed PO.  
Output: per-field comparisons and overall `PASSED`, `FAILED_RECOVERABLE`, or `FAILED_UNSAFE`.

## 4. Approved action tool

### `execute_approved_proposal`

This is the only model-callable write tool and is exposed only in the post-approval workflow phase. `proposal_id` identifies the persisted `purchase_proposals` record, not a model-generated object.

Input:

```json
{"proposal_id":"uuid"}
```

The dispatcher requires that the ID equals the current workflow state's approved proposal. The application then reloads the proposal, plan, plan lines, recorded approval, evidence hash, and current source versions. The model cannot provide supplier, product, node, quantity, price, currency, delivery, or idempotency values.

Output:

```json
{
  "proposal_id":"uuid",
  "status":"EXECUTED|PARTIAL|FAILED",
  "action_attempt_ids":["uuid"],
  "purchase_order_ids":["uuid"],
  "replayed_line_ids":[]
}
```

If the proposal changed, the tool returns `CONFLICT`; if approval or policy guards fail, it returns `POLICY_DENIED`. Provider exhaustion occurs before dispatch and moves the review to `AWAITING_EXECUTION_RETRY` without a write.

## 5. Internal write adapter

### `create_purchase_order`

This adapter is never exposed to the model. `execute_approved_proposal` calls it once per persisted plan line in stable sequence.

Input:

```json
{
  "review_id": "uuid",
  "decision_id": "uuid",
  "proposal_version": 1,
  "supplier_id": "uuid",
  "node_id": "uuid",
  "items": [{"product_id":"uuid","quantity":450,"unit_cost_minor":1250}],
  "currency": "INR",
  "expected_delivery_at": "2026-09-20T00:00:00Z",
  "idempotency_key": "review-uuid:create-po:v1:line-uuid"
}
```

Guards before adapter call:

- matching recorded approval;
- matching current proposal version and evidence hash;
- passed pre-execution policy;
- caller has execution permission;
- idempotency key is unique or matches the exact stored request.

Output: PO ID, external ID, status, normalized items, totals, timestamps, and whether the response was replayed.

Same key plus same request returns the original result. Same key plus different request returns `CONFLICT`.

For a multi-line plan, successful lines are committed and validated independently. If another line fails, successful lines remain authoritative and retries read them by their original keys; the review becomes `NEEDS_ATTENTION` rather than reporting complete.

## 6. Mock event tools

### `record_supplier_confirmation`

Used by the demo/test harness, not by the agent.

Input: external event ID, PO ID, product ID, confirmed quantity, event timestamp.  
Behavior: deduplicate event, update the mock observed PO, append an audit event, and trigger the partial-fulfilment workflow.

### `record_demand_signal`

Used by the development/test harness, not by the model.

Input: external event ID, product/node IDs, sales observation interval and units, baseline/revised forecast IDs, source version, and event timestamp.  
Behavior: validate matching scopes and interval, deduplicate by external event ID, persist versioned sales evidence and signal, append an audit event, and start a demand-change review.

## 7. Freshness policy

Defaults are configuration, recorded with the run:

| Evidence | Maximum age |
|---|---:|
| Inventory | 5 minutes |
| Open POs | 5 minutes |
| Budget | 5 minutes |
| Storage | 5 minutes |
| Supplier availability | 30 minutes |
| Supplier commercial terms | 24 hours |
| Forecast | 24 hours |
| Recent sales | 15 minutes since source observation; observation interval at least 24 hours |

## 8. Provider capability contract

- The function-calling adapter normalizes provider responses into `AgentToolCall` before dispatch.
- Gemini uses `gemini-3.8-flash` as primary.
- Groq explanations may use `groq/compound`; custom tools use `GROQ_AGENT_MODEL`, default `openai/gpt-oss-120b`.
- NVIDIA Nemotron is explanation-only until its configured model passes tool declaration, argument, multi-call, and error-turn contract tests.
- A provider that returns no valid tool calls (including prose-only output) is a failed provider attempt and immediately advances to the tool-capable fallback. Unknown, duplicate, or invalid calls are never dispatched. The mandatory manifest may auto-fill omitted facts only after at least one valid model-selected call and records `MANIFEST_AUTO_FILL` provenance.
- For post-approval invocation, each configured tool-capable provider receives one attempt to call the sole exposed tool. Invalid output advances to the next provider; exhaustion creates no write and waits for authorized retry.

Tests use a frozen clock. Production-like adapters may tighten these values.
