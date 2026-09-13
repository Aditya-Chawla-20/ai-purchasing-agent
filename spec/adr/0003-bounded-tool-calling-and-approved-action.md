# ADR 0003: Bounded tool calling and approved-proposal execution

Status: accepted  
Date: 2026-09-13

## Context

The fixed evidence collector is reliable but does not visibly demonstrate that an agent can decide what information to investigate. Unrestricted ReAct loops and model-authored purchase-order payloads would weaken latency, auditability, and financial safety.

## Decision

Use a bounded function-calling loop for investigation. The model receives only phase-specific typed tools. It may run at most three planning rounds and select ten unique tool-and-argument pairs, with at most two attempts for an individual failed read. A scenario-specific mandatory evidence manifest deterministically fills omitted safety-critical reads before policy evaluation without consuming the model-selection cap.

After a buyer approves an immutable proposal or sourcing-plan version, expose only `execute_approved_proposal(proposal_id)`. The backend resolves the identifier, revalidates evidence and approval, reconstructs every PO field, and applies stable per-line idempotency keys. The model cannot supply or alter operational fields. If all tool-capable providers fail, no deterministic write fallback runs; the review waits in `AWAITING_EXECUTION_RETRY`.

Gemini is the primary function-calling provider using its [function-calling API](https://ai.google.dev/gemini-api/docs/function-calling). Groq uses a separate tool-capable agent model listed in its [tool-use support matrix](https://console.groq.com/docs/tool-use/overview), while Compound remains available for explanations because [Compound does not accept custom user-provided tools](https://console.groq.com/docs/compound). NVIDIA remains an explanation fallback until its selected model passes the same tool contract tests.

## Consequences

- The system demonstrates genuine tool selection while deterministic code remains authoritative.
- Tool behavior is replayable from sanitized calls and immutable result references without recording hidden reasoning.
- Model availability can delay an approved action, but cannot cause an unauthorized or malformed write.
- The workflow, API, provider adapter, state model, and tests gain bounded-loop and execution-retry contracts.
