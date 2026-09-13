from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from purchasing.infrastructure.models import (
    Budget,
    Forecast,
    Inventory,
    Node,
    NodeProductPolicy,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    Recommendation,
    StorageCapacity,
    Supplier,
    SupplierProduct,
)


def seed_demo_data(session: Session) -> None:
    """Insert deterministic fixtures; safe to run repeatedly."""
    if session.scalar(select(Recommendation.id).limit(1)):
        return
    now = datetime.now(UTC)
    node = Node(code="BLR-01", name="Bengaluru Fulfilment Centre")
    main_supplier = Supplier(code="SUP-01", name="Fresh Farms", reliability_score=96)
    alternate = Supplier(code="SUP-02", name="Green Valley Supply", reliability_score=91)
    apples = Product(
        sku="APL-001",
        name="Royal Gala Apples",
        unit_volume=10,
    )
    berries = Product(sku="BER-002", name="Blueberries", unit_volume=8)
    flour = Product(
        sku="FLR-003",
        name="Whole Wheat Flour",
        unit_volume=5,
    )
    stale = Product(
        sku="STL-004",
        name="Seasonal Mangoes",
        unit_volume=12,
    )
    partial_product = Product(sku="APL-005", name="Organic Apples", unit_volume=10)
    session.add_all(
        [node, main_supplier, alternate, apples, berries, flour, stale, partial_product]
    )
    session.flush()
    session.add_all(
        [
            NodeProductPolicy(
                product_id=product.id,
                node_id=node.id,
                safety_stock=safety,
                review_period_days=2,
                observed_at=now,
            )
            for product, safety in [
                (apples, 50),
                (berries, 20),
                (flour, 10),
                (stale, 30),
                (partial_product, 50),
            ]
        ]
    )
    for supplier, product, cost, moq, lead, max_qty in [
        (main_supplier, apples, 1250, 50, 5, 500),
        (alternate, apples, 1325, 25, 6, 500),
        (main_supplier, berries, 700, 10, 3, 300),
        (main_supplier, flour, 500, 20, 2, 300),
        (main_supplier, stale, 1100, 10, 4, 200),
        (main_supplier, partial_product, 1250, 50, 5, 500),
        (alternate, partial_product, 1325, 25, 6, 500),
    ]:
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                product_id=product.id,
                unit_cost_minor=cost,
                currency="INR",
                minimum_order_quantity=moq,
                lead_time_days=lead,
                max_available_quantity=max_qty,
                terms_version="1",
                observed_at=now,
            )
        )
    for product, on_hand, reserved, damaged, demand, days in [
        (apples, 180, 20, 10, 700, 7),
        (berries, 60, 0, 0, 200, 5),
        (flour, 500, 20, 0, 350, 4),
        (stale, 25, 0, 0, 300, 6),
        (partial_product, 150, 0, 0, 700, 8),
    ]:
        session.add(
            Inventory(
                product_id=product.id,
                node_id=node.id,
                on_hand=on_hand,
                reserved=reserved,
                damaged=damaged,
                observed_at=now,
            )
        )
        session.add(
            Forecast(
                product_id=product.id,
                node_id=node.id,
                window_start=now - timedelta(days=1),
                window_end=now + timedelta(days=days + 1),
                expected_demand=demand,
                model_version="seed-v1",
                observed_at=now - (timedelta(days=3) if product is stale else timedelta(minutes=1)),
            )
        )
    session.add(
        Budget(
            node_id=node.id,
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            currency="INR",
            allocated_minor=1_000_000,
            committed_minor=400_000,
            observed_at=now,
        )
    )
    session.add(StorageCapacity(node_id=node.id, available_volume=4_500, observed_at=now))
    session.flush()

    # Existing confirmed inbound stock reduces the main 800-unit recommendation to 450.
    po = PurchaseOrder(
        external_id="PO-SEED-OPEN-001",
        supplier_id=main_supplier.id,
        node_id=node.id,
        status="OPEN",
        currency="INR",
        total_minor=125_000,
        expected_delivery_at=now + timedelta(days=3),
        idempotency_key="seed-open-po-1",
        created_at=now,
        updated_at=now,
    )
    session.add(po)
    session.flush()
    session.add(
        PurchaseOrderItem(
            purchase_order_id=po.id,
            product_id=apples.id,
            ordered_quantity=100,
            confirmed_quantity=100,
            received_quantity=0,
            unit_cost_minor=1250,
        )
    )

    cases = [
        ("REC-800", apples, main_supplier, 800),
        ("REC-ACCEPT", berries, main_supplier, 160),
        ("REC-REJECT", flour, main_supplier, 50),
        ("REC-INVESTIGATE", stale, main_supplier, 100),
    ]
    for ref, product, supplier, quantity in cases:
        session.add(
            Recommendation(
                source_reference=ref,
                source="mock-planner",
                product_id=product.id,
                node_id=node.id,
                preferred_supplier_id=supplier.id,
                recommended_quantity=quantity,
                created_at=now,
            )
        )
    # Scenario 2 fixture: a 500-unit order with only 250 confirmed by the supplier.
    partial = PurchaseOrder(
        external_id="PO-PARTIAL-500",
        supplier_id=main_supplier.id,
        node_id=node.id,
        status="OPEN",
        currency="INR",
        total_minor=625_000,
        expected_delivery_at=now + timedelta(days=4),
        idempotency_key="seed-partial-500",
        created_at=now,
        updated_at=now,
    )
    session.add(partial)
    session.flush()
    session.add(
        PurchaseOrderItem(
            purchase_order_id=partial.id,
            product_id=partial_product.id,
            ordered_quantity=500,
            confirmed_quantity=0,
            received_quantity=0,
            unit_cost_minor=1250,
        )
    )
    session.commit()
