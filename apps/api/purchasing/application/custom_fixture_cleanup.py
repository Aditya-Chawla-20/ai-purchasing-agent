"""Lifecycle management for isolated, local Scenario Lab fixtures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

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

_TERMINAL_REVIEW_STATUSES = {"COMPLETED", "REJECTED_BY_BUYER", "NEEDS_ATTENTION", "FAILED"}


def cleanup_expired_custom_fixtures(
    session: Session, *, now: datetime | None = None, retention_hours: int = 24
) -> int:
    """Delete only expired terminal custom scenarios and their isolated facts.

    The custom builder creates a unique product/node pair per scenario, which lets
    this routine remove operational data without ever selecting seeded or buyer
    records. It deliberately runs only in local development and test processes.
    """
    if settings.app_env.lower() not in {"development", "test"}:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=retention_hours)
    completed_at = func.coalesce(
        PurchasingReview.completed_at,
        PurchasingReview.updated_at,
        PurchasingReview.created_at,
    )
    reviews = session.scalars(
        select(PurchasingReview)
        .join(Recommendation, PurchasingReview.recommendation_id == Recommendation.id)
        .where(
            Recommendation.source == "demo-custom",
            PurchasingReview.status.in_(_TERMINAL_REVIEW_STATUSES),
            completed_at <= cutoff,
        )
    ).all()
    if not reviews:
        return 0

    review_ids = [review.id for review in reviews]
    recommendations = session.scalars(
        select(Recommendation).where(Recommendation.id.in_([r.recommendation_id for r in reviews]))
    ).all()
    product_ids = {recommendation.product_id for recommendation in recommendations}
    node_ids = {recommendation.node_id for recommendation in recommendations}
    plan_ids = set(session.scalars(select(SourcingPlan.id).where(SourcingPlan.review_id.in_(review_ids))))
    trace_ids = set(session.scalars(select(InvestigationTrace.id).where(InvestigationTrace.review_id.in_(review_ids))))
    po_ids = set(session.scalars(
        select(PurchaseOrderItem.purchase_order_id)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(PurchaseOrderItem.product_id.in_(product_ids), PurchaseOrder.node_id.in_(node_ids))
    ))
    supplier_ids = set(session.scalars(select(SupplierProduct.supplier_id).where(SupplierProduct.product_id.in_(product_ids))))

    # Delete children first: the schema intentionally has explicit FKs rather
    # than cascades so ordinary records cannot be removed accidentally.
    if po_ids:
        session.execute(delete(SupplierConfirmation).where(SupplierConfirmation.purchase_order_id.in_(po_ids)))
        session.execute(delete(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id.in_(po_ids)))
        session.execute(delete(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids)))
    if trace_ids:
        session.execute(delete(AgentToolCall).where(AgentToolCall.investigation_trace_id.in_(trace_ids)))
        session.execute(delete(InvestigationTrace).where(InvestigationTrace.id.in_(trace_ids)))
    session.execute(delete(ValidationResult).where(ValidationResult.review_id.in_(review_ids)))
    session.execute(delete(ActionAttempt).where(ActionAttempt.review_id.in_(review_ids)))
    session.execute(delete(ApprovalRequest).where(ApprovalRequest.review_id.in_(review_ids)))
    if plan_ids:
        session.execute(delete(PurchaseProposal).where(PurchaseProposal.sourcing_plan_id.in_(plan_ids)))
        session.execute(delete(SourcingPlanLine).where(SourcingPlanLine.sourcing_plan_id.in_(plan_ids)))
        session.execute(delete(SourcingOptionAssessment).where(SourcingOptionAssessment.sourcing_plan_id.in_(plan_ids)))
        session.execute(delete(SourcingPlan).where(SourcingPlan.id.in_(plan_ids)))
    session.execute(delete(PurchaseProposal).where(PurchaseProposal.review_id.in_(review_ids)))
    session.execute(delete(AuditEvent).where(AuditEvent.review_id.in_(review_ids)))
    session.execute(delete(Decision).where(Decision.review_id.in_(review_ids)))
    session.execute(delete(EvidenceSnapshot).where(EvidenceSnapshot.review_id.in_(review_ids)))
    session.execute(delete(PurchasingReview).where(PurchasingReview.id.in_(review_ids)))
    session.execute(delete(Recommendation).where(Recommendation.id.in_([r.id for r in recommendations])))

    # These facts belong to the unique custom product/node pairs. Supplier rows
    # are removed only after checking that they serve no remaining product.
    session.execute(delete(DemandSignal).where(DemandSignal.product_id.in_(product_ids), DemandSignal.node_id.in_(node_ids)))
    session.execute(delete(SalesObservation).where(SalesObservation.product_id.in_(product_ids), SalesObservation.node_id.in_(node_ids)))
    session.execute(delete(Forecast).where(Forecast.product_id.in_(product_ids), Forecast.node_id.in_(node_ids)))
    session.execute(delete(Inventory).where(Inventory.product_id.in_(product_ids), Inventory.node_id.in_(node_ids)))
    session.execute(delete(NodeProductPolicy).where(NodeProductPolicy.product_id.in_(product_ids), NodeProductPolicy.node_id.in_(node_ids)))
    session.execute(delete(SupplierProduct).where(SupplierProduct.product_id.in_(product_ids)))
    for supplier_id in supplier_ids:
        if not session.scalar(select(SupplierProduct.supplier_id).where(SupplierProduct.supplier_id == supplier_id)):
            session.execute(delete(Supplier).where(Supplier.id == supplier_id))
    session.execute(delete(Budget).where(Budget.node_id.in_(node_ids)))
    session.execute(delete(StorageCapacity).where(StorageCapacity.node_id.in_(node_ids)))
    session.execute(delete(Product).where(Product.id.in_(product_ids)))
    session.execute(delete(Node).where(Node.id.in_(node_ids)))
    session.commit()
    return len(review_ids)
