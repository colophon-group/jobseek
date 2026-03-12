"""LLM batch provider abstraction.

Defines the BatchProvider protocol, request/response data classes, and
a factory function that lazily imports the appropriate SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMUsage:
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


@dataclass
class BatchRequest:
    custom_id: str
    system_prompt: str
    user_content: str


class BatchProvider(Protocol):
    """Batch-oriented LLM provider."""

    async def submit_batch(
        self,
        requests: list[BatchRequest],
        response_schema: dict,
    ) -> str:
        """Submit a batch. Returns provider batch ID."""
        ...

    async def check_batch(self, batch_id: str) -> str:
        """Check batch status. Returns submitted|completed|failed|expired."""
        ...

    async def collect_results(
        self,
        batch_id: str,
    ) -> list[tuple[str, dict | None, LLMUsage | None]]:
        """Download results. Returns [(custom_id, parsed_json, usage), ...]."""
        ...


def create_provider(provider: str, model: str, api_key: str) -> BatchProvider:
    """Factory. Lazily imports the appropriate SDK."""
    if provider == "openai":
        from src.core.enrich.providers.openai import OpenAIBatchProvider

        return OpenAIBatchProvider(model=model, api_key=api_key)
    elif provider == "anthropic":
        from src.core.enrich.providers.anthropic import AnthropicBatchProvider

        return AnthropicBatchProvider(model=model, api_key=api_key)
    elif provider == "gemini":
        from src.core.enrich.providers.gemini import GeminiBatchProvider

        return GeminiBatchProvider(model=model, api_key=api_key)
    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}")
