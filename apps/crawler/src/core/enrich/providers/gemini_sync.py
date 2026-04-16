"""Google Gemini synchronous (non-batch) provider for local-mode enrichment."""

from __future__ import annotations

import json

from src.core.enrich.providers import LLMUsage


class GeminiSyncProvider:
    """Single-call Gemini provider using the aio generate_content API.

    Implements the SyncProvider Protocol:
        async def generate(system_prompt, user_content, response_schema) -> (dict, LLMUsage)
    """

    def __init__(self, model: str, api_key: str) -> None:
        from google import genai  # noqa: PLC0415

        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def generate(
        self,
        system_prompt: str,
        user_content: str,
        response_schema: dict,
    ) -> tuple[dict, LLMUsage]:
        """Make one structured JSON call. Returns (parsed_dict, LLMUsage)."""
        from google.genai import types  # noqa: PLC0415

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=response_schema,
            ),
        )

        text = response.candidates[0].content.parts[0].text
        parsed = json.loads(text)

        um = response.usage_metadata
        usage = LLMUsage(
            input_tokens=(um.prompt_token_count or 0) if um else 0,
            output_tokens=(um.candidates_token_count or 0) if um else 0,
            model=self._model,
            provider="gemini",
        )
        return parsed, usage
