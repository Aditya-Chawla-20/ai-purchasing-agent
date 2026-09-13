"""harden agentic purchasing contracts

Revision ID: 8b4e7f3c9d10
Revises: 737ea043a94f
"""

import sqlalchemy as sa
from alembic import op

revision = "8b4e7f3c9d10"
down_revision = "737ea043a94f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("forecasts") as batch:
        batch.drop_constraint("uq_forecast_window", type_="unique")
        batch.create_unique_constraint(
            "uq_forecast_window_version",
            ["product_id", "node_id", "window_start", "window_end", "model_version"],
        )
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("proposal_id", sa.String(), nullable=True))
        batch.create_foreign_key("fk_approval_proposal", "purchase_proposals", ["proposal_id"], ["id"])
    with op.batch_alter_table("node_product_policies") as batch:
        batch.add_column(sa.Column("demand_spike_threshold_bps", sa.Integer(), nullable=False, server_default="15000"))
        batch.add_column(sa.Column("minimum_sales_observation_hours", sa.Integer(), nullable=False, server_default="24"))


def downgrade() -> None:
    with op.batch_alter_table("node_product_policies") as batch:
        batch.drop_column("minimum_sales_observation_hours")
        batch.drop_column("demand_spike_threshold_bps")
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("fk_approval_proposal", type_="foreignkey")
        batch.drop_column("proposal_id")
    with op.batch_alter_table("forecasts") as batch:
        batch.drop_constraint("uq_forecast_window_version", type_="unique")
        batch.create_unique_constraint("uq_forecast_window", ["product_id", "node_id", "window_start", "window_end"])
