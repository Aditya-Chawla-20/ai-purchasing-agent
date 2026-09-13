"""Deterministic purchase recommendation policy."""

from __future__ import annotations

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
