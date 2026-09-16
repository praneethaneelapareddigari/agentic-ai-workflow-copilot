from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.cache import cache_manager, make_cache_key
from app.config import get_settings
from app.observability import METRICS, get_logger, log_event
from app.schemas import PlanStep, ToolExecutionResult
from app.tools.registry import ToolRegistry
from app.tools.implementations import ToolError

logger = get_logger(__name__)
settings = get_settings()

# Tools whose output only depends on their arguments (pure/idempotent) are
# safe to cache. Tools with side effects (email, calendar writes) must not be.
_CACHEABLE_TOOLS = {
    "web_search", "weather_lookup", "calculator", "currency_converter",
    "unit_converter", "translate_text", "summarize_text", "sentiment_analysis",
    "stock_price_lookup", "database_query", "code_executor", "file_reader",
    "geolocation_lookup",
}


@dataclass
class ExecutorConfig:
    max_retries: int = field(default_factory=lambda: settings.MAX_TOOL_RETRIES)
    retry_base_delay: float = 0.1


class ToolExecutor:
    """Runs plan steps against the tool registry with:
      - Redis (or in-memory fallback) result caching for idempotent tools
      - retry with exponential backoff on ToolError
      - per-call timing fed into METRICS
      - independent steps executed concurrently via asyncio.gather
    """

    def __init__(self, registry: ToolRegistry, config: ExecutorConfig | None = None):
        self._registry = registry
        self._config = config or ExecutorConfig()

    async def run_plan(self, steps: list[PlanStep]) -> list[ToolExecutionResult]:
        # Steps with no unmet dependencies can run concurrently; steps that
        # depend on an earlier step's output run after their dependencies.
        results: dict[int, ToolExecutionResult] = {}
        remaining = list(steps)

        while remaining:
            ready = [s for s in remaining if all(d in results for d in s.depends_on)]
            if not ready:
                # Circular / unsatisfiable dependency — bail out safely.
                for s in remaining:
                    results[s.step_id] = ToolExecutionResult(
                        step_id=s.step_id, tool_name=s.tool_name or "unknown",
                        arguments=s.tool_arguments, success=False,
                        error="Unresolvable step dependency", attempts=0,
                    )
                break
            done = await asyncio.gather(*(self._run_step(s) for s in ready))
            for s, r in zip(ready, done):
                results[s.step_id] = r
            remaining = [s for s in remaining if s.step_id not in results]

        return [results[s.step_id] for s in steps]

    async def _run_step(self, step: PlanStep) -> ToolExecutionResult:
        if not step.tool_name:
            return ToolExecutionResult(
                step_id=step.step_id, tool_name="none", arguments={},
                success=True, output=None,
            )

        try:
            spec = self._registry.get(step.tool_name)
        except KeyError as exc:
            return ToolExecutionResult(
                step_id=step.step_id, tool_name=step.tool_name,
                arguments=step.tool_arguments, success=False, error=str(exc),
            )

        cache_key = None
        if step.tool_name in _CACHEABLE_TOOLS:
            cache_key = make_cache_key("tool", step.tool_name, step.tool_arguments)
            cached = await cache_manager.get_json(cache_key)
            if cached is not None:
                log_event(logger, "INFO", f"Cache hit for tool '{step.tool_name}'", tool=step.tool_name)
                return ToolExecutionResult(
                    step_id=step.step_id, tool_name=step.tool_name,
                    arguments=step.tool_arguments, success=True, output=cached,
                    attempts=0, cache_hit=True, duration_ms=0,
                )

        attempts = 0
        last_error: str | None = None
        start = time.perf_counter()
        while attempts <= self._config.max_retries:
            attempts += 1
            try:
                output = await spec.func(**step.tool_arguments)
                duration_ms = int((time.perf_counter() - start) * 1000)
                METRICS.observe("tool.duration_ms", duration_ms, tool=step.tool_name)
                METRICS.incr("tool.success", tool=step.tool_name)
                if cache_key:
                    await cache_manager.set_json(cache_key, output)
                return ToolExecutionResult(
                    step_id=step.step_id, tool_name=step.tool_name,
                    arguments=step.tool_arguments, success=True, output=output,
                    attempts=attempts, cache_hit=False, duration_ms=duration_ms,
                )
            except ToolError as exc:
                last_error = str(exc)
                METRICS.incr("tool.retry", tool=step.tool_name)
                log_event(
                    logger, "INFO", f"Tool '{step.tool_name}' failed (attempt {attempts}): {exc}",
                    tool=step.tool_name, attempt=attempts,
                )
                if attempts <= self._config.max_retries:
                    await asyncio.sleep(self._config.retry_base_delay * (2 ** (attempts - 1)))
            except TypeError as exc:
                # Bad arguments from the planner — not worth retrying.
                last_error = f"Invalid arguments for {step.tool_name}: {exc}"
                break

        duration_ms = int((time.perf_counter() - start) * 1000)
        METRICS.incr("tool.failure", tool=step.tool_name)
        return ToolExecutionResult(
            step_id=step.step_id, tool_name=step.tool_name,
            arguments=step.tool_arguments, success=False, error=last_error,
            attempts=attempts, cache_hit=False, duration_ms=duration_ms,
        )
