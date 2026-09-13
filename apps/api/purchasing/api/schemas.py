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
