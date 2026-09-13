from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import perf_counter
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select

from purchasing.application.audit import record_event
from purchasing.application.evidence import collect_inputs, store_snapshot
from purchasing.application.explanations import ExplanationProvider
from purchasing.domain.policy import DecisionType, calculate_decision
from purchasing.infrastructure.db import SessionLocal
from purchasing.infrastructure.models import (
    ActionAttempt,
    ApprovalRequest,
    Decision,
    EvidenceSnapshot,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasingReview,
    Recommendation,
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


def _collect_and_audit(session, review_id: str, recommendation: Recommendation):
    started = perf_counter()
    record_event(session, review_id, "TOOL_CALL_STARTED", {"tool": "collect_purchasing_evidence"})
    inputs, payload, product, supplier = collect_inputs(session, recommendation)
    evidence_json = json.dumps(payload["evidence"], sort_keys=True, separators=(",", ":"))
    record_event(
        session,
        review_id,
        "TOOL_CALL_COMPLETED",
        {
            "tool": "collect_purchasing_evidence",
            "status": "SUCCEEDED" if payload["evidence_complete"] else "PARTIAL",
            "fact_count": len(payload["evidence"]),
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "output_hash": sha256(evidence_json.encode()).hexdigest(),
        },
    )
    return inputs, payload, product, supplier


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
        inputs, payload, _product, _supplier = _collect_and_audit(
            session, review.id, recommendation
        )
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
        review.status = "AWAITING_APPROVAL"
        session.add(
            ApprovalRequest(
                review_id=review.id, decision_id=saved.id, proposal_version=proposal_version
            )
        )
        record_event(
            session,
            review.id,
            "APPROVAL_REQUESTED",
            {"proposal_version": proposal_version, "quantity": decision.proposed_quantity},
        )
        session.commit()
        return {"decision_id": saved.id, "route": "approval"}


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
        inputs, payload, _product, _supplier = _collect_and_audit(
            session, review.id, recommendation
        )
        current = session.get(Decision, state["decision_id"])
        assert current is not None
        snap = session.get(EvidenceSnapshot, current.evidence_snapshot_id)
        refreshed = calculate_decision(inputs, payload.get("budget_currency"))
        changed = (
            refreshed.decision.value != current.decision_type
            or refreshed.proposed_quantity != current.proposed_quantity
            or refreshed.total_cost_minor != current.calculation_json.get("total_cost_minor")
            or refreshed.currency != current.calculation_json.get("currency")
            or not payload["evidence_complete"]
            or _evidence_fingerprint(payload)
            != _evidence_fingerprint(snap.payload_json if snap else {})
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
            review.status = "AWAITING_APPROVAL"
            session.add(
                ApprovalRequest(
                    review_id=review.id, decision_id=updated.id, proposal_version=updated.version
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
                },
            )
            session.commit()
            return {"decision_id": updated.id, "route": "approval"}
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
        snapshot = session.get(EvidenceSnapshot, decision.evidence_snapshot_id)
        recommendation = session.get(Recommendation, review.recommendation_id)
        terms = session.get(
            SupplierProduct, (recommendation.preferred_supplier_id, recommendation.product_id)
        )
        if not snapshot or not terms:
            review.status = "NEEDS_ATTENTION"
            session.commit()
            return {"route": "done"}
        inputs, payload, _product, _supplier = _collect_and_audit(
            session, review.id, recommendation
        )
        refreshed = calculate_decision(inputs, payload.get("budget_currency"))
        changed = (
            not payload["evidence_complete"]
            or refreshed.decision.value != decision.decision_type
            or refreshed.proposed_quantity != decision.proposed_quantity
            or refreshed.total_cost_minor != decision.calculation_json.get("total_cost_minor")
            or refreshed.currency != decision.calculation_json.get("currency")
            or _evidence_fingerprint(payload) != _evidence_fingerprint(snapshot.payload_json)
        )
        if changed:
            approval.status = "SUPERSEDED"
            review.status = "EVALUATING"
            record_event(
                session,
                review.id,
                "PRE_EXECUTION_REVALIDATED",
                {"changed": True, "before_write": True},
            )
            session.commit()
            return {"route": "recalculate"}
        key = f"{review.id}:create-po:v{decision.version}"
        existing = session.scalar(select(PurchaseOrder).where(PurchaseOrder.idempotency_key == key))
        recovered_existing = existing is not None
        if existing:
            po = existing
        else:
            now = datetime.now(UTC)
            po = PurchaseOrder(
                external_id=f"PO-{review.id[:8].upper()}-{decision.version}",
                supplier_id=recommendation.preferred_supplier_id,
                node_id=recommendation.node_id,
                status="OPEN",
                currency=terms.currency,
                total_minor=decision.proposed_quantity * terms.unit_cost_minor,
                expected_delivery_at=now + timedelta(days=terms.lead_time_days),
                idempotency_key=key,
                created_at=now,
                updated_at=now,
                recovery_attempts=review.recovery_attempts,
            )
            session.add(po)
            session.flush()
            session.add(
                PurchaseOrderItem(
                    purchase_order_id=po.id,
                    product_id=recommendation.product_id,
                    ordered_quantity=decision.proposed_quantity,
                    confirmed_quantity=0,
                    received_quantity=0,
                    unit_cost_minor=terms.unit_cost_minor,
                )
            )
        attempt = session.scalar(select(ActionAttempt).where(ActionAttempt.idempotency_key == key))
        if not attempt:
            attempt = ActionAttempt(
                review_id=review.id,
                decision_id=decision.id,
                attempt_number=1,
                idempotency_key=key,
                request_json={
                    "quantity": decision.proposed_quantity,
                    "supplier_id": recommendation.preferred_supplier_id,
                    "snapshot_hash": snapshot.snapshot_hash,
                },
                response_json={"purchase_order_id": po.id, "external_id": po.external_id},
                status="SUCCEEDED",
                completed_at=datetime.now(UTC),
            )
            session.add(attempt)
            session.flush()
        review.status = "VALIDATING"
        record_event(
            session,
            review.id,
            "ACTION_ATTEMPTED",
            {
                "decision_id": decision.id,
                "proposal_version": decision.version,
                "quantity": decision.proposed_quantity,
                "idempotency_key": key,
                "recovered_existing": recovered_existing,
            },
        )
        record_event(
            session,
            review.id,
            "ACTION_RESULT_OBSERVED",
            {
                "purchase_order_id": po.id,
                "external_id": po.external_id,
                "quantity": decision.proposed_quantity,
                "recovered_by_idempotency": recovered_existing,
            },
        )
        session.commit()
        return {"action_attempt_id": attempt.id, "route": "validate"}


def _validate(state: ReviewState) -> dict:
    with SessionLocal() as session:
        attempt = session.get(ActionAttempt, state["action_attempt_id"])
        review = session.get(PurchasingReview, state["review_id"])
        decision = session.get(Decision, attempt.decision_id) if attempt else None
        po_id = (
            attempt.response_json.get("purchase_order_id")
            if attempt and attempt.response_json
            else None
        )
        po = session.get(PurchaseOrder, po_id) if po_id else None
        items = (
            session.scalars(
                select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po_id)
            ).all()
            if po_id
            else []
        )
        item = items[0] if items else None
        recommendation = session.get(Recommendation, review.recommendation_id) if review else None
        snapshot = (
            session.get(EvidenceSnapshot, decision.evidence_snapshot_id) if decision else None
        )
        comparisons = [
            {
                "field": "supplier_id",
                "expected": recommendation.preferred_supplier_id if recommendation else None,
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
                "expected": decision.proposed_quantity if decision else None,
                "actual": item.ordered_quantity if item else None,
            },
            {
                "field": "currency",
                "expected": decision.calculation_json.get("currency") if decision else None,
                "actual": po.currency if po else None,
            },
            {
                "field": "total_minor",
                "expected": decision.calculation_json.get("total_cost_minor") if decision else None,
                "actual": po.total_minor if po else None,
            },
            {
                "field": "unit_cost_minor",
                "expected": snapshot.payload_json.get("unit_cost_minor") if snapshot else None,
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
            session, review.id, "VALIDATION_COMPLETED", {"status": status, "mismatches": mismatches}
        )
        session.commit()
        return {
            "validation_status": status,
            "route": "finalize" if status == "PASSED" else "failed",
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
        {"validate": "validate", "recalculate": "prepare", "done": END, "failed": END},
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
        try:
            self.graph.invoke(
                {"review_id": review_id}, config={"configurable": {"thread_id": review_id}}
            )
        except Exception as exc:
            self._fail(review_id, exc)

    def resume(self, review_id: str, approval: dict) -> None:
        try:
            self.graph.invoke(
                Command(resume=approval), config={"configurable": {"thread_id": review_id}}
            )
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
            }:
                review.status = "FAILED"
                review.completed_at = datetime.now(UTC)
                record_event(
                    session, review_id, "WORKFLOW_FAILED", {"error_type": type(error).__name__}
                )
                session.commit()
