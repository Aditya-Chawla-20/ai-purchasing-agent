from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewCreate(BaseModel):
    recommendation_id: str


class ReviewCreated(BaseModel):
    review_id: str
    status: str


class ApprovalInput(BaseModel):
    proposal_version: int = Field(gt=0)
    decision: Literal["APPROVE", "REJECT"]
    comment: str = Field(default="", max_length=500)


class SupplierConfirmationInput(BaseModel):
    external_event_id: str = Field(min_length=1, max_length=120)
    purchase_order_id: str
    product_id: str
    confirmed_quantity: int = Field(ge=0)
    event_at: datetime


class ScenarioAdvanceInput(BaseModel):
    review_id: str


class Problem(BaseModel):
    type: str
    title: str
    status: int
    code: str
    detail: str
    correlation_id: str


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    status: str
    source: str
    observed_at: str
    effective_from: str | None = None
    effective_until: str | None = None
    value: object | None = None
    version: str | None = None


class ExecutionRetryInput(BaseModel):
    proposal_version: int = Field(gt=0)


class DemandSignalInput(BaseModel):
    external_event_id: str = Field(min_length=1, max_length=120)
    product_id: str
    node_id: str
    window_start: datetime
    window_end: datetime
    units_sold: int = Field(ge=0)
    baseline_forecast_id: str
    revised_forecast_id: str
    source_version: str = Field(min_length=1, max_length=64)
    occurred_at: datetime


class CustomProductInput(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    unit_volume: int = Field(gt=0)


class CustomNodeInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)


class CustomInventoryInput(BaseModel):
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    damaged: int = Field(ge=0)


class CustomForecastInput(BaseModel):
    baseline: int = Field(ge=0)
    revised: int | None = None
    window_start: datetime
    window_end: datetime


class CustomOpenPOInput(BaseModel):
    supplier_code: str = Field(min_length=1, max_length=64)
    ordered_quantity: int = Field(ge=0)
    confirmed_quantity: int = Field(ge=0)
    received_quantity: int = Field(ge=0)
    unit_cost_minor: int = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    expected_delivery_at: datetime


class CustomSupplierInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    reliability_score_bps: int = Field(ge=0, le=10000)
    unit_cost_minor: int = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    minimum_order_quantity: int = Field(gt=0)
    lead_time_days: int = Field(ge=0)
    max_available_quantity: int = Field(ge=0)


class CustomBudgetInput(BaseModel):
    available_minor: int = Field(ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)


class CustomStorageInput(BaseModel):
    available_volume: int = Field(ge=0)


class CustomPolicyInput(BaseModel):
    safety_stock: int = Field(ge=0)
    review_period_days: int = Field(gt=0)
    demand_spike_threshold_bps: int = Field(default=15000, ge=10000)
    minimum_sales_observation_hours: int = Field(default=24, ge=24)


class CustomRecentSalesInput(BaseModel):
    units_sold: int = Field(ge=0)
    window_hours: int = Field(default=24, gt=0)


class CustomScenarioInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mode: Literal["RECOMMENDATION", "DEMAND_CHANGE"] = "RECOMMENDATION"
    product: CustomProductInput
    node: CustomNodeInput
    recommended_quantity: int = Field(ge=0)
    inventory: CustomInventoryInput
    forecasts: CustomForecastInput
    open_purchase_orders: list[CustomOpenPOInput] = Field(default_factory=list)
    suppliers: list[CustomSupplierInput] = Field(min_length=1)
    budget: CustomBudgetInput
    storage: CustomStorageInput
    policy: CustomPolicyInput
    recent_sales: CustomRecentSalesInput | None = None
