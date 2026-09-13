"""Repeatable local fixtures that exercise real Scenario 1 safeguards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from purchasing.application.audit import record_event
from purchasing.infrastructure.models import (
    ActionAttempt,
    ApprovalRequest,
    AuditEvent,
    Budget,
    Decision,
    EvidenceSnapshot,
    Inventory,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasingReview,
    Recommendation,
    StorageCapacity,
    Supplier,
    SupplierConfirmation,
    SupplierProduct,
    ValidationResult,
)


@dataclass(frozen=True)
class DemoScenario:
    title: str
    purpose: str
    expected_outcome: str
    next_step: str
    requires_advance: bool = False


SCENARIOS: dict[str, DemoScenario] = {
    "inventory-conflict": DemoScenario(
        title="Conflicting inventory counts",
        purpose="Reserved plus damaged stock exceeds on-hand inventory.",
        expected_outcome="The review escalates to investigate; no purchase order can be created.",
        next_step="Inspect the conflicting Inventory evidence and failed policy check.",
    ),
    "late-incoming-po": DemoScenario(
        title="Delayed incoming purchase order",
        purpose="An overdue open PO must not be treated as stock that will arrive in time.",
        expected_outcome="The delayed PO is excluded and the resulting requirement is 600 units.",
        next_step="Inspect Confirmed incoming and the revised net requirement.",
    ),
    "supplier-terms-change": DemoScenario(
        title="Supplier quote changes after review",
        purpose="Price and MOQ change after the buyer has inspected the first proposal.",
        expected_outcome=(
            "The first approval is superseded; a lower, re-approved proposal is required."
        ),
        next_step=(
            "Apply the supplier update, then approve the original proposal to trigger revalidation."
        ),
        requires_advance=True,
    ),
    "lost-create-response": DemoScenario(
        title="Purchase order response lost",
        purpose="The supplier created the PO, but the original response was unavailable.",
        expected_outcome=(
            "The workflow recovers the matching PO by idempotency key and validates it once."
        ),
        next_step="Approve the proposal and inspect the successful read-back validation.",
    ),
    "wrong-persisted-po": DemoScenario(
        title="Persisted PO has wrong quantity",
        purpose="The supplier record under the idempotency key differs from the approved proposal.",
        expected_outcome=(
            "Read-back validation fails safely and the review requires buyer attention."
        ),
        next_step="Approve the proposal and inspect the quantity mismatch in validation.",
    ),
}


def get_scenario(name: str) -> DemoScenario:
    try:
        return SCENARIOS[name]
    except KeyError as exc:
        raise ValueError("Unknown demo scenario.") from exc


def reset_scenario(session: Session, name: str) -> tuple[PurchasingReview, DemoScenario]:
    """Restore scoped seed state and create one review for the selected guard."""
    scenario = get_scenario(name)
    _clear_seeded_review_history(session)
    _restore_baseline(session)

    recommendation = session.scalar(
        select(Recommendation).where(Recommendation.source_reference == "REC-800")
    )
    if not recommendation:
        raise RuntimeError("The primary demo recommendation is unavailable.")

    now = datetime.now(UTC)
    if name == "inventory-conflict":
        inventory = session.get(Inventory, (recommendation.product_id, recommendation.node_id))
        assert inventory is not None
        inventory.reserved = inventory.on_hand
        inventory.damaged = 1
        inventory.version += 1
        inventory.observed_at = now
    elif name == "late-incoming-po":
        _make_delayed_supply_visible(session, recommendation, now)

    review = PurchasingReview(
        recommendation_id=recommendation.id,
        status="CREATED",
        idempotency_key=f"demo-scenario:{name}:{now.timestamp()}",
    )
    session.add(review)
    session.flush()
    if name in {"lost-create-response", "wrong-persisted-po"}:
        _persist_ambiguous_purchase_order(
            session,
            review,
            recommendation,
            wrong_quantity=name == "wrong-persisted-po",
        )
    record_event(
        session,
        review.id,
        "DEMO_SCENARIO_PREPARED",
        {"scenario": name, "expected_outcome": scenario.expected_outcome},
        "SYSTEM",
        "scenario-lab",
    )
    return review, scenario


def advance_scenario(session: Session, name: str, review_id: str) -> DemoScenario:
    """Apply the one deliberate post-review supplier update used by the lab."""
    scenario = get_scenario(name)
    if name != "supplier-terms-change":
        raise ValueError("This scenario has no additional step.")
    review = session.get(PurchasingReview, review_id)
    if not review or review.status != "AWAITING_APPROVAL":
        raise ValueError("The selected review is not awaiting buyer approval.")
    recommendation = session.get(Recommendation, review.recommendation_id)
    assert recommendation is not None
    terms = session.get(
        SupplierProduct, (recommendation.preferred_supplier_id, recommendation.product_id)
    )
    assert terms is not None
    terms.unit_cost_minor = 2_000
    terms.minimum_order_quantity = 100
    terms.terms_version = "scenario-v2"
    terms.observed_at = datetime.now(UTC)
    record_event(
        session,
        review.id,
        "DEMO_SUPPLIER_TERMS_CHANGED",
        {
            "unit_cost_minor": terms.unit_cost_minor,
            "minimum_order_quantity": terms.minimum_order_quantity,
        },
        "SYSTEM",
        "scenario-lab",
    )
    return scenario


def _clear_seeded_review_history(session: Session) -> None:
    references = ("REC-800", "REC-ACCEPT", "REC-REJECT", "REC-INVESTIGATE")
    review_ids = list(
        session.scalars(
            select(PurchasingReview.id)
            .join(Recommendation)
            .where(Recommendation.source_reference.in_(references))
        )
    )
    if not review_ids:
        return
    attempts = session.scalars(
        select(ActionAttempt).where(ActionAttempt.review_id.in_(review_ids))
    ).all()
    po_ids = {
        attempt.response_json.get("purchase_order_id")
        for attempt in attempts
        if attempt.response_json and attempt.response_json.get("purchase_order_id")
    }
    po_ids.update(
        session.scalars(
            select(PurchaseOrder.id).where(PurchaseOrder.idempotency_key.like("demo-scenario:%"))
        )
    )
    session.execute(delete(AuditEvent).where(AuditEvent.review_id.in_(review_ids)))
    session.execute(delete(ValidationResult).where(ValidationResult.review_id.in_(review_ids)))
    session.execute(delete(ActionAttempt).where(ActionAttempt.review_id.in_(review_ids)))
    session.execute(delete(ApprovalRequest).where(ApprovalRequest.review_id.in_(review_ids)))
    session.execute(delete(Decision).where(Decision.review_id.in_(review_ids)))
    session.execute(delete(EvidenceSnapshot).where(EvidenceSnapshot.review_id.in_(review_ids)))
    session.execute(delete(PurchasingReview).where(PurchasingReview.id.in_(review_ids)))
    if po_ids:
        session.execute(
            delete(SupplierConfirmation).where(SupplierConfirmation.purchase_order_id.in_(po_ids))
        )
        session.execute(
            delete(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id.in_(po_ids))
        )
        session.execute(delete(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids)))


def _restore_baseline(session: Session) -> None:
    now = datetime.now(UTC)
    apples = session.scalar(
        select(Recommendation).where(Recommendation.source_reference == "REC-800")
    )
    supplier = session.scalar(select(Supplier).where(Supplier.code == "SUP-01"))
    if not apples or not supplier:
        raise RuntimeError("The seeded demo data is unavailable.")
    inventory = session.get(Inventory, (apples.product_id, apples.node_id))
    terms = session.get(SupplierProduct, (supplier.id, apples.product_id))
    storage = session.get(StorageCapacity, apples.node_id)
    budget = session.scalar(select(Budget).where(Budget.node_id == apples.node_id))
    open_po = session.scalar(
        select(PurchaseOrder).where(PurchaseOrder.external_id == "PO-SEED-OPEN-001")
    )
    partial = session.scalar(
        select(PurchaseOrder).where(PurchaseOrder.external_id == "PO-PARTIAL-500")
    )
    assert inventory and terms and storage and budget and open_po and partial
    inventory.on_hand, inventory.reserved, inventory.damaged = 180, 20, 10
    inventory.version += 1
    inventory.observed_at = now
    terms.unit_cost_minor = 1_250
    terms.minimum_order_quantity = 50
    terms.max_available_quantity = 500
    terms.terms_version, terms.observed_at = "1", now
    storage.available_volume, storage.version, storage.observed_at = 4_500, storage.version + 1, now
    budget.allocated_minor, budget.committed_minor = 1_000_000, 400_000
    budget.version, budget.observed_at = budget.version + 1, now
    open_po.status = "OPEN"
    open_po.expected_delivery_at = now + timedelta(days=3)
    open_po.updated_at = now
    partial.status = "OPEN"
    partial.expected_delivery_at = now + timedelta(days=4)
    partial.updated_at = now
    partial.recovery_attempts = 0
    partial_item = session.scalar(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == partial.id)
    )
    assert partial_item is not None
    partial_item.confirmed_quantity = 0


def _make_delayed_supply_visible(
    session: Session, recommendation: Recommendation, now: datetime
) -> None:
    open_po = session.scalar(
        select(PurchaseOrder).where(PurchaseOrder.external_id == "PO-SEED-OPEN-001")
    )
    terms = session.get(
        SupplierProduct, (recommendation.preferred_supplier_id, recommendation.product_id)
    )
    storage = session.get(StorageCapacity, recommendation.node_id)
    budget = session.scalar(select(Budget).where(Budget.node_id == recommendation.node_id))
    assert open_po and terms and storage and budget
    open_po.expected_delivery_at, open_po.updated_at = now - timedelta(days=1), now
    terms.max_available_quantity, terms.observed_at = 800, now
    storage.available_volume, storage.observed_at = 7_000, now
    budget.allocated_minor, budget.committed_minor, budget.observed_at = 1_400_000, 400_000, now


def _persist_ambiguous_purchase_order(
    session: Session,
    review: PurchasingReview,
    recommendation: Recommendation,
    *,
    wrong_quantity: bool,
) -> None:
    terms = session.get(
        SupplierProduct, (recommendation.preferred_supplier_id, recommendation.product_id)
    )
    assert terms is not None
    quantity = 350 if wrong_quantity else 450
    now = datetime.now(UTC)
    po = PurchaseOrder(
        external_id=f"PO-RECOVERED-{review.id[:8].upper()}",
        supplier_id=recommendation.preferred_supplier_id,
        node_id=recommendation.node_id,
        status="OPEN",
        currency=terms.currency,
        total_minor=quantity * terms.unit_cost_minor,
        expected_delivery_at=now + timedelta(days=365),
        idempotency_key=f"{review.id}:create-po:v1",
        created_at=now,
        updated_at=now,
    )
    session.add(po)
    session.flush()
    session.add(
        PurchaseOrderItem(
            purchase_order_id=po.id,
            product_id=recommendation.product_id,
            ordered_quantity=quantity,
            confirmed_quantity=0,
            received_quantity=0,
            unit_cost_minor=terms.unit_cost_minor,
        )
    )
