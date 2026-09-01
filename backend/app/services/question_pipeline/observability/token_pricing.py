"""
Per-1M-token USD rates for cost estimation.

These are PLACEHOLDER values, not verified against a live pricing page as
of writing - confirm the exact current rate for whatever model_name this
app actually calls before treating any $ total this produces as accurate
enough for a finance/reporting decision. Update _RATES below once confirmed.
"""
from typing import Dict, Tuple

# model_name -> (input_usd_per_million_tokens, output_usd_per_million_tokens)
_RATES: Dict[str, Tuple[float, float]] = {
    # This pipeline's actual model (backend/app/services/llm/openai_client.py,
    # OPENAI_MODEL env var, defaults to gpt-4o-mini) - confirm against
    # https://openai.com/api/pricing before trusting cost totals.
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
}
_DEFAULT_RATE: Tuple[float, float] = (0.15, 0.60)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _RATES.get(model, _DEFAULT_RATE)
    cost = (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate
    return round(cost, 6)
