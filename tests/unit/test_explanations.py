import pytest
from purchasing.application.explanations import (
    DecisionExplanation,
    ExplanationFactor,
    ExplanationProvider,
    fallback_explanation,
    gemini_explanation_schema,
)
from purchasing.domain.policy import EvidenceFact, PurchaseInputs, calculate_decision
from purchasing.settings import settings


@pytest.fixture
def decision():
    return calculate_decision(
        PurchaseInputs(
            original_quantity=800,
            on_hand=180,
            reserved=20,
            damaged=10,
            confirmed_incoming=100,
            forecast_demand=700,
            safety_stock=50,
            unit_cost_minor=1250,
            currency="INR",
            budget_available_minor=600_000,
            minimum_order_quantity=50,
            available_supplier_quantity=500,
            available_storage_volume=4500,
            product_unit_volume=10,
            evidence=(
                EvidenceFact(
                    name="inventory",
                    source="mock-erp",
                    observed_at="2026-09-13T00:00:00Z",
                    value=180,
                ),
            ),
        )
    )


def test_fallback_explanation_uses_policy_values(decision):
    explanation = fallback_explanation(decision)
    ExplanationProvider._validate_grounding(explanation, decision)
    assert "800" in explanation.summary
    assert "450" in explanation.summary
    assert "approval" in explanation.next_action.lower()


@pytest.mark.parametrize(
    "factor",
    [
        ExplanationFactor(reason_code="INVENTED_REASON"),
        ExplanationFactor(reason_code="NEED_LOWER_THAN_ORIGINAL", evidence_refs=["secret"]),
    ],
)
def test_grounding_rejects_unknown_reason_or_evidence(decision, factor):
    explanation = DecisionExplanation(
        summary="Modify from 800 to 450 units.",
        important_factors=[factor],
        constraint_summary="All hard limits pass.",
        next_action="Buyer approval is required.",
    )
    with pytest.raises(ValueError):
        ExplanationProvider._validate_grounding(explanation, decision)


def test_grounding_rejects_unsupported_numeric_claim(decision):
    explanation = DecisionExplanation(
        summary="Modify from 800 to 451 units.",
        constraint_summary="All hard limits pass.",
        next_action="Buyer approval is required.",
    )
    with pytest.raises(ValueError, match="numeric"):
        ExplanationProvider._validate_grounding(explanation, decision)


def test_gemini_schema_omits_unsupported_additional_properties():
    schema = gemini_explanation_schema().model_dump(mode="json", exclude_none=True)
    assert "additional_properties" not in str(schema)


def test_provider_failure_uses_configured_next_provider(monkeypatch, decision):
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "llm_fallback_providers", "groq,nvidia")
    monkeypatch.setattr(settings, "gemini_api_key", "configured")
    monkeypatch.setattr(settings, "groq_api_key", "configured")

    calls = []

    def unavailable(*_args, **_kwargs):
        calls.append("gemini")
        raise RuntimeError("provider unavailable")

    def groq_response(_self, provider, *_args, **_kwargs):
        calls.append(provider)
        return fallback_explanation(decision)

    monkeypatch.setattr(ExplanationProvider, "_gemini", unavailable)
    monkeypatch.setattr(ExplanationProvider, "_openai_compatible", groq_response)

    _explanation, provider = ExplanationProvider().explain(decision)

    assert calls == ["gemini", "groq"]
    assert provider == "groq"


def test_disabled_llm_uses_template_without_contacting_a_provider(monkeypatch, decision):
    monkeypatch.setattr(settings, "llm_enabled", False)
    monkeypatch.setattr(settings, "gemini_api_key", "configured")
    monkeypatch.setattr(
        ExplanationProvider,
        "_gemini",
        lambda *_args: pytest.fail("A disabled LLM must not make a provider call"),
    )

    _explanation, provider = ExplanationProvider().explain(decision)

    assert provider == "template"
