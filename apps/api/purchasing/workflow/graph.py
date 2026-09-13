from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select

from purchasing.application.audit import record_event
from purchasing.application.evidence import build_inputs_from_tool_results, store_snapshot
from purchasing.application.explanations import ExplanationProvider
from purchasing.application.providers import ProviderCoordinator
from purchasing.application.sourcing import create_and_persist_sourcing_plan
from purchasing.application.tools import (
    MANDATORY_MANIFESTS,
    READ_ONLY_TOOLS,
    ToolContext,
    ToolDispatcher,
    complete_mandatory_manifest,
    tool_execute_approved_proposal,
)
from purchasing.domain.policy import DecisionType, calculate_decision
from purchasing.infrastructure.db import SessionLocal
from purchasing.infrastructure.models import (
    ActionAttempt,
    ApprovalRequest,
    Decision,
    EvidenceSnapshot,
    InvestigationTrace,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseProposal,
    PurchasingReview,
    Recommendation,
    SourcingPlan,
    SourcingPlanLine,
    SupplierProduct,
    ValidationResult,
    new_id,
)

logger = logging.getLogger(__name__)


class ReviewState(TypedDict, total=False):
    review_id: str
    approval: dict[str, Any]
    decision_id: str
    action_attempt_id: str
    validation_status: str
    route: str


def _evidence_fingerprint(payload: dict[str, Any]) -> str:
    facts = [
        {key: fact.get(key) for key in ("name", "status", "value", "version")}
        for fact in payload.get("evidence", [])
    ]
    return json.dumps(facts, sort_keys=True, separators=(",", ":"))


def _collect_tool_evidence(
    review: PurchasingReview,
    recommendation: Recommendation,
    scenario_type: str,
    trace_id: str | None = None,
):
    """Run provider planning without holding a SQLite session or transaction open."""
    # Scope discovery is persisted before the provider is called. Each tool batch
    # below receives its own short-lived session, so slow/failing providers cannot
    # monopolize SQLite's write lock.
    with SessionLocal() as scope_session:
        supplier_ids = set(scope_session.scalars(select(SupplierProduct.supplier_id).where(
            SupplierProduct.product_id == recommendation.product_id
        )).all())
        po_ids = set(scope_session.scalars(select(PurchaseOrder.id).where(
            PurchaseOrder.node_id == recommendation.node_id
        )).all())
    dispatcher = ToolDispatcher(
        session=None,
        trace_id=trace_id,
        review_id=review.id,
        context=ToolContext(
            review_id=review.id,
            product_id=recommendation.product_id,
            node_id=recommendation.node_id,
            preferred_supplier_id=recommendation.preferred_supplier_id,
            allowed_supplier_ids=frozenset(supplier_ids),
            allowed_purchase_order_ids=frozenset(po_ids),
        ),
    )
    coordinator = ProviderCoordinator()
    collected: dict[str, Any] = {}
    selected: list[str] = []
    situation = {
        "review_id": review.id,
        "recommendation_id": recommendation.id,
        "product_id": recommendation.product_id,
        "node_id": recommendation.node_id,
        "preferred_supplier_id": recommendation.preferred_supplier_id,
        "recommended_quantity": recommendation.recommended_quantity,
        "scenario_type": scenario_type,
    }
    rounds_run = 0
    for round_number in range(1, 4):
        try:
            provider, model, calls = coordinator.run_investigation_round(situation, selected, round_number)
        except Exception as exc:
            logger.warning("Investigation provider unavailable: %s", exc)
            break
        if not calls:
            break
        rounds_run = round_number
        with SessionLocal() as dispatch_session:
            dispatcher.session = dispatch_session
            for call in calls:
                if dispatcher.total_calls >= dispatcher.max_total_calls:
                    break
                response = dispatcher.dispatch(
                    call.tool_name, round_number=round_number, args=call.arguments,
                    provider=provider, model=model,
                )
                collected[call.tool_name] = response
                if response.ok and call.tool_name not in selected:
                    selected.append(call.tool_name)
            dispatch_session.commit()
    with SessionLocal() as manifest_session:
        dispatcher.session = manifest_session
        collected = complete_mandatory_manifest(
            session=manifest_session, dispatcher=dispatcher, scenario_type=scenario_type,
            product_id=recommendation.product_id, node_id=recommendation.node_id,
            preferred_supplier_id=recommendation.preferred_supplier_id, collected_tools=collected,
        )
        manifest_session.commit()
    if trace_id:
        with SessionLocal() as trace_session:
            trace = trace_session.get(InvestigationTrace, trace_id)
            if trace:
                trace.rounds_used = rounds_run
                trace.status = "COMPLETED" if all(result.ok for result in collected.values()) else "DEFICIENT"
                trace.completed_at = datetime.now(UTC)
                trace_session.commit()
    return (*build_inputs_from_tool_results(recommendation, collected), collected)


def _prepare(state: ReviewState) -> dict:
    with SessionLocal() as session:
        review = session.get(PurchasingReview, state["review_id"])
        if not review:
            return {"route": "failed"}
        recommendation = session.get(Recommendation, review.recommendation_id)
        if not recommendation:
            review.status = "FAILED"
            session.commit()
            return {"route": "failed"}
        review.status = "COLLECTING_EVIDENCE"
        session.flush()
        record_event(session, review.id, "WORKFLOW_STATE_CHANGED", {"status": review.status})

        scenario_type = review.scenario_type or "recommendation-review"
        if review.recovery_attempts > 0 and scenario_type == "recommendation-review":
            scenario_type = "supplier-shortfall"

        # Create or fetch InvestigationTrace
        allowed_tools = list(READ_ONLY_TOOLS)
        mandatory_manifest = list(
            MANDATORY_MANIFESTS.get(
                scenario_type, MANDATORY_MANIFESTS["recommendation-review"]
            )
        )
        trace = session.scalar(
            select(InvestigationTrace)
            .where(InvestigationTrace.review_id == review.id)
            .order_by(InvestigationTrace.started_at.desc())
            .limit(1)
        )
        if not trace:
            trace = InvestigationTrace(
                review_id=review.id,
                scenario_type=scenario_type,
                provider="coordinator",
                model=ProviderCoordinator().primary.model_name,
                allowed_tools_json=allowed_tools,
                mandatory_manifest_json=mandatory_manifest,
                status="IN_PROGRESS",
                rounds_used=0,
                started_at=datetime.now(UTC),
            )
            session.add(trace)
            session.flush()
        else:
            trace.allowed_tools_json = allowed_tools
            trace.mandatory_manifest_json = mandatory_manifest

        # Persist and release the setup transaction before network provider calls.
        session.commit()
        inputs, payload, _tool_results = _collect_tool_evidence(
            review, recommendation, scenario_type, trace.id
        )
        session.flush()
        sequence = (
            session.scalar(
                select(EvidenceSnapshot.sequence)
                .where(EvidenceSnapshot.review_id == review.id)
                .order_by(EvidenceSnapshot.sequence.desc())
                .limit(1)
            )
            or 0
        ) + 1
        snapshot = store_snapshot(session, review.id, sequence, payload)
        review.status = "EVALUATING"
        decision = calculate_decision(inputs, payload.get("budget_currency"))
        decision_id = new_id()
        explanation, provider = ExplanationProvider().explain(decision, payload.get("errors"))
        saved = Decision(
            id=decision_id,
            review_id=review.id,
            evidence_snapshot_id=snapshot.id,
            version=review.current_proposal_version + 1,
            decision_type=decision.decision.value,
            original_quantity=decision.original_quantity,
            raw_need=decision.raw_need,
            proposed_quantity=decision.proposed_quantity,
            confidence_label=decision.confidence.value,
            reason_codes_json=list(decision.reason_codes),
            calculation_json=decision.model_dump(mode="json", exclude={"evidence"}),
            explanation_json=explanation.model_dump(mode="json"),
        )
        session.add(saved)
        session.flush()
        record_event(
            session,
            review.id,
            "EVIDENCE_SNAPSHOT_CREATED",
            {
                "snapshot_hash": snapshot.snapshot_hash,
                "complete": payload["evidence_complete"],
                "errors": payload["errors"],
            },
        )
        record_event(
            session,
            review.id,
            "DECISION_CALCULATED",
            {
                "decision": decision.decision.value,
                "quantity": decision.proposed_quantity,
                "reason_codes": list(decision.reason_codes),
                "policy_version": review.policy_version,
            },
        )
        record_event(session, review.id, "EXPLANATION_GENERATED", {"provider": provider})
        if provider == "template":
            record_event(
                session,
                review.id,
                "EXPLANATION_FALLBACK_USED",
                {"reason": "UNAVAILABLE_OR_INVALID"},
            )
        if decision.decision == DecisionType.INVESTIGATE:
            review.status = "NEEDS_ATTENTION"
            record_event(
                session,
                review.id,
                "REVIEW_ESCALATED",
                {"reason_codes": list(decision.reason_codes)},
            )
            session.commit()
            return {"decision_id": saved.id, "route": "done"}
        if decision.decision == DecisionType.REJECT or decision.proposed_quantity == 0:
            review.status = "COMPLETED"
            review.completed_at = datetime.now(UTC)
            record_event(
                session,
                review.id,
                "REVIEW_COMPLETED",
                {"decision": decision.decision.value, "action": "NONE"},
            )
            session.commit()
            return {"decision_id": saved.id, "route": "done"}

        proposal_version = saved.version
        review.current_proposal_version = proposal_version

        hard_cap = (
            min(decision.budget_units, decision.storage_units)
            if (decision.budget_units and decision.storage_units)
            else decision.proposed_quantity
        )
        target_need = min(decision.raw_need, hard_cap) if decision.raw_need > 0 else decision.proposed_quantity

        sourcing_plan, plan_result, proposal = create_and_persist_sourcing_plan(
            session=session,
            review_id=review.id,
            decision_id=saved.id,
            snapshot_id=snapshot.id,
            snapshot_hash=snapshot.snapshot_hash,
            product_id=recommendation.product_id,
            node_id=recommendation.node_id,
            currency=payload.get("budget_currency", "INR"),
            raw_need=target_need,
            budget_available_minor=inputs.budget_available_minor,
            available_storage_volume=inputs.available_storage_volume,
            product_unit_volume=inputs.product_unit_volume,
            proposal_version=proposal_version,
            preferred_supplier_id=recommendation.preferred_supplier_id,
            alternate_sourcing_required=(scenario_type != "recommendation-review" or review.recovery_attempts > 0),
        )

        record_event(
            session,
            review.id,
            "SOURCING_PLAN_CREATED",
            {
                "sourcing_plan_id": sourcing_plan.id,
                "total_quantity": sourcing_plan.total_quantity,
                "line_count": len(plan_result.lines),
                "status": plan_result.status,
            },
        )

        # In multi-supplier shortfall cases, if full coverage is infeasible:
        if plan_result.status != "COMPLETE":
            review.status = "NEEDS_ATTENTION"
            record_event(
                session,
                review.id,
                "REVIEW_ESCALATED",
                {"reason_codes": ["SHORTFALL_UNRESOLVED"], "sourcing_plan_id": sourcing_plan.id},
            )
            session.commit()
            return {"decision_id": saved.id, "sourcing_plan_id": sourcing_plan.id, "route": "done"}

        # If multi-supplier allocation covered the full raw need (e.g. 600 across 2 suppliers where preferred was 300):
        if plan_result.covered_quantity > saved.proposed_quantity:
            saved.proposed_quantity = plan_result.covered_quantity
            saved.calculation_json["total_cost_minor"] = plan_result.total_cost_minor
            session.flush()

        review.status = "AWAITING_APPROVAL"
        session.add(
            ApprovalRequest(
                review_id=review.id, decision_id=saved.id, proposal_id=proposal.id,
                proposal_version=proposal_version
            )
        )
        record_event(
            session,
            review.id,
            "APPROVAL_REQUESTED",
            {
                "proposal_version": proposal_version,
                "quantity": saved.proposed_quantity,
                "proposal_id": proposal.id,
                "sourcing_plan_id": sourcing_plan.id,
            },
        )
        session.commit()
        return {
            "decision_id": saved.id,
            "proposal_id": proposal.id,
            "sourcing_plan_id": sourcing_plan.id,
            "route": "approval",
        }


def _approval(state: ReviewState) -> dict:
    response = interrupt(
        {
            "review_id": state["review_id"],
            "decision_id": state["decision_id"],
            "message": "Buyer approval required.",
        }
    )
    return {"approval": response}


def _prevalidate(state: ReviewState) -> dict:
    approval = state.get("approval", {})
    with SessionLocal() as session:
        review = session.get(PurchasingReview, state["review_id"])
        if not review:
            return {"route": "failed"}
        request = session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.review_id == review.id,
                ApprovalRequest.proposal_version == approval.get("proposal_version"),
            )
        )
        if approval.get("decision") != "APPROVE" or not request or request.status != "APPROVED":
            review.status = "REJECTED_BY_BUYER"
            review.completed_at = datetime.now(UTC)
            record_event(
                session,
                review.id,
                "REVIEW_COMPLETED",
                {"decision": "REJECTED_BY_BUYER"},
                "BUYER",
                approval.get("actor_id"),
            )
            session.commit()
            return {"route": "done"}
        recommendation = session.get(Recommendation, review.recommendation_id)
        session.commit()
        inputs, payload, _tool_results = _collect_tool_evidence(
            review, recommendation, review.scenario_type or "recommendation-review"
        )
        current = session.get(Decision, state["decision_id"])
        assert current is not None
        snap = session.get(EvidenceSnapshot, current.evidence_snapshot_id)
        refreshed = calculate_decision(inputs, payload.get("budget_currency"))
        proposal = session.scalar(
            select(PurchaseProposal).where(
                PurchaseProposal.review_id == review.id,
                PurchaseProposal.proposal_version == current.version,
            )
        )
        plan = (
            session.get(SourcingPlan, proposal.sourcing_plan_id)
            if proposal and proposal.sourcing_plan_id
            else None
        )
        fingerprint_changed = (
            not payload["evidence_complete"]
            or _evidence_fingerprint(payload)
            != _evidence_fingerprint(snap.payload_json if snap else {})
        )
        plan_lines = (
            session.scalars(
                select(SourcingPlanLine).where(
                    SourcingPlanLine.sourcing_plan_id == plan.id
                )
            ).all()
            if plan
            else []
        )
        if plan and len(plan_lines) > 1:
            changed = fingerprint_changed
        else:
            changed = (
                refreshed.decision.value != current.decision_type
                or refreshed.proposed_quantity != current.proposed_quantity
                or refreshed.total_cost_minor != current.calculation_json.get("total_cost_minor")
                or refreshed.currency != current.calculation_json.get("currency")
                or fingerprint_changed
            )
        if changed:
            record_event(
                session,
                review.id,
                "PRE_EXECUTION_REVALIDATED",
                {
                    "changed": True,
                    "previous_quantity": current.proposed_quantity,
                    "new_quantity": refreshed.proposed_quantity,
                },
            )
            review.status = "EVALUATING"
            sequence = (
                session.scalar(
                    select(EvidenceSnapshot.sequence)
                    .where(EvidenceSnapshot.review_id == review.id)
                    .order_by(EvidenceSnapshot.sequence.desc())
                    .limit(1)
                )
                or 0
            ) + 1
            new_snapshot = store_snapshot(session, review.id, sequence, payload)
            new_id_ = new_id()
            explanation, provider = ExplanationProvider().explain(refreshed, payload.get("errors"))
            updated = Decision(
                id=new_id_,
                review_id=review.id,
                evidence_snapshot_id=new_snapshot.id,
                version=current.version + 1,
                decision_type=refreshed.decision.value,
                original_quantity=refreshed.original_quantity,
                raw_need=refreshed.raw_need,
                proposed_quantity=refreshed.proposed_quantity,
                confidence_label=refreshed.confidence.value,
                reason_codes_json=list(refreshed.reason_codes),
                calculation_json=refreshed.model_dump(mode="json", exclude={"evidence"}),
                explanation_json=explanation.model_dump(mode="json"),
            )
            session.add(updated)
            session.flush()
            request.status = "SUPERSEDED"
            if proposal:
                proposal.status = "SUPERSEDED"
            record_event(
                session,
                review.id,
                "DECISION_CALCULATED",
                {
                    "decision": refreshed.decision.value,
                    "quantity": refreshed.proposed_quantity,
                    "reason_codes": list(refreshed.reason_codes),
                    "policy_version": review.policy_version,
                },
            )
            review.current_proposal_version = updated.version
            if refreshed.decision == DecisionType.INVESTIGATE:
                review.status = "NEEDS_ATTENTION"
                record_event(
                    session,
                    review.id,
                    "REVIEW_ESCALATED",
                    {"reason_codes": list(refreshed.reason_codes)},
                )
                session.commit()
                return {"decision_id": updated.id, "route": "done"}
            if refreshed.decision == DecisionType.REJECT or refreshed.proposed_quantity == 0:
                review.status = "COMPLETED"
                review.completed_at = datetime.now(UTC)
                record_event(
                    session, review.id, "REVIEW_COMPLETED", {"decision": "REJECT", "action": "NONE"}
                )
                session.commit()
                return {"decision_id": updated.id, "route": "done"}
            hard_cap = (
                min(refreshed.budget_units, refreshed.storage_units)
                if (refreshed.budget_units and refreshed.storage_units)
                else refreshed.proposed_quantity
            )
            target_need = (
                min(refreshed.raw_need, hard_cap)
                if refreshed.raw_need > 0
                else refreshed.proposed_quantity
            )
            sourcing_plan, plan_result, proposal = create_and_persist_sourcing_plan(
                session=session,
                review_id=review.id,
                decision_id=updated.id,
                snapshot_id=new_snapshot.id,
                snapshot_hash=new_snapshot.snapshot_hash,
                product_id=recommendation.product_id,
                node_id=recommendation.node_id,
                currency=payload.get("budget_currency", "INR"),
                raw_need=target_need,
                budget_available_minor=inputs.budget_available_minor,
                available_storage_volume=inputs.available_storage_volume,
                product_unit_volume=inputs.product_unit_volume,
                proposal_version=updated.version,
                preferred_supplier_id=recommendation.preferred_supplier_id,
                alternate_sourcing_required=(review.recovery_attempts > 0),
            )
            if plan_result.covered_quantity > updated.proposed_quantity:
                updated.proposed_quantity = plan_result.covered_quantity
                updated.calculation_json["total_cost_minor"] = plan_result.total_cost_minor
                session.flush()

            review.status = "AWAITING_APPROVAL"
            session.add(
                ApprovalRequest(
                    review_id=review.id, decision_id=updated.id, proposal_id=proposal.id,
                    proposal_version=updated.version
                )
            )
            record_event(
                session,
                review.id,
                "APPROVAL_REQUESTED",
                {
                    "proposal_version": updated.version,
                    "quantity": updated.proposed_quantity,
                    "provider": provider,
                    "proposal_id": proposal.id,
                    "sourcing_plan_id": sourcing_plan.id,
                },
            )
            session.commit()
            return {
                "decision_id": updated.id,
                "proposal_id": proposal.id,
                "sourcing_plan_id": sourcing_plan.id,
                "route": "approval",
            }
        record_event(
            session,
            review.id,
            "PRE_EXECUTION_REVALIDATED",
            {"changed": False, "snapshot_hash": snap.snapshot_hash if snap else None},
        )
        review.status = "EXECUTING"
        session.commit()
        return {"route": "execute"}


def _execute(state: ReviewState) -> dict:
    with SessionLocal() as session:
        review = session.get(PurchasingReview, state["review_id"])
        decision = session.get(Decision, state["decision_id"])
        if not review or not decision:
            return {"route": "failed"}
        approval = session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.review_id == review.id,
                ApprovalRequest.proposal_version == decision.version,
            )
        )
        if (
            review.current_proposal_version != decision.version
            or not approval
            or approval.status != "APPROVED"
        ):
            review.status = "NEEDS_ATTENTION"
            record_event(
                session, review.id, "REVIEW_ESCALATED", {"reason": "APPROVAL_GUARD_FAILED"}
            )
            session.commit()
            return {"route": "done"}
        proposal = session.scalar(
            select(PurchaseProposal).where(
                PurchaseProposal.review_id == review.id,
                PurchaseProposal.proposal_version == decision.version,
            )
        )
        if not proposal or approval.proposal_id != proposal.id:
            review.status = "NEEDS_ATTENTION"
            record_event(session, review.id, "REVIEW_ESCALATED", {"reason": "PROPOSAL_GUARD_FAILED"})
            session.commit()
            return {"route": "done"}
        coordinator = ProviderCoordinator()
        action_call = coordinator.execute_action_tool_call(proposal.id)
        if (
            not action_call
            or getattr(action_call, "tool_name", None) != "execute_approved_proposal"
            or action_call.arguments != {"proposal_id": proposal.id}
        ):
            review.status = "AWAITING_EXECUTION_RETRY"
            record_event(
                session,
                review.id,
                "ACTION_FAILED",
                {"reason": "PROVIDER_EXECUTION_FAILED", "proposal_id": proposal.id if proposal else None},
            )
            session.commit()
            return {"route": "retry"}
        plan_lines = session.scalars(
            select(SourcingPlanLine).where(SourcingPlanLine.sourcing_plan_id == proposal.sourcing_plan_id)
        ).all()
        existing_keys = {
            line.idempotency_key
            for line in plan_lines
            if session.scalar(select(PurchaseOrder.id).where(PurchaseOrder.idempotency_key == line.idempotency_key))
        }
        proposal.status = "APPROVED"
        _ok, purchase_orders, _validations = tool_execute_approved_proposal(session, proposal.id, review.id)
        attempts = session.scalars(
            select(ActionAttempt).where(ActionAttempt.decision_id == decision.id).order_by(ActionAttempt.id)
        ).all()
        for purchase_order in purchase_orders:
            record_event(session, review.id, "ACTION_RESULT_OBSERVED", {
                "purchase_order_id": purchase_order.id, "external_id": purchase_order.external_id,
                "recovered_by_idempotency": purchase_order.idempotency_key in existing_keys,
            })
        review.status = "VALIDATING"
        session.commit()
        return {"action_attempt_id": attempts[0].id if attempts else None, "route": "validate"}


def _validate(state: ReviewState) -> dict:
    with SessionLocal() as session:
        review = session.get(PurchasingReview, state["review_id"])
        if not review:
            return {"route": "failed"}
        decision = session.scalar(
            select(Decision)
            .where(Decision.review_id == review.id)
            .order_by(Decision.version.desc())
            .limit(1)
        )
        recommendation = session.get(Recommendation, review.recommendation_id)
        snapshot = (
            session.get(EvidenceSnapshot, decision.evidence_snapshot_id) if decision else None
        )

        attempts = (
            session.scalars(
                select(ActionAttempt)
                .where(ActionAttempt.decision_id == decision.id)
                .order_by(ActionAttempt.id)
            ).all()
            if decision
            else []
        )
        if not attempts and state.get("action_attempt_id"):
            att = session.get(ActionAttempt, state["action_attempt_id"])
            if att:
                attempts = [att]

        all_mismatches = []
        overall_status = "PASSED"

        for attempt in attempts:
            po_id = (attempt.response_json or {}).get("purchase_order_id")
            po = session.get(PurchaseOrder, po_id) if po_id else None
            items = (
                session.scalars(
                    select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po_id)
                ).all()
                if po_id
                else []
            )
            item = items[0] if items else None
            line = (
                session.get(SourcingPlanLine, attempt.sourcing_plan_line_id)
                if attempt.sourcing_plan_line_id
                else None
            )

            expected_supplier = (
                line.supplier_id
                if line
                else (recommendation.preferred_supplier_id if recommendation else None)
            )
            expected_quantity = (
                line.quantity if line else (decision.proposed_quantity if decision else None)
            )
            expected_total = (
                line.total_cost_minor
                if line
                else (decision.calculation_json.get("total_cost_minor") if decision else None)
            )
            expected_unit_cost = (
                line.unit_cost_minor
                if line
                else (snapshot.payload_json.get("unit_cost_minor") if snapshot else None)
            )

            comparisons = [
                {
                    "field": "supplier_id",
                    "expected": expected_supplier,
                    "actual": po.supplier_id if po else None,
                },
                {
                    "field": "node_id",
                    "expected": recommendation.node_id if recommendation else None,
                    "actual": po.node_id if po else None,
                },
                {
                    "field": "product_id",
                    "expected": recommendation.product_id if recommendation else None,
                    "actual": item.product_id if item else None,
                },
                {"field": "item_count", "expected": 1, "actual": len(items)},
                {
                    "field": "quantity",
                    "expected": expected_quantity,
                    "actual": item.ordered_quantity if item else None,
                },
                {
                    "field": "currency",
                    "expected": decision.calculation_json.get("currency") if decision else None,
                    "actual": po.currency if po else None,
                },
                {
                    "field": "total_minor",
                    "expected": expected_total,
                    "actual": po.total_minor if po else None,
                },
                {
                    "field": "unit_cost_minor",
                    "expected": expected_unit_cost,
                    "actual": item.unit_cost_minor if item else None,
                },
                {"field": "status", "expected": "OPEN", "actual": po.status if po else None},
                {
                    "field": "idempotency_key",
                    "expected": attempt.idempotency_key if attempt else None,
                    "actual": po.idempotency_key if po else None,
                },
            ]
            mismatches = [x["field"] for x in comparisons if x["expected"] != x["actual"]]
            status = "PASSED" if not mismatches else "FAILED_UNSAFE"
            if status != "PASSED":
                overall_status = "FAILED_UNSAFE"
                all_mismatches.extend(mismatches)

            session.add(
                ValidationResult(
                    review_id=review.id,
                    action_attempt_id=attempt.id,
                    status=status,
                    comparisons_json=comparisons,
                    mismatch_codes_json=mismatches,
                )
            )

        record_event(
            session,
            review.id,
            "VALIDATION_COMPLETED",
            {"status": overall_status, "mismatches": all_mismatches},
        )
        session.commit()
        return {
            "validation_status": overall_status,
            "route": "finalize" if overall_status == "PASSED" else "failed",
        }


def _finalize(state: ReviewState) -> dict:
    with SessionLocal() as session:
        review = session.get(PurchasingReview, state["review_id"])
        if review:
            review.status = (
                "COMPLETED" if state.get("validation_status") == "PASSED" else "NEEDS_ATTENTION"
            )
            review.completed_at = datetime.now(UTC)
            record_event(
                session,
                review.id,
                "REVIEW_COMPLETED" if review.status == "COMPLETED" else "REVIEW_ESCALATED",
                {"status": review.status},
            )
            session.commit()
    return {"route": "done"}


def _route_prepare(state: ReviewState) -> str:
    return state.get("route", "failed")


def _route_prevalidate(state: ReviewState) -> str:
    return state.get("route", "failed")


def _route_validate(state: ReviewState) -> str:
    return state.get("route", "failed")


def _route_execute(state: ReviewState) -> str:
    return state.get("route", "failed")


def build_graph(checkpointer):
    builder = StateGraph(ReviewState)
    builder.add_node("prepare", _prepare)
    builder.add_node("approval", _approval)
    builder.add_node("prevalidate", _prevalidate)
    builder.add_node("execute", _execute)
    builder.add_node("validate", _validate)
    builder.add_node("finalize", _finalize)
    builder.add_edge(START, "prepare")
    builder.add_conditional_edges(
        "prepare", _route_prepare, {"approval": "approval", "done": END, "failed": END}
    )
    builder.add_edge("approval", "prevalidate")
    builder.add_conditional_edges(
        "prevalidate",
        _route_prevalidate,
        {"approval": "approval", "execute": "execute", "done": END, "failed": END},
    )
    builder.add_conditional_edges(
        "execute",
        _route_execute,
        {"validate": "validate", "recalculate": "prepare", "retry": END, "done": END, "failed": END},
    )
    builder.add_conditional_edges(
        "validate", _route_validate, {"finalize": "finalize", "failed": "finalize"}
    )
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


class ReviewWorkflow:
    def __init__(self, graph):
        self.graph = graph

    def start(self, review_id: str) -> None:
        logger.info("Workflow start called for review: %s", review_id)
        try:
            self.graph.invoke(
                {"review_id": review_id}, config={"configurable": {"thread_id": review_id}}
            )
            logger.info("Workflow invoke completed for review: %s", review_id)
        except Exception as exc:
            logger.exception("Workflow invoke failed for review %s: %s", review_id, exc)
            self._fail(review_id, exc)

    def resume(self, review_id: str, approval: dict) -> None:
        try:
            self.graph.invoke(
                Command(resume=approval), config={"configurable": {"thread_id": review_id}}
            )
        except Exception as exc:
            self._fail(review_id, exc)

    def retry_execution(self, review_id: str) -> None:
        try:
            with SessionLocal() as session:
                review = session.get(PurchasingReview, review_id)
                if not review or review.status != "AWAITING_EXECUTION_RETRY":
                    return
                decision = session.scalar(
                    select(Decision)
                    .where(Decision.review_id == review.id)
                    .order_by(Decision.version.desc())
                    .limit(1)
                )
                approval = session.scalar(
                    select(ApprovalRequest)
                    .where(ApprovalRequest.review_id == review.id)
                    .order_by(ApprovalRequest.proposal_version.desc())
                    .limit(1)
                )
                if not decision or not approval:
                    return

                recommendation = session.get(Recommendation, review.recommendation_id)
                session.commit()
                inputs, payload, _tool_results = _collect_tool_evidence(
                    review, recommendation, review.scenario_type or "recommendation-review"
                )
                snap = session.get(EvidenceSnapshot, decision.evidence_snapshot_id)
                refreshed = calculate_decision(inputs, payload.get("budget_currency"))
                changed = (
                    refreshed.decision.value != decision.decision_type
                    or refreshed.proposed_quantity != decision.proposed_quantity
                    or refreshed.total_cost_minor != decision.calculation_json.get("total_cost_minor")
                    or refreshed.currency != decision.calculation_json.get("currency")
                    or not payload["evidence_complete"]
                    or _evidence_fingerprint(payload)
                    != _evidence_fingerprint(snap.payload_json if snap else {})
                )
                if changed:
                    approval.status = "SUPERSEDED"
                    review.status = "AWAITING_APPROVAL"
                    record_event(
                        session,
                        review.id,
                        "PRE_EXECUTION_REVALIDATED",
                        {"changed": True, "reason": "FACTS_CHANGED_DURING_RETRY"},
                    )
                    session.commit()
                    return

                review.status = "EXECUTING"
                session.commit()

            state: ReviewState = {
                "review_id": review_id,
                "decision_id": decision.id,
                "approval": {"decision": "APPROVE", "proposal_version": decision.version},
            }
            exec_res = _execute(state)
            if exec_res.get("route") == "validate":
                state["action_attempt_id"] = exec_res["action_attempt_id"]
                val_res = _validate(state)
                state["validation_status"] = val_res["validation_status"]
                _finalize(state)
        except Exception as exc:
            self._fail(review_id, exc)

    @staticmethod
    def _fail(review_id: str, error: Exception) -> None:
        logger.exception("Review workflow failed", exc_info=error)
        with SessionLocal() as session:
            review = session.get(PurchasingReview, review_id)
            if review and review.status not in {
                "COMPLETED",
                "REJECTED_BY_BUYER",
                "NEEDS_ATTENTION",
                "AWAITING_EXECUTION_RETRY",
            }:
                review.status = "FAILED"
                review.completed_at = datetime.now(UTC)
                record_event(
                    session, review_id, "WORKFLOW_FAILED", {"error_type": type(error).__name__}
                )
                session.commit()
