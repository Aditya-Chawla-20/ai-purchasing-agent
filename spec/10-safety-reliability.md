# Safety, Security, and Reliability

## 1. Trust boundaries

Untrusted inputs include recommendation text, supplier messages/events, model output, browser input, and all external API responses. They are validated at the boundary and cannot override policies.

Trusted control inputs are versioned policy configuration, authenticated role, approved proposal record, and deterministic domain code. Operational facts are authoritative only after schema, freshness, and consistency validation.

## 2. Action safety controls

1. Before approval, no write tool is exposed. After approval, the model sees only `execute_approved_proposal(proposal_id)`.
2. The dispatcher accepts only the current persisted proposal ID; the server reconstructs every write field.
3. Every write requires a recorded approval and policy pass.
4. Mutable facts are refreshed before execution and before an execution retry.
5. Every plan line and result are tied to a unique stable idempotency key.
6. The system reads each PO back and validates it.
7. A mismatch or partial multi-line execution triggers attention/recovery, never a false success.
8. Recovery and model investigation have hard limits.

Defense in depth applies even in mock mode so the design is visible in code and tests.

## 3. Prompt-injection controls

- External text is delimited and labeled as data.
- Supplier text is converted into an allowlisted typed event before workflow use.
- System policy and tool descriptions cannot be modified by retrieved content.
- Model access is limited to phase-scoped strict schemas and output budgets.
- Tool arguments are independently authorized and validated.
- Model output is never executed as code or SQL.
- The action-tool identifier is matched to workflow state; arbitrary or stale proposal IDs are denied.
- Mandatory evidence manifests prevent prompt content from persuading the model to omit a safety-critical read.

## 4. Data protection

- `.env` is gitignored; `.env.example` contains names and safe placeholders only.
- Logs and audit payloads redact authorization headers, API keys, prompt secrets, and unnecessary personal data.
- SQL is parameterized through repositories/ORM.
- CORS is restricted to the configured local frontend origin.
- Error responses expose stable codes and correlation IDs, not internals.

## 5. Consistency and idempotency

- Approval uses proposal version for optimistic concurrency.
- Pre-execution checks compare source versions and snapshot hash.
- `create_purchase_order` is unique on idempotency key.
- Multi-line plans derive one stable idempotency key per immutable plan line.
- Supplier events are unique on external event ID.
- Audit events are append-only.
- State mutation plus audit append occurs in one application transaction where possible.

## 6. Failure classification

| Failure | Treatment |
|---|---|
| Read timeout/unavailable | bounded retry; then investigate |
| Invalid or contradictory data | investigate immediately |
| Explanation timeout/schema error | try explanation fallbacks, then use deterministic explanation |
| Investigation provider failure/bounds exhausted | try the tool-capable fallback, then `NEEDS_ATTENTION`; no conclusive decision |
| Post-approval tool provider failure | enter `AWAITING_EXECUTION_RETRY`; no write |
| Execution retry after facts changed | supersede approval and request a new one |
| Approval conflict | reload current proposal; do not execute |
| Create response lost | retry same idempotency key, then read by key |
| Constraint changed before write | recalculate and request approval again |
| Observed PO mismatch | stop, classify, recover or escalate |
| Database/checkpoint unavailable | fail closed; no external write |

## 7. Configuration guardrails

Validate configuration at startup:

- environment name and mock-mode flag;
- database URL points to an allowed local path for the demo;
- LLM provider/model and API key requirements;
- separate explanation and tool-calling model capability declarations;
- investigation round/call/attempt limits and the demand-spike threshold;
- freshness windows, retry counts, recovery limit;
- approval policy and maximum demo order value;
- allowed frontend origin.

Unsafe or inconsistent configuration prevents readiness.

## 8. Production evolution notes

Not part of the MVP: replace local identity with OIDC/RBAC, SQLite with PostgreSQL, in-process execution with durable workers, mock adapters with ERP/supplier APIs, and local secrets with a secret manager. These are adapter/topology changes; decision invariants remain unchanged.
