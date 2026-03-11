"""Anthropic Message Batches provider."""

from __future__ import annotations

import json

from src.core.llm_providers import BatchRequest, LLMUsage


class AnthropicBatchProvider:
    def __init__(self, model: str, api_key: str) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def submit_batch(
        self,
        requests: list[BatchRequest],
        response_schema: dict,
    ) -> str:
        batch_requests = []
        for req in requests:
            batch_requests.append(
                {
                    "custom_id": req.custom_id,
                    "params": {
                        "model": self._model,
                        "max_tokens": 4096,
                        "system": req.system_prompt,
                        "messages": [{"role": "user", "content": req.user_content}],
                        "tools": [
                            {
                                "name": "enrichment_result",
                                "description": "Structured extraction result",
                                "input_schema": response_schema,
                            }
                        ],
                        "tool_choice": {"type": "tool", "name": "enrichment_result"},
                    },
                }
            )

        batch = await self._client.messages.batches.create(requests=batch_requests)
        return batch.id

    async def check_batch(self, batch_id: str) -> str:
        batch = await self._client.messages.batches.retrieve(batch_id)
        return _map_status(batch.processing_status)

    async def collect_results(
        self,
        batch_id: str,
    ) -> list[tuple[str, dict | None, LLMUsage | None]]:
        results: list[tuple[str, dict | None, LLMUsage | None]] = []

        async for item in self._client.messages.batches.results(batch_id):
            custom_id = item.custom_id
            parsed = None
            usage = None

            if item.result.type == "succeeded":
                message = item.result.message
                for block in message.content:
                    if block.type == "tool_use" and block.name == "enrichment_result":
                        raw = block.input
                        parsed = raw if isinstance(raw, dict) else json.loads(raw)
                        break

                usage = LLMUsage(
                    input_tokens=message.usage.input_tokens,
                    output_tokens=message.usage.output_tokens,
                    model=self._model,
                    provider="anthropic",
                )

            results.append((custom_id, parsed, usage))

        return results


def _map_status(status: str) -> str:
    mapping = {
        "ended": "completed",
        "failed": "failed",
        "expired": "expired",
        "canceling": "failed",
        "canceled": "failed",
    }
    return mapping.get(status, "submitted")
