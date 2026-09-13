# ADR 0002: Deterministic decisions, model-assisted explanations

Status: accepted  
Date: 2026-09-12

## Context

Purchase quantities depend on arithmetic and hard operational constraints. Model output can be non-deterministic and must not become an unreviewed operational fact.

## Decision

The domain policy engine computes the outcome, quantity, constraint results, and reason codes. The LLM receives that closed decision package and produces a concise explanation in a typed schema. If the model is unavailable or its output is invalid, the system renders a deterministic template explanation.

The LLM may decide which approved read tool is relevant and may request execution through the opaque approved-proposal tool defined by ADR 0003. It cannot author a write payload, alter calculated fields, or weaken policies.

## Consequences

- The same evidence and policy version always produce the same operational decision.
- Evaluation can separate business correctness from explanation quality.
- The demo remains usable without an external model.
- Natural-language flexibility is intentionally narrower than a general agent.
