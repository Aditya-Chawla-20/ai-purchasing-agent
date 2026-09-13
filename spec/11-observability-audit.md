# Observability and Audit Specification

## 1. Objectives

The system must answer: what input started the review, what evidence was consulted, which rules ran, what the buyer approved, what action was attempted, what result was observed, and why the run ended in its current state.

Auditability records decisions and evidence, not private model chain-of-thought.

## 2. Correlation model

Every log, audit event, tool call, and trace carries:

- `review_id`;
- `correlation_id` for one API/workflow continuation;
- `action_attempt_id` where applicable;
- workflow and policy version;
- event name and UTC timestamp.

## 3. Audit events

Minimum event types:

```text
REVIEW_CREATED
WORKFLOW_STATE_CHANGED
INVESTIGATION_STARTED
INVESTIGATION_ROUND_COMPLETED
MANDATORY_EVIDENCE_COMPLETED
INVESTIGATION_EXHAUSTED
TOOL_CALL_STARTED
TOOL_CALL_COMPLETED
TOOL_CALL_FAILED
EVIDENCE_SNAPSHOT_CREATED
DECISION_CALCULATED
EXPLANATION_GENERATED
EXPLANATION_FALLBACK_USED
APPROVAL_REQUESTED
APPROVAL_RECORDED
PRE_EXECUTION_REVALIDATED
ACTION_TOOL_REQUESTED
ACTION_TOOL_PROVIDER_FAILED
EXECUTION_RETRY_REQUIRED
EXECUTION_RETRY_REQUESTED
ACTION_ATTEMPTED
ACTION_RESULT_OBSERVED
VALIDATION_COMPLETED
SUPPLIER_CONFIRMATION_RECEIVED
DEMAND_SIGNAL_RECEIVED
SOURCING_PLAN_CREATED
SUPPLIER_OPTION_EXCLUDED
RECOVERY_STARTED
REVIEW_COMPLETED
REVIEW_ESCALATED
```

Tool events store investigation round/sequence, tool name, duration, status, normalized input/output hash, result reference, source/version, provider/model, attempt, and error code. Full sensitive payloads are excluded. Model prompts, scratchpads, and chain-of-thought are never audit fields.

## 4. Application logs

Emit structured JSON in backend runtime. Levels:

- `INFO`: lifecycle transitions, business outcome, expected retries;
- `WARN`: stale/conflicting evidence, fallback explanation, recoverable mismatch;
- `ERROR`: terminal infrastructure failure, unsafe observed action mismatch;
- `DEBUG`: sanitized local diagnostic details, disabled by default outside development.

No success log is emitted before validation passes.

## 5. Metrics

Core counters/histograms:

- reviews started and completed by decision type;
- reviews awaiting approval and needs-attention count;
- evidence/tool latency and failure count by tool;
- investigation rounds, unique calls, manifest-added calls, invalid calls, and bounds exhaustion;
- decision duration and end-to-end duration;
- approvals, rejections, and stale-approval conflicts;
- action attempts, retries, duplicate-prevention replays;
- validation pass/fail by mismatch code;
- recovery attempts and escalations;
- LLM latency, invalid output count, and fallback count.
- sourcing-plan line count, excluded suppliers by reason, coverage ratio, reliability score, and total cost;
- action-tool provider failures and reviews awaiting execution retry;
- demand signals received, confirmed spikes, no-change outcomes, and supplemental plans.

For the assignment, metrics may be calculated from audit rows and displayed in tests/console; a monitoring stack is not required.

## 6. Optional tracing

LangSmith MAY trace graph nodes, tool calls, model latency, and token usage when configured. It is optional and must be disabled cleanly. Raw operational secrets and approval comments are excluded. The local audit store remains the authoritative record.

## 7. Audit acceptance criteria

- Timeline order remains stable after refresh.
- Every state change has exactly one corresponding audit event.
- Every conclusive decision links a snapshot hash and policy version.
- Every action links approval, decision, idempotency key, and validation.
- Redaction tests prove configured secret fields are absent.
- A failed model call is visible without exposing its credential or hidden prompt.
- Every conclusive investigation proves its mandatory evidence manifest was satisfied.
- Every sourcing-plan line links its approval, action attempt, idempotency key, observed PO, and validation.
