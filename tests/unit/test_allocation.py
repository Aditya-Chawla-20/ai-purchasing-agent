from purchasing.domain.policy import (
    SupplierCandidate,
    allocate_sourcing_plan,
)


def test_single_supplier_sufficient():
    cand = SupplierCandidate(
        supplier_id="sup-1",
        supplier_code="SUP-01",
        supplier_name="Alpha",
        reliability_score_bps=9500,
        unit_cost_minor=1000,
        currency="INR",
        minimum_order_quantity=50,
        lead_time_days=2,
        max_available_quantity=500,
    )
    result = allocate_sourcing_plan(
        raw_need=250,
        candidates=[cand],
        budget_available_minor=1_000_000,
        available_storage_volume=5000,
        product_unit_volume=1,
        target_currency="INR",
    )
    assert result.is_complete is True
    assert result.status == "COMPLETE"
    assert result.covered_quantity == 250
    assert len(result.lines) == 1
    assert result.lines[0].quantity == 250
    assert result.lines[0].supplier_id == "sup-1"


def test_two_suppliers_required_for_shortfall_ev_018():
    # Supplier 1 only has 250 available. Need is 450.
    s1 = SupplierCandidate(
        supplier_id="sup-1",
        supplier_code="SUP-01",
        supplier_name="Primary",
        reliability_score_bps=9800,
        unit_cost_minor=1200,
        currency="INR",
        minimum_order_quantity=50,
        lead_time_days=2,
        max_available_quantity=250,
    )
    s2 = SupplierCandidate(
        supplier_id="sup-2",
        supplier_code="SUP-02",
        supplier_name="Secondary",
        reliability_score_bps=9200,
        unit_cost_minor=1300,
        currency="INR",
        minimum_order_quantity=50,
        lead_time_days=3,
        max_available_quantity=300,
    )
    result = allocate_sourcing_plan(
        raw_need=450,
        candidates=[s1, s2],
        budget_available_minor=1_000_000,
        available_storage_volume=5000,
        product_unit_volume=1,
        target_currency="INR",
    )
    assert result.is_complete is True
    assert result.status == "COMPLETE"
    assert result.covered_quantity == 450
    assert len(result.lines) == 2
    # S1 maxes out at 250, S2 supplies 200
    line_map = {line.supplier_id: line.quantity for line in result.lines}
    assert line_map["sup-1"] == 250
    assert line_map["sup-2"] == 200


def test_reliability_first_over_cost_ev_019():
    # Both s1 and s2 can supply 200.
    # s1 is cheaper (1000) but less reliable (8500 bps).
    # s2 is more expensive (1200) but more reliable (9500 bps).
    s1 = SupplierCandidate(
        supplier_id="sup-cheap",
        supplier_code="SUP-CHEAP",
        supplier_name="Budget Vendor",
        reliability_score_bps=8500,
        unit_cost_minor=1000,
        currency="INR",
        minimum_order_quantity=50,
        lead_time_days=2,
        max_available_quantity=500,
    )
    s2 = SupplierCandidate(
        supplier_id="sup-reliable",
        supplier_code="SUP-REL",
        supplier_name="Reliable Vendor",
        reliability_score_bps=9500,
        unit_cost_minor=1200,
        currency="INR",
        minimum_order_quantity=50,
        lead_time_days=2,
        max_available_quantity=500,
    )
    result = allocate_sourcing_plan(
        raw_need=200,
        candidates=[s1, s2],
        budget_available_minor=1_000_000,
        available_storage_volume=5000,
        product_unit_volume=1,
        target_currency="INR",
    )
    assert result.is_complete is True
    assert len(result.lines) == 1
    # Reliability first: sup-reliable must be chosen despite higher unit cost!
    assert result.lines[0].supplier_id == "sup-reliable"
    assert result.lines[0].quantity == 200


def test_infeasible_coverage_returns_partial_ev_020():
    # Need 500, but only 200 available across all suppliers
    s1 = SupplierCandidate(
        supplier_id="sup-1",
        supplier_code="SUP-01",
        supplier_name="Alpha",
        reliability_score_bps=9000,
        unit_cost_minor=1000,
        currency="INR",
        minimum_order_quantity=50,
        lead_time_days=2,
        max_available_quantity=200,
    )
    result = allocate_sourcing_plan(
        raw_need=500,
        candidates=[s1],
        budget_available_minor=1_000_000,
        available_storage_volume=5000,
        product_unit_volume=1,
        target_currency="INR",
    )
    assert result.is_complete is False
    assert result.status == "PARTIAL"
    assert result.covered_quantity == 200
    assert len(result.lines) == 1


def test_exclusion_reasons():
    s_stale = SupplierCandidate(
        supplier_id="s-stale",
        supplier_code="S-STALE",
        supplier_name="Stale",
        terms_fresh=False,
        reliability_score_bps=9000,
        unit_cost_minor=1000,
        currency="INR",
        minimum_order_quantity=50,
        lead_time_days=1,
    )
    s_curr = SupplierCandidate(
        supplier_id="s-curr",
        supplier_code="S-CURR",
        supplier_name="USD",
        currency="USD",
        reliability_score_bps=9000,
        unit_cost_minor=1000,
        minimum_order_quantity=50,
        lead_time_days=1,
    )
    result = allocate_sourcing_plan(
        raw_need=100,
        candidates=[s_stale, s_curr],
        budget_available_minor=100_000,
        available_storage_volume=1000,
        product_unit_volume=1,
        target_currency="INR",
    )
    assert result.is_complete is False
    assert result.status == "INFEASIBLE"
    assessments = {a.supplier_id: a.exclusion_reason_codes for a in result.assessments}
    assert "STALE_SUPPLIER_TERMS" in assessments["s-stale"]
    assert "CURRENCY_MISMATCH" in assessments["s-curr"]


def test_large_moq_one_search_escalates_instead_of_enumerating_every_quantity():
    candidate = SupplierCandidate(
        supplier_id="sup-scale",
        supplier_code="SUP-SCALE",
        supplier_name="Scale-safe vendor",
        reliability_score_bps=9500,
        unit_cost_minor=1000,
        currency="INR",
        minimum_order_quantity=1,
        lead_time_days=1,
        max_available_quantity=10_000,
    )
    result = allocate_sourcing_plan(
        raw_need=5_000,
        candidates=[candidate],
        budget_available_minor=10_000_000,
        available_storage_volume=10_000,
        product_unit_volume=1,
        target_currency="INR",
    )
    assert result.status == "INFEASIBLE"
    assert result.reasons == ("ALLOCATION_COMPLEXITY_LIMIT_REACHED",)
