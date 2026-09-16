from __future__ import annotations

import json
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.observability import get_logger, timed

logger = get_logger(__name__)
settings = get_settings()

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Raised when the LLM cannot produce a valid structured response
    after all retries are exhausted."""


class StructuredLLMClient:
    """Wraps the OpenAI chat completions API to reliably return a validated
    pydantic model, with:
      - JSON-mode / schema-guided prompting (structured outputs)
      - automatic retry with exponential backoff on transient failures
      - automatic *reprompting* with the validation error if the model
        returns malformed JSON, up to LLM_MAX_RETRIES times
    """

    def __init__(self) -> None:
        self._client = None
        if not settings.USE_MOCK_LLM and settings.OPENAI_API_KEY:
            from openai import AsyncOpenAI  # imported lazily; optional dep

            self._client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY, timeout=settings.LLM_TIMEOUT_SECONDS
            )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    )
    async def _call_raw(self, system_prompt: str, user_prompt: str) -> str:
        if self._client is None:
            raise LLMError("StructuredLLMClient invoked without a configured OpenAI client")
        with timed("llm.call"):
            response = await self._client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        return response.choices[0].message.content or "{}"

    async def get_structured(
        self, system_prompt: str, user_prompt: str, schema: Type[T]
    ) -> T:
        """Call the LLM and parse+validate its JSON reply into `schema`.

        If parsing/validation fails, the error is fed back to the model as
        additional context and it is retried up to LLM_MAX_RETRIES times
        before raising LLMError.
        """
        last_error: Exception | None = None
        prompt = user_prompt
        for attempt in range(1, settings.LLM_MAX_RETRIES + 1):
            try:
                raw = await self._call_raw(
                    system_prompt
                    + f"\n\nRespond ONLY with JSON matching this schema:\n{schema.model_json_schema()}",
                    prompt,
                )
                data = json.loads(raw)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.info(f"LLM structured output invalid on attempt {attempt}: {exc}")
                prompt = (
                    user_prompt
                    + f"\n\nYour previous response was invalid ({exc}). "
                    "Return corrected JSON only, no prose."
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.info(f"LLM call failed on attempt {attempt}: {exc}")
        raise LLMError(f"LLM failed to produce valid {schema.__name__} after retries: {last_error}")
