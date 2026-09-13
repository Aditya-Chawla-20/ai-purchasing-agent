# Graph Report - .  (2026-09-13)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 323 nodes · 759 edges · 22 communities (19 shown, 3 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 73 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- routes.py
- graph.py
- devDependencies
- App.tsx
- supplier_confirmation
- ExplanationProvider
- calculate_decision
- compilerOptions
- test_review_workflow.py
- compilerOptions
- db.py
- tsconfig.json
- purchasing/__init__.py
- ai-purchasing-agent

## God Nodes (most connected - your core abstractions)
1. `Base` - 24 edges
2. `ReviewState` - 24 edges
3. `calculate_decision()` - 22 edges
4. `DemoScenario` - 20 edges
5. `ReviewWorkflow` - 19 edges
6. `record_event()` - 18 edges
7. `compilerOptions` - 18 edges
8. `seed_demo_data()` - 17 edges
9. `ExplanationProvider` - 16 edges
10. `PurchaseDecision` - 15 edges

## Surprising Connections (you probably didn't know these)
- `test_disabled_llm_uses_template_without_contacting_a_provider()` --calls--> `ExplanationProvider`  [EXTRACTED]
  tests/unit/test_explanations.py → apps/api/purchasing/application/explanations.py
- `test_provider_failure_uses_configured_next_provider()` --calls--> `ExplanationProvider`  [EXTRACTED]
  tests/unit/test_explanations.py → apps/api/purchasing/application/explanations.py
- `DemoScenario` --uses--> `ApprovalRequest`  [INFERRED]
  apps/api/purchasing/application/demo_scenarios.py → apps/api/purchasing/infrastructure/models.py
- `DemoScenario` --uses--> `Decision`  [INFERRED]
  apps/api/purchasing/application/demo_scenarios.py → apps/api/purchasing/infrastructure/models.py
- `test_grounding_rejects_unknown_reason_or_evidence()` --calls--> `DecisionExplanation`  [EXTRACTED]
  tests/unit/test_explanations.py → apps/api/purchasing/application/explanations.py

## Import Cycles
- None detected.

## Communities (22 total, 3 thin omitted)

### Community 0 - "routes.py"
Cohesion: 0.17
Nodes (39): advance_scenario(), _clear_seeded_review_history(), DemoScenario, get_scenario(), _make_delayed_supply_visible(), _persist_ambiguous_purchase_order(), Recommendation, Session (+31 more)

### Community 1 - "graph.py"
Cohesion: 0.11
Nodes (34): Any, Session, record_event(), Session, store_snapshot(), DecisionType, ApprovalRequest, Decision (+26 more)

### Community 2 - "devDependencies"
Cohesion: 0.05
Nodes (37): dependencies, lucide-react, react, react-dom, vite, devDependencies, jsdom, @testing-library/jest-dom (+29 more)

### Community 3 - "App.tsx"
Cohesion: 0.11
Nodes (20): api, DemoScenario, Evidence, Recommendation, Review, TimelineEvent, App(), constraintDetail() (+12 more)

### Community 4 - "supplier_confirmation"
Cohesion: 0.19
Nodes (24): advance_demo_scenario(), create_review(), decide_approval(), get_events(), get_review(), list_recommendations(), partial_fulfilment_fixture(), get (+16 more)

### Community 5 - "ExplanationProvider"
Cohesion: 0.20
Nodes (16): DecisionExplanation, ExplanationFactor, ExplanationProvider, fallback_explanation(), gemini_explanation_schema(), BaseModel, Provider adapters for short explanations; never used for policy or…, Return the small Gemini schema the API accepts. Pydantic emits… (+8 more)

### Community 6 - "calculate_decision"
Cohesion: 0.19
Nodes (23): calculate_decision(), Confidence, ConstraintResult, EvidenceFact, EvidenceStatus, _investigate(), PurchaseInputs, BaseModel (+15 more)

### Community 7 - "compilerOptions"
Cohesion: 0.08
Nodes (24): compilerOptions, allowImportingTsExtensions, jsx, lib, module, moduleDetection, moduleResolution, noEmit (+16 more)

### Community 8 - "test_review_workflow.py"
Cohesion: 0.18
Nodes (17): approve_current(), recommendation(), reset_scenario(), start(), test_buyer_rejection_is_audited_and_never_creates_order(), test_changed_inventory_supersedes_approval_and_requires_new_proposal(), test_delayed_open_po_is_not_counted_as_eligible_incoming_supply(), test_exact_need_recommendation_is_accepted() (+9 more)

### Community 9 - "compilerOptions"
Cohesion: 0.12
Nodes (16): compilerOptions, allowImportingTsExtensions, lib, module, moduleDetection, moduleResolution, noEmit, skipLibCheck (+8 more)

### Community 10 - "db.py"
Cohesion: 0.18
Nodes (7): _enable_sqlite_foreign_keys(), get_session(), Session, get_settings(), Settings, BaseSettings, listens_for

## Knowledge Gaps
- **60 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+55 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `calculate_decision()` connect `calculate_decision` to `routes.py`, `graph.py`, `supplier_confirmation`, `ExplanationProvider`?**
  _High betweenness centrality (0.048) - this node is a cross-community bridge._
- **Why does `ExplanationProvider` connect `ExplanationProvider` to `graph.py`?**
  _High betweenness centrality (0.023) - this node is a cross-community bridge._
- **Why does `ReviewWorkflow` connect `graph.py` to `routes.py`, `ExplanationProvider`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **Are the 20 inferred relationships involving `Base` (e.g. with `ActionAttempt` and `ApprovalRequest`) actually correct?**
  _`Base` has 20 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `ReviewState` (e.g. with `ExplanationProvider` and `DecisionType`) actually correct?**
  _`ReviewState` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 16 inferred relationships involving `DemoScenario` (e.g. with `ActionAttempt` and `ApprovalRequest`) actually correct?**
  _`DemoScenario` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `ReviewWorkflow` (e.g. with `ExplanationProvider` and `DecisionType`) actually correct?**
  _`ReviewWorkflow` has 12 INFERRED edges - model-reasoned connections that need verification._