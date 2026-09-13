import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from purchasing.api.schemas import (
    ApprovalInput,
    CustomScenarioInput,
    DemandSignalInput,
    ExecutionRetryInput,
    ReviewCreate,
    ScenarioAdvanceInput,
    SupplierConfirmationInput,
)
from purchasing.application.audit import record_event
from purchasing.application.demand import evaluate_demand_signal
from purchasing.application.demo_scenarios import advance_scenario, reset_scenario
from purchasing.application.evidence import collect_inputs
from purchasing.domain.policy import DecisionType, calculate_decision
from purchasing.infrastructure.db import get_session
from purchasing.infrastructure.models import (
    ActionAttempt,
    AgentToolCall,
    ApprovalRequest,
    AuditEvent,
    Budget,
    Decision,
    DemandSignal,
    EvidenceSnapshot,
    Forecast,
    Inventory,
    InvestigationTrace,
    Node,
    NodeProductPolicy,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseProposal,
    PurchasingReview,
    Recommendation,
    SalesObservation,
    SourcingOptionAssessment,
    SourcingPlan,
    SourcingPlanLine,
    StorageCapacity,
    Supplier,
    SupplierConfirmation,
    SupplierProduct,
    ValidationResult,
)
from purchasing.settings import settings

router = APIRouter(prefix="/api/v1")


def current_role(x_demo_role: str | None = Header(default=None)) -> tuple[str, str]:
    if settings.app_env.lower() not in {"development", "test"} or not settings.demo_auth_enabled:
        raise HTTPException(
            503, "The local demo identity is only available in development and test environments."
        )
    role = (x_demo_role or "BUYER").upper()
    if role not in {"VIEWER", "BUYER", "SYSTEM", "DEMO_ADMIN"}:
        raise HTTPException(403, "Unknown demo role.")
    return role, "demo-user"


def require(role: tuple[str, str], allowed: set[str]) -> tuple[str, str]:
    if role[0] not in allowed:
        raise HTTPException(403, "This action is not allowed for your role.")
    return role


def require_local_demo() -> None:
    if settings.app_env.lower() not in {"development", "test"}:
        raise HTTPException(404, "Scenario Lab is only available in the local demo.")


@router.get("/recommendations")
def list_recommendations(
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"VIEWER", "BUYER", "DEMO_ADMIN"})
    rows = session.scalars(
        select(Recommendation).order_by(Recommendation.created_at.desc()).limit(limit)
    ).all()
    reviews = session.scalars(
        select(PurchasingReview).order_by(PurchasingReview.created_at.desc())
    ).all()
    by_recommendation = {}
    for review in reviews:
        by_recommendation.setdefault(review.recommendation_id, review)
    result = []
    for item in rows:
        review = by_recommendation.get(item.id)
        if status and (not review or review.status != status):
            continue
        product = session.get(Product, item.product_id)
        node = session.get(Node, item.node_id)
        supplier = session.get(Supplier, item.preferred_supplier_id)
        result.append(
            {
                "id": item.id,
                "source_reference": item.source_reference,
                "product": {"id": product.id, "sku": product.sku, "name": product.name},
                "node": {"id": node.id, "code": node.code, "name": node.name},
                "supplier": {"id": supplier.id, "name": supplier.name},
                "recommended_quantity": item.recommended_quantity,
                "review_id": review.id if review else None,
                "review_status": review.status if review else None,
            }
        )
    return {"items": result, "next_cursor": None}


@router.post("/demo/scenarios/{scenario}/reset", status_code=202)
def reset_demo_scenario(
    scenario: str,
    background: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"BUYER", "DEMO_ADMIN"})
    require_local_demo()
    try:
        review, details = reset_scenario(session, scenario)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    session.commit()
    background.add_task(request.app.state.workflow.start, review.id)
    return {
        "review_id": review.id,
        "scenario": scenario,
        "title": details.title,
        "purpose": details.purpose,
        "expected_outcome": details.expected_outcome,
        "next_step": details.next_step,
        "requires_advance": details.requires_advance,
    }


@router.post("/demo/scenarios/{scenario}/advance")
def advance_demo_scenario(
    scenario: str,
    body: ScenarioAdvanceInput,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"BUYER", "DEMO_ADMIN"})
    require_local_demo()
    try:
        details = advance_scenario(session, scenario, body.review_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    return {"scenario": scenario, "review_id": body.review_id, "next_step": details.next_step}


@router.post("/reviews", status_code=202)
def create_review(
    body: ReviewCreate,
    background: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
    role=Depends(current_role),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    require(role, {"BUYER", "DEMO_ADMIN"})
    recommendation = session.get(Recommendation, body.recommendation_id)
    if not recommendation:
        raise HTTPException(404, "Recommendation not found.")
    if idempotency_key:
        same_key = session.scalar(
            select(PurchasingReview).where(PurchasingReview.idempotency_key == idempotency_key)
        )
        if same_key:
            if same_key.recommendation_id != recommendation.id:
                raise HTTPException(
                    409,
                    detail={
                        "code": "IDEMPOTENCY_KEY_REUSED",
                        "detail": "Use a new key for a different recommendation.",
                    },
                )
            return {
                "review_id": same_key.id,
                "status": same_key.status,
                "links": {"self": f"/api/v1/reviews/{same_key.id}"},
            }
    existing = session.scalar(
        select(PurchasingReview)
        .where(PurchasingReview.recommendation_id == recommendation.id)
        .order_by(PurchasingReview.created_at.desc())
    )
    if existing and existing.status not in {
        "COMPLETED",
        "FAILED",
        "REJECTED_BY_BUYER",
        "NEEDS_ATTENTION",
    }:
        if idempotency_key:
            if existing.idempotency_key and existing.idempotency_key != idempotency_key:
                raise HTTPException(
                    409,
                    detail={
                        "code": "ACTIVE_REVIEW_EXISTS",
                        "detail": "An active review already exists for this recommendation.",
                    },
                )
            if not existing.idempotency_key:
                existing.idempotency_key = idempotency_key
                session.commit()
        return {
            "review_id": existing.id,
            "status": existing.status,
            "links": {"self": f"/api/v1/reviews/{existing.id}"},
        }
    review = PurchasingReview(
        recommendation_id=recommendation.id, status="CREATED", idempotency_key=idempotency_key
    )
    session.add(review)
    session.flush()
    record_event(
        session,
        review.id,
        "REVIEW_CREATED",
        {"recommendation_id": recommendation.id, "idempotency_key": idempotency_key},
        "BUYER",
        role[1],
    )
    session.commit()
    background.add_task(request.app.state.workflow.start, review.id)
    return {
        "review_id": review.id,
        "status": review.status,
        "links": {"self": f"/api/v1/reviews/{review.id}"},
    }


@router.get("/reviews/{review_id}")
def get_review(review_id: str, session: Session = Depends(get_session), role=Depends(current_role)):
    require(role, {"VIEWER", "BUYER", "DEMO_ADMIN"})
    review = session.get(PurchasingReview, review_id)
    if not review:
        raise HTTPException(404, "Review not found.")
    recommendation = session.get(Recommendation, review.recommendation_id)
    product = session.get(Product, recommendation.product_id)
    node = session.get(Node, recommendation.node_id)
    supplier = session.get(Supplier, recommendation.preferred_supplier_id)
    snapshot = session.scalar(
        select(EvidenceSnapshot)
        .where(EvidenceSnapshot.review_id == review.id)
        .order_by(EvidenceSnapshot.sequence.desc())
        .limit(1)
    )
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
    attempt = session.scalar(
        select(ActionAttempt)
        .where(ActionAttempt.review_id == review.id)
        .order_by(ActionAttempt.attempt_number.desc())
        .limit(1)
    )
    validation = session.scalar(
        select(ValidationResult)
        .where(ValidationResult.review_id == review.id)
        .order_by(ValidationResult.observed_at.desc())
        .limit(1)
    )
    po = (
        session.get(PurchaseOrder, (attempt.response_json or {}).get("purchase_order_id"))
        if attempt
        else None
    )
    proposal = session.scalar(
        select(PurchaseProposal)
        .where(PurchaseProposal.review_id == review.id)
        .order_by(PurchaseProposal.proposal_version.desc())
        .limit(1)
    )
    sourcing_plan = session.scalar(
        select(SourcingPlan)
        .where(SourcingPlan.review_id == review.id)
        .order_by(SourcingPlan.version.desc())
        .limit(1)
    )
    trace = session.scalar(
        select(InvestigationTrace)
        .where(InvestigationTrace.review_id == review.id)
        .order_by(InvestigationTrace.started_at.desc())
        .limit(1)
    )
    all_attempts = session.scalars(
        select(ActionAttempt)
        .where(ActionAttempt.review_id == review.id)
        .order_by(ActionAttempt.attempt_number.asc())
    ).all()
    tool_calls = (
        session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.investigation_trace_id == trace.id)
            .order_by(AgentToolCall.sequence.asc())
        ).all()
        if trace
        else []
    )
    lines = (
        session.scalars(
            select(SourcingPlanLine)
            .where(SourcingPlanLine.sourcing_plan_id == sourcing_plan.id)
            .order_by(SourcingPlanLine.sequence.asc())
        ).all()
        if sourcing_plan
        else []
    )
    assessments = (
        session.scalars(
            select(SourcingOptionAssessment)
            .where(SourcingOptionAssessment.sourcing_plan_id == sourcing_plan.id)
        ).all()
        if sourcing_plan
        else []
    )
    po_by_key = {
        po_row.idempotency_key: po_row.id
        for po_row in session.scalars(
            select(PurchaseOrder).where(PurchaseOrder.idempotency_key.like(f"{review.id}:%"))
        ).all()
    }
    return {
        "id": review.id,
        "status": review.status,
        "recovery_attempts": review.recovery_attempts,
        "recommendation": {
            "id": recommendation.id,
            "source_reference": recommendation.source_reference,
            "product": {"id": product.id, "sku": product.sku, "name": product.name},
            "node": {"id": node.id, "code": node.code, "name": node.name},
            "supplier": {"id": supplier.id, "name": supplier.name},
            "recommended_quantity": recommendation.recommended_quantity,
        },
        "investigation": {
            "status": trace.status if trace else "PENDING",
            "rounds_used": trace.rounds_used if trace else 0,
            "mandatory_evidence_complete": bool(trace.completed_at or trace.status == "COMPLETED") if trace else False,
            "tool_calls": [
                {
                    "tool_name": tc.tool_name,
                    "round_number": tc.round_number,
                    "call_index": tc.sequence,
                    "status": tc.status,
                    "arguments": tc.arguments_json,
                    "result_ref": tc.result_ref,
                    "error_code": tc.error_code,
                    "latency_ms": tc.duration_ms,
                }
                for tc in tool_calls
            ],
        },
        "evidence": {
            "items": (snapshot.payload_json.get("evidence", []) if snapshot else []),
            "completeness": snapshot.completeness_status if snapshot else "PENDING",
            "snapshot_hash": snapshot.snapshot_hash if snapshot else None,
            "errors": snapshot.payload_json.get("errors", []) if snapshot else [],
        },
        "decision": (
            {
                "version": decision.version,
                "type": decision.decision_type,
                "original_quantity": decision.original_quantity,
                "proposed_quantity": decision.proposed_quantity,
                "confidence": decision.confidence_label,
                "reason_codes": decision.reason_codes_json,
                "calculations": decision.calculation_json,
                "constraints": decision.calculation_json.get("constraints", []),
                "explanation": decision.explanation_json,
            }
            if decision
            else None
        ),
        "sourcing_plan": (
            {
                "id": sourcing_plan.id,
                "version": sourcing_plan.version,
                "raw_need": sourcing_plan.raw_need,
                "total_quantity": sourcing_plan.total_quantity,
                "total_cost_minor": sourcing_plan.total_cost_minor,
                "currency": sourcing_plan.currency,
                "need_by_at": sourcing_plan.need_by_at.isoformat() if sourcing_plan.need_by_at else None,
                "lines": [
                    {
                        "id": line.id,
                        "line_number": line.sequence,
                        "supplier_id": line.supplier_id,
                        "allocated_quantity": line.quantity,
                        "unit_cost_minor": line.unit_cost_minor,
                        "total_cost_minor": line.total_cost_minor,
                        "currency": sourcing_plan.currency,
                        "expected_delivery_at": line.expected_delivery_at.isoformat() if line.expected_delivery_at else None,
                        "idempotency_key": line.idempotency_key,
                        "purchase_order_id": po_by_key.get(line.idempotency_key),
                    }
                    for line in lines
                ],
                "supplier_assessments": [
                    {
                        "supplier_id": a.supplier_id,
                        "eligible": a.eligible,
                        "reason_codes": a.exclusion_reason_codes_json,
                        "terms": a.normalized_terms_json,
                    }
                    for a in assessments
                ],
            }
            if sourcing_plan
            else None
        ),
        "approval": (
            {
                "status": approval.status,
                "proposal_id": proposal.id if proposal else None,
                "proposal_version": approval.proposal_version,
                "requested_at": approval.requested_at.isoformat(),
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
                "comment": approval.comment,
            }
            if approval
            else None
        ),
        "action": (
            {
                "status": attempt.status,
                "purchase_order_id": po.id if po else None,
                "external_id": po.external_id if po else None,
                "total_minor": po.total_minor if po else None,
                "currency": po.currency if po else None,
            }
            if attempt
            else None
        ),
        "actions": [
            {
                "attempt_number": a.attempt_number,
                "action_type": a.action_type,
                "status": a.status,
                "purchase_order_id": (a.response_json or {}).get("purchase_order_id"),
                "external_id": (a.response_json or {}).get("external_id"),
                "total_minor": (a.response_json or {}).get("total_minor"),
                "currency": (a.response_json or {}).get("currency"),
                "idempotency_key": a.idempotency_key,
            }
            for a in all_attempts
        ],
        "validation": (
            {
                "status": validation.status,
                "comparisons": validation.comparisons_json,
                "mismatches": validation.mismatch_codes_json,
            }
            if validation
            else None
        ),
        "links": {"self": f"/api/v1/reviews/{review.id}"},
    }


@router.get("/reviews/{review_id}/sourcing-plan")
def get_review_sourcing_plan(
    review_id: str,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"VIEWER", "BUYER", "DEMO_ADMIN"})
    review = session.get(PurchasingReview, review_id)
    if not review:
        raise HTTPException(404, "Review not found.")
    sourcing_plan = session.scalar(
        select(SourcingPlan)
        .where(SourcingPlan.review_id == review.id)
        .order_by(SourcingPlan.version.desc())
        .limit(1)
    )
    if not sourcing_plan:
        return {"review_id": review_id, "sourcing_plan": None}
    lines = session.scalars(
        select(SourcingPlanLine)
        .where(SourcingPlanLine.sourcing_plan_id == sourcing_plan.id)
        .order_by(SourcingPlanLine.sequence.asc())
    ).all()
    assessments = session.scalars(
        select(SourcingOptionAssessment)
        .where(SourcingOptionAssessment.sourcing_plan_id == sourcing_plan.id)
    ).all()
    po_by_key = {
        po_row.idempotency_key: po_row.id
        for po_row in session.scalars(
            select(PurchaseOrder).where(PurchaseOrder.idempotency_key.like(f"{review.id}:%"))
        ).all()
    }
    return {
        "review_id": review_id,
        "sourcing_plan": {
            "id": sourcing_plan.id,
            "version": sourcing_plan.version,
            "raw_need": sourcing_plan.raw_need,
            "total_quantity": sourcing_plan.total_quantity,
            "total_cost_minor": sourcing_plan.total_cost_minor,
            "currency": sourcing_plan.currency,
            "need_by_at": sourcing_plan.need_by_at.isoformat() if sourcing_plan.need_by_at else None,
            "lines": [
                {
                    "id": line.id,
                    "line_number": line.sequence,
                    "supplier_id": line.supplier_id,
                    "allocated_quantity": line.quantity,
                    "unit_cost_minor": line.unit_cost_minor,
                    "total_cost_minor": line.total_cost_minor,
                    "currency": sourcing_plan.currency,
                    "expected_delivery_at": line.expected_delivery_at.isoformat() if line.expected_delivery_at else None,
                    "idempotency_key": line.idempotency_key,
                    "purchase_order_id": po_by_key.get(line.idempotency_key),
                }
                for line in lines
            ],
            "supplier_assessments": [
                {
                    "supplier_id": a.supplier_id,
                    "eligible": a.eligible,
                    "reason_codes": a.exclusion_reason_codes_json,
                    "terms": a.normalized_terms_json,
                }
                for a in assessments
            ],
        },
    }


@router.get("/reviews/{review_id}/tool-trace")
def get_review_tool_trace(
    review_id: str,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"VIEWER", "BUYER", "DEMO_ADMIN"})
    review = session.get(PurchasingReview, review_id)
    if not review:
        raise HTTPException(404, "Review not found.")
    trace = session.scalar(
        select(InvestigationTrace)
        .where(InvestigationTrace.review_id == review.id)
        .order_by(InvestigationTrace.started_at.desc())
        .limit(1)
    )
    tool_calls = (
        session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.investigation_trace_id == trace.id)
            .order_by(AgentToolCall.sequence.asc())
        ).all()
        if trace
        else []
    )
    return {
        "review_id": review_id,
        "investigation": {
            "status": trace.status if trace else "PENDING",
            "rounds_used": trace.rounds_used if trace else 0,
            "mandatory_evidence_complete": bool(trace.completed_at or trace.status == "COMPLETED") if trace else False,
            "tool_calls": [
                {
                    "tool_name": tc.tool_name,
                    "round_number": tc.round_number,
                    "call_index": tc.sequence,
                    "status": tc.status,
                    "arguments": tc.arguments_json,
                    "result_ref": tc.result_ref,
                    "error_code": tc.error_code,
                    "latency_ms": tc.duration_ms,
                }
                for tc in tool_calls
            ],
        },
    }


@router.get("/reviews/{review_id}/events")
def get_events(
    review_id: str,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=250),
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"VIEWER", "BUYER", "DEMO_ADMIN"})
    if not session.get(PurchasingReview, review_id):
        raise HTTPException(404, "Review not found.")
    rows = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.review_id == review_id, AuditEvent.id > after_id)
        .order_by(AuditEvent.id)
        .limit(limit + 1)
    ).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "actor_type": e.actor_type,
                "created_at": e.created_at.isoformat(),
                "payload": e.payload_json,
            }
            for e in rows
        ],
        "next_cursor": rows[-1].id if more and rows else None,
    }


@router.post("/reviews/{review_id}/approval", status_code=202)
def decide_approval(
    review_id: str,
    body: ApprovalInput,
    background: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"BUYER", "DEMO_ADMIN"})
    review = session.get(PurchasingReview, review_id)
    approval_request = session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.review_id == review_id,
            ApprovalRequest.proposal_version == body.proposal_version,
        )
    )
    if not review or not approval_request:
        raise HTTPException(404, "Pending proposal not found.")
    if approval_request.status in {"APPROVED", "REJECTED"}:
        recorded_decision = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
        same_response = (
            approval_request.status == recorded_decision
            and approval_request.comment == (body.comment.strip() or None)
        )
        if same_response:
            return {"review_id": review_id, "status": review.status}
        raise HTTPException(
            409,
            detail={
                "code": "APPROVAL_ALREADY_RECORDED",
                "detail": "This proposal already has a different buyer response.",
            },
        )
    if (
        review.status != "AWAITING_APPROVAL"
        or approval_request.status != "PENDING"
        or review.current_proposal_version != body.proposal_version
    ):
        raise HTTPException(
            409,
            detail={
                "code": "STALE_PROPOSAL",
                "detail": "Reload the review and decide on its current proposal.",
            },
        )
    if body.decision == "REJECT" and not body.comment.strip():
        raise HTTPException(422, "A rejection comment is required.")
    approval_request.status = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
    approval_request.decided_at = datetime.now(UTC)
    approval_request.decided_by = role[1]
    approval_request.comment = body.comment.strip() or None
    record_event(
        session,
        review.id,
        "APPROVAL_RECORDED",
        {
            "decision": body.decision,
            "proposal_version": body.proposal_version,
            "comment": approval_request.comment,
        },
        "BUYER",
        role[1],
    )
    session.commit()
    approval = {
        "decision": body.decision,
        "proposal_version": body.proposal_version,
        "actor_id": role[1],
    }
    background.add_task(request.app.state.workflow.resume, review_id, approval)
    return {
        "review_id": review_id,
        "status": "EXECUTING" if body.decision == "APPROVE" else "REJECTED_BY_BUYER",
    }


@router.post("/mock/supplier-confirmations", status_code=202)
def supplier_confirmation(
    body: SupplierConfirmationInput,
    background: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"DEMO_ADMIN"})
    if settings.app_env.lower() not in {"development", "test"}:
        raise HTTPException(404, "The mock supplier adapter is disabled outside local demo mode.")
    previous = session.scalar(
        select(SupplierConfirmation).where(
            SupplierConfirmation.external_event_id == body.external_event_id
        )
    )
    if previous:
        if (
            previous.purchase_order_id != body.purchase_order_id
            or previous.product_id != body.product_id
            or previous.confirmed_quantity != body.confirmed_quantity
        ):
            raise HTTPException(409, "Supplier event ID was reused with different event data.")
        return {"status": "DUPLICATE_IGNORED", "review_id": None}
    po = session.get(PurchaseOrder, body.purchase_order_id)
    if not po:
        raise HTTPException(404, "Purchase order not found.")
    if po.status != "OPEN":
        raise HTTPException(409, "Supplier confirmation requires an open purchase order.")
    item = session.scalar(
        select(PurchaseOrderItem).where(
            PurchaseOrderItem.purchase_order_id == po.id,
            PurchaseOrderItem.product_id == body.product_id,
        )
    )
    if not item or body.confirmed_quantity > item.ordered_quantity:
        raise HTTPException(422, "Confirmation does not match the purchase order item.")
    if body.confirmed_quantity < item.confirmed_quantity:
        raise HTTPException(
            409, "A supplier confirmation cannot reduce previously confirmed quantity."
        )
    if body.confirmed_quantity == item.confirmed_quantity:
        session.add(
            SupplierConfirmation(
                external_event_id=body.external_event_id,
                purchase_order_id=po.id,
                product_id=body.product_id,
                confirmed_quantity=body.confirmed_quantity,
                event_at=body.event_at,
            )
        )
        session.commit()
        return {"status": "UNCHANGED_IGNORED", "review_id": None}
    item.confirmed_quantity = body.confirmed_quantity
    po.version += 1
    po.updated_at = datetime.now(UTC)
    confirmation = SupplierConfirmation(
        external_event_id=body.external_event_id,
        purchase_order_id=po.id,
        product_id=body.product_id,
        confirmed_quantity=body.confirmed_quantity,
        event_at=body.event_at,
    )
    session.add(confirmation)
    product = session.get(Product, body.product_id)
    if not product:
        raise HTTPException(422, "No purchasing context exists for the confirmed item.")
    if body.confirmed_quantity == item.ordered_quantity:
        session.commit()
        return {"status": "ACCEPTED", "review_id": None}
    terms = session.scalars(
        select(SupplierProduct)
        .join(Supplier)
        .where(
            SupplierProduct.product_id == body.product_id,
            SupplierProduct.supplier_id != po.supplier_id,
            Supplier.active.is_(True),
        )
    ).first()
    supplier_id = terms.supplier_id if terms else po.supplier_id
    followup = Recommendation(
        source_reference=f"PARTIAL-{body.external_event_id}",
        source="supplier-confirmation",
        product_id=body.product_id,
        node_id=po.node_id,
        preferred_supplier_id=supplier_id,
        recommended_quantity=max(0, item.ordered_quantity - body.confirmed_quantity),
        created_at=datetime.now(UTC),
    )
    session.add(followup)
    session.flush()
    no_alternate_supplier = terms is None
    recovery_limit_reached = po.recovery_attempts >= settings.max_recovery_attempts
    coverage_sufficient = False
    if no_alternate_supplier or recovery_limit_reached:
        recovery_inputs, _payload, _product, _supplier = collect_inputs(session, followup)
        recomputed = calculate_decision(recovery_inputs, _payload.get("budget_currency"))
        coverage_sufficient = recomputed.decision == DecisionType.REJECT
    exhausted = (no_alternate_supplier or recovery_limit_reached) and not coverage_sufficient
    if not exhausted:
        po.recovery_attempts += 1
    review = PurchasingReview(
        recommendation_id=followup.id,
        status=(
            "NEEDS_ATTENTION" if exhausted else ("COMPLETED" if coverage_sufficient else "CREATED")
        ),
        recovery_attempts=po.recovery_attempts,
        completed_at=datetime.now(UTC) if exhausted or coverage_sufficient else None,
    )
    session.add(review)
    session.flush()
    record_event(
        session,
        review.id,
        "REVIEW_CREATED",
        {
            "trigger": "SUPPLIER_PARTIAL_FULFILMENT",
            "purchase_order_id": po.id,
            "shortfall": item.ordered_quantity - body.confirmed_quantity,
        },
        "SYSTEM",
        "supplier-confirmation",
    )
    record_event(
        session,
        review.id,
        "SUPPLIER_CONFIRMATION_RECEIVED",
        {
            "event_id": body.external_event_id,
            "purchase_order_id": po.id,
            "confirmed_quantity": body.confirmed_quantity,
        },
    )
    if coverage_sufficient:
        record_event(
            session,
            review.id,
            "PARTIAL_FULFILMENT_ACCEPTED",
            {"reason": "EXISTING_COVERAGE_SUFFICIENT"},
        )
    elif exhausted:
        record_event(
            session,
            review.id,
            "REVIEW_ESCALATED",
            {
                "reason": "NO_ALTERNATE_SUPPLIER"
                if no_alternate_supplier
                else "RECOVERY_LIMIT_REACHED",
                "attempts": po.recovery_attempts,
            },
        )
    else:
        record_event(
            session,
            review.id,
            "RECOVERY_STARTED",
            {"attempt": po.recovery_attempts, "limit": settings.max_recovery_attempts},
        )
    session.commit()
    if not exhausted and not coverage_sufficient:
        background.add_task(request.app.state.workflow.start, review.id)
    return {
        "status": "NEEDS_ATTENTION" if exhausted else "ACCEPTED",
        "review_id": review.id,
    }


@router.get("/demo/partial-fulfilment")
def partial_fulfilment_fixture(session: Session = Depends(get_session), role=Depends(current_role)):
    require(role, {"VIEWER", "BUYER", "DEMO_ADMIN"})
    po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.external_id == "PO-PARTIAL-500"))
    if not po:
        raise HTTPException(404, "Partial fulfilment fixture is unavailable.")
    item = session.scalar(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id)
    )
    return {
        "purchase_order_id": po.id,
        "external_id": po.external_id,
        "product_id": item.product_id,
        "ordered_quantity": item.ordered_quantity,
        "confirmed_quantity": item.confirmed_quantity,
        "status": po.status,
    }


@router.post("/reviews/{review_id}/execution-retry", status_code=202)
@router.post("/reviews/{review_id}/retry-execution", status_code=202)
def execution_retry(
    review_id: str,
    body: ExecutionRetryInput,
    background: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"BUYER", "SYSTEM", "DEMO_ADMIN"})
    review = session.get(PurchasingReview, review_id)
    if not review:
        raise HTTPException(404, "Review not found.")
    if review.status != "AWAITING_EXECUTION_RETRY":
        raise HTTPException(
            409,
            detail={
                "code": "INVALID_STATE",
                "detail": f"Review is in {review.status} state, not AWAITING_EXECUTION_RETRY.",
            },
        )
    if review.current_proposal_version != body.proposal_version:
        raise HTTPException(
            409,
            detail={
                "code": "STALE_PROPOSAL",
                "detail": "Proposal version does not match the active proposal.",
            },
        )
    record_event(
        session,
        review.id,
        "EXECUTION_RETRY_REQUESTED",
        {"proposal_version": body.proposal_version},
        actor_type=role[0],
        actor_id=role[1],
    )
    session.commit()
    background.add_task(request.app.state.workflow.retry_execution, review.id)
    return {"review_id": review.id, "status": "INVESTIGATING"}


@router.post("/mock/demand-signals", status_code=202)
@router.post("/demand-signals/trigger", status_code=202)
def mock_demand_signals(
    body: DemandSignalInput,
    background: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"DEMO_ADMIN"})
    if settings.app_env.lower() not in {"development", "test"}:
        raise HTTPException(404, "Demand signal injection is disabled outside local demo mode.")

    if (body.window_end - body.window_start).total_seconds() < 24 * 3600:
        raise HTTPException(422, "Observation interval must be at least 24 hours.")

    product = session.get(Product, body.product_id)
    node = session.get(Node, body.node_id)
    if not product or not node:
        raise HTTPException(422, "Product or node not found.")

    baseline = session.get(Forecast, body.baseline_forecast_id)
    revised = session.get(Forecast, body.revised_forecast_id)
    if not baseline or not revised:
        raise HTTPException(422, "Baseline or revised forecast not found.")
    if any(
        forecast.product_id != body.product_id or forecast.node_id != body.node_id
        for forecast in (baseline, revised)
    ) or baseline.id == revised.id or baseline.model_version == revised.model_version:
        raise HTTPException(422, "Forecasts must be distinct versions for the same product and node.")

    existing_signal = session.scalar(
        select(DemandSignal).where(DemandSignal.external_event_id == body.external_event_id)
    )
    if existing_signal:
        rec = session.scalar(
            select(Recommendation).where(Recommendation.source_reference == f"DEMAND-{body.external_event_id}")
        )
        rev = session.scalar(
            select(PurchasingReview).where(PurchasingReview.recommendation_id == rec.id)
        ) if rec else None
        return {"status": "DUPLICATE_IGNORED", "review_id": rev.id if rev else None}

    sales_obs = session.scalar(
        select(SalesObservation).where(
            SalesObservation.product_id == body.product_id,
            SalesObservation.node_id == body.node_id,
            SalesObservation.window_start == body.window_start,
            SalesObservation.window_end == body.window_end,
            SalesObservation.source_version == body.source_version,
        )
    )
    if not sales_obs:
        sales_obs = SalesObservation(
            product_id=body.product_id,
            node_id=body.node_id,
            window_start=body.window_start,
            window_end=body.window_end,
            units_sold=body.units_sold,
            source_version=body.source_version,
            observed_at=body.occurred_at,
        )
        session.add(sales_obs)
        session.flush()
    else:
        sales_obs.units_sold = body.units_sold
        sales_obs.observed_at = body.occurred_at
        session.flush()

    result, signal = evaluate_demand_signal(
        session, body.product_id, body.node_id, external_event_id=body.external_event_id,
        baseline_forecast_id=baseline.id, revised_forecast_id=revised.id,
        sales_observation_id=sales_obs.id,
    )
    if result.spike_detected or result.status == "INVESTIGATE":
        pref_supp = session.scalars(
            select(SupplierProduct)
            .join(Supplier)
            .where(SupplierProduct.product_id == body.product_id, Supplier.active.is_(True))
            .order_by(Supplier.reliability_score_bps.desc())
        ).first()
        supp_id = pref_supp.supplier_id if pref_supp else None
        rec = Recommendation(
            source_reference=f"DEMAND-{body.external_event_id}",
            source="demand-spike" if result.spike_detected else "demand-evidence-investigate",
            product_id=body.product_id,
            node_id=body.node_id,
            preferred_supplier_id=supp_id,
            recommended_quantity=result.supplemental_need if result.spike_detected else 0,
            created_at=datetime.now(UTC),
        )
        session.add(rec)
        session.flush()
        review = PurchasingReview(
            recommendation_id=rec.id,
            scenario_type="demand-change",
            status="CREATED",
            idempotency_key=f"demand-signal:{body.external_event_id}",
        )
        session.add(review)
        session.flush()
        record_event(
            session,
            review.id,
            "REVIEW_CREATED",
            {
                "trigger": "DEMAND_SPIKE" if result.spike_detected else "DEMAND_EVIDENCE_DEFICIENT",
                "ratio_bps": result.ratio_bps,
                "supplemental_need": result.supplemental_need,
            },
            "SYSTEM",
            "demand-signal",
        )
        session.commit()
        background.add_task(request.app.state.workflow.start, review.id)
        return {"status": "ACCEPTED" if result.spike_detected else "INVESTIGATE", "review_id": review.id}
    else:
        session.commit()
        return {"status": "NO_SPIKE", "review_id": None}


@router.post("/demo/custom-scenarios", status_code=201)
@router.post("/demo/custom-scenario", status_code=201)
def create_custom_scenario(
    body: CustomScenarioInput,
    background: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
    role=Depends(current_role),
):
    require(role, {"BUYER", "DEMO_ADMIN"})
    require_local_demo()

    if body.forecasts.window_end <= body.forecasts.window_start:
        raise HTTPException(422, "Forecast window_end must be after window_start.")

    codes = [s.code for s in body.suppliers]
    if len(codes) != len(set(codes)):
        raise HTTPException(422, "Supplier codes must be unique.")

    for po_item in body.open_purchase_orders:
        if po_item.supplier_code not in set(codes):
            raise HTTPException(
                422, f"Open PO supplier code '{po_item.supplier_code}' does not match any supplier in the request."
            )

    if body.mode == "DEMAND_CHANGE" and (
        body.forecasts.revised is None or body.recent_sales is None
    ):
        raise HTTPException(422, "Demand change mode requires revised forecast and recent sales evidence.")

    now = datetime.now(UTC)

    product = Product(sku=f"{body.product.sku}-{uuid.uuid4().hex[:4]}", name=body.product.name, unit_volume=body.product.unit_volume, active=True)
    node = Node(code=f"{body.node.code}-{uuid.uuid4().hex[:4]}", name=body.node.name, active=True)
    session.add(product)
    session.add(node)
    session.flush()

    inv = Inventory(
        product_id=product.id,
        node_id=node.id,
        on_hand=body.inventory.on_hand,
        reserved=body.inventory.reserved,
        damaged=body.inventory.damaged,
        version=1,
        observed_at=now,
    )
    pol = NodeProductPolicy(
        product_id=product.id,
        node_id=node.id,
        safety_stock=body.policy.safety_stock,
        review_period_days=body.policy.review_period_days,
        demand_spike_threshold_bps=body.policy.demand_spike_threshold_bps,
        minimum_sales_observation_hours=body.policy.minimum_sales_observation_hours,
        version=1,
        observed_at=now,
    )
    storage = StorageCapacity(
        node_id=node.id,
        available_volume=body.storage.available_volume,
        version=1,
        observed_at=now,
    )
    budget = Budget(
        node_id=node.id,
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=30),
        allocated_minor=body.budget.available_minor,
        committed_minor=0,
        currency=body.budget.currency,
        version=1,
        observed_at=now,
    )
    session.add_all([inv, pol, storage, budget])

    fc_base = Forecast(
        product_id=product.id,
        node_id=node.id,
        window_start=body.forecasts.window_start,
        window_end=body.forecasts.window_end,
        expected_demand=body.forecasts.baseline,
        model_version="custom-baseline",
        observed_at=now,
    )
    session.add(fc_base)
    if body.forecasts.revised is not None:
        fc_rev = Forecast(
            product_id=product.id,
            node_id=node.id,
            window_start=body.forecasts.window_start,
            window_end=body.forecasts.window_end,
            expected_demand=body.forecasts.revised,
            model_version="custom-revised",
            observed_at=now,
        )
        session.add(fc_rev)

    suppliers_map = {}
    for s in body.suppliers:
        supp = Supplier(
            code=f"{s.code}-{uuid.uuid4().hex[:4]}",
            name=s.name,
            reliability_score_bps=s.reliability_score_bps,
            active=True,
        )
        session.add(supp)
        session.flush()
        suppliers_map[s.code] = supp
        sp = SupplierProduct(
            supplier_id=supp.id,
            product_id=product.id,
            unit_cost_minor=s.unit_cost_minor,
            currency=s.currency,
            minimum_order_quantity=s.minimum_order_quantity,
            lead_time_days=s.lead_time_days,
            max_available_quantity=s.max_available_quantity,
            terms_version="custom-v1",
            observed_at=now,
        )
        session.add(sp)

    for po_in in body.open_purchase_orders:
        s_obj = suppliers_map[po_in.supplier_code]
        po = PurchaseOrder(
            external_id=f"PO-CUSTOM-{uuid.uuid4().hex[:6].upper()}",
            supplier_id=s_obj.id,
            node_id=node.id,
            status="OPEN",
            currency=po_in.currency,
            total_minor=po_in.ordered_quantity * po_in.unit_cost_minor,
            expected_delivery_at=po_in.expected_delivery_at,
            idempotency_key=f"demo-custom-po:{uuid.uuid4().hex}",
            created_at=now,
            updated_at=now,
        )
        session.add(po)
        session.flush()
        poi = PurchaseOrderItem(
            purchase_order_id=po.id,
            product_id=product.id,
            ordered_quantity=po_in.ordered_quantity,
            confirmed_quantity=po_in.confirmed_quantity,
            received_quantity=po_in.received_quantity,
            unit_cost_minor=po_in.unit_cost_minor,
        )
        session.add(poi)

    if body.recent_sales:
        so = SalesObservation(
            product_id=product.id,
            node_id=node.id,
            window_start=now - timedelta(hours=body.recent_sales.window_hours),
            window_end=now,
            units_sold=body.recent_sales.units_sold,
            source_version="custom-sales",
            observed_at=now,
        )
        session.add(so)

    preferred_supp = suppliers_map[body.suppliers[0].code]
    rec = Recommendation(
        source_reference=f"REC-CUSTOM-{uuid.uuid4().hex[:6].upper()}",
        source="demo-custom",
        product_id=product.id,
        node_id=node.id,
        preferred_supplier_id=preferred_supp.id,
        recommended_quantity=body.recommended_quantity,
        created_at=now,
    )
    session.add(rec)
    session.flush()

    review = PurchasingReview(
        recommendation_id=rec.id,
        scenario_type="demand-change" if body.mode == "DEMAND_CHANGE" else "custom-scenario",
        status="CREATED",
        idempotency_key=f"demo-custom-review:{uuid.uuid4().hex}",
    )
    session.add(review)
    session.flush()

    record_event(
        session,
        review.id,
        "DEMO_SCENARIO_PREPARED",
        {"scenario": "custom", "name": body.name, "mode": body.mode},
        "SYSTEM",
        "scenario-lab",
    )
    session.commit()
    background.add_task(request.app.state.workflow.start, review.id)

    return {
        "scenario_id": f"custom-{review.id[:8]}",
        "recommendation_id": rec.id,
        "review_id": review.id,
        "status": review.status,
        "links": {"self": f"/api/v1/reviews/{review.id}"},
    }
