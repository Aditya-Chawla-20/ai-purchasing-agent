# Product Scope

## 1. Problem statement

Buyers currently assemble inventory, demand, open-order, supplier, budget, and storage information manually before acting on a system-generated purchase recommendation. The product reduces this work while keeping numerical decisions reproducible and high-impact actions controlled.

## 2. User and job to be done

Primary user: retail or quick-commerce buyer.

When a purchasing recommendation or operational exception arrives, the buyer wants the system to investigate the relevant facts, propose a safe action with a concise explanation, execute an authorized decision, and confirm that the result is acceptable.

## 3. Target outcome

Given a recommendation to purchase 800 units for one product, supplier, and fulfilment node, the system returns one of:

- `ACCEPT`: 800 is safe and justified;
- `MODIFY`: purchase is justified, but a different feasible quantity is safer;
- `REJECT`: no purchase should be made;
- `INVESTIGATE`: no reliable decision can be made with current evidence.

The same bounded workflow also responds to supplier shortfalls and demand changes. The outcome includes the proposed quantity or sourcing plan, normalized evidence, selected tool trace, supplier comparison, constraint results, explanation, approval status, action results, and validation status.

## 4. In scope

### P0 - must ship

- Scenario 1 from intake through post-action validation.
- Seeded data for products, node inventory, forecast, open POs, supplier terms, budget, and storage.
- Evidence freshness and completeness checks.
- Deterministic requirement and feasibility calculations.
- Human approval interrupt for all PO writes in the demo.
- Idempotent mock PO creation.
- Audit timeline and decision explanation.
- Automated happy, modification, rejection, investigation, and duplicate-retry tests.

### P1 - agentic feedback-loop depth

- Supplier confirms only 250 of a 500-unit PO.
- Recompute inventory coverage after the partial confirmation.
- Use bounded function calling to investigate the exception and compare all eligible suppliers.
- Produce a reliability-first, cost-aware multi-supplier plan when one supplier cannot cover the justified need.
- Limit recovery attempts and prevent duplicate POs.

### P2 - demand-change breadth and evaluator control

- Demand-spike trigger using versioned recent-sales and revised-forecast evidence.
- Supplemental sourcing through the same allocation, approval, execution, and validation path.
- Development/test-only custom scenario builder using the real repositories and workflow.
- Optional hosted trace link.

## 5. Out of scope

- Production forecasting; forecasts are supplied as mock operational facts.
- Autonomous PO placement without a policy gate.
- Natural-language database access or LLM-generated SQL.
- Contract/document RAG.
- Network-wide allocation, transfers between fulfilment nodes, and replenishment optimization.
- Currency conversion, taxes, payment, receiving, returns, and invoicing.
- Full identity provider integration.
- Real supplier or ERP integration.
- Multi-tenant production infrastructure.

## 6. Success measures

| Measure | MVP target |
|---|---:|
| Deterministic decision correctness on curated cases | 100% |
| Hard-constraint violations executed | 0 |
| Duplicate POs under retries | 0 |
| Required evidence captured before a conclusive decision | 100% |
| Completed actions with post-action validation | 100% |
| Unsupported claims in displayed explanation | 0 |
| Invalid or out-of-scope model tool calls dispatched | 0 |
| Model-controlled PO fields accepted | 0 |
| Executed sourcing-plan lines with passed read-back validation | 100% |
| Local workflow time, excluding approval and external-provider latency | under 10 seconds |

## 7. Assumptions

- One recommendation targets one SKU, one node, and one preferred supplier.
- A sourcing plan may contain any number of eligible supplier lines for that SKU and node.
- Quantities are integer units and costs use integer minor currency units.
- Forecast demand is already aggregated for a requested date interval.
- Open PO quantity means outstanding quantity expected within the planning horizon.
- Storage capacity is expressed as volume and converted using product unit volume.
- An already-issued PO is not silently edited for a demand spike; any additional need becomes a supplemental plan.
- All timestamps are stored in UTC and displayed in the browser's locale.

## 8. Product non-goals

The UI is not a chat surface. The model is not the source of operational truth. A fluent explanation or tool request does not override failed constraint checks, and confidence does not authorize an action. Tool calling demonstrates bounded autonomy, not unrestricted agency.
