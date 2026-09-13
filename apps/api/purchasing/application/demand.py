"""Demand spike detection and signal evaluation service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from purchasing.domain.policy import DemandSpikeInputs, DemandSpikeResult, evaluate_demand_spike
from purchasing.infrastructure.models import (
    DemandSignal,
    Forecast,
    Inventory,
    NodeProductPolicy,
    PurchaseOrder,
    PurchaseOrderItem,
    SalesObservation,
)


def evaluate_demand_signal(
    session: Session,
    product_id: str,
    node_id: str,
    threshold_bps: int = 15000,
    external_event_id: str | None = None,
    baseline_forecast_id: str | None = None,
    revised_forecast_id: str | None = None,
    sales_observation_id: str | None = None,
) -> tuple[DemandSpikeResult, DemandSignal | None]:
    now = datetime.now(UTC)

    # 1. Fetch recent sales observation
    sales = session.get(SalesObservation, sales_observation_id) if sales_observation_id else session.scalar(
        select(SalesObservation)
        .where(
            SalesObservation.product_id == product_id,
            SalesObservation.node_id == node_id,
        )
        .order_by(SalesObservation.window_end.desc())
        .limit(1)
    )

    # 2. Fetch baseline forecast
    baseline = session.get(Forecast, baseline_forecast_id) if baseline_forecast_id else session.scalar(
        select(Forecast)
        .where(
            Forecast.product_id == product_id,
            Forecast.node_id == node_id,
            Forecast.model_version.like("%baseline%") | Forecast.model_version.like("%seed%"),
        )
        .order_by(Forecast.observed_at.asc())
        .limit(1)
    )

    # 3. Fetch revised forecast (newer version)
    revised = session.get(Forecast, revised_forecast_id) if revised_forecast_id else session.scalar(
        select(Forecast)
        .where(
            Forecast.product_id == product_id,
            Forecast.node_id == node_id,
            Forecast.model_version.like("%revised%"),
        )
        .order_by(Forecast.observed_at.desc())
        .limit(1)
    )

    # 4. Fetch policy for safety stock
    policy = session.get(NodeProductPolicy, (product_id, node_id))
    safety_stock = policy.safety_stock if policy else 0
    threshold_bps = policy.demand_spike_threshold_bps if policy else threshold_bps

    # 5. Fetch inventory and eligible open POs
    inv = session.get(Inventory, (product_id, node_id))
    usable = max(0, inv.on_hand - inv.reserved - inv.damaged) if inv else 0

    open_po_rows = session.execute(
        select(PurchaseOrderItem)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
        .where(
            PurchaseOrder.node_id == node_id,
            PurchaseOrderItem.product_id == product_id,
            PurchaseOrder.status.in_(["OPEN", "CONFIRMED"]),
            PurchaseOrder.expected_delivery_at >= now,
        )
    ).scalars().all()
    eligible_incoming = sum(max(0, item.ordered_quantity - item.received_quantity) for item in open_po_rows)

    sales_fresh = sales is not None and (now - sales.observed_at.replace(tzinfo=UTC)) < timedelta(hours=24)
    forecast_fresh = baseline is not None and revised is not None

    invalid_scope = any(
        item and (item.product_id != product_id or item.node_id != node_id)
        for item in (sales, baseline, revised)
    )
    invalid_versions = bool(
        baseline and revised and (baseline.id == revised.id or baseline.model_version == revised.model_version)
    )
    invalid_window = bool(
        sales and (sales.window_end - sales.window_start) < timedelta(hours=24)
    )
    if not sales or not baseline or not revised or invalid_scope or invalid_versions or invalid_window:
        res = DemandSpikeResult(
            spike_detected=False,
            ratio_bps=0,
            threshold_bps=threshold_bps,
            status="INVESTIGATE",
            supplemental_need=0,
            target_stock=0,
            inventory_position=usable + eligible_incoming,
            reasons=("CONFLICTING_DEMAND_EVIDENCE" if invalid_scope or invalid_versions or invalid_window else "MISSING_REQUIRED_DEMAND_EVIDENCE",),
        )
        return res, None

    sales_hours = max(1, int((sales.window_end - sales.window_start).total_seconds() // 3600))
    baseline_hours = max(1, int((baseline.window_end - baseline.window_start).total_seconds() // 3600))
    revised_hours = max(1, int((revised.window_end - revised.window_start).total_seconds() // 3600))

    inputs = DemandSpikeInputs(
        sales_window_hours=sales_hours,
        units_sold=sales.units_sold,
        baseline_forecast_demand=baseline.expected_demand,
        baseline_forecast_hours=baseline_hours,
        revised_forecast_demand=revised.expected_demand,
        revised_forecast_hours=revised_hours,
        threshold_bps=threshold_bps,
        minimum_sales_observation_hours=24,
        usable_on_hand=usable,
        eligible_incoming=eligible_incoming,
        safety_stock=safety_stock,
        sales_fresh=sales_fresh,
        forecast_fresh=forecast_fresh,
        baseline_forecast_id=baseline.id,
        revised_forecast_id=revised.id,
    )

    result = evaluate_demand_spike(inputs)

    signal = None
    if external_event_id:
        ext_event = external_event_id or f"SIGNAL-SPIKE-{product_id[:8]}-{uuid.uuid4().hex[:8]}"
        existing = session.scalar(
            select(DemandSignal).where(DemandSignal.external_event_id == ext_event)
        )
        if existing:
            signal = existing
        else:
            signal = DemandSignal(
                external_event_id=ext_event,
                product_id=product_id,
                node_id=node_id,
                sales_observation_id=sales.id,
                baseline_forecast_id=baseline.id,
                revised_forecast_id=revised.id,
                actual_to_baseline_ratio_bps=result.ratio_bps,
                threshold_bps=threshold_bps,
                source_version=sales.source_version,
                triggered_at=now,
                created_at=now,
            )
            session.add(signal)
            session.flush()

    return result, signal
