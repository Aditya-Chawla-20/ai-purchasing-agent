from purchasing.domain.policy import (
    DecisionType,
    EvidenceFact,
    EvidenceStatus,
    PurchaseInputs,
    calculate_decision,
)


def inputs(**overrides):
    values = {
        "original_quantity": 800,
        "on_hand": 180,
        "reserved": 20,
        "damaged": 10,
        "confirmed_incoming": 100,
        "forecast_demand": 700,
        "safety_stock": 50,
        "unit_cost_minor": 1250,
        "currency": "INR",
        "budget_available_minor": 600_000,
        "minimum_order_quantity": 50,
        "available_supplier_quantity": 500,
        "available_storage_volume": 4500,
        "product_unit_volume": 10,
        "evidence": (
            EvidenceFact(
                name="inventory", source="test", observed_at="2026-09-13T00:00:00Z", value=1
            ),
        ),
    }
    values.update(overrides)
    return PurchaseInputs(**values)


def test_modify_800_recommendation_to_450_for_open_po_budget_and_storage():
    decision = calculate_decision(inputs())
    assert decision.decision == DecisionType.MODIFY
    assert decision.proposed_quantity == 450
    assert decision.inventory_position == 250
    assert decision.raw_need == 500
    assert "OPEN_PO_REDUCES_NEED" in decision.reason_codes
    assert all(check.passed for check in decision.constraints)


def test_accept_when_recommended_quantity_is_exact_need():
    case = inputs(
        original_quantity=160,
        on_hand=60,
        reserved=0,
        damaged=0,
        confirmed_incoming=0,
        forecast_demand=200,
        safety_stock=20,
        minimum_order_quantity=10,
        available_supplier_quantity=300,
        available_storage_volume=8000,
    )
    assert calculate_decision(case).decision == DecisionType.ACCEPT


def test_reject_when_inventory_position_covers_target():
    case = inputs(
        original_quantity=50,
        on_hand=500,
        reserved=20,
        damaged=0,
        confirmed_incoming=0,
        forecast_demand=350,
        safety_stock=10,
    )
    result = calculate_decision(case)
    assert result.decision == DecisionType.REJECT
    assert result.proposed_quantity == 0
    assert "NO_NET_REQUIREMENT" in result.reason_codes


def test_stale_evidence_requires_investigation():
    case = inputs(
        evidence=(
            EvidenceFact(
                name="forecast",
                status=EvidenceStatus.STALE,
                source="test",
                observed_at="2020-01-01T00:00:00Z",
            ),
        )
    )
    assert calculate_decision(case).decision == DecisionType.INVESTIGATE


def test_conflicting_inventory_requires_investigation_instead_of_clamping_stock():
    case = inputs(
        evidence=(
            EvidenceFact(
                name="inventory",
                status=EvidenceStatus.CONFLICTING,
                source="test",
                observed_at="2026-09-13T00:00:00Z",
            ),
        )
    )
    decision = calculate_decision(case)
    assert decision.decision == DecisionType.INVESTIGATE
    assert decision.reason_codes == ("CONFLICTING_EVIDENCE",)


def test_currency_mismatch_requires_investigation():
    assert calculate_decision(inputs(), budget_currency="USD").reason_codes == (
        "CURRENCY_MISMATCH",
    )


def test_budget_and_storage_below_moq_do_not_break_hard_limits():
    case = inputs(budget_available_minor=20_000, available_storage_volume=300)
    decision = calculate_decision(case)
    assert decision.decision == DecisionType.INVESTIGATE
    assert decision.proposed_quantity == 0
    assert "BUDGET_LIMITED" in decision.reason_codes
    assert "STORAGE_LIMITED" in decision.reason_codes


def test_moq_rounding_is_allowed_only_within_all_hard_limits():
    case = inputs(
        original_quantity=101,
        on_hand=0,
        reserved=0,
        damaged=0,
        confirmed_incoming=0,
        forecast_demand=101,
        safety_stock=0,
        minimum_order_quantity=50,
        budget_available_minor=200_000,
        available_storage_volume=2000,
        product_unit_volume=10,
        available_supplier_quantity=150,
    )
    decision = calculate_decision(case)
    assert decision.proposed_quantity == 150
    assert "MOQ_ROUNDING_APPLIED" in decision.reason_codes
    assert next(c for c in decision.constraints if c.code == "NEED_JUSTIFIED").passed


def test_supplier_capacity_is_explained_when_it_caps_a_feasible_order():
    decision = calculate_decision(
        inputs(
            original_quantity=600,
            on_hand=0,
            reserved=0,
            damaged=0,
            confirmed_incoming=0,
            forecast_demand=600,
            safety_stock=0,
            available_supplier_quantity=500,
            available_storage_volume=10_000,
            budget_available_minor=1_000_000,
        )
    )
    assert decision.proposed_quantity == 500
    assert "SUPPLIER_AVAILABILITY_LIMITED" in decision.reason_codes
