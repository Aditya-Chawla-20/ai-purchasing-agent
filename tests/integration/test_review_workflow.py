from datetime import UTC, datetime

from purchasing.infrastructure.db import SessionLocal
from purchasing.infrastructure.models import (
    Inventory,
    PurchaseOrder,
    PurchaseOrderItem,
    Recommendation,
)
from purchasing.settings import settings
from sqlalchemy import select


def recommendation(client, source_reference: str):
    rows = client.get("/api/v1/recommendations").json()["items"]
    return next(row for row in rows if row["source_reference"] == source_reference)


def start(client, ref: str, key: str):
    item = recommendation(client, ref)
    return client.post(
        "/api/v1/reviews", json={"recommendation_id": item["id"]}, headers={"Idempotency-Key": key}
    )


def test_main_review_pauses_for_approval_then_creates_and_validates_po(client):
    response = start(client, "REC-800", "test-review-main")
    assert response.status_code == 202
    review_id = response.json()["review_id"]
    review = client.get(f"/api/v1/reviews/{review_id}").json()
    assert review["status"] == "AWAITING_APPROVAL"
    assert review["decision"]["type"] == "MODIFY"
    assert review["decision"]["proposed_quantity"] == 450
    assert {item["name"] for item in review["evidence"]["items"]} == {
        "inventory",
        "supplier_terms",
        "supplier_availability",
        "budget",
        "storage",
        "planning_policy",
        "forecast",
        "open_purchase_orders",
    }
    approved = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={
            "proposal_version": 1,
            "decision": "APPROVE",
            "comment": "Reviewed evidence and constraints",
        },
    )
    assert approved.status_code == 202
    final = client.get(f"/api/v1/reviews/{review_id}").json()
    assert final["status"] == "COMPLETED"
    assert final["action"]["status"] == "SUCCEEDED"
    assert final["validation"]["status"] == "PASSED"
    assert {
        "supplier_id",
        "node_id",
        "product_id",
        "item_count",
        "quantity",
        "currency",
        "unit_cost_minor",
        "total_minor",
        "status",
        "idempotency_key",
    } == {item["field"] for item in final["validation"]["comparisons"]}
    assert (
        len(
            [
                event
                for event in client.get(f"/api/v1/reviews/{review_id}/events").json()["items"]
                if event["event_type"] == "ACTION_RESULT_OBSERVED"
            ]
        )
        == 1
    )
    replay = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={
            "proposal_version": 1,
            "decision": "APPROVE",
            "comment": "Reviewed evidence and constraints",
        },
    )
    assert replay.status_code == 202
    assert replay.json()["status"] == "COMPLETED"


def test_no_need_recommendation_completes_without_purchase_order(client):
    response = start(client, "REC-REJECT", "test-review-reject")
    review = client.get(f"/api/v1/reviews/{response.json()['review_id']}").json()
    assert review["status"] == "COMPLETED"
    assert review["decision"]["type"] == "REJECT"
    assert review["action"] is None


def test_exact_need_recommendation_is_accepted(client):
    response = start(client, "REC-ACCEPT", "test-review-accept")
    review = client.get(f"/api/v1/reviews/{response.json()['review_id']}").json()
    assert review["decision"]["type"] == "ACCEPT"
    assert review["decision"]["proposed_quantity"] == 160
    assert review["status"] == "AWAITING_APPROVAL"


def test_buyer_rejection_is_audited_and_never_creates_order(client):
    response = start(client, "REC-800", "test-review-buyer-reject")
    review_id = response.json()["review_id"]
    rejected = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={"proposal_version": 1, "decision": "REJECT", "comment": "Defer this buy."},
    )
    assert rejected.status_code == 202
    review = client.get(f"/api/v1/reviews/{review_id}").json()
    assert review["status"] == "REJECTED_BY_BUYER"
    assert review["action"] is None
    replay = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={"proposal_version": 1, "decision": "REJECT", "comment": "Defer this buy."},
    )
    assert replay.status_code == 202
    conflict = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={"proposal_version": 1, "decision": "APPROVE", "comment": "Change my mind."},
    )
    assert conflict.status_code == 409


def test_changed_inventory_supersedes_approval_and_requires_new_proposal(client):
    response = start(client, "REC-800", "test-review-stale-approval")
    review_id = response.json()["review_id"]
    review = client.get(f"/api/v1/reviews/{review_id}").json()
    recommendation_id = review["recommendation"]["id"]
    with SessionLocal() as session:
        recommendation_row = session.get(Recommendation, recommendation_id)
        inventory = session.get(
            Inventory, (recommendation_row.product_id, recommendation_row.node_id)
        )
        inventory.on_hand += 100
        inventory.version += 1
        inventory.observed_at = datetime.now(UTC)
        session.commit()

    approved = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={"proposal_version": 1, "decision": "APPROVE", "comment": "Recheck first."},
    )
    assert approved.status_code == 202
    refreshed = client.get(f"/api/v1/reviews/{review_id}").json()
    assert refreshed["status"] == "AWAITING_APPROVAL"
    assert refreshed["decision"]["version"] == 2
    assert refreshed["decision"]["proposed_quantity"] == 400
    assert refreshed["approval"]["proposal_version"] == 2
    assert refreshed["action"] is None
    stale = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={"proposal_version": 1, "decision": "APPROVE", "comment": "Old version."},
    )
    assert stale.status_code == 409
    current = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={"proposal_version": 2, "decision": "APPROVE", "comment": "Reapproved."},
    )
    assert current.status_code == 202
    final = client.get(f"/api/v1/reviews/{review_id}").json()
    assert final["status"] == "COMPLETED"
    assert final["decision"]["proposed_quantity"] == 400
    assert final["validation"]["status"] == "PASSED"


def test_viewer_cannot_start_a_purchase_review(client):
    item = recommendation(client, "REC-REJECT")
    response = client.post(
        "/api/v1/reviews",
        headers={"X-Demo-Role": "VIEWER"},
        json={"recommendation_id": item["id"]},
    )
    assert response.status_code == 403


def test_health_reports_database_and_checkpoint_readiness(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database_ready"] is True
    assert response.json()["workflow_ready"] is True


def test_stale_forecast_escalates_without_purchase_order(client):
    response = start(client, "REC-INVESTIGATE", "test-review-stale")
    review = client.get(f"/api/v1/reviews/{response.json()['review_id']}").json()
    assert review["status"] == "NEEDS_ATTENTION"
    assert review["decision"]["type"] == "INVESTIGATE"
    assert "forecast is stale" in review["evidence"]["errors"]
    assert review["action"] is None


def test_review_request_idempotency_key_replays_original_review(client):
    item = recommendation(client, "REC-REJECT")
    body = {"recommendation_id": item["id"]}
    first = client.post(
        "/api/v1/reviews", json=body, headers={"Idempotency-Key": "same-review-key"}
    )
    second = client.post(
        "/api/v1/reviews", json=body, headers={"Idempotency-Key": "same-review-key"}
    )
    assert first.json()["review_id"] == second.json()["review_id"]
    mismatch = client.post(
        "/api/v1/reviews",
        json={"recommendation_id": recommendation(client, "REC-INVESTIGATE")["id"]},
        headers={"Idempotency-Key": "same-review-key"},
    )
    assert mismatch.status_code == 409


def test_partial_supplier_confirmation_recomputes_need_and_creates_followup_review(client):
    fixture = client.get("/api/v1/demo/partial-fulfilment").json()
    response = client.post(
        "/api/v1/mock/supplier-confirmations",
        headers={"X-Demo-Role": "DEMO_ADMIN"},
        json={
            "external_event_id": "supplier-event-test-250",
            "purchase_order_id": fixture["purchase_order_id"],
            "product_id": fixture["product_id"],
            "confirmed_quantity": 250,
            "event_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 202
    review = client.get(f"/api/v1/reviews/{response.json()['review_id']}").json()
    assert review["status"] == "AWAITING_APPROVAL"
    assert review["decision"]["proposed_quantity"] == 350
    duplicate = client.post(
        "/api/v1/mock/supplier-confirmations",
        headers={"X-Demo-Role": "DEMO_ADMIN"},
        json={
            "external_event_id": "supplier-event-test-250",
            "purchase_order_id": fixture["purchase_order_id"],
            "product_id": fixture["product_id"],
            "confirmed_quantity": 250,
            "event_at": datetime.now(UTC).isoformat(),
        },
    )
    assert duplicate.json()["status"] == "DUPLICATE_IGNORED"
    conflicting_duplicate = client.post(
        "/api/v1/mock/supplier-confirmations",
        headers={"X-Demo-Role": "DEMO_ADMIN"},
        json={
            "external_event_id": "supplier-event-test-250",
            "purchase_order_id": fixture["purchase_order_id"],
            "product_id": fixture["product_id"],
            "confirmed_quantity": 300,
            "event_at": datetime.now(UTC).isoformat(),
        },
    )
    assert conflicting_duplicate.status_code == 409

    unchanged = client.post(
        "/api/v1/mock/supplier-confirmations",
        headers={"X-Demo-Role": "DEMO_ADMIN"},
        json={
            "external_event_id": "supplier-event-test-250-second",
            "purchase_order_id": fixture["purchase_order_id"],
            "product_id": fixture["product_id"],
            "confirmed_quantity": 250,
            "event_at": datetime.now(UTC).isoformat(),
        },
    )
    assert unchanged.json() == {"status": "UNCHANGED_IGNORED", "review_id": None}


def reset_scenario(client, name: str):
    response = client.post(f"/api/v1/demo/scenarios/{name}/reset")
    assert response.status_code == 202, response.text
    return response.json()


def approve_current(client, review_id: str):
    review = client.get(f"/api/v1/reviews/{review_id}").json()
    response = client.post(
        f"/api/v1/reviews/{review_id}/approval",
        json={
            "proposal_version": review["decision"]["version"],
            "decision": "APPROVE",
            "comment": "Exercise the Scenario 1 safeguard.",
        },
    )
    assert response.status_code == 202, response.text
    return client.get(f"/api/v1/reviews/{review_id}").json()


def test_inventory_conflict_scenario_escalates_without_a_purchase_order(client):
    fixture = reset_scenario(client, "inventory-conflict")
    review = client.get(f"/api/v1/reviews/{fixture['review_id']}").json()
    inventory = next(item for item in review["evidence"]["items"] if item["name"] == "inventory")
    assert review["status"] == "NEEDS_ATTENTION"
    assert review["decision"]["type"] == "INVESTIGATE"
    assert inventory["status"] == "CONFLICTING"
    assert review["action"] is None


def test_delayed_open_po_is_not_counted_as_eligible_incoming_supply(client):
    fixture = reset_scenario(client, "late-incoming-po")
    review = client.get(f"/api/v1/reviews/{fixture['review_id']}").json()
    assert review["status"] == "AWAITING_APPROVAL"
    assert review["decision"]["calculations"]["inventory_position"] == 150
    assert review["decision"]["calculations"]["raw_need"] == 600
    assert review["decision"]["proposed_quantity"] == 600


def test_supplier_terms_change_supersedes_the_original_approval(client):
    fixture = reset_scenario(client, "supplier-terms-change")
    review_id = fixture["review_id"]
    first = client.get(f"/api/v1/reviews/{review_id}").json()
    assert first["decision"]["proposed_quantity"] == 450
    advanced = client.post(
        "/api/v1/demo/scenarios/supplier-terms-change/advance",
        json={"review_id": review_id},
    )
    assert advanced.status_code == 200
    refreshed = approve_current(client, review_id)
    assert refreshed["status"] == "AWAITING_APPROVAL"
    assert refreshed["decision"]["version"] == 2
    assert refreshed["decision"]["proposed_quantity"] == 300
    assert refreshed["action"] is None


def test_lost_create_response_recovers_one_matching_purchase_order(client):
    fixture = reset_scenario(client, "lost-create-response")
    review_id = fixture["review_id"]
    final = approve_current(client, review_id)
    assert final["status"] == "COMPLETED"
    assert final["validation"]["status"] == "PASSED"
    observed = [
        event
        for event in client.get(f"/api/v1/reviews/{review_id}/events").json()["items"]
        if event["event_type"] == "ACTION_RESULT_OBSERVED"
    ]
    assert observed[-1]["payload"]["recovered_by_idempotency"] is True
    with SessionLocal() as session:
        orders = session.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.idempotency_key == f"{review_id}:create-po:v1"
            )
        ).all()
        assert len(orders) == 1
        assert (
            len(
                session.scalars(
                    select(PurchaseOrderItem).where(
                        PurchaseOrderItem.purchase_order_id == orders[0].id
                    )
                ).all()
            )
            == 1
        )


def test_wrong_persisted_po_quantity_fails_read_back_validation(client):
    fixture = reset_scenario(client, "wrong-persisted-po")
    final = approve_current(client, fixture["review_id"])
    assert final["status"] == "NEEDS_ATTENTION"
    assert final["validation"]["status"] == "FAILED_UNSAFE"
    assert "quantity" in final["validation"]["mismatches"]


def test_scenario_lab_is_unavailable_outside_local_demo(client, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    response = client.post("/api/v1/demo/scenarios/inventory-conflict/reset")
    assert response.status_code in {404, 503}
