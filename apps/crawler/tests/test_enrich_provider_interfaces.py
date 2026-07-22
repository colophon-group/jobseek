"""Runtime interface checks for optional enrichment provider SDKs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.enrich.providers import BatchRequest
from src.core.enrich.providers.anthropic import AnthropicBatchProvider
from src.core.enrich.providers.gemini import GeminiBatchProvider


async def test_gemini_v2_batch_generate_content_interface(monkeypatch):
    """The v2 SDK retains the Batch API and GenerateContent request types we use."""
    from google import genai

    create = AsyncMock(return_value=SimpleNamespace(name="batches/test"))
    client = SimpleNamespace(aio=SimpleNamespace(batches=SimpleNamespace(create=create)))
    monkeypatch.setattr(genai, "Client", lambda *, api_key: client)

    provider = GeminiBatchProvider(model="gemini-test", api_key="test-key")
    batch_id = await provider.submit_batch(
        [BatchRequest("request-1", "Follow the schema", "Describe this role")],
        {"type": "object", "properties": {"title": {"type": "string"}}},
    )

    assert batch_id == "batches/test"
    create.assert_awaited_once()
    call = create.await_args.kwargs
    assert call["model"] == "gemini-test"
    inlined_request = call["src"].inlined_requests[0]
    assert inlined_request.metadata == {"key": "request-1"}
    assert inlined_request.model == "gemini-test"
    assert inlined_request.contents[0].parts[0].text == "Describe this role"
    assert inlined_request.config.system_instruction == "Follow the schema"
    assert inlined_request.config.response_mime_type == "application/json"


async def test_gemini_v2_inlined_batch_response_interface(monkeypatch):
    """Response metadata carries the caller key after the v2 batch model rename."""
    from google import genai
    from google.genai import types

    response = types.InlinedResponse(
        metadata={"key": "request-1"},
        response=types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text='{"title":"Engineer"}')],
                    )
                )
            ],
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=12,
                candidates_token_count=4,
            ),
        ),
    )
    get = AsyncMock(
        return_value=SimpleNamespace(dest=types.BatchJobDestination(inlined_responses=[response]))
    )
    client = SimpleNamespace(aio=SimpleNamespace(batches=SimpleNamespace(get=get)))
    monkeypatch.setattr(genai, "Client", lambda *, api_key: client)

    provider = GeminiBatchProvider(model="gemini-test", api_key="test-key")
    results = await provider.collect_results("batches/test")

    assert results[0][0] == "request-1"
    assert results[0][1] == {"title": "Engineer"}
    assert results[0][2] is not None
    assert results[0][2].input_tokens == 12
    assert results[0][2].output_tokens == 4


async def test_gemini_v2_rejects_response_without_request_key(monkeypatch):
    """Never persist a provider response that cannot be mapped to a posting."""
    from google import genai
    from google.genai import types

    get = AsyncMock(
        return_value=SimpleNamespace(
            dest=types.BatchJobDestination(inlined_responses=[types.InlinedResponse(metadata={})])
        )
    )
    client = SimpleNamespace(aio=SimpleNamespace(batches=SimpleNamespace(get=get)))
    monkeypatch.setattr(genai, "Client", lambda *, api_key: client)

    provider = GeminiBatchProvider(model="gemini-test", api_key="test-key")
    with pytest.raises(ValueError, match="missing request metadata key"):
        await provider.collect_results("batches/test")


async def test_anthropic_batch_messages_interface(monkeypatch):
    """The current Anthropic SDK retains the async Message Batches call shape."""
    import anthropic

    create = AsyncMock(return_value=SimpleNamespace(id="batch_test"))
    batches = SimpleNamespace(create=create)
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda *, api_key: client)

    provider = AnthropicBatchProvider(model="claude-test", api_key="test-key")
    batch_id = await provider.submit_batch(
        [BatchRequest("request-1", "Follow the schema", "Describe this role")],
        {"type": "object", "properties": {"title": {"type": "string"}}},
    )

    assert batch_id == "batch_test"
    create.assert_awaited_once()
    request = create.await_args.kwargs["requests"][0]
    assert request["custom_id"] == "request-1"
    assert request["params"]["model"] == "claude-test"
    assert request["params"]["tool_choice"] == {
        "type": "tool",
        "name": "enrichment_result",
    }
