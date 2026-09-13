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


def build_inputs_from_tool_results(
    recommendation: Recommendation,
    results: dict[str, object],
) -> tuple[PurchaseInputs, dict]:
    """Build the policy snapshot solely from normalized tool envelopes.

    This deliberately has no database access: a tool call that was omitted,
    failed, stale, or scoped incorrectly cannot be silently replaced by a
    repository read later in the workflow.
    """
    now = datetime.now(UTC)

    def envelope(name: str) -> tuple[dict, object | None]:
        response = results.get(name)
        if response is None or not getattr(response, "ok", False):
            return {}, response
        return dict(getattr(response, "data", {}) or {}), response

    def timestamp(response: object | None) -> datetime | None:
        value = getattr(response, "observed_at", None)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
        except ValueError:
            return None

    def fact(name: str, value: object, response: object | None, max_age: timedelta) -> EvidenceFact:
        observed = timestamp(response)
        status = EvidenceStatus.FRESH
        if response is None or not getattr(response, "ok", False) or value is None:
            status = EvidenceStatus.MISSING
        elif observed is None or now - observed > max_age:
            status = EvidenceStatus.STALE
        return EvidenceFact(
            name=name,
            status=status,
            source=getattr(response, "source", "tool-dispatcher") if response else "tool-dispatcher",
            observed_at=observed.isoformat() if observed else now.isoformat(),
            value=value,
            version=getattr(response, "source_version", None) if response else None,
        )

    inventory, inventory_response = envelope("get_inventory")
    terms, terms_response = envelope("get_supplier_terms")
    forecast, forecast_response = envelope("get_demand_forecast")
    budget, budget_response = envelope("get_budget")
    storage, storage_response = envelope("get_storage_capacity")
    policy, policy_response = envelope("get_planning_policy")
    open_pos, po_response = envelope("list_open_purchase_orders")
    recent_sales, sales_response = envelope("get_recent_sales")

    horizon_days = int(terms.get("lead_time_days", 0)) + int(policy.get("review_period_days", 0))
    horizon = now + timedelta(days=horizon_days)
    incoming = 0
    for item in open_pos.get("items", []):
        try:
            delivery = datetime.fromisoformat(str(item["expected_delivery_at"]).replace("Z", "+00:00"))
            delivery = delivery.replace(tzinfo=UTC) if delivery.tzinfo is None else delivery
        except (KeyError, TypeError, ValueError):
            continue
        if now <= delivery <= horizon:
            incoming += max(0, int(item.get("confirmed_quantity", 0)) - int(item.get("received_quantity", 0)))

    inventory_conflict = bool(inventory) and int(inventory.get("reserved", 0)) + int(inventory.get("damaged", 0)) > int(inventory.get("on_hand", 0))
    facts = [
        fact("inventory", inventory or None, inventory_response, timedelta(minutes=settings.demo_volatile_freshness_minutes)),
        fact("supplier_terms", terms or None, terms_response, timedelta(hours=24)),
        fact("supplier_availability", terms.get("max_available_quantity") if terms else None, terms_response, timedelta(minutes=settings.demo_volatile_freshness_minutes)),
        fact("forecast", forecast or None, forecast_response, timedelta(hours=24)),
        fact("budget", budget or None, budget_response, timedelta(minutes=settings.demo_volatile_freshness_minutes)),
        fact("storage", storage or None, storage_response, timedelta(minutes=settings.demo_volatile_freshness_minutes)),
        fact("planning_policy", policy or None, policy_response, timedelta(hours=24)),
        fact("open_purchase_orders", incoming if po_response and getattr(po_response, "ok", False) else None, po_response, timedelta(minutes=settings.demo_volatile_freshness_minutes)),
    ]
    if "get_recent_sales" in results:
        facts.append(fact("recent_sales", recent_sales or None, sales_response, timedelta(minutes=15)))
    if inventory_conflict:
        facts[0] = facts[0].model_copy(update={"status": EvidenceStatus.CONFLICTING})
    complete = all(item.status == EvidenceStatus.FRESH for item in facts)
    raw = {
        "recommendation_id": recommendation.id,
        "product_id": recommendation.product_id,
        "node_id": recommendation.node_id,
        "supplier_id": recommendation.preferred_supplier_id,
        "original_quantity": recommendation.recommended_quantity,
        "on_hand": int(inventory.get("on_hand", 0)),
        "reserved": int(inventory.get("reserved", 0)),
        "damaged": int(inventory.get("damaged", 0)),
        "confirmed_incoming": incoming,
        "forecast_demand": int(forecast.get("expected_demand", 0)),
        "safety_stock": int(policy.get("safety_stock", 0)),
        "unit_cost_minor": int(terms.get("unit_cost_minor", 1)),
        "currency": str(terms.get("currency", budget.get("currency", "INR"))),
        "budget_available_minor": int(budget.get("available_minor", 0)),
        "budget_currency": budget.get("currency"),
        "minimum_order_quantity": int(terms.get("minimum_order_quantity", 1)),
        "available_supplier_quantity": terms.get("max_available_quantity"),
        "available_storage_volume": int(storage.get("available_volume", 0)),
        "product_unit_volume": int(policy.get("product_unit_volume", 1)),
        "evidence": [item.model_dump(mode="json") for item in facts],
        "evidence_complete": complete,
        "errors": [
            f"{item.name} is {item.status.value.lower()}"
            for item in facts if item.status != EvidenceStatus.FRESH
        ],
        "horizon_days": horizon_days,
    }
    inputs = PurchaseInputs.model_validate({
        key: value for key, value in raw.items()
        if key not in {"recommendation_id", "product_id", "node_id", "supplier_id", "budget_currency", "errors", "horizon_days"}
    })
    return inputs, {**raw, "policy_inputs": inputs.model_dump(mode="json")}


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
