"""Application service for multi-supplier candidate evaluation and sourcing plan persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from purchasing.domain.policy import (
    MultiSupplierPlanResult,
    SupplierCandidate,
    allocate_sourcing_plan,
)
from purchasing.infrastructure.models import (
    PurchaseProposal,
    SourcingOptionAssessment,
    SourcingPlan,
    SourcingPlanLine,
    Supplier,
    SupplierProduct,
)


def get_supplier_candidates(
    session: Session, product_id: str, node_id: str, currency: str = "INR"
) -> list[SupplierCandidate]:
    now = datetime.now(UTC)
    rows = session.execute(
        select(Supplier, SupplierProduct)
        .join(SupplierProduct, SupplierProduct.supplier_id == Supplier.id)
        .where(SupplierProduct.product_id == product_id)
    ).all()

    candidates: list[SupplierCandidate] = []
    for sup, terms in rows:
        deliv = (now + timedelta(days=terms.lead_time_days)).isoformat()
        is_fresh = (now - terms.observed_at.replace(tzinfo=UTC)) < timedelta(hours=24)
        candidates.append(
            SupplierCandidate(
                supplier_id=sup.id,
                supplier_code=sup.code,
                supplier_name=sup.name,
                active=sup.active,
                reliability_score_bps=sup.reliability_score_bps,
                unit_cost_minor=terms.unit_cost_minor,
                currency=terms.currency,
                minimum_order_quantity=terms.minimum_order_quantity,
                lead_time_days=terms.lead_time_days,
                max_available_quantity=terms.max_available_quantity,
                terms_version=terms.terms_version,
                expected_delivery_at=deliv,
                terms_fresh=is_fresh,
            )
        )
    return candidates


def create_and_persist_sourcing_plan(
    session: Session,
    review_id: str,
    decision_id: str,
    snapshot_id: str,
    snapshot_hash: str,
    product_id: str,
    node_id: str,
    currency: str,
    raw_need: int,
    budget_available_minor: int,
    available_storage_volume: int,
    product_unit_volume: int,
    need_by_at: str | None = None,
    proposal_version: int = 1,
    preferred_supplier_id: str | None = None,
    alternate_sourcing_required: bool = False,
) -> tuple[SourcingPlan, MultiSupplierPlanResult, PurchaseProposal]:
    candidates = get_supplier_candidates(session, product_id, node_id, currency)
    
    # Spec 05 §8: Allocation runs only after the single-supplier policy cannot
    # cover a positive justified need or a recovery/demand-change review explicitly requests alternate sourcing.
    candidates_to_use = candidates
    if preferred_supplier_id and not alternate_sourcing_required:
        pref = next((c for c in candidates if c.supplier_id == preferred_supplier_id), None)
        if (
            pref is not None
            and pref.active
            and pref.terms_fresh
            and (pref.max_available_quantity is None or pref.max_available_quantity >= raw_need)
            and raw_need >= pref.minimum_order_quantity
        ):
            candidates_to_use = [pref]

    plan_result = allocate_sourcing_plan(
        raw_need=raw_need,
        candidates=candidates_to_use,
        budget_available_minor=budget_available_minor,
        available_storage_volume=available_storage_volume,
        product_unit_volume=product_unit_volume,
        target_currency=currency,
        need_by_at=need_by_at,
    )

    now = datetime.now(UTC)
    need_by_dt = datetime.fromisoformat(need_by_at) if need_by_at else None

    # Persist SourcingPlan
    sourcing_plan = SourcingPlan(
        review_id=review_id,
        decision_id=decision_id,
        evidence_snapshot_id=snapshot_id,
        version=proposal_version,
        product_id=product_id,
        node_id=node_id,
        currency=currency,
        raw_need=raw_need,
        total_quantity=plan_result.covered_quantity,
        total_cost_minor=plan_result.total_cost_minor,
        need_by_at=need_by_dt,
        allocation_policy_version="v1",
        status=plan_result.status,
        created_at=now,
    )
    session.add(sourcing_plan)
    session.flush()

    # Persist Assessments
    for asm in plan_result.assessments:
        assessment_row = SourcingOptionAssessment(
            sourcing_plan_id=sourcing_plan.id,
            supplier_id=asm.supplier_id,
            eligible=asm.eligible,
            exclusion_reason_codes_json=list(asm.exclusion_reason_codes),
            normalized_terms_json={},
        )
        session.add(assessment_row)

    # Persist Lines
    is_single_line = len(plan_result.lines) == 1
    for line in plan_result.lines:
        line_deliv = datetime.fromisoformat(line.expected_delivery_at) if line.expected_delivery_at else None
        idempotency_key = (
            f"{review_id}:create-po:v{proposal_version}"
            if is_single_line
            else f"{review_id}:create-po:v{proposal_version}:{line.supplier_id}"
        )
        line_row = SourcingPlanLine(
            sourcing_plan_id=sourcing_plan.id,
            supplier_id=line.supplier_id,
            sequence=line.sequence,
            quantity=line.quantity,
            unit_cost_minor=line.unit_cost_minor,
            total_cost_minor=line.total_cost_minor,
            minimum_order_quantity=line.minimum_order_quantity,
            reliability_score_bps=line.reliability_score_bps,
            expected_delivery_at=line_deliv,
            inclusion_reason_codes_json=list(line.inclusion_reason_codes),
            idempotency_key=idempotency_key,
        )
        session.add(line_row)

    # Persist PurchaseProposal
    proposal = PurchaseProposal(
        review_id=review_id,
        decision_id=decision_id,
        sourcing_plan_id=sourcing_plan.id,
        proposal_version=proposal_version,
        evidence_snapshot_hash=snapshot_hash,
        status="PENDING",
        created_at=now,
    )
    session.add(proposal)
    session.flush()

    return sourcing_plan, plan_result, proposal
