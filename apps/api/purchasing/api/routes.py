from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from purchasing.api.schemas import (
    ApprovalInput,
    ReviewCreate,
    ScenarioAdvanceInput,
    SupplierConfirmationInput,
)
from purchasing.application.audit import record_event
from purchasing.application.demo_scenarios import advance_scenario, reset_scenario
from purchasing.application.evidence import collect_inputs
from purchasing.domain.policy import DecisionType, calculate_decision
from purchasing.infrastructure.db import get_session
from purchasing.infrastructure.models import (
    ActionAttempt,
    ApprovalRequest,
    AuditEvent,
    Decision,
    EvidenceSnapshot,
    Node,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasingReview,
    Recommendation,
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
        "approval": (
            {
                "status": approval.status,
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
        "validation": (
            {
                "status": validation.status,
                "comparisons": validation.comparisons_json,
                "mismatches": validation.mismatch_codes_json,
            }
            if validation
            else None
        ),
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
