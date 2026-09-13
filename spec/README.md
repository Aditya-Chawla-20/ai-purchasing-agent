# AI Purchasing Agent - Specification Index

Status: implementation expansion contract  
Baseline: Scenario 1 and guarded partial-fulfilment recovery  
Target build: bounded tool-calling investigation plus Scenarios 1-3  
Constraint coverage: Scenario 4 is applied across every purchasing flow

## 1. Purpose

This folder is the source of truth for what will be built. The implementation must not introduce behavior that contradicts these specifications without first recording the change in the relevant spec and, for an architectural change, an ADR.

The product is a bounded purchasing decision system, not a general chatbot. It collects evidence, computes a safe recommendation, explains the result, obtains approval when required, executes through controlled services, and validates the outcome.

## 2. Design principles

1. Deterministic code owns calculations, allocations, constraints, authorization, and write payloads.
2. The LLM selects typed investigation tools and may invoke one guarded post-approval action tool; it may not invent facts or action fields.
3. Every material claim in a decision must point to captured evidence or a policy result.
4. Every write is validated immediately before execution and is idempotent.
5. Uncertainty, missing data, stale data, or conflicting data causes investigation or escalation.
6. Workflow state is durable and can pause for human approval.
7. Scenario depth, feedback, and evaluation take priority over feature breadth.

## 3. Locked implementation baseline

| Concern | Choice | Scope note |
|---|---|---|
| Frontend | React, TypeScript, Vite | Single buyer workspace |
| API | FastAPI and Pydantic | REST; polling is sufficient for MVP |
| Workflow | LangGraph | Explicit nodes, branches, checkpointing, interrupt/resume |
| Business logic | Plain Python services | Framework-independent and unit-testable |
| Persistence | SQLite | Operational data, audit data, and local checkpoints |
| ORM/migrations | SQLAlchemy 2 and Alembic | Minimal repository boundary |
| LLM | Capability-aware provider adapters | Gemini tool-calling primary; separate Groq agent and explanation models; NVIDIA explanation fallback; no Ollama |
| Tests | Pytest plus frontend component tests | Business rules are the source of truth |
| Tracing | Structured logs; LangSmith optional | App must work without paid services |

Explicitly excluded from the target build: CrewAI, vector databases, graph databases, conversational memory products, RAG, event brokers, microservices, Kubernetes, network-wide replenishment optimization, and production forecasting.

## 4. Reading and execution order

| Order | Spec | Question answered |
|---:|---|---|
| 1 | [Product scope](./00-product-scope.md) | What outcome are we building? |
| 2 | [Requirements](./01-requirements.md) | What must the system do? |
| 3 | [Architecture](./02-architecture.md) | How are responsibilities separated? |
| 4 | [Domain model](./03-domain-model.md) | What terms and invariants exist? |
| 5 | [Data model](./04-data-model.md) | What is persisted? |
| 6 | [Decision policy](./05-decision-policy.md) | How is the quantity and outcome determined? |
| 7 | [Agent workflow](./06-agent-workflow.md) | How does the run progress and recover? |
| 8 | [Tool contracts](./07-tool-contracts.md) | What can the agent access? |
| 9 | [API contract](./08-api-contract.md) | How do UI and backend communicate? |
| 10 | [Frontend UX](./09-frontend-ux.md) | What does the buyer see and do? |
| 11 | [Safety and reliability](./10-safety-reliability.md) | How are unsafe actions prevented? |
| 12 | [Observability](./11-observability-audit.md) | How is a decision reconstructed? |
| 13 | [Testing and evaluation](./12-testing-evaluation.md) | How do we prove correctness? |
| 14 | [Delivery plan](./13-delivery-plan.md) | In what order will it be implemented? |
| 15 | [Demo runbook](./14-demo-runbook.md) | How will the result be demonstrated? |

Architecture decisions are recorded under [`adr/`](./adr/).

## 5. Requirement language

`MUST`, `SHOULD`, and `MAY` are normative. Requirement IDs are stable and are referenced by tests and the traceability matrix in the evaluation spec.

## 6. Definition of done

The expanded assignment build is complete only when:

- the buyer can start a review for the seeded 800-unit recommendation;
- a bounded model-driven investigation selects typed tools and the mandatory-evidence guard obtains every safety-critical fact;
- deterministic logic returns accept, modify, reject, or investigate;
- the buyer can approve or reject an approval-gated proposal;
- an approved single- or multi-supplier plan creates exactly one purchase order per plan line;
- the workflow validates the persisted result;
- a partial supplier fulfilment compares suppliers and produces a validated recovery plan or escalation;
- a demand-spike signal produces a no-change decision, supplemental plan, or investigation from captured sales and forecast evidence;
- evaluators can create a validated local custom scenario without changing code;
- audit history explains what happened without relying on hidden model reasoning;
- the required automated scenarios pass from a clean checkout;
- the repository includes setup instructions, architecture, `.env.example`, and no secrets.
