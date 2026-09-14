"""Capability-aware LLM provider adapters for investigation tool calling and approved action execution."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from purchasing.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCallRequest:
    tool_name: str
    arguments: dict[str, Any]


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_inventory",
        "description": "Get current on-hand, reserved, and damaged inventory for product and node.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["product_id", "node_id"],
        },
    },
    {
        "name": "get_demand_forecast",
        "description": "Get expected forecast demand for a product and node.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "node_id": {"type": "string"},
                "forecast_id": {"type": "string", "nullable": True},
            },
            "required": ["product_id", "node_id"],
        },
    },
    {
        "name": "list_open_purchase_orders",
        "description": "List existing open or confirmed purchase orders for this product and node.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["product_id", "node_id"],
        },
    },
    {
        "name": "get_supplier_terms",
        "description": "Get pricing, MOQ, lead time, and max availability from a supplier.",
        "parameters": {
            "type": "object",
            "properties": {
                "supplier_id": {"type": "string"},
                "product_id": {"type": "string"},
            },
            "required": ["supplier_id", "product_id"],
        },
    },
    {
        "name": "get_budget",
        "description": "Get purchasing budget and available funds for a fulfillment node.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "currency": {"type": "string", "default": "INR"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "get_storage_capacity",
        "description": "Get available warehouse storage capacity volume for a node.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "get_planning_policy",
        "description": "Get safety stock, review period, and product unit volume.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["product_id", "node_id"],
        },
    },
    {
        "name": "get_purchase_order",
        "description": "Fetch details of an existing purchase order by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "purchase_order_id": {"type": "string"},
            },
            "required": ["purchase_order_id"],
        },
    },
    {
        "name": "list_supplier_options",
        "description": "List all candidate suppliers and terms for a product and node.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["product_id", "node_id"],
        },
    },
    {
        "name": "get_recent_sales",
        "description": "Get recent sales observations over a time window for velocity analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "node_id": {"type": "string"},
                "hours": {"type": "integer", "default": 24},
            },
            "required": ["product_id", "node_id"],
        },
    },
]

ACTION_TOOL_SCHEMA: dict[str, Any] = {
    "name": "execute_approved_proposal",
    "description": "Execute an approved purchasing proposal by its unique proposal ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "proposal_id": {"type": "string"},
        },
        "required": ["proposal_id"],
    },
}


class BaseToolProvider:
    provider_name: str = "base"
    model_name: str = "base"

    def plan_investigation_round(
        self,
        situation: dict[str, Any],
        already_collected: list[str],
        round_number: int,
    ) -> list[ToolCallRequest]:
        raise NotImplementedError

    def generate_investigation_calls(
        self,
        situation: dict[str, Any],
        already_collected: list[str],
        round_number: int,
    ) -> list[ToolCallRequest]:
        return self.plan_investigation_round(situation, already_collected, round_number)

    def invoke_action_tool(self, proposal_id: str) -> ToolCallRequest | None:
        raise NotImplementedError

    def generate_action_call(self, proposal_id: str) -> ToolCallRequest | None:
        return self.invoke_action_tool(proposal_id)


class FakeScriptedToolProvider(BaseToolProvider):
    """Deterministic scriptable tool provider for offline unit and integration tests."""

    provider_name: str = "fake-provider"
    model_name: str = "mock-agent"

    def __init__(
        self,
        scripted_calls: list[list[ToolCallRequest]] | None = None,
        fail_action: bool = False,
    ):
        self.scripted_calls = scripted_calls or []
        self.fail_action = fail_action

    def plan_investigation_round(
        self,
        situation: dict[str, Any],
        already_collected: list[str],
        round_number: int,
    ) -> list[ToolCallRequest]:
        idx = round_number - 1
        if idx < len(self.scripted_calls):
            return self.scripted_calls[idx]

        # Default intelligent investigation selection:
        # Round 1: inventory, forecast, open POs, terms
        # Round 2: budget, storage, policy
        product_id = situation.get("product_id", "")
        node_id = situation.get("node_id", "")
        supplier_id = situation.get("preferred_supplier_id", "")

        if round_number == 1:
            calls = [
                ToolCallRequest("get_inventory", {"product_id": product_id, "node_id": node_id}),
                ToolCallRequest("get_demand_forecast", {"product_id": product_id, "node_id": node_id}),
                ToolCallRequest("list_open_purchase_orders", {"product_id": product_id, "node_id": node_id}),
                ToolCallRequest("get_supplier_terms", {"supplier_id": supplier_id, "product_id": product_id}),
            ]
            if situation.get("scenario_type") == "demand-change":
                calls.append(ToolCallRequest("get_recent_sales", {"product_id": product_id, "node_id": node_id, "hours": 24}))
            elif situation.get("scenario_type") == "supplier-shortfall":
                calls.append(ToolCallRequest("list_supplier_options", {"product_id": product_id, "node_id": node_id}))
            return calls
        elif round_number == 2:
            return [
                ToolCallRequest("get_budget", {"node_id": node_id, "currency": "INR"}),
                ToolCallRequest("get_storage_capacity", {"node_id": node_id}),
                ToolCallRequest("get_planning_policy", {"product_id": product_id, "node_id": node_id}),
            ]
        return []

    def invoke_action_tool(self, proposal_id: str) -> ToolCallRequest | None:
        if self.fail_action:
            return None
        return ToolCallRequest("execute_approved_proposal", {"proposal_id": proposal_id})


class GeminiToolProvider(BaseToolProvider):
    """Gemini native function-calling adapter. It never parses tool JSON from prose."""

    provider_name = "gemini"
    model_name = settings.gemini_model

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.gemini_api_key

    @staticmethod
    def _calls(response: Any) -> list[ToolCallRequest]:
        calls = getattr(response, "function_calls", None) or []
        return [ToolCallRequest(call.name, dict(call.args or {})) for call in calls]

    def _generate(self, prompt: str, schemas: list[dict[str, Any]]) -> list[ToolCallRequest]:
        if not self.api_key:
            raise RuntimeError("Gemini tool provider is not configured")
        from google import genai
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name=schema["name"],
                description=schema["description"],
                parameters_json_schema=schema["parameters"],
            )
            for schema in schemas
        ]
        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=declarations)],
                temperature=0,
            ),
        )
        return self._calls(response)

    def plan_investigation_round(
        self, situation: dict[str, Any], already_collected: list[str], round_number: int
    ) -> list[ToolCallRequest]:
        return self._generate(
            "Select only the needed read functions for this purchasing review. "
            "Do not explain or invent facts. "
            f"Situation: {json.dumps(situation, default=str)}. "
            f"Already collected: {already_collected}. Round: {round_number}/3.",
            TOOL_SCHEMAS,
        )

    def invoke_action_tool(self, proposal_id: str) -> ToolCallRequest | None:
        calls = self._generate(
            "The buyer approved this immutable proposal. Call execute_approved_proposal exactly once "
            f"with proposal_id={proposal_id}; do not provide any other fields.",
            [ACTION_TOOL_SCHEMA],
        )
        return calls[0] if len(calls) == 1 else None


class GroqToolProvider(BaseToolProvider):
    """OpenAI-compatible Groq adapter for a model that supports custom tools."""

    provider_name = "groq"
    model_name = settings.groq_agent_model

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.groq_api_key

    def _generate(self, prompt: str, schemas: list[dict[str, Any]]) -> list[ToolCallRequest]:
        if not self.api_key:
            raise RuntimeError("Groq tool provider is not configured")
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=settings.groq_base_url, timeout=settings.llm_timeout_seconds)
        response = client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a function router. Invoke one or more supplied functions now. "
                        "Do not describe, name, or list functions in prose. Never invent purchasing facts."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            tools=[{"type": "function", "function": schema} for schema in schemas],
            tool_choice="required",
            temperature=0,
        )
        calls = response.choices[0].message.tool_calls or []
        return [ToolCallRequest(call.function.name, json.loads(call.function.arguments)) for call in calls]

    def plan_investigation_round(self, situation: dict[str, Any], already_collected: list[str], round_number: int) -> list[ToolCallRequest]:
        return self._generate(
            "Invoke the read functions needed for this review now; respond only with function calls. "
            f"Situation: {json.dumps(situation, default=str)}. Collected: {already_collected}. Round {round_number}/3.",
            TOOL_SCHEMAS,
        )

    def invoke_action_tool(self, proposal_id: str) -> ToolCallRequest | None:
        calls = self._generate(
            f"Call execute_approved_proposal once with proposal_id={proposal_id}.", [ACTION_TOOL_SCHEMA]
        )
        return calls[0] if len(calls) == 1 else None


class ProviderCoordinator:
    """Ordered, capability-aware failover. The fake provider is test-only, never production fallback."""

    def __init__(self, primary_provider: BaseToolProvider | None = None, fallback_provider: BaseToolProvider | None = None):
        self.failures: list[dict[str, Any]] = []
        if primary_provider is not None:
            self._primary = primary_provider
            self.fallback = fallback_provider
        elif not settings.llm_enabled:
            # Offline correctness tests exercise the same dispatcher/workflow without credentials.
            self._primary = FakeScriptedToolProvider()
            self.fallback = FakeScriptedToolProvider()
        else:
            self._primary = GeminiToolProvider()
            self.fallback = GroqToolProvider()

    @property
    def primary_provider(self) -> BaseToolProvider:
        return self._primary

    @primary_provider.setter
    def primary_provider(self, provider: BaseToolProvider) -> None:
        self._primary = provider

    @property
    def primary(self) -> BaseToolProvider:
        return self._primary

    @primary.setter
    def primary(self, provider: BaseToolProvider) -> None:
        self._primary = provider

    def _call_investigation(
        self,
        provider: BaseToolProvider,
        situation: dict[str, Any],
        already_collected: list[str],
        round_number: int,
    ) -> list[ToolCallRequest]:
        if hasattr(provider, "generate_investigation_calls"):
            try:
                return provider.generate_investigation_calls(situation, already_collected, round_number)
            except TypeError:
                pass
        return provider.plan_investigation_round(situation, already_collected, round_number)

    def run_investigation_round(
        self,
        situation: dict[str, Any],
        already_collected: list[str],
        round_number: int,
    ) -> tuple[str, str, list[ToolCallRequest]]:
        providers = [self.primary] + ([self.fallback] if self.fallback else [])
        last_error: Exception | None = None
        for provider in providers:
            started = time.perf_counter()
            try:
                calls = self._call_investigation(
                    provider, situation, already_collected, round_number
                )
                if not calls:
                    raise RuntimeError("Provider returned no tool calls")
                return provider.provider_name, provider.model_name, calls
            except Exception as exc:
                last_error = exc
                code = "EMPTY_TOOL_CALLS" if str(exc) == "Provider returned no tool calls" else "PROVIDER_UNAVAILABLE"
                self.failures.append(
                    {
                        "provider": provider.provider_name,
                        "model": provider.model_name,
                        "failure_code": code,
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                    }
                )
                logger.warning("Tool provider %s failed: %s", provider.provider_name, exc)
        raise RuntimeError("No tool-capable provider is available") from last_error

    def drain_failures(self) -> list[dict[str, Any]]:
        failures, self.failures = self.failures, []
        return failures

    def investigate_round(
        self,
        session: Any = None,
        dispatcher: Any = None,
        round_number: int = 1,
        context: dict[str, Any] | None = None,
    ) -> list[ToolCallRequest]:
        situation = dict(context or {})
        if dispatcher and getattr(dispatcher, "context", None):
            ctx = dispatcher.context
            situation.setdefault("review_id", ctx.review_id)
            situation.setdefault("product_id", getattr(ctx, "product_id", None))
            situation.setdefault("node_id", getattr(ctx, "node_id", None))
            situation.setdefault("preferred_supplier_id", getattr(ctx, "preferred_supplier_id", None))
        _p, _m, calls = self.run_investigation_round(
            situation=situation,
            already_collected=[],
            round_number=round_number,
        )
        return calls

    def execute_action_tool_call(self, proposal_id: str) -> ToolCallRequest | None:
        try:
            if hasattr(self.primary, "generate_action_call"):
                call = self.primary.generate_action_call(proposal_id)
            else:
                call = self.primary.invoke_action_tool(proposal_id)
            if call is not None:
                return call
        except Exception as e:
            logger.warning("Primary provider failed to invoke action tool: %s", e)
        if self.fallback is None:
            return None
        try:
            if hasattr(self.fallback, "generate_action_call"):
                return self.fallback.generate_action_call(proposal_id)
            return self.fallback.invoke_action_tool(proposal_id)
        except Exception as exc:
            logger.warning("Groq provider failed to invoke action tool: %s", exc)
            return None
