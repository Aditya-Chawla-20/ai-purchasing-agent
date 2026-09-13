from purchasing.domain.policy import DemandSpikeInputs, evaluate_demand_spike


def test_demand_spike_detected_supplemental_plan_required_ev_022():
    # 24 hours sales = 150 units. Baseline forecast = 100 units over 24h.
    # Ratio = (150 * 24 * 10000) // (100 * 24) = 15000 bps (1.5x)
    # Revised forecast = 300. Usable on hand = 50, eligible incoming = 50.
    # Target = 300 + 20 (safety stock) = 320.
    # Inventory position = 100.
    # Supplemental need = 320 - 100 = 220.
    inputs = DemandSpikeInputs(
        sales_window_hours=24,
        units_sold=150,
        baseline_forecast_demand=100,
        baseline_forecast_hours=24,
        revised_forecast_demand=300,
        revised_forecast_hours=24,
        threshold_bps=15000,
        usable_on_hand=50,
        eligible_incoming=50,
        safety_stock=20,
    )
    res = evaluate_demand_spike(inputs)
    assert res.spike_detected is True
    assert res.ratio_bps == 15000
    assert res.status == "SUPPLEMENTAL_PLAN_REQUIRED"
    assert res.supplemental_need == 220


def test_demand_spike_with_sufficient_coverage_ev_023():
    # Spike confirmed (2.0x), but current inventory + open PO covers the revised need!
    # Revised = 150 + 20 = 170. Usable = 100, incoming = 100 (pos = 200).
    # Supplemental need = 0.
    inputs = DemandSpikeInputs(
        sales_window_hours=24,
        units_sold=200,
        baseline_forecast_demand=100,
        baseline_forecast_hours=24,
        revised_forecast_demand=150,
        revised_forecast_hours=24,
        threshold_bps=15000,
        usable_on_hand=100,
        eligible_incoming=100,
        safety_stock=20,
    )
    res = evaluate_demand_spike(inputs)
    assert res.spike_detected is True
    assert res.status == "COVERAGE_SUFFICIENT"
    assert res.supplemental_need == 0


def test_stale_or_invalid_evidence_investigates_ev_024():
    stale_sales = DemandSpikeInputs(
        sales_window_hours=24,
        units_sold=200,
        baseline_forecast_demand=100,
        baseline_forecast_hours=24,
        revised_forecast_demand=200,
        revised_forecast_hours=24,
        sales_fresh=False,
    )
    res = evaluate_demand_spike(stale_sales)
    assert res.status == "INVESTIGATE"
    assert "STALE_SALES_EVIDENCE" in res.reasons

    insufficient_window = DemandSpikeInputs(
        sales_window_hours=12,  # < 24h
        units_sold=200,
        baseline_forecast_demand=100,
        baseline_forecast_hours=24,
        revised_forecast_demand=200,
        revised_forecast_hours=24,
    )
    res2 = evaluate_demand_spike(insufficient_window)
    assert res2.status == "INVESTIGATE"
    assert "INSUFFICIENT_SALES_OBSERVATION_WINDOW" in res2.reasons


def test_no_spike_when_velocity_below_threshold():
    # Only 1.2x (12000 bps vs 15000 threshold)
    inputs = DemandSpikeInputs(
        sales_window_hours=24,
        units_sold=120,
        baseline_forecast_demand=100,
        baseline_forecast_hours=24,
        revised_forecast_demand=150,
        revised_forecast_hours=24,
        threshold_bps=15000,
    )
    res = evaluate_demand_spike(inputs)
    assert res.spike_detected is False
    assert res.status == "NO_CHANGE"
    assert res.ratio_bps == 12000
