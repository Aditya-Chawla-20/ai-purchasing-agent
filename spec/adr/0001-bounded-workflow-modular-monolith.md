# ADR 0001: Bounded workflow in a modular monolith

Status: accepted  
Date: 2026-09-12

## Context

The system must investigate, decide, pause, act, validate, and recover within a 6-8 hour implementation window. The domain is high consequence enough that unconstrained model autonomy is inappropriate.

## Decision

Build a modular FastAPI monolith. Use LangGraph only for explicit workflow state, branches, checkpointing, and human interrupts. Use typed Python domain services for calculations and policy. Provide only narrow tools. Require validation around every action.

## Consequences

Positive:

- the graph is easy to explain and test;
- numerical results are reproducible;
- local deployment remains small;
- modules can later be extracted behind existing ports.

Negative:

- workflow transitions require deliberate schema/version management;
- SQLite limits production concurrency;
- the architecture demonstrates one bounded agent instead of multi-agent collaboration.

Rejected alternatives:

- unrestricted ReAct agent: hard to guarantee tool sequence and safety;
- CrewAI/multiple agents: duplicated coordination and larger failure surface;
- microservices: operational overhead without assignment value;
- RAG/vector store: core evidence is structured and exact.

