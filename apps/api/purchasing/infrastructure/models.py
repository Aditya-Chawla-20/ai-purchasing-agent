from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from purchasing.infrastructure.db import Base


def new_id() -> str:
    return str(uuid4())


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String)
    unit_of_measure: Mapped[str] = mapped_column(String, default="EA")
    unit_volume: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class NodeProductPolicy(Base):
    __tablename__ = "node_product_policies"
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    safety_stock: Mapped[int] = mapped_column(Integer, default=0)
    review_period_days: Mapped[int] = mapped_column(Integer, default=2)
    version: Mapped[int] = mapped_column(Integer, default=1)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reliability_score: Mapped[int] = mapped_column(Integer, default=95)


class SupplierProduct(Base):
    __tablename__ = "supplier_products"
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    unit_cost_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    minimum_order_quantity: Mapped[int] = mapped_column(Integer, default=1)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=1)
    max_available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terms_version: Mapped[str] = mapped_column(String, default="1")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Inventory(Base):
    __tablename__ = "inventory"
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    damaged: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "node_id", "window_start", "window_end", name="uq_forecast_window"
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_demand: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String, default="seed-v1")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Budget(Base):
    __tablename__ = "budgets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    allocated_minor: Mapped[int] = mapped_column(Integer)
    committed_minor: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class StorageCapacity(Base):
    __tablename__ = "storage_capacity"
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    available_volume: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    external_id: Mapped[str] = mapped_column(String, unique=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    status: Mapped[str] = mapped_column(String, default="OPEN")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    total_minor: Mapped[int] = mapped_column(Integer)
    expected_delivery_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0)


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    ordered_quantity: Mapped[int] = mapped_column(Integer)
    confirmed_quantity: Mapped[int] = mapped_column(Integer, default=0)
    received_quantity: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost_minor: Mapped[int] = mapped_column(Integer)


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String, default="mock-planner")
    source_reference: Mapped[str] = mapped_column(String, unique=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    preferred_supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    recommended_quantity: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PurchasingReview(Base):
    __tablename__ = "purchasing_reviews"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String, default="CREATED", index=True)
    policy_version: Mapped[str] = mapped_column(String, default="v1")
    workflow_version: Mapped[str] = mapped_column(String, default="v1")
    current_proposal_version: Mapped[int] = mapped_column(Integer, default=0)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("purchasing_reviews.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    snapshot_hash: Mapped[str] = mapped_column(String, unique=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    completeness_status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("purchasing_reviews.id"), index=True)
    evidence_snapshot_id: Mapped[str] = mapped_column(ForeignKey("evidence_snapshots.id"))
    version: Mapped[int] = mapped_column(Integer)
    decision_type: Mapped[str] = mapped_column(String)
    original_quantity: Mapped[int] = mapped_column(Integer)
    raw_need: Mapped[int] = mapped_column(Integer)
    proposed_quantity: Mapped[int] = mapped_column(Integer)
    confidence_label: Mapped[str] = mapped_column(String)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON)
    calculation_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    explanation_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("review_id", "version", name="uq_decision_review_version"),)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("purchasing_reviews.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"))
    proposal_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="PENDING")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    comment: Mapped[str | None] = mapped_column(String, nullable=True)


class ActionAttempt(Base):
    __tablename__ = "action_attempts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("purchasing_reviews.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String, default="CREATE_PO")
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, default="STARTED")
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(ForeignKey("purchasing_reviews.id"), index=True)
    action_attempt_id: Mapped[str] = mapped_column(ForeignKey("action_attempts.id"))
    status: Mapped[str] = mapped_column(String)
    comparisons_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    mismatch_codes_json: Mapped[list[str]] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SupplierConfirmation(Base):
    __tablename__ = "supplier_confirmations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    external_event_id: Mapped[str] = mapped_column(String, unique=True)
    purchase_order_id: Mapped[str] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    confirmed_quantity: Mapped[int] = mapped_column(Integer)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("purchasing_reviews.id"), index=True)
    event_type: Mapped[str] = mapped_column(String)
    actor_type: Mapped[str] = mapped_column(String, default="SYSTEM")
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


Index(
    "ix_purchase_orders_status_delivery", PurchaseOrder.status, PurchaseOrder.expected_delivery_at
)
