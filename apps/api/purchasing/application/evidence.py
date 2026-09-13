import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from purchasing.domain.policy import EvidenceFact, EvidenceStatus, PurchaseInputs
from purchasing.infrastructure.models import (
    Budget,
    EvidenceSnapshot,
    Forecast,
    Inventory,
    Node,
    NodeProductPolicy,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    Recommendation,
    StorageCapacity,
    Supplier,
    SupplierProduct,
)
from purchasing.settings import settings


def collect_inputs(
    session: Session, recommendation: Recommendation
) -> tuple[PurchaseInputs, dict, Product, Supplier]:
    now = datetime.now(UTC)
    product = session.get(Product, recommendation.product_id)
    node = session.get(Node, recommendation.node_id)
    supplier = session.get(Supplier, recommendation.preferred_supplier_id)
    terms = (
        session.get(SupplierProduct, (supplier.id, product.id)) if supplier and product else None
    )
    inventory = session.get(Inventory, (product.id, node.id)) if product and node else None
    planning_policy = (
        session.get(NodeProductPolicy, (product.id, node.id)) if product and node else None
    )
    budget = (
        session.scalar(
            select(Budget)
            .where(
                Budget.node_id == node.id,
                Budget.period_start <= now,
                Budget.period_end >= now,
            )
            .order_by(Budget.period_end.desc())
        )
        if node
        else None
    )
    storage = session.get(StorageCapacity, node.id) if node else None
    facts: list[EvidenceFact] = []
    errors: list[str] = []

    def fact(
        name: str,
        value,
        observed_at,
        source: str,
        version: str | None = None,
        max_age: timedelta = timedelta(hours=24),
        effective_from: datetime | None = None,
        effective_until: datetime | None = None,
        conflicting: bool = False,
    ):
        if value is None or observed_at is None:
            facts.append(
                EvidenceFact(
                    name=name,
                    status=EvidenceStatus.MISSING,
                    source=source,
                    observed_at=now.isoformat(),
                    effective_from=effective_from.isoformat() if effective_from else None,
                    effective_until=effective_until.isoformat() if effective_until else None,
                    value=None,
                    version=version,
                )
            )
            errors.append(f"{name} is missing")
            return
        ts = observed_at.replace(tzinfo=UTC) if observed_at.tzinfo is None else observed_at
        status = (
            EvidenceStatus.CONFLICTING
            if conflicting
            else EvidenceStatus.STALE
            if now - ts > max_age
            else EvidenceStatus.FRESH
        )
        facts.append(
            EvidenceFact(
                name=name,
                status=status,
                source=source,
                observed_at=ts.isoformat(),
                effective_from=effective_from.isoformat() if effective_from else None,
                effective_until=effective_until.isoformat() if effective_until else None,
                value=value,
                version=version,
            )
        )
        if status == EvidenceStatus.STALE:
            errors.append(f"{name} is stale")
        elif status == EvidenceStatus.CONFLICTING:
            errors.append(f"{name} quantities are inconsistent")

    fact(
        "inventory",
        {"on_hand": inventory.on_hand, "reserved": inventory.reserved, "damaged": inventory.damaged}
        if inventory
        else None,
        inventory.observed_at if inventory else None,
        "mock-erp",
        str(inventory.version) if inventory else None,
        timedelta(minutes=settings.demo_volatile_freshness_minutes),
        conflicting=bool(inventory and inventory.reserved + inventory.damaged > inventory.on_hand),
    )
    fact(
        "supplier_terms",
        {
            "moq": terms.minimum_order_quantity,
            "lead_time_days": terms.lead_time_days,
            "unit_cost_minor": terms.unit_cost_minor,
        }
        if terms
        else None,
        terms.observed_at if terms else None,
        "mock-supplier",
        terms.terms_version if terms else None,
    )
    fact(
        "supplier_availability",
        {"max_available_quantity": terms.max_available_quantity} if terms else None,
        terms.observed_at if terms else None,
        "mock-supplier",
        terms.terms_version if terms else None,
        timedelta(minutes=settings.demo_volatile_freshness_minutes),
    )
    fact(
        "budget",
        {
            "available_minor": budget.allocated_minor - budget.committed_minor,
            "currency": budget.currency,
        }
        if budget
        else None,
        budget.observed_at if budget else None,
        "mock-finance",
        str(budget.version) if budget else None,
        timedelta(minutes=settings.demo_volatile_freshness_minutes),
        budget.period_start if budget else None,
        budget.period_end if budget else None,
    )
    fact(
        "storage",
        storage.available_volume if storage else None,
        storage.observed_at if storage else None,
        "mock-warehouse",
        str(storage.version) if storage else None,
        timedelta(minutes=settings.demo_volatile_freshness_minutes),
    )

    fact(
        "planning_policy",
        {
            "safety_stock": planning_policy.safety_stock,
            "review_period_days": planning_policy.review_period_days,
        }
        if planning_policy
        else None,
        planning_policy.observed_at if planning_policy else None,
        "mock-planning-policy",
        str(planning_policy.version) if planning_policy else None,
    )

    horizon_days = (
        terms.lead_time_days + planning_policy.review_period_days
        if terms and planning_policy
        else 0
    )
    forecast = (
        session.scalar(
            select(Forecast)
            .where(Forecast.product_id == product.id, Forecast.node_id == node.id)
            .order_by(Forecast.observed_at.desc())
        )
        if product and node
        else None
    )
    forecast_start = (
        forecast.window_start.replace(tzinfo=UTC)
        if forecast and forecast.window_start.tzinfo is None
        else forecast.window_start
        if forecast
        else None
    )
    forecast_end = (
        forecast.window_end.replace(tzinfo=UTC)
        if forecast and forecast.window_end.tzinfo is None
        else forecast.window_end
        if forecast
        else None
    )
    if forecast and (forecast_start > now or forecast_end < now + timedelta(days=horizon_days)):
        forecast_value = None
        forecast_observed = forecast.observed_at
    else:
        forecast_value = forecast.expected_demand if forecast else None
        forecast_observed = forecast.observed_at if forecast else None
    fact(
        "forecast",
        forecast_value,
        forecast_observed,
        "mock-forecast",
        forecast.model_version if forecast else None,
        effective_from=forecast.window_start if forecast else None,
        effective_until=forecast.window_end if forecast else None,
    )

    incoming = 0
    if product and node:
        rows = session.execute(
            select(PurchaseOrderItem, PurchaseOrder)
            .join(PurchaseOrder)
            .where(
                PurchaseOrderItem.product_id == product.id,
                PurchaseOrder.node_id == node.id,
                PurchaseOrder.status == "OPEN",
                PurchaseOrder.expected_delivery_at >= now,
                PurchaseOrder.expected_delivery_at <= now + timedelta(days=horizon_days),
            )
        ).all()
        incoming = sum(
            max(0, item.confirmed_quantity - item.received_quantity) for item, _po in rows
        )
    fact(
        "open_purchase_orders",
        incoming,
        now,
        "mock-erp",
        "derived-v1",
        timedelta(minutes=settings.demo_volatile_freshness_minutes),
    )

    complete = not errors and all(item.status == EvidenceStatus.FRESH for item in facts)
    raw = {
        "recommendation_id": recommendation.id,
        "product_id": product.id if product else None,
        "node_id": node.id if node else None,
        "supplier_id": supplier.id if supplier else None,
        "original_quantity": recommendation.recommended_quantity,
        "on_hand": inventory.on_hand if inventory else 0,
        "reserved": inventory.reserved if inventory else 0,
        "damaged": inventory.damaged if inventory else 0,
        "confirmed_incoming": incoming,
        "forecast_demand": forecast_value or 0,
        "safety_stock": planning_policy.safety_stock if planning_policy else 0,
        "unit_cost_minor": terms.unit_cost_minor if terms else 1,
        "currency": terms.currency if terms else (budget.currency if budget else "INR"),
        "budget_available_minor": budget.allocated_minor - budget.committed_minor if budget else 0,
        "budget_currency": budget.currency if budget else None,
        "minimum_order_quantity": terms.minimum_order_quantity if terms else 1,
        "available_supplier_quantity": terms.max_available_quantity if terms else None,
        "available_storage_volume": storage.available_volume if storage else 0,
        "product_unit_volume": product.unit_volume if product else 1,
        "evidence": [item.model_dump(mode="json") for item in facts],
        "evidence_complete": complete,
        "errors": errors,
        "horizon_days": horizon_days,
    }
    inputs = PurchaseInputs.model_validate(
        {
            k: v
            for k, v in raw.items()
            if k
            not in {
                "recommendation_id",
                "product_id",
                "node_id",
                "supplier_id",
                "budget_currency",
                "errors",
                "horizon_days",
            }
        }
    )
    payload = {**raw, "policy_inputs": inputs.model_dump(mode="json")}
    return inputs, payload, product, supplier


def store_snapshot(
    session: Session, review_id: str, sequence: int, payload: dict
) -> EvidenceSnapshot:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    snapshot = EvidenceSnapshot(
        review_id=review_id,
        sequence=sequence,
        snapshot_hash=sha256(canonical.encode()).hexdigest(),
        payload_json=payload,
        completeness_status="COMPLETE" if payload["evidence_complete"] else "DEFICIENT",
    )
    session.add(snapshot)
    session.flush()
    return snapshot
