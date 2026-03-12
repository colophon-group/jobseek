"""Google Gemini Batch API provider."""

from __future__ import annotations

from src.core.enrich.providers import BatchRequest, LLMUsage


class GeminiBatchProvider:
    def __init__(self, model: str, api_key: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def submit_batch(
        self,
        requests: list[BatchRequest],
        response_schema: dict,
    ) -> str:
        from google.genai import types

        inline_requests = []
        for req in requests:
            inline_requests.append(
                types.BatchJobSource(
                    key=req.custom_id,
                    request=types.GenerateContentRequest(
                        model=self._model,
                        contents=[
                            types.Content(
                                role="user",
                                parts=[types.Part(text=req.user_content)],
                            )
                        ],
                        config=types.GenerateContentConfig(
                            system_instruction=req.system_prompt,
                            response_mime_type="application/json",
                            response_schema=response_schema,
                        ),
                    ),
                )
            )

        batch = await self._client.aio.batches.create(
            model=self._model,
            src=types.BatchJobSource(inline_requests=inline_requests),
        )
        return batch.name

    async def check_batch(self, batch_id: str) -> str:
        batch = await self._client.aio.batches.get(name=batch_id)
        return _map_status(batch.state.name if batch.state else "")

    async def collect_results(
        self,
        batch_id: str,
    ) -> list[tuple[str, dict | None, LLMUsage | None]]:
        import json

        batch = await self._client.aio.batches.get(name=batch_id)
        results: list[tuple[str, dict | None, LLMUsage | None]] = []

        if not batch.dest or not batch.dest.inline_responses:
            return results

        for resp in batch.dest.inline_responses:
            custom_id = resp.key
            parsed = None
            usage = None

            if resp.response and resp.response.candidates:
                candidate = resp.response.candidates[0]
                if candidate.content and candidate.content.parts:
                    text = candidate.content.parts[0].text
                    if text:
                        parsed = json.loads(text)

            if resp.response and resp.response.usage_metadata:
                um = resp.response.usage_metadata
                usage = LLMUsage(
                    input_tokens=um.prompt_token_count or 0,
                    output_tokens=um.candidates_token_count or 0,
                    model=self._model,
                    provider="gemini",
                )

            results.append((custom_id, parsed, usage))

        return results


def _map_status(state: str) -> str:
    mapping = {
        "JOB_STATE_SUCCEEDED": "completed",
        "JOB_STATE_FAILED": "failed",
        "JOB_STATE_CANCELLED": "failed",
    }
    return mapping.get(state, "submitted")
