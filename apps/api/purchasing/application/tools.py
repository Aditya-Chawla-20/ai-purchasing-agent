"""Typed purchasing tools, context envelopes, dispatcher, and mandatory manifest guards."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from purchasing.application.audit import record_event
from purchasing.infrastructure.models import (
    ActionAttempt,
    AgentToolCall,
    ApprovalRequest,
    Budget,
    Forecast,
    Inventory,
    NodeProductPolicy,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseProposal,
    SalesObservation,
    SourcingPlan,
    SourcingPlanLine,
    StorageCapacity,
    Supplier,
    SupplierProduct,
    ValidationResult,
    new_id,
)


@dataclass(frozen=True)
class ToolContext:
    review_id: str
    correlation_id: str = ""
    actor: dict[str, str] = field(default_factory=lambda: {"type": "SYSTEM", "id": "workflow"})
    deadline_ms: int = 2000
    product_id: str | None = None
    node_id: str | None = None
    preferred_supplier_id: str | None = None
    allowed_supplier_ids: frozenset[str] = field(default_factory=frozenset)
    allowed_purchase_order_ids: frozenset[str] = field(default_factory=frozenset)
    phase: str = "investigation"
    max_rounds: int = 3
    max_total_calls: int = 10


@dataclass
class ToolResponse:
    ok: bool
    data: dict[str, Any]
    source: str
    source_version: str = "1"
    observed_at: str = ""
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "source": self.source,
            "source_version": self.source_version,
            "observed_at": self.observed_at,
            "warnings": self.warnings,
            "error_code": self.error_code,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Individual Read Tool Implementations
# ---------------------------------------------------------------------------


def tool_get_inventory(session: Session, context: ToolContext, product_id: str, node_id: str) -> ToolResponse:
    inv = session.get(Inventory, (product_id, node_id))
    if not inv:
        return ToolResponse(
            ok=False,
            data={},
            source="mock-erp",
            source_version="none",
            observed_at=_now_iso(),
            error_code="NOT_FOUND",
            warnings=["Inventory record not found"],
        )
    return ToolResponse(
        ok=True,
        data={
            "product_id": inv.product_id,
            "node_id": inv.node_id,
            "on_hand": inv.on_hand,
            "reserved": inv.reserved,
            "damaged": inv.damaged,
            "usable_on_hand": max(0, inv.on_hand - inv.reserved - inv.damaged),
            "version": str(inv.version),
            "observed_at": inv.observed_at.isoformat(),
        },
        source="mock-wms",
        source_version=str(inv.version),
        observed_at=inv.observed_at.isoformat(),
    )


def tool_get_demand_forecast(
    session: Session,
    context: ToolContext,
    product_id: str,
    node_id: str,
    forecast_id: str | None = None,
) -> ToolResponse:
    query = select(Forecast).where(Forecast.product_id == product_id, Forecast.node_id == node_id)
    if forecast_id:
        query = query.where(Forecast.id == forecast_id)
    else:
        query = query.order_by(Forecast.observed_at.desc())

    fc = session.scalar(query.limit(1))
    if not fc:
        return ToolResponse(
            ok=False,
            data={},
            source="mock-forecaster",
            source_version="none",
            observed_at=_now_iso(),
            error_code="NOT_FOUND",
            warnings=["Forecast not found"],
        )
    return ToolResponse(
        ok=True,
        data={
            "forecast_id": fc.id,
            "product_id": fc.product_id,
            "node_id": fc.node_id,
            "expected_demand": fc.expected_demand,
            "window_start": fc.window_start.isoformat(),
            "window_end": fc.window_end.isoformat(),
            "model_version": fc.model_version,
            "observed_at": fc.observed_at.isoformat(),
        },
        source="mock-forecaster",
        source_version=fc.model_version,
        observed_at=fc.observed_at.isoformat(),
    )


def tool_list_open_purchase_orders(
    session: Session, context: ToolContext, product_id: str, node_id: str
) -> ToolResponse:
    now = datetime.now(UTC)
    rows = session.execute(
        select(PurchaseOrder, PurchaseOrderItem)
        .join(PurchaseOrderItem, PurchaseOrderItem.purchase_order_id == PurchaseOrder.id)
        .where(
            PurchaseOrder.node_id == node_id,
            PurchaseOrderItem.product_id == product_id,
            PurchaseOrder.status.in_(["OPEN", "CONFIRMED"]),
        )
    ).all()

    items = []
    eligible_qty = 0
    for po, item in rows:
        outstanding = max(0, item.ordered_quantity - item.received_quantity)
        is_delayed = po.expected_delivery_at and po.expected_delivery_at.replace(tzinfo=UTC) < now
        if not is_delayed:
            eligible_qty += outstanding
        items.append(
            {
                "purchase_order_id": po.id,
                "external_id": po.external_id,
                "supplier_id": po.supplier_id,
                "ordered_quantity": item.ordered_quantity,
                "confirmed_quantity": item.confirmed_quantity,
                "received_quantity": item.received_quantity,
                "outstanding_quantity": outstanding,
                "expected_delivery_at": po.expected_delivery_at.isoformat() if po.expected_delivery_at else None,
                "is_delayed": is_delayed,
                "status": po.status,
            }
        )

    return ToolResponse(
        ok=True,
        data={"items": items, "eligible_incoming_quantity": eligible_qty},
        source="mock-erp",
        source_version="1",
        observed_at=_now_iso(),
    )


def tool_get_supplier_terms(
    session: Session, context: ToolContext, supplier_id: str, product_id: str
) -> ToolResponse:
    terms = session.get(SupplierProduct, (supplier_id, product_id))
    sup = session.get(Supplier, supplier_id)
    if not terms or not sup:
        return ToolResponse(
            ok=False,
            data={},
            source="mock-supplier-portal",
            source_version="none",
            observed_at=_now_iso(),
            error_code="NOT_FOUND",
            warnings=["Supplier terms not found"],
        )
    return ToolResponse(
        ok=True,
        data={
            "supplier_id": terms.supplier_id,
            "product_id": terms.product_id,
            "supplier_name": sup.name,
            "unit_cost_minor": terms.unit_cost_minor,
            "currency": terms.currency,
            "minimum_order_quantity": terms.minimum_order_quantity,
            "lead_time_days": terms.lead_time_days,
            "max_available_quantity": terms.max_available_quantity,
            "terms_version": terms.terms_version,
            "reliability_score_bps": sup.reliability_score_bps,
            "active": sup.active,
            "observed_at": terms.observed_at.isoformat(),
        },
        source="mock-supplier-portal",
        source_version=terms.terms_version,
        observed_at=terms.observed_at.isoformat(),
    )


def tool_get_budget(
    session: Session, context: ToolContext, node_id: str, currency: str = "INR"
) -> ToolResponse:
    now = datetime.now(UTC)
    budget = session.scalar(
        select(Budget)
        .where(
            Budget.node_id == node_id,
            Budget.period_start <= now,
            Budget.period_end >= now,
            Budget.currency == currency,
        )
        .order_by(Budget.period_end.desc())
        .limit(1)
    )
    if not budget:
        return ToolResponse(
            ok=False,
            data={},
            source="mock-finance",
            source_version="none",
            observed_at=_now_iso(),
            error_code="NOT_FOUND",
            warnings=["Budget record not found"],
        )
    return ToolResponse(
        ok=True,
        data={
            "budget_id": budget.id,
            "node_id": budget.node_id,
            "allocated_minor": budget.allocated_minor,
            "committed_minor": budget.committed_minor,
            "available_minor": max(0, budget.allocated_minor - budget.committed_minor),
            "currency": budget.currency,
            "version": str(budget.version),
            "observed_at": budget.observed_at.isoformat(),
        },
        source="mock-finance",
        source_version=str(budget.version),
        observed_at=budget.observed_at.isoformat(),
    )


def tool_get_storage_capacity(session: Session, context: ToolContext, node_id: str) -> ToolResponse:
    storage = session.get(StorageCapacity, node_id)
    if not storage:
        return ToolResponse(
            ok=False,
            data={},
            source="mock-facility",
            source_version="none",
            observed_at=_now_iso(),
            error_code="NOT_FOUND",
            warnings=["Storage capacity not found"],
        )
    return ToolResponse(
        ok=True,
        data={
            "node_id": storage.node_id,
            "available_volume": storage.available_volume,
            "version": str(storage.version),
            "observed_at": storage.observed_at.isoformat(),
        },
        source="mock-facility",
        source_version=str(storage.version),
        observed_at=storage.observed_at.isoformat(),
    )


def tool_get_planning_policy(
    session: Session, context: ToolContext, product_id: str, node_id: str
) -> ToolResponse:
    policy = session.get(NodeProductPolicy, (product_id, node_id))
    prod = session.get(Product, product_id)
    if not policy or not prod:
        return ToolResponse(
            ok=False,
            data={},
            source="mock-policy-service",
            source_version="none",
            observed_at=_now_iso(),
            error_code="NOT_FOUND",
            warnings=["Planning policy not found"],
        )
    return ToolResponse(
        ok=True,
        data={
            "product_id": policy.product_id,
            "node_id": policy.node_id,
            "safety_stock": policy.safety_stock,
            "review_period_days": policy.review_period_days,
            "product_unit_volume": prod.unit_volume,
            "version": str(policy.version),
            "observed_at": policy.observed_at.isoformat(),
        },
        source="mock-policy-service",
        source_version=str(policy.version),
        observed_at=policy.observed_at.isoformat(),
    )


def tool_get_purchase_order(
    session: Session, context: ToolContext, purchase_order_id: str
) -> ToolResponse:
    po = session.get(PurchaseOrder, purchase_order_id)
    if not po:
        # Also try by external_id
        po = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.external_id == purchase_order_id)
        )
    if not po:
        return ToolResponse(
            ok=False,
            data={},
            source="mock-erp",
            source_version="none",
            observed_at=_now_iso(),
            error_code="NOT_FOUND",
            warnings=["Purchase order not found"],
        )
    items = session.scalars(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id)
    ).all()
    return ToolResponse(
        ok=True,
        data={
            "purchase_order_id": po.id,
            "external_id": po.external_id,
            "supplier_id": po.supplier_id,
            "node_id": po.node_id,
            "status": po.status,
            "currency": po.currency,
            "total_minor": po.total_minor,
            "items": [
                {
                    "product_id": item.product_id,
                    "ordered_quantity": item.ordered_quantity,
                    "confirmed_quantity": item.confirmed_quantity,
                    "received_quantity": item.received_quantity,
                    "unit_cost_minor": item.unit_cost_minor,
                }
                for item in items
            ],
            "expected_delivery_at": po.expected_delivery_at.isoformat() if po.expected_delivery_at else None,
        },
        source="mock-erp",
        source_version=str(po.version),
        observed_at=po.updated_at.isoformat(),
    )


def tool_list_supplier_options(
    session: Session, context: ToolContext, product_id: str, node_id: str
) -> ToolResponse:
    now = datetime.now(UTC)
    rows = session.execute(
        select(Supplier, SupplierProduct)
        .join(SupplierProduct, SupplierProduct.supplier_id == Supplier.id)
        .where(SupplierProduct.product_id == product_id)
    ).all()

    candidates = []
    for sup, terms in rows:
        deliv = (now + timedelta(days=terms.lead_time_days)).isoformat()
        candidates.append(
            {
                "supplier_id": sup.id,
                "supplier_code": sup.code,
                "supplier_name": sup.name,
                "active": sup.active,
                "reliability_score_bps": sup.reliability_score_bps,
                "unit_cost_minor": terms.unit_cost_minor,
                "currency": terms.currency,
                "minimum_order_quantity": terms.minimum_order_quantity,
                "lead_time_days": terms.lead_time_days,
                "max_available_quantity": terms.max_available_quantity,
                "terms_version": terms.terms_version,
                "expected_delivery_at": deliv,
                "terms_fresh": (now - terms.observed_at.replace(tzinfo=UTC)) < timedelta(hours=24),
            }
        )

    return ToolResponse(
        ok=True,
        data={"candidates": candidates},
        source="mock-supplier-master",
        source_version="1",
        observed_at=_now_iso(),
    )


def tool_get_recent_sales(
    session: Session, context: ToolContext, product_id: str, node_id: str, hours: int = 24
) -> ToolResponse:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=hours)
    obs = session.scalar(
        select(SalesObservation)
        .where(
            SalesObservation.product_id == product_id,
            SalesObservation.node_id == node_id,
            SalesObservation.window_end >= cutoff,
        )
        .order_by(SalesObservation.window_end.desc())
        .limit(1)
    )
    if not obs:
        return ToolResponse(
            ok=False,
            data={},
            source="mock-pos",
            source_version="none",
            observed_at=_now_iso(),
            error_code="NOT_FOUND",
            warnings=["No sales observation recorded"],
        )
    return ToolResponse(
        ok=True,
        data={
            "observation_id": obs.id,
            "product_id": obs.product_id,
            "node_id": obs.node_id,
            "window_start": obs.window_start.isoformat(),
            "window_end": obs.window_end.isoformat(),
            "units_sold": obs.units_sold,
            "hours": max(1, int((obs.window_end - obs.window_start).total_seconds() // 3600)),
            "source": obs.source,
            "source_version": obs.source_version,
            "observed_at": obs.observed_at.isoformat(),
        },
        source=obs.source,
        source_version=obs.source_version,
        observed_at=obs.observed_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Tool Dispatcher with Strict Bounds & Audit (Spec 07)
# ---------------------------------------------------------------------------

READ_TOOL_FUNCTIONS = {
    "get_inventory": tool_get_inventory,
    "get_demand_forecast": tool_get_demand_forecast,
    "list_open_purchase_orders": tool_list_open_purchase_orders,
    "get_supplier_terms": tool_get_supplier_terms,
    "get_budget": tool_get_budget,
    "get_storage_capacity": tool_get_storage_capacity,
    "get_planning_policy": tool_get_planning_policy,
    "get_purchase_order": tool_get_purchase_order,
    "list_supplier_options": tool_list_supplier_options,
    "get_recent_sales": tool_get_recent_sales,
}

READ_ONLY_TOOLS: list[str] = list(READ_TOOL_FUNCTIONS.keys())

# These labels and summaries are deliberately produced by application code, not
# model prose. They make the audit trail useful to a buyer without persisting a
# provider response or any hidden model reasoning.
TOOL_AUDIT_LABELS = {
    "get_inventory": "Read inventory position",
    "get_demand_forecast": "Read demand forecast",
    "list_open_purchase_orders": "Check incoming purchase orders",
    "get_supplier_terms": "Read supplier terms",
    "get_budget": "Check purchase budget",
    "get_storage_capacity": "Check storage capacity",
    "get_planning_policy": "Read replenishment policy",
    "get_purchase_order": "Read purchase order",
    "list_supplier_options": "Compare supplier options",
    "get_recent_sales": "Read recent sales velocity",
}


def _audit_result_summary(tool_name: str, response: ToolResponse) -> str:
    """Return a compact, safe operational result for the buyer-facing audit log."""
    if not response.ok:
        return "No evidence was accepted; the workflow will apply its safety guard."

    data = response.data
    if tool_name == "get_inventory":
        return f"Usable stock: {data.get('usable_on_hand', 0)} units."
    if tool_name == "get_demand_forecast":
        return f"Expected demand: {data.get('expected_demand', 0)} units."
    if tool_name == "list_open_purchase_orders":
        return f"Eligible incoming: {data.get('eligible_incoming_quantity', 0)} units across {len(data.get('items', []))} order(s)."
    if tool_name == "get_supplier_terms":
        return f"MOQ {data.get('minimum_order_quantity', 0)} · available {data.get('max_available_quantity', 0)} units."
    if tool_name == "get_budget":
        return f"Available budget: {data.get('available_minor', 0)} {data.get('currency', '')}."
    if tool_name == "get_storage_capacity":
        return f"Available storage: {data.get('available_volume', 0)} volume units."
    if tool_name == "get_planning_policy":
        return f"Safety stock: {data.get('safety_stock', 0)} units · review period: {data.get('review_period_days', 0)} days."
    if tool_name == "list_supplier_options":
        return f"Compared {len(data.get('candidates', []))} supplier option(s)."
    if tool_name == "get_recent_sales":
        return f"Sales velocity: {data.get('units_sold', 0)} units in {data.get('hours', 0)} hours."
    if tool_name == "get_purchase_order":
        return f"Purchase order status: {data.get('status', 'recorded')}."
    return "Evidence captured."


def _audit_scope_summary(arguments: dict[str, Any]) -> str:
    """Describe validated scope without surfacing opaque internal identifiers."""
    if "purchase_order_id" in arguments:
        return "Approved purchase-order scope"
    if "supplier_id" in arguments:
        return "Approved supplier and product scope"
    if "product_id" in arguments and "node_id" in arguments:
        return "Product and fulfilment-node scope"
    if "node_id" in arguments:
        return "Fulfilment-node scope"
    return "Current review scope"

TOOL_ARGUMENTS: dict[str, tuple[set[str], set[str]]] = {
    "get_inventory": ({"product_id", "node_id"}, {"product_id", "node_id"}),
    "get_demand_forecast": ({"product_id", "node_id"}, {"product_id", "node_id", "forecast_id"}),
    "list_open_purchase_orders": ({"product_id", "node_id"}, {"product_id", "node_id"}),
    "get_supplier_terms": ({"supplier_id", "product_id"}, {"supplier_id", "product_id"}),
    "get_budget": ({"node_id"}, {"node_id", "currency"}),
    "get_storage_capacity": ({"node_id"}, {"node_id"}),
    "get_planning_policy": ({"product_id", "node_id"}, {"product_id", "node_id"}),
    "get_purchase_order": ({"purchase_order_id"}, {"purchase_order_id"}),
    "list_supplier_options": ({"product_id", "node_id"}, {"product_id", "node_id"}),
    "get_recent_sales": ({"product_id", "node_id"}, {"product_id", "node_id", "hours"}),
}


class ToolDispatcher:
    def __init__(
        self,
        session: Session | None,
        trace_id: str | None = None,
        review_id: str | None = None,
        max_total_calls: int = 10,
        context: ToolContext | None = None,
    ):
        self.session = session
        self.context = context
        self.trace_id = trace_id or ""
        self.review_id = review_id or (context.review_id if context else "")
        self.max_total_calls = getattr(context, "max_total_calls", max_total_calls) if context else max_total_calls
        self.sequence = 0
        self.calls_by_normalized_key: dict[str, int] = {}
        self.seen_in_round: set[tuple[int, str, str]] = set()

    @property
    def total_calls(self) -> int:
        return self.sequence

    def _validate_scope(self, tool_name: str, args: dict[str, Any]) -> str | None:
        if not self.context or self.context.phase != "investigation":
            return "POLICY_DENIED"
        required, allowed = TOOL_ARGUMENTS[tool_name]
        if not required.issubset(args) or set(args) - allowed:
            return "INVALID_ARGUMENT"
        if args.get("product_id") not in {None, self.context.product_id}:
            return "UNAUTHORIZED"
        if args.get("node_id") not in {None, self.context.node_id}:
            return "UNAUTHORIZED"
        supplier_id = args.get("supplier_id")
        allowed_suppliers = set(self.context.allowed_supplier_ids) | {self.context.preferred_supplier_id}
        if supplier_id and supplier_id not in allowed_suppliers:
            return "UNAUTHORIZED"
        po_id = args.get("purchase_order_id")
        if po_id and po_id not in self.context.allowed_purchase_order_ids:
            return "UNAUTHORIZED"
        if tool_name == "get_recent_sales" and (not isinstance(args.get("hours", 24), int) or args.get("hours", 24) < 24):
            return "INVALID_ARGUMENT"
        return None

    def dispatch(
        self,
        tool_name: str,
        round_number: int = 1,
        args: dict[str, Any] | None = None,
        arguments: dict[str, Any] | None = None,
        provider: str = "gemini",
        model: str = "gemini-3.8-flash",
    ) -> ToolResponse:
        call_args = arguments if arguments is not None else (args or {})
        started = perf_counter()

        if self.session is None:
            raise RuntimeError("A short-lived database session is required to dispatch a tool")

        if self.total_calls >= self.max_total_calls:
            response = ToolResponse(
                ok=False,
                data={},
                source="dispatcher",
                source_version="1",
                observed_at=_now_iso(),
                error_code="TOOL_LIMIT_REACHED",
                warnings=[f"Exceeded max total calls ({self.max_total_calls})"],
            )
            record_event(
                self.session,
                self.review_id,
                "TOOL_CALL_COMPLETED",
                {
                    "tool": tool_name,
                    "display_name": TOOL_AUDIT_LABELS.get(tool_name, "Run approved read tool"),
                    "status": "FAILED",
                    "round": round_number,
                    "duration_ms": int((perf_counter() - started) * 1000),
                    "error_code": response.error_code,
                    "summary": _audit_result_summary(tool_name, response),
                    "scope": _audit_scope_summary(call_args),
                    "source": response.source,
                    "provider": provider,
                    "model": model,
                    "attempt": 0,
                },
            )
            return response

        self.sequence += 1
        arg_key = f"{tool_name}:{json.dumps(call_args, sort_keys=True)}"
        call_tuple = (round_number, tool_name, json.dumps(call_args, sort_keys=True))

        if tool_name not in READ_TOOL_FUNCTIONS:
            resp = ToolResponse(
                ok=False,
                data={},
                source="dispatcher",
                source_version="1",
                observed_at=_now_iso(),
                error_code="TOOL_NOT_ALLOWED",
                warnings=[f"Unknown or unauthorized tool '{tool_name}'"],
            )
        elif (scope_error := self._validate_scope(tool_name, call_args)) is not None:
            resp = ToolResponse(
                ok=False, data={}, source="dispatcher", source_version="1", observed_at=_now_iso(),
                error_code=scope_error, warnings=["Tool arguments are outside the current review scope"],
            )
        elif call_tuple in self.seen_in_round:
            resp = ToolResponse(
                ok=False,
                data={},
                source="dispatcher",
                source_version="1",
                observed_at=_now_iso(),
                error_code="DUPLICATE_TOOL_CALL",
                warnings=[f"Duplicate tool call '{tool_name}' in round {round_number}"],
            )
        else:
            self.seen_in_round.add(call_tuple)
            attempt = self.calls_by_normalized_key.get(arg_key, 0) + 1
            self.calls_by_normalized_key[arg_key] = attempt

            if attempt > 2:
                resp = ToolResponse(
                    ok=False,
                    data={},
                    source="dispatcher",
                    source_version="1",
                    observed_at=_now_iso(),
                    error_code="ATTEMPTS_EXHAUSTED",
                    warnings=[f"Exceeded max attempts (2) for tool '{tool_name}' with these arguments"],
                )
            else:
                fn = READ_TOOL_FUNCTIONS[tool_name]
                ctx = self.context or ToolContext(review_id=self.review_id, correlation_id=new_id())
                try:
                    resp = fn(self.session, ctx, **call_args)
                except Exception as e:
                    resp = ToolResponse(
                        ok=False,
                        data={},
                        source="dispatcher",
                        source_version="1",
                        observed_at=_now_iso(),
                        error_code="INTERNAL",
                        warnings=[f"Tool error: {str(e)}"],
                    )

        duration_ms = int((perf_counter() - started) * 1000)
        arg_hash = sha256(json.dumps(call_args, sort_keys=True).encode()).hexdigest()
        result_hash = sha256(json.dumps(resp.data, sort_keys=True).encode()).hexdigest()

        # Persist AgentToolCall only if trace_id is present
        if self.trace_id:
            attempt = self.calls_by_normalized_key.get(arg_key, 1)
            tool_call_record = AgentToolCall(
                investigation_trace_id=self.trace_id,
                round_number=round_number,
                sequence=self.sequence,
                tool_name=tool_name,
                arguments_json=call_args,
                arguments_hash=arg_hash,
                result_ref=resp.source,
                result_hash=result_hash,
                provider=provider,
                model=model,
                attempt=attempt,
                status="SUCCEEDED" if resp.ok else "FAILED",
                error_code=resp.error_code,
                duration_ms=duration_ms,
            )
            self.session.add(tool_call_record)
            self.session.flush()

        record_event(
            self.session,
            self.review_id,
            "TOOL_CALL_COMPLETED",
            {
                "tool": tool_name,
                "display_name": TOOL_AUDIT_LABELS.get(tool_name, "Run approved read tool"),
                "status": "SUCCEEDED" if resp.ok else "FAILED",
                "round": round_number,
                "duration_ms": duration_ms,
                "error_code": resp.error_code,
                "summary": _audit_result_summary(tool_name, resp),
                "scope": _audit_scope_summary(call_args),
                "source": resp.source,
                "provider": provider,
                "model": model,
                "attempt": self.calls_by_normalized_key.get(arg_key, 1),
            },
        )
        return resp


# ---------------------------------------------------------------------------
# Mandatory Evidence Manifest Guard (Spec 07 §1)
# ---------------------------------------------------------------------------

MANDATORY_MANIFESTS = {
    "recommendation-review": [
        "get_inventory",
        "get_demand_forecast",
        "list_open_purchase_orders",
        "get_supplier_terms",
        "get_budget",
        "get_storage_capacity",
        "get_planning_policy",
    ],
    "supplier-shortfall": [
        "get_inventory",
        "get_demand_forecast",
        "list_open_purchase_orders",
        "get_supplier_terms",
        "get_budget",
        "get_storage_capacity",
        "get_planning_policy",
        "get_purchase_order",
        "list_supplier_options",
    ],
    "demand-change": [
        "get_inventory",
        "get_demand_forecast",
        "list_open_purchase_orders",
        "get_supplier_terms",
        "get_budget",
        "get_storage_capacity",
        "get_planning_policy",
        "get_recent_sales",
        "list_supplier_options",
    ],
}


def complete_mandatory_manifest(
    session: Session,
    dispatcher: ToolDispatcher,
    scenario_type: str,
    product_id: str,
    node_id: str,
    preferred_supplier_id: str,
    po_id: str | None = None,
    collected_tools: dict[str, ToolResponse] | None = None,
) -> dict[str, ToolResponse]:
    """Auto-fetches any omitted safety-critical facts deterministically before decision."""
    results = dict(collected_tools or {})
    manifest = MANDATORY_MANIFESTS.get(scenario_type, MANDATORY_MANIFESTS["recommendation-review"])

    args_by_tool = {
        "get_inventory": {"product_id": product_id, "node_id": node_id},
        "get_demand_forecast": {"product_id": product_id, "node_id": node_id},
        "list_open_purchase_orders": {"product_id": product_id, "node_id": node_id},
        "get_supplier_terms": {"supplier_id": preferred_supplier_id, "product_id": product_id},
        "get_budget": {"node_id": node_id},
        "get_storage_capacity": {"node_id": node_id},
        "get_planning_policy": {"product_id": product_id, "node_id": node_id},
        "get_purchase_order": {"purchase_order_id": po_id or ""},
        "list_supplier_options": {"product_id": product_id, "node_id": node_id},
        "get_recent_sales": {"product_id": product_id, "node_id": node_id, "hours": 24},
    }

    for req_tool in manifest:
        # If missing or failed previously, invoke via mandatory guard
        if req_tool not in results or not results[req_tool].ok:
            args = args_by_tool.get(req_tool, {})
            # Skip get_purchase_order if no po_id is provided
            if req_tool == "get_purchase_order" and not po_id:
                continue
            resp = dispatcher.dispatch(
                tool_name=req_tool,
                args=args,
                round_number=99,  # Distinct round number for manifest guard
                provider="mandatory-manifest-guard",
                model="deterministic",
            )
            results[req_tool] = resp

    return results


# ---------------------------------------------------------------------------
# Approved Action Execution Tool (ADR 0003 & Spec 06 §3)
# ---------------------------------------------------------------------------


def tool_execute_approved_proposal(
    session: Session,
    proposal_id: str,
    review_id: str,
    actor_id: str | None = None,
) -> tuple[bool, list[PurchaseOrder], list[ValidationResult]]:
    """Execute ONLY the current buyer-approved immutable proposal.

    The function receives no commercial fields. It reloads plan lines and only
    accepts an approval that explicitly names this proposal.
    """
    proposal = session.get(PurchaseProposal, proposal_id)
    if not proposal or proposal.review_id != review_id:
        raise ValueError(f"Proposal '{proposal_id}' is invalid or does not belong to this review.")

    approval = session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.review_id == review_id,
            ApprovalRequest.proposal_id == proposal_id,
            ApprovalRequest.proposal_version == proposal.proposal_version,
            ApprovalRequest.status == "APPROVED",
        )
    )
    if proposal.status != "APPROVED" or not approval:
        raise ValueError(f"Proposal '{proposal_id}' cannot be executed in status '{proposal.status}'.")

    now = datetime.now(UTC)
    created_pos: list[PurchaseOrder] = []
    validations: list[ValidationResult] = []

    plan = session.get(SourcingPlan, proposal.sourcing_plan_id) if proposal.sourcing_plan_id else None
    if not plan:
        raise ValueError("Approved proposal has no immutable sourcing plan.")
    lines = session.scalars(
        select(SourcingPlanLine).where(SourcingPlanLine.sourcing_plan_id == plan.id).order_by(SourcingPlanLine.sequence)
    ).all()
    if not lines:
        raise ValueError("Approved proposal has no executable plan lines.")
    for line in lines:
        po = session.scalar(select(PurchaseOrder).where(PurchaseOrder.idempotency_key == line.idempotency_key))
        if not po:
            po = PurchaseOrder(
                external_id=f"PO-{line.idempotency_key[:8]}-{line.sequence}", supplier_id=line.supplier_id,
                node_id=plan.node_id, status="OPEN", currency=plan.currency,
                total_minor=line.total_cost_minor, expected_delivery_at=line.expected_delivery_at or now,
                idempotency_key=line.idempotency_key, created_at=now, updated_at=now,
            )
            session.add(po)
            session.flush()
            session.add(PurchaseOrderItem(
                purchase_order_id=po.id, product_id=plan.product_id, ordered_quantity=line.quantity,
                confirmed_quantity=0, received_quantity=0, unit_cost_minor=line.unit_cost_minor,
            ))
            session.flush()
        attempt = session.scalar(select(ActionAttempt).where(ActionAttempt.idempotency_key == line.idempotency_key))
        if not attempt:
            attempt = ActionAttempt(
                review_id=review_id, decision_id=proposal.decision_id, sourcing_plan_line_id=line.id,
                attempt_number=1, action_type="CREATE_PO", idempotency_key=line.idempotency_key,
                request_json={"supplier_id": line.supplier_id, "quantity": line.quantity, "total_minor": line.total_cost_minor},
                response_json={
                    "purchase_order_id": po.id,
                    "external_id": po.external_id,
                    "total_minor": po.total_minor,
                    "currency": po.currency,
                },
                status="SUCCEEDED",
                started_at=now, completed_at=now,
            )
            session.add(attempt)
            session.flush()
        created_pos.append(po)
    proposal.status = "EXECUTED"
    session.flush()
    return True, created_pos, validations
