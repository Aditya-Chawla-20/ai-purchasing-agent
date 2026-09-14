"""Opt-in provider contract checks; never run in the default suite."""

import os

import pytest
from purchasing.application.providers import GeminiToolProvider, GroqToolProvider
from purchasing.settings import settings

pytestmark = pytest.mark.live_provider


SITUATION = {
    "review_id": "live-contract-review",
    "product_id": "product-1",
    "node_id": "node-1",
    "preferred_supplier_id": "supplier-1",
    "recommended_quantity": 800,
    "scenario_type": "recommendation-review",
}


def _require_live(key: str | None) -> None:
    if os.getenv("RUN_LIVE_PROVIDER_TESTS") != "1" or not key:
        pytest.skip("Set RUN_LIVE_PROVIDER_TESTS=1 and the provider key to run live contracts.")


def test_gemini_returns_native_tool_calls():
    _require_live(settings.gemini_api_key)
    calls = GeminiToolProvider().plan_investigation_round(SITUATION, [], 1)
    assert calls
    assert all(call.tool_name and isinstance(call.arguments, dict) for call in calls)


def test_groq_agent_returns_native_tool_calls():
    _require_live(settings.groq_api_key)
    calls = GroqToolProvider().plan_investigation_round(SITUATION, [], 1)
    assert calls
    assert all(call.tool_name and isinstance(call.arguments, dict) for call in calls)
