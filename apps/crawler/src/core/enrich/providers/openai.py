"""OpenAI Batch API provider."""

from __future__ import annotations

import io
import json

from src.core.enrich.providers import BatchRequest, LLMUsage


class OpenAIBatchProvider:
    def __init__(self, model: str, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def submit_batch(
        self,
        requests: list[BatchRequest],
        response_schema: dict,
    ) -> str:
        lines: list[str] = []
        for req in requests:
            body = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": req.system_prompt},
                    {"role": "user", "content": req.user_content},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "enrichment_result",
                        "strict": True,
                        "schema": response_schema,
                    },
                },
            }
            lines.append(
                json.dumps(
                    {
                        "custom_id": req.custom_id,
                        "method": "POST",
                        "url": "/v1/chat/completions",
                        "body": body,
                    }
                )
            )

        jsonl = "\n".join(lines)
        file = await self._client.files.create(
            file=io.BytesIO(jsonl.encode()),
            purpose="batch",
        )
        batch = await self._client.batches.create(
            input_file_id=file.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        return batch.id

    async def check_batch(self, batch_id: str) -> str:
        batch = await self._client.batches.retrieve(batch_id)
        return _map_status(batch.status)

    async def collect_results(
        self,
        batch_id: str,
    ) -> list[tuple[str, dict | None, LLMUsage | None]]:
        batch = await self._client.batches.retrieve(batch_id)
        if not batch.output_file_id:
            return []

        content = await self._client.files.content(batch.output_file_id)
        results: list[tuple[str, dict | None, LLMUsage | None]] = []

        for line in content.text.strip().split("\n"):
            if not line:
                continue
            row = json.loads(line)
            custom_id = row["custom_id"]
            resp = row.get("response", {})
            body = resp.get("body", {})

            parsed = None
            usage = None

            if body.get("choices"):
                message = body["choices"][0].get("message", {})
                raw = message.get("content")
                if raw:
                    parsed = json.loads(raw)

            if body.get("usage"):
                u = body["usage"]
                usage = LLMUsage(
                    input_tokens=u.get("prompt_tokens", 0),
                    output_tokens=u.get("completion_tokens", 0),
                    model=self._model,
                    provider="openai",
                )

            results.append((custom_id, parsed, usage))

        return results


def _map_status(status: str) -> str:
    mapping = {
        "completed": "completed",
        "failed": "failed",
        "expired": "expired",
        "cancelled": "failed",
        "cancelling": "failed",
    }
    return mapping.get(status, "submitted")
