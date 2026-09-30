"""A chat model behind any OpenAI-compatible endpoint (``LODESTAR_LLM_PROVIDER=openai``).

Use it to point Lodestar at a larger model: a company gateway, vLLM, Ollama, LM Studio or a
hosted API. Only the chat model moves. Embeddings and the index stay on this machine.

What is sent: the conversation messages, which contain the question and the code snippets that
were retrieved for it (and, for Insights summaries, short excerpts). Nothing else leaves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

# The prompts ask for short answers; the caps only protect against a runaway model.
STREAM_MAX_TOKENS = 1500
COMPLETE_MAX_TOKENS = 400


class RemoteLLM:
    """``LLMClient`` implementation for an OpenAI-compatible ``/v1/chat/completions`` API."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 120.0, http_client=None):
        from openai import AsyncOpenAI

        self.base_url = base_url
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",  # local servers ignore it, the client insists on one
            timeout=timeout,
            max_retries=1,
            http_client=http_client,
        )

    async def stream_chat(self, messages: list[dict], model: str) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            temperature=0.1,
            max_tokens=STREAM_MAX_TOKENS,
        )
        async for event in stream:
            if event.choices and event.choices[0].delta.content:
                yield event.choices[0].delta.content

    async def complete(self, messages: list[dict], model: str) -> str:
        response = await self._client.chat.completions.create(
            model=model, messages=messages, temperature=0.0, max_tokens=COMPLETE_MAX_TOKENS
        )
        return response.choices[0].message.content or ""

    async def check(self) -> str | None:
        """None when the endpoint answers, else a short reason. Used by the Settings page."""
        try:
            await self._client.models.list()
        except Exception as exc:  # any failure is reported the same way
            return f"{type(exc).__name__}: {exc}"[:200]
        return None
