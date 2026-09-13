# Buyer Workspace UX Specification

## 1. Product shape

Use a focused operational workspace, not a chat interface. The main route is `/reviews/:reviewId`; `/` shows available seeded recommendations and recent reviews.

## 2. Review page hierarchy

```text
Review header
├── SKU, node, supplier, source recommendation
├── state badge and last updated time
└── primary action when applicable

Decision summary
├── ACCEPT / MODIFY / REJECT / INVESTIGATE
├── original -> proposed quantity
├── confidence label
└── concise explanation

Why this decision
├── important factors with evidence links
├── calculation breakdown
└── hard and advisory constraint checks

Evidence
├── inventory
├── forecast
├── open purchase orders
├── supplier terms
├── budget
└── storage

Investigation
├── selected tool calls and outcomes
├── mandatory evidence coverage
└── provider/model and bounded-round status

Sourcing plan, when applicable
├── supplier allocation table
├── rejected suppliers and reason codes
├── reliability, cost, MOQ, delivery, and global-limit comparison
└── aggregate quantity, cost, storage, and validation progress

Approval / action / validation
└── context-sensitive panel

Timeline
└── ordered audit events
```

## 3. State-specific behavior

| State | UI behavior |
|---|---|
| Collecting/evaluating | Show progress skeleton and completed evidence cards; no write controls |
| Investigating | Show completed tool calls as they arrive, current round, and mandatory evidence progress; no hidden reasoning |
| Awaiting approval | Show proposal version, impact, all failed/advisory checks, approve and reject controls |
| Awaiting execution retry | Preserve approved-plan summary; explain provider failure and offer an authorized retry control |
| Executing/validating | Disable decision controls; show idempotent action progress |
| Completed | Show action/no-action result and passed validation badge |
| Needs attention | Show missing/unsafe factors and specific buyer next step; no misleading success state |
| Failed | Show correlation ID and retry/start-over guidance only when safe |

## 4. Approval interaction

- Approve and reject are visually distinct and keyboard reachable.
- Approval opens a confirmation dialog summarizing every supplier line, SKU, node, total quantity, total cost, delivery range, and proposal version.
- Reject requires a comment.
- While the request is pending, controls are disabled to prevent double submission.
- A `409 STALE_PROPOSAL` closes the dialog, refreshes the page, and tells the buyer the facts changed.
- The frontend never assumes approval means execution succeeded.

## 5. Explanation and provenance

- Facts display source and observed-at timestamp.
- Calculated values are labeled `Calculated` and show the formula inputs.
- Model-authored text is labeled `AI explanation`.
- Missing/stale evidence has a visible warning icon plus text, not color alone.
- The UI does not display hidden reasoning; it displays concise reason codes translated into buyer language.
- Tool-call cards display tool, purpose, status, duration, and evidence produced; raw prompts and model reasoning are never shown.

## 6. Supplier and demand visualizations

- Multi-supplier plans use a compact table with allocated quantity, MOQ, availability, reliability, unit/total cost, expected delivery, inclusion reason, action state, and validation state.
- Rejected supplier options remain visible in a collapsed comparison with explicit exclusion reasons.
- Scenario 3 shows one compact coverage chart comparing baseline demand, actual sales velocity projected over the same interval, revised forecast, usable inventory, and eligible incoming supply.
- Charts always include numeric labels and table/text equivalents; color is not the only carrier of meaning.
- A plan is never rendered as successful until every line has passed read-back validation.

## 7. Custom Scenario Lab

- A development/test-only builder supports recommendation and demand-change modes.
- The form groups product/node, inventory/demand, supplier table, open POs, budget/storage, and planning policy. Demand-change mode additionally requests baseline/revised forecast and recent-sales evidence.
- Supplier and open-PO rows are addable/removable with inline validation. The submit action creates persisted demo facts and opens the normal review route.
- The builder contains no client-side decision preview; server validation and the standard workflow remain authoritative.
- Scenario reset adds `demand-spike` to the existing repeatable safeguards.

## 8. Accessibility and responsiveness

- Meet WCAG 2.1 AA contrast for core controls.
- Semantic headings and tables, visible focus, labels for icons, and live-region updates for state changes.
- Desktop-first design at 1280 px; remains usable at 768 px by stacking cards.
- Do not hide failed constraints or approval impact behind hover-only UI.

## 9. Frontend state

Use server state as the source of truth. A small query layer handles fetching, polling, request deduplication, and cache invalidation. Local state is limited to dialogs, comments, and display preferences. Do not duplicate policy calculations in TypeScript.

## 10. Empty and error states

- Empty recommendations: explain how to load seed data.
- Partial evidence failure: retain successful evidence and show which source failed.
- LLM unavailable: display the deterministic explanation without degrading decision controls.
- Network failure: show retry without discarding a typed rejection comment.
- Investigation exhausted: list failed/missing tools and show a safe needs-attention outcome.
- Action provider unavailable: retain the approved proposal and offer execution retry; never imply the PO exists.
- Multi-line partial execution: show successful and failed lines separately and direct the buyer to attention/recovery.
