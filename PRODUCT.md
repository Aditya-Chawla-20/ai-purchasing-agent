# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Retail buyers managing replenishment for a quick-commerce fulfilment network. They need to decide whether a purchasing recommendation is safe to approve while balancing inventory, demand, incoming supply, supplier limits, budget, and storage.

## Product Purpose

The AI Purchasing Agent turns an untrusted purchase recommendation into an explainable, buyer-approved action. It gathers operational evidence, calculates a deterministic decision, creates an idempotent purchase order only after approval, and validates the recorded result.

## Positioning

The system separates model-selected investigation and generated explanation from purchasing correctness: deterministic policy owns quantities, constraints, allocation, approvals, writes, and validation.

## Operating Context

Buyers work in a purchasing queue across fulfilment nodes. The primary review can accept, modify, reject, or investigate a recommendation. A supplier partial-fulfilment event can create a recovery review.

## Capabilities and Constraints

- React and TypeScript buyer workspace with FastAPI backend.
- Evidence snapshots include inventory, forecast, open POs, supplier terms, budget, storage, and planning policy.
- Purchase-order writes require buyer approval and read-back validation.
- Gemini is the primary native tool-calling provider with a tool-capable Groq fallback; Gemini, Groq Compound, and NVIDIA may phrase explanations independently. Investigation-provider exhaustion stops safely, while explanation-provider exhaustion uses a labelled deterministic template.
- The MVP intentionally excludes demand-spike handling.

## Brand Commitments

- Product name: Stockwise.
- The buyer asked for a complete Rappi-inspired commerce layout and energy, without copying Rappi’s logo, imagery, or proprietary content.
- Use original purchase-agent data, language, and controls.

## Evidence on Hand

- Seeded recommendations and purchase-review data are supplied through the local API.
- No licensed third-party marketing imagery or Rappi assets are available for reuse.

## Product Principles

- Make operational trade-offs legible before an irreversible action.
- Treat every recommendation as evidence to verify, never an instruction to execute.
- Keep approval, action, and validation visible in the buyer’s flow.
- Make safe fallbacks clear rather than hiding them.

## Accessibility & Inclusion

- Preserve keyboard-accessible controls and readable status text.
- Do not express important purchase state through color alone.
