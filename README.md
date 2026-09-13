# AI Purchasing Agent

A small buyer workspace for reviewing purchase recommendations, grounding a deterministic decision in operational evidence, obtaining buyer approval, creating one idempotent purchase order, and validating the persisted result. The main demo modifies the seeded 800-unit recommendation to 450 units; a partial-supplier-confirmation path recomputes the remaining need.

## Architecture

```mermaid
flowchart LR
    Buyer[React + TypeScript] --> API[FastAPI / Pydantic]
    API --> Graph[LangGraph review workflow]
    Graph --> Domain[Plain Python decision policy]
    Graph --> Explain[Gemini / NVIDIA / Groq explanation adapter]
    Graph --> Store[SQLAlchemy + SQLite]
    Graph --> Checkpoint[SQLite workflow checkpoints]
    Graph --> Supplier[Mock supplier / PO adapter]
    Graph --> Audit[Append-only review events]
    Audit --> Store
```

The model only phrases a decision from a closed, typed package. Python owns quantities, constraints, approval checks, and writes. The configured provider falls through to explicitly configured backup providers, then to a deterministic explanation if every provider is missing, unavailable, slow, or returns invalid output. No Ollama, RAG, vector database, multi-agent framework, or external service is required.

## Approach and safety model

The workflow treats a planner recommendation as an input to verify, not an instruction to execute. It captures inventory, forecast, confirmed inbound supply, supplier terms, budget, storage capacity, and planning policy in an evidence snapshot. The deterministic policy then calculates usable stock, inventory position, target stock, raw need, and the largest feasible MOQ-compliant order.

Every purchase-order write requires buyer approval. After approval, the workflow refreshes the volatile evidence and creates a new proposal if any operational value changed. A write uses a proposal-version idempotency key, then the system reads the persisted purchase order back and compares supplier, node, item, quantity, price, currency, status, and idempotency key before showing success. Missing, stale, conflicting, or currency-incompatible facts result in `INVESTIGATE`, never an automatic order.

## Requirements

- Python 3.11+
- `uv`
- Node.js 22+ and `pnpm`

## Run locally

From the repository root:

```bash
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn purchasing.main:app --app-dir apps/api --reload
```

In a second terminal:

```bash
pnpm --dir apps/web install
pnpm --dir apps/web dev
```

Open the Vite URL (normally `http://localhost:5173`). The API runs at `http://localhost:8000`; interactive API docs are at `/docs`. Seed fixtures are added on API startup and are safe to re-run.

The application works without an API key using its deterministic explanation. To enable model wording, add provider keys to `.env`; `LLM_PROVIDER=gemini` is primary by default and `LLM_FALLBACK_PROVIDERS=groq,nvidia` specifies the ordered backups. Provider model names, OpenAI-compatible base URLs, and request timeout are configurable there. Keep `.env` local; do not commit credentials. Choose Groq only after measuring the configured provider’s latency with a representative prompt.

## Verify

```bash
uv run pytest
uv run ruff check apps/api tests
pnpm --dir apps/web test -- --run
pnpm --dir apps/web build
```

Tests use a temporary SQLite database and deterministic explanations. They set `LLM_ENABLED=false`, so they do not call paid or free-tier model APIs even if local provider keys exist. The workflow tests cover the 800-unit review, acceptance/rejection/investigation, approval, PO read-back validation, request idempotency, and partial fulfilment.

The current automated suite covers these evaluation cases:

| Scenario | Expected result | Evidence |
|---|---|---|
| 800-unit recommendation | `MODIFY` to 450, approval, validated PO | Integration workflow test |
| Exact calculated need | `ACCEPT`, then approval required for the PO | Integration workflow test |
| Existing coverage sufficient | `REJECT`, no PO | Integration workflow test |
| Stale forecast | `INVESTIGATE`, no PO | Integration workflow test |
| Buyer rejects proposal | Terminal rejection, no PO | Integration workflow test |
| Facts change after approval | Proposal is superseded and re-approved | Integration workflow test |
| MOQ, budget, storage, supplier capacity | Feasible quantity never exceeds a hard limit | Domain unit tests |
| Partial supplier confirmation | Coverage is recomputed; duplicate cumulative confirmations are ignored | Integration workflow test |

The Scenario Lab tests additionally cover conflicting inventory, overdue incoming orders, supplier quote changes after review, lost create responses, and deliberately malformed persisted purchase orders.

## Demo

1. Open the recommendation queue and select `REC-800`.
2. Start the review. Inspect its evidence, inventory position, requirement, caps, and `MODIFY 450` decision.
3. Approve the exact proposal version. The backend refreshes inputs before writing.
4. Confirm the purchase order is read back and validation passes.
5. Use “Run supplier exception demo” to submit a 250-of-500 confirmation and inspect the recomputed proposal.
6. Use Scenario Lab to reset any of five focused Scenario 1 safeguards and inspect the real workflow outcome.

See [spec/14-demo-runbook.md](spec/14-demo-runbook.md) for the detailed walkthrough and [spec/README.md](spec/README.md) for the complete implementation contract.

## What to demonstrate in the assignment

Use the 800-unit flow as the primary end-to-end scenario. It proves that the system gathers several independent facts, modifies an unsafe recommendation, requires human approval, creates exactly one PO, and validates the observed result. Then run the supplier-exception control to show that a 250-of-500 confirmation recalculates coverage before proposing another action.

Scenario 3 (a demand spike) is intentionally outside this MVP. The assignment requires at least one end-to-end scenario and prioritizes depth, feedback, and validation over implementing all four scenarios.

## Clean setup check

The documented setup assumes a new checkout with no existing local SQLite database. If you previously ran an older schema locally, move its `purchasing.db` and `checkpoints.db` aside before running the migration. Database files are ignored by Git and can be recreated from the migration and seed data.
