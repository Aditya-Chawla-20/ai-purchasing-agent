"""Integration tests for expanded agent workflow and evaluator scenarios (EV-014 through EV-025)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from purchasing.application.demo_scenarios import _restore_baseline
from purchasing.application.providers import BaseToolProvider, ProviderCoordinator
from purchasing.application.tools import ToolContext, ToolDispatcher, ToolResponse
from purchasing.infrastructure.db import SessionLocal
from purchasing.infrastructure.models import (
    Forecast,
    InvestigationTrace,
    Node,
    Product,
    PurchasingReview,
    Recommendation,
    Supplier,
)
from sqlalchemy import select


@pytest.fixture(autouse=True)
def clean_expansion_state():
    with SessionLocal() as session:
        _restore_baseline(session)
        session.commit()
    yield
    with SessionLocal() as session:
        _restore_baseline(session)
        session.commit()


def _get_seeded_context(session):
    rec = session.scalar(
        select(Recommendation).where(Recommendation.source_reference == "REC-800")
    )
    product = session.get(Product, rec.product_id)
    node = session.get(Node, rec.node_id)
    supplier = session.get(Supplier, rec.preferred_supplier_id)
    baseline_fc = session.scalar(
        select(Forecast)
        .where(
            Forecast.product_id == rec.product_id,
            Forecast.node_id == rec.node_id,
        )
        .order_by(Forecast.observed_at.asc())
        .limit(1)
    )
    revised_fc = session.scalar(
        select(Forecast)
        .where(
            Forecast.product_id == rec.product_id,
            Forecast.node_id == rec.node_id,
            Forecast.model_version.like("%revised%"),
        )
        .order_by(Forecast.observed_at.desc())
        .limit(1)
    )
    if not revised_fc:
        now = datetime.now(UTC)
        revised_fc = Forecast(
            product_id=rec.product_id,
            node_id=rec.node_id,
            window_start=now,
            window_end=now + timedelta(days=8),
            expected_demand=950,
            model_version="revised-v2",
            observed_at=now,
        )
        session.add(revised_fc)
        session.commit()
    return rec, product, node, supplier, baseline_fc, revised_fc


def test_ev014_manifest_autofills_omitted_reads(client):
    """EV-014: Mandatory Evidence Manifest adds omitted safety-critical facts."""
    res = client.post(
        "/api/v1/demo/scenarios/late-incoming-po/reset",
        headers={"X-Demo-Role": "BUYER"},
    )
    assert res.status_code == 202
    review_id = res.json()["review_id"]

    review = client.get(f"/api/v1/reviews/{review_id}", headers={"X-Demo-Role": "BUYER"}).json()
    assert review["status"] == "AWAITING_APPROVAL"
    assert review["investigation"]["mandatory_evidence_complete"] is True

    evidence_names = {item["name"] for item in review["evidence"]["items"]}
    for mandatory in [
        "inventory",
        "forecast",
        "open_purchase_orders",
        "supplier_terms",
        "supplier_availability",
        "budget",
        "storage",
        "planning_policy",
    ]:
        assert mandatory in evidence_names

    with SessionLocal() as session:
        trace = session.scalar(
            select(InvestigationTrace)
            .where(InvestigationTrace.review_id == review_id)
            .order_by(InvestigationTrace.started_at.desc())
            .limit(1)
        )
        assert trace is not None
        assert len(trace.mandatory_manifest_json) > 0


def test_ev015_bounded_tool_calling_limits(client):
    """EV-015: ToolDispatcher enforces bounds on total calls, unauthorized tools, and duplicates."""
    with SessionLocal() as session:
        rec, product, node, supplier, _, _ = _get_seeded_context(session)
        review = session.scalar(
            select(PurchasingReview).where(PurchasingReview.recommendation_id == rec.id)
        )
        if not review:
            review = PurchasingReview(
                recommendation_id=rec.id,
                scenario_type="recommendation-review",
                status="CREATED",
                idempotency_key=f"ev015-{uuid_suffix()}",
            )
            session.add(review)
            session.commit()
        ctx = ToolContext(
            review_id=review.id,
            product_id=product.id,
            node_id=node.id,
            preferred_supplier_id=supplier.id,
            phase="investigation",
            max_rounds=3,
            max_total_calls=5,
        )
        dispatcher = ToolDispatcher(session=session, context=ctx)

        # 1. Unauthorized tool
        unauth = dispatcher.dispatch(
            tool_name="unauthorized_system_tool",
            round_number=1,
            arguments={},
        )
        assert not unauth.ok
        assert unauth.error_code == "TOOL_NOT_ALLOWED"

        # 2. Legitimate call
        call1 = dispatcher.dispatch(
            tool_name="get_inventory",
            round_number=1,
            arguments={"product_id": product.id, "node_id": node.id},
        )
        assert call1.ok

        # 3. Duplicate read with same arguments in same round
        dup = dispatcher.dispatch(
            tool_name="get_inventory",
            round_number=1,
            arguments={"product_id": product.id, "node_id": node.id},
        )
        assert not dup.ok
        assert dup.error_code == "DUPLICATE_TOOL_CALL"

        # 4. Burn remaining calls until max_total_calls (5)
        dispatcher.dispatch(
            tool_name="get_budget",
            round_number=2,
            arguments={"node_id": node.id},
        )
        dispatcher.dispatch(
            tool_name="get_storage_capacity",
            round_number=2,
            arguments={"node_id": node.id},
        )
        dispatcher.dispatch(
            tool_name="get_planning_policy",
            round_number=2,
            arguments={"product_id": product.id, "node_id": node.id},
        )

        assert dispatcher.total_calls == 5

        # 5. Call 6 should exceed total call limit
        exceeded = dispatcher.dispatch(
            tool_name="get_demand_forecast",
            round_number=3,
            arguments={"product_id": product.id, "node_id": node.id},
        )
        assert not exceeded.ok
        assert exceeded.error_code == "TOOL_LIMIT_REACHED"


def test_ev016_provider_coordinator_fallback():
    """EV-016: ProviderCoordinator handles tool provider fallbacks and actions cleanly."""
    class FailingProvider(BaseToolProvider):
        def generate_investigation_calls(self, *args, **kwargs):
            raise ConnectionError("Primary provider timeout")

        def generate_action_call(self, *args, **kwargs):
            raise ConnectionError("Primary provider error")

    coordinator = ProviderCoordinator()
    coordinator.primary_provider = FailingProvider()

    with SessionLocal() as session:
        rec, product, node, supplier, _, _ = _get_seeded_context(session)
        ctx = ToolContext(
            review_id=rec.id,
            product_id=product.id,
            node_id=node.id,
            preferred_supplier_id=supplier.id,
            phase="investigation",
        )
        dispatcher = ToolDispatcher(session=session, context=ctx)

        # Should fall back cleanly to secondary scripted provider
        calls = coordinator.investigate_round(
            session=session,
            dispatcher=dispatcher,
            round_number=1,
            context={"scenario_type": "recommendation-review"},
        )
        assert len(calls) > 0


def test_ev017_awaiting_execution_retry_on_provider_failure_and_recovery(client):
    """EV-017: Provider failure leads to AWAITING_EXECUTION_RETRY, preserving approval until retry."""
    res = client.post(
        "/api/v1/demo/scenarios/late-incoming-po/reset",
        headers={"X-Demo-Role": "BUYER"},
    )
    assert res.status_code == 202
    review_id = res.json()["review_id"]

    review = client.get(f"/api/v1/reviews/{review_id}").json()
    assert review["status"] == "AWAITING_APPROVAL"

    # Simulate provider failure during execute
    with patch.object(
        ProviderCoordinator,
        "execute_action_tool_call",
        return_value=ToolResponse(
            ok=False,
            data={},
            error_code="PROVIDER_TEMPORARILY_UNAVAILABLE",
            source="mock-provider",
            error_message="Connection to supplier gateway timed out",
        ),
    ):
        appr = client.post(
            f"/api/v1/reviews/{review_id}/approval",
            json={
                "proposal_version": 1,
                "decision": "APPROVE",
                "comment": "Approved for provider failure test",
            },
            headers={"X-Demo-Role": "BUYER"},
        )
        assert appr.status_code == 202

        review_after_fail = client.get(f"/api/v1/reviews/{review_id}").json()
        assert review_after_fail["status"] == "AWAITING_EXECUTION_RETRY"
        assert review_after_fail["action"] is None

    # Now retry execution as BUYER (provider is healthy)
    retry_res = client.post(
        f"/api/v1/reviews/{review_id}/execution-retry",
        json={"proposal_version": 1},
        headers={"X-Demo-Role": "BUYER"},
    )
    assert retry_res.status_code == 202

    # Verify review reaches completed with validated purchase order
    final_review = client.get(f"/api/v1/reviews/{review_id}").json()
    assert final_review["status"] == "COMPLETED"
    assert final_review["validation"]["status"] == "PASSED"
    assert final_review["action"]["purchase_order_id"] is not None


def test_ev018_and_ev019_multi_supplier_shortfall_allocation_and_execution(client):
    """EV-018 & EV-019: Multi-supplier allocation when preferred supplier availability is insufficient."""
    reset_res = client.post(
        "/api/v1/demo/scenarios/multi-supplier-shortfall/reset",
        headers={"X-Demo-Role": "BUYER"},
    )
    assert reset_res.status_code == 202
    review_id = reset_res.json()["review_id"]

    review = client.get(f"/api/v1/reviews/{review_id}").json()
    assert review["status"] == "AWAITING_APPROVAL"
    sourcing_plan = review["sourcing_plan"]
    assert sourcing_plan is not None

    # Preferred supplier SUP-01 has max 300; raw need is 600 -> plan allocates to both SUP-01 and SUP-02
    assert len(sourcing_plan["lines"]) >= 2
    total_alloc = sum(line["allocated_quantity"] for line in sourcing_plan["lines"])
    assert total_alloc >= 450

    # Approve the multi-line proposal
    appr_res = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={
            "proposal_version": 1,
            "decision": "APPROVE",
            "comment": "Approve multi-supplier plan",
        },
        headers={"X-Demo-Role": "BUYER"},
    )
    assert appr_res.status_code == 202

    final_review = client.get(f"/api/v1/reviews/{review_id}").json()
    assert final_review["status"] == "COMPLETED"
    assert final_review["validation"]["status"] == "PASSED"

    # Both purchase orders exist and are recorded in actions
    assert len(final_review["actions"]) >= 2
    po_ids = {act["purchase_order_id"] for act in final_review["actions"]}
    assert len(po_ids) >= 2


def test_ev021_and_ev022_demand_spike_detection_and_deduplication(client):
    """EV-021 & EV-022: Demand spike velocity >= 1.5x triggers supplemental order without altering existing POs."""
    with SessionLocal() as session:
        rec, product, node, supplier, baseline_fc, revised_fc = _get_seeded_context(session)
        now = datetime.now(UTC)
        event_id = f"ev021-spike-{uuid_suffix()}"

        payload = {
            "external_event_id": event_id,
            "product_id": product.id,
            "node_id": node.id,
            "window_start": (now - timedelta(hours=24)).isoformat(),
            "window_end": now.isoformat(),
            "units_sold": 250,
            "baseline_forecast_id": baseline_fc.id,
            "revised_forecast_id": revised_fc.id,
            "source_version": "pos-v2",
            "occurred_at": now.isoformat(),
        }

        # 1. Post demand signal
        res = client.post(
            "/api/v1/mock/demand-signals",
            json=payload,
            headers={"X-Demo-Role": "DEMO_ADMIN"},
        )
        assert res.status_code == 202
        data = res.json()
        assert data["status"] == "ACCEPTED"
        review_id = data["review_id"]
        assert review_id is not None

        # 2. Duplicate submission is ignored idempotently
        dup_res = client.post(
            "/api/v1/mock/demand-signals",
            json=payload,
            headers={"X-Demo-Role": "DEMO_ADMIN"},
        )
        assert dup_res.status_code == 202
        assert dup_res.json()["status"] == "DUPLICATE_IGNORED"

        # 3. Check demand review
        review = client.get(f"/api/v1/reviews/{review_id}").json()
        assert review["status"] in {"AWAITING_APPROVAL", "COMPLETED"}


def test_ev023_custom_scenario_lab_execution(client):
    """EV-023: Custom Scenario Lab creates isolated facts and executes complete workflow without code changes."""
    now = datetime.now(UTC)
    payload = {
      "name": "Custom Evaluator Test",
      "mode": "RECOMMENDATION",
      "product": {"sku": "CUSTOM-EV23", "name": "Custom Widget", "unit_volume": 2},
      "node": {"code": "NODE-EV23", "name": "Custom Hub"},
      "recommended_quantity": 600,
      "inventory": {"on_hand": 100, "reserved": 0, "damaged": 0},
      "forecasts": {
        "baseline": 500,
        "revised": None,
        "window_start": now.isoformat(),
        "window_end": (now + timedelta(days=7)).isoformat(),
      },
      "suppliers": [
        {
          "code": "SUP-EV-A",
          "name": "Supplier Alpha",
          "reliability_score_bps": 9600,
          "unit_cost_minor": 1200,
          "currency": "INR",
          "minimum_order_quantity": 25,
          "lead_time_days": 2,
          "max_available_quantity": 250,
        },
        {
          "code": "SUP-EV-B",
          "name": "Supplier Beta",
          "reliability_score_bps": 9100,
          "unit_cost_minor": 1350,
          "currency": "INR",
          "minimum_order_quantity": 25,
          "lead_time_days": 3,
          "max_available_quantity": 500,
        },
      ],
      "budget": {"available_minor": 1000000, "currency": "INR"},
      "storage": {"available_volume": 5000},
      "policy": {"safety_stock": 50, "review_period_days": 2},
    }

    res = client.post(
        "/api/v1/demo/custom-scenarios",
        json=payload,
        headers={"X-Demo-Role": "BUYER"},
    )
    assert res.status_code == 201
    res_data = res.json()
    review_id = res_data["review_id"]
    assert review_id is not None

    review = client.get(f"/api/v1/reviews/{review_id}").json()
    assert review["status"] == "AWAITING_APPROVAL"
    assert review["sourcing_plan"] is not None
    assert len(review["sourcing_plan"]["lines"]) == 2

    # Approve custom proposal
    appr = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={
            "proposal_version": 1,
            "decision": "APPROVE",
            "comment": "Approved custom test proposal",
        },
        headers={"X-Demo-Role": "BUYER"},
    )
    assert appr.status_code == 202

    final_rev = client.get(f"/api/v1/reviews/{review_id}").json()
    assert final_rev["status"] == "COMPLETED"
    assert final_rev["validation"]["status"] == "PASSED"
    assert len(final_rev["actions"]) == 2


def uuid_suffix():
    import uuid
    return uuid.uuid4().hex[:8]
