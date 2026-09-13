"""Deterministic purchase recommendation policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DecisionType(StrEnum):
    ACCEPT = "ACCEPT"
    MODIFY = "MODIFY"
    REJECT = "REJECT"
    INVESTIGATE = "INVESTIGATE"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EvidenceStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"


class EvidenceFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    status: EvidenceStatus = EvidenceStatus.FRESH
    source: str
    observed_at: str
    effective_from: str | None = None
    effective_until: str | None = None
    value: Any = None
    version: str | None = None


class PurchaseInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    original_quantity: int = Field(ge=0)
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    damaged: int = Field(ge=0)
    confirmed_incoming: int = Field(ge=0)
    forecast_demand: int = Field(ge=0)
    safety_stock: int = Field(ge=0)
    unit_cost_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    budget_available_minor: int = Field(ge=0)
    minimum_order_quantity: int = Field(gt=0)
    available_supplier_quantity: int | None = Field(default=None, ge=0)
    available_storage_volume: int = Field(ge=0)
    product_unit_volume: int = Field(gt=0)
    evidence: tuple[EvidenceFact, ...] = ()
    evidence_complete: bool = True
    advisory_warnings: tuple[str, ...] = ()


class ConstraintResult(BaseModel):
    code: str
    passed: bool
    severity: str = "hard"
    observed: int | str | None = None
    limit: int | str | None = None


class PurchaseDecision(BaseModel):
    decision: DecisionType
    original_quantity: int
    proposed_quantity: int
    usable_on_hand: int
    inventory_position: int
    target_stock: int
    raw_need: int
    budget_units: int
    storage_units: int
    supplier_units: int | None
    total_cost_minor: int
    currency: str
    confidence: Confidence
    reason_codes: tuple[str, ...]
    constraints: tuple[ConstraintResult, ...]
    evidence: tuple[EvidenceFact, ...]


def calculate_decision(
    inputs: PurchaseInputs, budget_currency: str | None = None
) -> PurchaseDecision:
    """Calculate an explainable purchase outcome without model or I/O dependencies."""
    if budget_currency and budget_currency.upper() != inputs.currency.upper():
        return _investigate(inputs, "CURRENCY_MISMATCH")
    deficient = [fact for fact in inputs.evidence if fact.status != EvidenceStatus.FRESH]
    if deficient:
        status_code = {
            EvidenceStatus.STALE: "STALE_REQUIRED_EVIDENCE",
            EvidenceStatus.MISSING: "MISSING_REQUIRED_EVIDENCE",
            EvidenceStatus.CONFLICTING: "CONFLICTING_EVIDENCE",
        }
        return _investigate(inputs, status_code[deficient[0].status])
    if not inputs.evidence_complete:
        return _investigate(inputs, "MISSING_REQUIRED_EVIDENCE")

    usable = max(0, inputs.on_hand - inputs.reserved - inputs.damaged)
    inventory_position = usable + inputs.confirmed_incoming
    target = inputs.forecast_demand + inputs.safety_stock
    need = max(0, target - inventory_position)
    budget_units = inputs.budget_available_minor // inputs.unit_cost_minor
    storage_units = inputs.available_storage_volume // inputs.product_unit_volume
    supplier_units = inputs.available_supplier_quantity
    hard_limits = [budget_units, storage_units]
    if supplier_units is not None:
        hard_limits.append(supplier_units)
    non_need_cap = min(hard_limits)

    feasible = 0
    if need > 0 and non_need_cap >= inputs.minimum_order_quantity:
        rounded_up = (
            (need + inputs.minimum_order_quantity - 1) // inputs.minimum_order_quantity
        ) * inputs.minimum_order_quantity
        if rounded_up <= non_need_cap:
            feasible = rounded_up
        else:
            feasible = (
                non_need_cap // inputs.minimum_order_quantity
            ) * inputs.minimum_order_quantity

    reasons: list[str] = []
    if need == 0:
        decision = DecisionType.REJECT
        reasons.append("NO_NET_REQUIREMENT")
    elif feasible == 0:
        decision = DecisionType.INVESTIGATE
        if budget_units < inputs.minimum_order_quantity:
            reasons.extend(["BUDGET_LIMITED", "BELOW_MOQ_NO_FEASIBLE_ORDER"])
        if storage_units < inputs.minimum_order_quantity:
            reasons.extend(["STORAGE_LIMITED", "BELOW_MOQ_NO_FEASIBLE_ORDER"])
        if supplier_units is not None and supplier_units < inputs.minimum_order_quantity:
            reasons.extend(["SUPPLIER_AVAILABILITY_LIMITED", "BELOW_MOQ_NO_FEASIBLE_ORDER"])
        if not reasons:
            reasons.append("BELOW_MOQ_NO_FEASIBLE_ORDER")
    elif feasible == inputs.original_quantity:
        decision = DecisionType.ACCEPT
        reasons.append("NEED_MATCHES_ORIGINAL")
    else:
        decision = DecisionType.MODIFY
        reasons.append(
            "NEED_LOWER_THAN_ORIGINAL"
            if feasible < inputs.original_quantity
            else "MOQ_ROUNDING_APPLIED"
        )

    if inputs.confirmed_incoming:
        reasons.append("OPEN_PO_REDUCES_NEED")
    if feasible > need:
        reasons.append("MOQ_ROUNDING_APPLIED")
    if budget_units < need:
        reasons.append("BUDGET_LIMITED")
    if storage_units < need:
        reasons.append("STORAGE_LIMITED")
    if supplier_units is not None and supplier_units < need:
        reasons.append("SUPPLIER_AVAILABILITY_LIMITED")
    total_cost = feasible * inputs.unit_cost_minor
    constraints = (
        ConstraintResult(code="EVIDENCE_COMPLETE", passed=True),
        ConstraintResult(code="POSITIVE_UNIT_COST", passed=True, observed=inputs.unit_cost_minor),
        ConstraintResult(code="INTEGER_QUANTITY", passed=True, observed=feasible),
        ConstraintResult(
            code="MOQ_SATISFIED",
            passed=feasible == 0
            or feasible >= inputs.minimum_order_quantity
            and feasible % inputs.minimum_order_quantity == 0,
            observed=feasible,
            limit=inputs.minimum_order_quantity,
        ),
        ConstraintResult(
            code="WITHIN_BUDGET",
            passed=total_cost <= inputs.budget_available_minor,
            observed=total_cost,
            limit=inputs.budget_available_minor,
        ),
        ConstraintResult(
            code="WITHIN_STORAGE",
            passed=feasible <= storage_units,
            observed=feasible,
            limit=storage_units,
        ),
        ConstraintResult(
            code="WITHIN_SUPPLIER_AVAILABILITY",
            passed=supplier_units is None or feasible <= supplier_units,
            observed=feasible,
            limit=supplier_units,
        ),
        ConstraintResult(
            code="NEED_JUSTIFIED",
            passed=feasible <= need
            or (
                feasible
                == ((need + inputs.minimum_order_quantity - 1) // inputs.minimum_order_quantity)
                * inputs.minimum_order_quantity
                and feasible <= non_need_cap
            ),
            observed=feasible,
            limit=need,
        ),
    )
    confidence = Confidence.MEDIUM if inputs.advisory_warnings else Confidence.HIGH
    if decision == DecisionType.INVESTIGATE:
        confidence = Confidence.LOW
    return PurchaseDecision(
        decision=decision,
        original_quantity=inputs.original_quantity,
        proposed_quantity=feasible,
        usable_on_hand=usable,
        inventory_position=inventory_position,
        target_stock=target,
        raw_need=need,
        budget_units=budget_units,
        storage_units=storage_units,
        supplier_units=supplier_units,
        total_cost_minor=total_cost,
        currency=inputs.currency.upper(),
        confidence=confidence,
        reason_codes=tuple(dict.fromkeys(reasons)),
        constraints=constraints,
        evidence=inputs.evidence,
    )


def _investigate(inputs: PurchaseInputs, reason: str) -> PurchaseDecision:
    return PurchaseDecision(
        decision=DecisionType.INVESTIGATE,
        original_quantity=inputs.original_quantity,
        proposed_quantity=0,
        usable_on_hand=max(0, inputs.on_hand - inputs.reserved - inputs.damaged),
        inventory_position=0,
        target_stock=0,
        raw_need=0,
        budget_units=0,
        storage_units=0,
        supplier_units=inputs.available_supplier_quantity,
        total_cost_minor=0,
        currency=inputs.currency.upper(),
        confidence=Confidence.LOW,
        reason_codes=(reason,),
        constraints=(ConstraintResult(code=reason, passed=False),),
        evidence=inputs.evidence,
    )


# ---------------------------------------------------------------------------
# Multi-Supplier Allocation (Spec 05 §8)
# ---------------------------------------------------------------------------


class SupplierCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    supplier_id: str
    supplier_code: str
    supplier_name: str
    active: bool = True
    reliability_score_bps: int = Field(ge=0, le=10000)
    unit_cost_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    minimum_order_quantity: int = Field(gt=0)
    lead_time_days: int = Field(ge=0)
    max_available_quantity: int | None = Field(default=None, ge=0)
    terms_version: str = "1"
    expected_delivery_at: str | None = None
    terms_fresh: bool = True


class SourcingPlanLineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    supplier_id: str
    supplier_code: str
    supplier_name: str
    sequence: int
    quantity: int
    unit_cost_minor: int
    total_cost_minor: int
    minimum_order_quantity: int
    reliability_score_bps: int
    expected_delivery_at: str | None = None
    inclusion_reason_codes: tuple[str, ...] = ()


class SourcingOptionAssessmentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    supplier_id: str
    supplier_code: str
    eligible: bool
    exclusion_reason_codes: tuple[str, ...] = ()


class MultiSupplierPlanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw_need: int
    covered_quantity: int
    total_cost_minor: int
    weighted_reliability_bps: int
    status: str  # COMPLETE, PARTIAL, INFEASIBLE
    is_complete: bool
    lines: tuple[SourcingPlanLineResult, ...] = ()
    assessments: tuple[SourcingOptionAssessmentResult, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AllocationState:
    quantities: tuple[int, ...]
    quantity: int
    reliability_numerator: int
    cost_minor: int
    volume: int
    line_count: int
    latest_delivery: str
    supplier_ids: tuple[str, ...]


_MAX_ALLOCATION_OPTIONS_PER_SUPPLIER = 1_000
_MAX_ALLOCATION_FRONTIER_STATES = 20_000


def _allocation_rank(state: _AllocationState, raw_need: int) -> tuple:
    return (
        -min(state.quantity, raw_need),
        -state.reliability_numerator,
        state.cost_minor,
        state.line_count,
        state.latest_delivery,
        state.supplier_ids,
    )


def _dominates(left: _AllocationState, right: _AllocationState) -> bool:
    """Return whether left can never lose to right after the same candidate stage."""
    return (
        left.reliability_numerator >= right.reliability_numerator
        and left.cost_minor <= right.cost_minor
        and left.volume <= right.volume
        and left.line_count <= right.line_count
        and left.latest_delivery <= right.latest_delivery
        and (
            left.reliability_numerator > right.reliability_numerator
            or left.cost_minor < right.cost_minor
            or left.volume < right.volume
            or left.line_count < right.line_count
            or left.latest_delivery < right.latest_delivery
            or left.supplier_ids <= right.supplier_ids
        )
    )


def allocate_sourcing_plan(
    raw_need: int,
    candidates: list[SupplierCandidate],
    budget_available_minor: int,
    available_storage_volume: int,
    product_unit_volume: int,
    target_currency: str,
    need_by_at: str | None = None,
) -> MultiSupplierPlanResult:
    """Deterministically allocate a multi-supplier plan optimizing coverage then reliability."""
    assessments: list[SourcingOptionAssessmentResult] = []
    eligible_candidates: list[SupplierCandidate] = []

    storage_units = (
        available_storage_volume // product_unit_volume if product_unit_volume > 0 else 0
    )

    for cand in candidates:
        exclusions: list[str] = []
        if not cand.active:
            exclusions.append("SUPPLIER_INACTIVE")
        if not cand.terms_fresh:
            exclusions.append("STALE_SUPPLIER_TERMS")
        if cand.currency.upper() != target_currency.upper():
            exclusions.append("CURRENCY_MISMATCH")
        if cand.max_available_quantity is not None and cand.max_available_quantity < cand.minimum_order_quantity:
            exclusions.append("INSUFFICIENT_SUPPLIER_AVAILABILITY")
        if need_by_at and cand.expected_delivery_at and cand.expected_delivery_at > need_by_at:
            exclusions.append("DELIVERY_PAST_NEED_BY")
        if cand.minimum_order_quantity * cand.unit_cost_minor > budget_available_minor:
            exclusions.append("BUDGET_EXCEEDED_AT_MOQ")
        if cand.minimum_order_quantity > storage_units:
            exclusions.append("STORAGE_EXCEEDED_AT_MOQ")

        is_eligible = len(exclusions) == 0
        assessments.append(
            SourcingOptionAssessmentResult(
                supplier_id=cand.supplier_id,
                supplier_code=cand.supplier_code,
                eligible=is_eligible,
                exclusion_reason_codes=tuple(exclusions),
            )
        )
        if is_eligible:
            eligible_candidates.append(cand)

    if raw_need <= 0:
        return MultiSupplierPlanResult(
            raw_need=0,
            covered_quantity=0,
            total_cost_minor=0,
            weighted_reliability_bps=10000,
            status="COMPLETE",
            is_complete=True,
            assessments=tuple(assessments),
            reasons=("NO_NET_REQUIREMENT",),
        )

    if not eligible_candidates:
        return MultiSupplierPlanResult(
            raw_need=raw_need,
            covered_quantity=0,
            total_cost_minor=0,
            weighted_reliability_bps=0,
            status="INFEASIBLE",
            is_complete=False,
            assessments=tuple(assessments),
            reasons=("NO_ELIGIBLE_SUPPLIERS",),
        )

    # Sort eligible candidates by supplier_id for stable iteration
    eligible_candidates.sort(key=lambda c: c.supplier_id)
    max_moq = max(c.minimum_order_quantity for c in eligible_candidates)
    max_search_quantity = raw_need + max_moq - 1

    frontier: dict[int, list[_AllocationState]] = {
        0: [_AllocationState((), 0, 0, 0, 0, 0, "", ())]
    }
    complexity_limited = False
    for candidate in eligible_candidates:
        next_frontier: dict[int, list[_AllocationState]] = {}
        next_frontier_count = 0
        for state in (item for bucket in frontier.values() for item in bucket):
            max_for_candidate = max_search_quantity - state.quantity
            if candidate.max_available_quantity is not None:
                max_for_candidate = min(max_for_candidate, candidate.max_available_quantity)
            option_count = max_for_candidate // candidate.minimum_order_quantity
            if option_count > _MAX_ALLOCATION_OPTIONS_PER_SUPPLIER:
                complexity_limited = True
                break
            for quantity in range(0, max_for_candidate + 1, candidate.minimum_order_quantity):
                cost = state.cost_minor + quantity * candidate.unit_cost_minor
                volume = state.volume + quantity * product_unit_volume
                if cost > budget_available_minor or volume > available_storage_volume:
                    break
                effective = min(quantity, max(0, raw_need - state.quantity))
                next_state = _AllocationState(
                    quantities=state.quantities + (quantity,),
                    quantity=state.quantity + quantity,
                    reliability_numerator=state.reliability_numerator + effective * candidate.reliability_score_bps,
                    cost_minor=cost,
                    volume=volume,
                    line_count=state.line_count + int(quantity > 0),
                    latest_delivery=max(state.latest_delivery, candidate.expected_delivery_at or "") if quantity else state.latest_delivery,
                    supplier_ids=state.supplier_ids + ((candidate.supplier_id,) if quantity else ()),
                )
                bucket = next_frontier.setdefault(next_state.quantity, [])
                if any(_dominates(existing, next_state) for existing in bucket):
                    continue
                dominated_count = sum(
                    _dominates(next_state, existing) for existing in bucket
                )
                if dominated_count:
                    bucket[:] = [existing for existing in bucket if not _dominates(next_state, existing)]
                    next_frontier_count -= dominated_count
                bucket.append(next_state)
                next_frontier_count += 1
                if next_frontier_count > _MAX_ALLOCATION_FRONTIER_STATES:
                    complexity_limited = True
                    break
            if complexity_limited:
                break
        if complexity_limited:
            break
        frontier = next_frontier

    if complexity_limited:
        return MultiSupplierPlanResult(
            raw_need=raw_need,
            covered_quantity=0,
            total_cost_minor=0,
            weighted_reliability_bps=0,
            status="INFEASIBLE",
            is_complete=False,
            assessments=tuple(assessments),
            reasons=("ALLOCATION_COMPLEXITY_LIMIT_REACHED",),
        )

    feasible_states = [item for bucket in frontier.values() for item in bucket if item.quantity > 0]
    if not feasible_states:
        return MultiSupplierPlanResult(
            raw_need=raw_need,
            covered_quantity=0,
            total_cost_minor=0,
            weighted_reliability_bps=0,
            status="INFEASIBLE",
            is_complete=False,
            assessments=tuple(assessments),
            reasons=("NO_FEASIBLE_ALLOCATION_WITHIN_CONSTRAINTS",),
        )

    best = min(feasible_states, key=lambda state: _allocation_rank(state, raw_need))
    best_quantities = best.quantities
    best_covered = min(best.quantity, raw_need)
    best_rel_num = best.reliability_numerator
    best_cost = best.cost_minor
    is_complete = best_covered >= raw_need
    weighted_rel = best_rel_num // raw_need if raw_need > 0 else 10000

    plan_lines: list[SourcingPlanLineResult] = []
    seq = 1
    for idx, qty in enumerate(best_quantities):
        if qty > 0:
            cand = eligible_candidates[idx]
            plan_lines.append(
                SourcingPlanLineResult(
                    supplier_id=cand.supplier_id,
                    supplier_code=cand.supplier_code,
                    supplier_name=cand.supplier_name,
                    sequence=seq,
                    quantity=qty,
                    unit_cost_minor=cand.unit_cost_minor,
                    total_cost_minor=qty * cand.unit_cost_minor,
                    minimum_order_quantity=cand.minimum_order_quantity,
                    reliability_score_bps=cand.reliability_score_bps,
                    expected_delivery_at=cand.expected_delivery_at,
                    inclusion_reason_codes=("ALLOCATED_BY_RELIABILITY_AND_CAPACITY",),
                )
            )
            seq += 1

    status = "COMPLETE" if is_complete else "PARTIAL"
    reasons = ("MULTI_SUPPLIER_ALLOCATION_SUCCESSFUL",) if is_complete else ("PARTIAL_ALLOCATION_ONLY",)

    return MultiSupplierPlanResult(
        raw_need=raw_need,
        covered_quantity=best_covered,
        total_cost_minor=best_cost,
        weighted_reliability_bps=weighted_rel,
        status=status,
        is_complete=is_complete,
        lines=tuple(plan_lines),
        assessments=tuple(assessments),
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Demand Spike Evaluation (Spec 05 §9)
# ---------------------------------------------------------------------------


class DemandSpikeInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    sales_window_hours: int = Field(ge=1)
    units_sold: int = Field(ge=0)
    baseline_forecast_demand: int = Field(ge=0)
    baseline_forecast_hours: int = Field(ge=1)
    revised_forecast_demand: int = Field(ge=0)
    revised_forecast_hours: int = Field(ge=1)
    threshold_bps: int = 15000  # 1.5x default
    minimum_sales_observation_hours: int = 24
    usable_on_hand: int = 0
    eligible_incoming: int = 0
    safety_stock: int = 0
    sales_fresh: bool = True
    forecast_fresh: bool = True
    baseline_forecast_id: str | None = None
    revised_forecast_id: str | None = None


class DemandSpikeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    spike_detected: bool
    ratio_bps: int
    threshold_bps: int
    status: str  # NO_CHANGE, COVERAGE_SUFFICIENT, SUPPLEMENTAL_PLAN_REQUIRED, INVESTIGATE
    supplemental_need: int
    target_stock: int
    inventory_position: int
    reasons: tuple[str, ...] = ()


def evaluate_demand_spike(inputs: DemandSpikeInputs) -> DemandSpikeResult:
    """Evaluate demand changes using exact integer cross-multiplication without floating point."""
    if not inputs.sales_fresh:
        return DemandSpikeResult(
            spike_detected=False,
            ratio_bps=0,
            threshold_bps=inputs.threshold_bps,
            status="INVESTIGATE",
            supplemental_need=0,
            target_stock=0,
            inventory_position=0,
            reasons=("STALE_SALES_EVIDENCE",),
        )

    if not inputs.forecast_fresh:
        return DemandSpikeResult(
            spike_detected=False,
            ratio_bps=0,
            threshold_bps=inputs.threshold_bps,
            status="INVESTIGATE",
            supplemental_need=0,
            target_stock=0,
            inventory_position=0,
            reasons=("STALE_FORECAST_EVIDENCE",),
        )

    if inputs.sales_window_hours < inputs.minimum_sales_observation_hours:
        return DemandSpikeResult(
            spike_detected=False,
            ratio_bps=0,
            threshold_bps=inputs.threshold_bps,
            status="INVESTIGATE",
            supplemental_need=0,
            target_stock=0,
            inventory_position=0,
            reasons=("INSUFFICIENT_SALES_OBSERVATION_WINDOW",),
        )

    if inputs.baseline_forecast_demand == 0 and inputs.units_sold > 0:
        return DemandSpikeResult(
            spike_detected=False,
            ratio_bps=0,
            threshold_bps=inputs.threshold_bps,
            status="INVESTIGATE",
            supplemental_need=0,
            target_stock=0,
            inventory_position=0,
            reasons=("ZERO_BASELINE_DEMAND",),
        )

    if inputs.units_sold <= 0 or inputs.baseline_forecast_demand <= 0:
        return DemandSpikeResult(
            spike_detected=False,
            ratio_bps=0,
            threshold_bps=inputs.threshold_bps,
            status="NO_CHANGE",
            supplemental_need=0,
            target_stock=0,
            inventory_position=0,
            reasons=("NO_SALES_VELOCITY",),
        )

    # Integer cross-multiplication:
    # actual_rate = units_sold / sales_window_hours
    # baseline_rate = baseline_forecast_demand / baseline_forecast_hours
    # ratio = (units_sold * baseline_forecast_hours) / (baseline_forecast_demand * sales_window_hours)
    actual_numerator = inputs.units_sold * inputs.baseline_forecast_hours * 10000
    baseline_denominator = inputs.baseline_forecast_demand * inputs.sales_window_hours
    ratio_bps = actual_numerator // baseline_denominator

    spike_detected = ratio_bps >= inputs.threshold_bps

    if not spike_detected:
        return DemandSpikeResult(
            spike_detected=False,
            ratio_bps=ratio_bps,
            threshold_bps=inputs.threshold_bps,
            status="NO_CHANGE",
            supplemental_need=0,
            target_stock=0,
            inventory_position=0,
            reasons=("DEMAND_SPIKE_THRESHOLD_NOT_MET",),
        )

    # Spike confirmed: calculate target stock and supplemental need
    target_stock = inputs.revised_forecast_demand + inputs.safety_stock
    inventory_position = inputs.usable_on_hand + inputs.eligible_incoming
    supplemental_need = max(0, target_stock - inventory_position)

    if supplemental_need == 0:
        status = "COVERAGE_SUFFICIENT"
        reasons = ("DEMAND_SPIKE_CONFIRMED_COVERAGE_SUFFICIENT",)
    else:
        status = "SUPPLEMENTAL_PLAN_REQUIRED"
        reasons = ("DEMAND_SPIKE_CONFIRMED_SUPPLEMENTAL_PLAN_REQUIRED",)

    return DemandSpikeResult(
        spike_detected=True,
        ratio_bps=ratio_bps,
        threshold_bps=inputs.threshold_bps,
        status=status,
        supplemental_need=supplemental_need,
        target_stock=target_stock,
        inventory_position=inventory_position,
        reasons=reasons,
    )
