# Demo Runbook

## 1. Demo goal

Demonstrate a system that chooses relevant typed tools, questions an incoming recommendation, uses multiple facts, compares suppliers, obtains authorization, executes only an immutable approved plan, validates every result, and handles supplier and demand exceptions.

Target core demo duration: 7-10 minutes. Scenario 3 and custom input are optional follow-on segments.

## 2. Pre-demo checks

- Start from documented seed data.
- Confirm API health and frontend load.
- Run the fast automated evaluation suite.
- Use deterministic explanation mode as a fallback if the external model is unavailable.
- Confirm Gemini is the primary tool provider and a tool-capable Groq agent model—not Groq Compound—is configured as fallback.
- Keep the audit timeline visible and do not inspect raw database rows during the main flow.

## 3. Flow A - modify and execute the 800-unit recommendation

1. Open the seeded recommendation for 800 units.
2. Start the review and show the model selecting typed read tools over one or more bounded rounds.
3. Show that the mandatory evidence manifest fills any safety-critical read the model omitted.
4. Show the computed inventory position, target stock, raw need, and caps.
5. Show `MODIFY` with the seeded feasible quantity and reason codes.
6. Explain that the text summarizes an already-computed decision.
7. Approve the exact proposal version.
8. Show the model receiving only `execute_approved_proposal` and calling it with the persisted proposal ID.
9. Show pre-execution revalidation, one PO creation attempt, read-back, and passed validation.
10. Refresh the page to prove durable state.

Expected proof:

- original 800 is not trusted;
- multiple constraints affect the final quantity;
- approval is explicit;
- PO result, not request success alone, determines completion.

## 4. Flow B - stale approval protection

1. Start an approval-ready review.
2. Change a volatile mock fact before approval, such as inventory or budget.
3. Approve the displayed version.
4. Show that pre-execution validation creates a new proposal or cancels the now-unneeded action.

Expected proof: approval is tied to facts and version; it is not a blank authorization.

## 5. Flow C - supplier fulfils only 250 of 500 units

1. Show the validated 500-unit PO.
2. Submit a supplier confirmation for 250 units using the demo control.
3. Show the system recompute current coverage and remaining need.
4. Show every candidate supplier, exclusion reason, reliability/cost/MOQ/delivery comparison, and deterministic allocation.
5. Approve the immutable recovery plan and show one idempotent, validated PO per supplier line.

Expected proof: the agent does not assume the missing 250 must automatically be reordered, and the model does not perform allocation arithmetic.

## 6. Flow D - demand spike and supplemental purchase

1. Reset the `demand-spike` Scenario Lab case.
2. Show baseline forecast, at least 24 hours of recent sales, the `>= 1.5x` velocity signal, and the newer revised forecast.
3. Show the investigator obtain inventory, eligible incoming POs, supplier options, budget, storage, and planning policy.
4. Compare baseline demand, projected actual velocity, revised demand, and available supply in the coverage visual.
5. Show that the issued PO is unchanged and the uncovered quantity becomes a supplemental sourcing plan.
6. Approve, execute, and validate every supplemental plan line.

Expected proof: a changed forecast triggers a new evidence-backed plan instead of blindly editing or duplicating the existing PO.

## 7. Flow E - model action provider unavailable

1. Use a demo/fake provider mode that makes both tool-capable providers fail after approval.
2. Approve a valid proposal and show `AWAITING_EXECUTION_RETRY` with no PO created.
3. Restore a provider and select retry.
4. Show the evidence refresh; if unchanged, execute with original line idempotency keys. If changed, show renewed approval.

Expected proof: model availability can delay an action but cannot bypass approval, freshness, or idempotency.

## 8. Custom evaluator case

1. Open the development-only custom scenario builder.
2. Enter a product/node, recommendation, inventory, forecast, supplier rows, open POs, budget, storage, and policy—or choose demand-change mode and add recent sales/revised forecast.
3. Submit and show that the created review uses the same tool, decision, approval, execution, and validation views as seeded cases.
4. Point out that no calculation runs in the browser and custom records cannot overwrite seeded data.

Expected proof: an evaluator can test new facts without code changes or a parallel simulator.

## 9. Automated evaluation evidence

Show concise results for at least:

- accept;
- modify;
- reject;
- investigate on missing/stale evidence;
- duplicate create retry;
- observed PO mismatch;
- partial fulfilment.
- bounded tool selection and mandatory evidence completion;
- multi-supplier allocation and partial multi-line execution;
- action-provider failure and safe retry;
- demand spike with supplemental purchase and sufficient-coverage no-op;
- custom scenario isolation.

Explain that business decisions use exact expected outputs, while explanation quality is evaluated for grounding and clarity.

## 10. Scenario Lab

Use the Scenario Lab in the buyer workspace to reset and inspect five repeatable safeguards:

- conflicting inventory -> `INVESTIGATE`, no PO;
- delayed open PO -> it does not reduce the requirement;
- supplier quote change -> original approval is superseded;
- lost create response -> the matching PO is recovered by idempotency key;
- wrong persisted PO -> read-back validation escalates safely.
- demand spike -> recent sales and revised forecast produce a supplemental plan or safe no-op.

The supplier quote case has one extra control: apply the supplier update, then approve the visible
first proposal to show the new proposal version. Scenario reset is available only in development/test.

## 11. Likely evaluator questions

### Why use an agent?

The model dynamically selects phase-scoped tools over a bounded loop and invokes the approved action tool. The graph enforces lifecycle and recovery, while arithmetic, supplier allocation, authorization, and validation remain deterministic because they require reproducibility.

### Why not RAG or a vector database?

The required evidence is structured and exact. RAG becomes useful only when supplier contracts, SOPs, or emails enter scope.

### What happens if the model fails?

Explanation failure uses a deterministic explanation. Investigation-provider exhaustion stops safely in `NEEDS_ATTENTION`. If all tool providers fail after approval, the unchanged approved proposal waits in `AWAITING_EXECUTION_RETRY`; no PO is created until a valid tool call occurs.

### How do you prevent duplicate orders?

Proposal versioning, pre-write validation, a stable idempotency key, a uniqueness constraint, and result lookup after ambiguous failures.

### What would change in production?

External adapters, authentication, worker topology, PostgreSQL, secret management, and monitoring. The domain policies, approval contract, and validation loop remain.

## 12. Demo completion criteria

The demo succeeds only if the audience can see the full chain:

```text
situation -> model-selected tools -> mandatory evidence -> deterministic decision/allocation
-> approval -> proposal-ID action tool -> prevalidation -> per-line action
-> observed results -> validation -> recovery/complete
```
