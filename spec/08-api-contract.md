# HTTP API Contract

## 1. Conventions

- Base path: `/api/v1`.
- JSON request and response bodies use `snake_case` consistently.
- IDs are UUID strings.
- Timestamps are ISO-8601 UTC.
- Errors use RFC 9457-style problem details with stable application codes.
- Mutation requests accept `Idempotency-Key`; approval additionally uses proposal version for optimistic concurrency.
- API responses never expose prompts, chain-of-thought, secrets, or raw exception traces.

## 2. Endpoints

### `GET /health`

Returns liveness, database readiness, workflow checkpoint readiness, and configured LLM mode. It does not test the external model on every request.

### `GET /recommendations`

Lists seeded and development-created recommendations visible to the current actor. Query: `status`, `limit`, optional cursor.

### `POST /reviews`

Starts a review.

Request:

```json
{"recommendation_id":"uuid"}
```

Response `202 Accepted`:

```json
{
  "review_id":"uuid",
  "status":"INVESTIGATING",
  "links":{"self":"/api/v1/reviews/uuid"}
}
```

The endpoint is idempotent for the same recommendation and request idempotency key.

### `GET /reviews/{review_id}`

Returns the current read model:

```json
{
  "id":"uuid",
  "status":"AWAITING_APPROVAL",
  "recommendation":{},
  "investigation":{
    "status":"COMPLETE",
    "rounds_used":2,
    "mandatory_evidence_complete":true,
    "tool_calls":[]
  },
  "evidence":{"items":[],"completeness":"COMPLETE","snapshot_hash":"sha256:..."},
  "decision":{
    "version":1,
    "type":"MODIFY",
    "original_quantity":800,
    "proposed_quantity":450,
    "confidence":"HIGH",
    "reason_codes":[],
    "calculations":{},
    "constraints":[],
    "explanation":{}
  },
  "sourcing_plan":{
    "id":"uuid",
    "version":1,
    "raw_need":450,
    "total_quantity":450,
    "total_cost_minor":562500,
    "currency":"INR",
    "need_by_at":"...",
    "lines":[],
    "supplier_assessments":[]
  },
  "approval":{"status":"PENDING","proposal_id":"uuid","proposal_version":1,"expires_at":"..."},
  "action":null,
  "actions":[],
  "validation":null,
  "links":{}
}
```

Unknown ID -> `404`. The representation must be safe to poll and stable across refreshes.

`action` and the top-level summary fields remain populated for a single-line plan. `actions` and `validation.results` are authoritative for multi-line plans. Additive fields do not change existing single-supplier clients.

### `GET /reviews/{review_id}/events`

Returns ordered sanitized timeline events. Query supports `after_id` and `limit`. Pagination order is ascending event ID so the UI can append reliably.

### `POST /reviews/{review_id}/approval`

Request:

```json
{
  "proposal_version":1,
  "decision":"APPROVE",
  "comment":"Reviewed constraints"
}
```

Response `202` with current status. `REJECT` requires a non-empty comment for the assignment demo. Stale version or non-pending state -> `409 CONFLICT`. A repeated identical response returns the recorded outcome.

### `POST /reviews/{review_id}/execution-retry`

Request:

```json
{"proposal_version":1}
```

Allowed only for a `BUYER` or `SYSTEM` actor while the review is `AWAITING_EXECUTION_RETRY`. The workflow refreshes volatile evidence before exposing the approved action tool again. An unchanged proposal retains approval and per-line idempotency keys; changed facts supersede approval and return the review to `AWAITING_APPROVAL`. Other states or stale versions return `409`.

### `POST /mock/supplier-confirmations`

Development/test only; disabled outside mock mode. Accepts the supplier event contract and returns `202`. Repeating the same external event ID is a no-op.

### `POST /mock/demand-signals`

Development/test only. Accepts:

```json
{
  "external_event_id":"demand-event-1",
  "product_id":"uuid",
  "node_id":"uuid",
  "window_start":"...",
  "window_end":"...",
  "units_sold":240,
  "baseline_forecast_id":"uuid",
  "revised_forecast_id":"uuid",
  "source_version":"sales-v2",
  "occurred_at":"..."
}
```

Returns `202` with the new or existing demand-change review. Repeating the event ID is a no-op. Invalid scope, forecast order, or an interval shorter than 24 hours returns `422`.

### `POST /demo/custom-scenarios`

Development/test only. Creates isolated operational facts, a recommendation or demand signal, and starts the normal workflow.

```json
{
  "name":"Evaluator case",
  "mode":"RECOMMENDATION|DEMAND_CHANGE",
  "product":{"sku":"CUSTOM-1","name":"Custom item","unit_volume":2},
  "node":{"code":"CUSTOM-NODE","name":"Custom node"},
  "recommended_quantity":800,
  "inventory":{"on_hand":100,"reserved":0,"damaged":0},
  "forecasts":{"baseline":500,"revised":null,"window_start":"...","window_end":"..."},
  "open_purchase_orders":[{"supplier_code":"SUP-1","ordered_quantity":100,"confirmed_quantity":100,"received_quantity":0,"unit_cost_minor":1200,"currency":"INR","expected_delivery_at":"..."}],
  "suppliers":[{"code":"SUP-1","name":"Supplier 1","reliability_score_bps":9700,"unit_cost_minor":1200,"currency":"INR","minimum_order_quantity":25,"lead_time_days":3,"max_available_quantity":500}],
  "budget":{"available_minor":1000000,"currency":"INR"},
  "storage":{"available_volume":5000},
  "policy":{"safety_stock":50,"review_period_days":2},
  "recent_sales":null
}
```

All quantities and money are non-negative integers; unit cost and unit volume are positive; reliability is `0..10000` basis points; currencies are ISO codes; windows are ordered; supplier codes are unique; every PO supplier code resolves within the request; every nested record is forced to the created product/node. Recommendation mode requires at least one supplier. Demand-change mode additionally requires non-null baseline/revised forecasts and at least 24 hours of recent sales. Server-generated IDs, timestamps, versions, sources, and idempotency keys cannot be supplied. Returns `201` with `scenario_id`, `recommendation_id`, `review_id`, and links. These records are tagged `demo-custom` and never overwrite seeded or unrelated records.

### `POST /demo/scenarios/{scenario}/reset`

Development/test only. Restores scoped seeded records and starts the chosen Scenario Lab review.
Supported values are `inventory-conflict`, `late-incoming-po`, `supplier-terms-change`,
`lost-create-response`, `wrong-persisted-po`, and `demand-spike`. It never truncates unrelated local data.

### `POST /demo/scenarios/{scenario}/advance`

Development/test only. Accepts `{"review_id":"uuid"}`. Currently used only by
`supplier-terms-change` to apply a post-review supplier price and MOQ update before approval.

## 3. Problem response

```json
{
  "type":"https://example.local/problems/stale-proposal",
  "title":"Proposal is no longer current",
  "status":409,
  "code":"STALE_PROPOSAL",
  "detail":"Review the recalculated proposal before approving.",
  "correlation_id":"uuid"
}
```

## 4. Authentication and authorization

For the local assignment, a documented demo-user header or fixed local session is acceptable. The application layer still enforces roles:

- `VIEWER`: read reviews and events;
- `BUYER`: start reviews, approve/reject proposals, and retry an approved action after provider failure;
- `SYSTEM`: dispatch validated tools and execute approved proposals;
- `DEMO_ADMIN`: submit mock events and create/reset custom scenarios.

The role model is not delegated to the frontend.

## 5. Polling behavior

The UI polls an active review every 1 second, backs off to 5 seconds after 30 seconds, and stops in terminal states or while awaiting stable buyer input. SSE is a future improvement, not an MVP dependency.

## 6. Compatibility

Breaking response changes require `/api/v2` or an explicitly coordinated frontend change. Additive fields are allowed. Domain enum values are centrally generated or exhaustively handled by the frontend.
