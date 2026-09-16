from __future__ import annotations

from typing import Protocol, Type, TypeVar

from pydantic import BaseModel

from app.config import get_settings
from app.llm.client import StructuredLLMClient
from app.llm.mock import MockLLMClient
from app.tools.registry import ToolRegistry, default_registry

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    async def get_structured(self, system_prompt: str, user_prompt: str, schema: Type[T]) -> T: ...


def build_llm_client(registry: ToolRegistry = default_registry) -> LLMClient:
    settings = get_settings()
    if settings.USE_MOCK_LLM or not settings.OPENAI_API_KEY:
        return MockLLMClient(registry=registry, max_plan_steps=settings.MAX_PLAN_STEPS)
    return StructuredLLMClient()
