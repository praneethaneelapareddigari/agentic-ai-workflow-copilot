"""
A deterministic, dependency-free stand-in for the OpenAI-backed
StructuredLLMClient. It implements the exact same `get_structured` contract
so the pipeline code has zero branching between "mock mode" and "real mode" —
only `app.config.Settings.USE_MOCK_LLM` decides which one gets constructed.

This is what lets the whole 8-stage pipeline, including planning and tool
selection over 15 tools, be demoed and unit-tested with no API key and no
network access, while remaining a drop-in replacement for the real client.
"""
from __future__ import annotations

import json
import re
from typing import Type, TypeVar

from pydantic import BaseModel

from app.schemas import ExecutionPlan, FinalResponse, IntentResult, PlanStep, ValidationResult
from app.tools.registry import ToolRegistry

T = TypeVar("T", bound=BaseModel)

_KEYWORD_TOOL_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bweather\b|\btemperature\b|\bforecast\b", re.I), "weather_lookup"),
    (re.compile(r"\bconvert\b.*\b(usd|eur|gbp|inr|jpy)\b|\bcurrency\b|\bexchange rate\b", re.I), "currency_converter"),
    (re.compile(r"\bkm\b|\bmiles?\b|\bkg\b|\blbs?\b|\bcelsius\b|\bfahrenheit\b", re.I), "unit_converter"),
    (re.compile(r"[-+*/]\s*\d|\bcalculate\b|\bmultiply\b|\bdivide\b", re.I), "calculator"),
    (re.compile(r"\bemail\b|\bsend .* message\b", re.I), "send_email"),
    (re.compile(r"\bschedule\b|\bmeeting\b|\bcalendar\b|\bbook .* time\b", re.I), "create_calendar_event"),
    (re.compile(r"\btranslate\b", re.I), "translate_text"),
    (re.compile(r"\bsummarize\b|\btl;?dr\b", re.I), "summarize_text"),
    (re.compile(r"\bsentiment\b|\bhow (does|do) .* feel\b", re.I), "sentiment_analysis"),
    (re.compile(r"\bstock\b|\bshare price\b|\bticker\b", re.I), "stock_price_lookup"),
    (re.compile(r"\bcustomer(s)?\b|\bticket(s)?\b|\bmrr\b|\bdatabase\b", re.I), "database_query"),
    (re.compile(r"\baverage\b|\bmean\b|\bmedian\b|\bstatistics\b", re.I), "code_executor"),
    (re.compile(r"\bfile\b|\bdocument\b|\bread .* onboarding\b|\bpricing\b", re.I), "file_reader"),
    (re.compile(r"\btimezone\b|\bcoordinates\b|\blocated\b|\bwhere is\b", re.I), "geolocation_lookup"),
    (re.compile(r"\bsearch\b|\bfind (out|information)\b|\blook up\b|\blatest\b|\bnews\b", re.I), "web_search"),
]


def _default_args_for(tool_name: str, message: str) -> dict:
    """Best-effort argument extraction from free text; good enough for a
    deterministic demo/test harness (not meant to rival a real LLM)."""
    if tool_name == "weather_lookup":
        m = re.search(r"\bin ([A-Za-z]+(?: [A-Za-z]+)?)\b(?=[.?!,]| and | for |$)", message)
        return {"location": (m.group(1).strip() if m else "New York")}
    if tool_name == "calculator":
        m = re.search(r"([\d.\s+\-*/()]{3,})", message)
        return {"expression": (m.group(1).strip() if m else "2 + 2")}
    if tool_name == "currency_converter":
        m = re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z]{3})\s*(?:to|->|in)\s*([A-Za-z]{3})", message, re.I)
        if m:
            return {"amount": float(m.group(1)), "from_currency": m.group(2).upper(), "to_currency": m.group(3).upper()}
        return {"amount": 100.0, "from_currency": "USD", "to_currency": "EUR"}
    if tool_name == "unit_converter":
        m = re.search(r"(\d+(?:\.\d+)?)\s*(km|mi|kg|lb|c|f)\b.*?(km|mi|kg|lb|c|f)\b", message, re.I)
        if m:
            return {"value": float(m.group(1)), "from_unit": m.group(2), "to_unit": m.group(3)}
        return {"value": 10.0, "from_unit": "km", "to_unit": "mi"}
    if tool_name == "send_email":
        return {"to": "team@example.com", "subject": "Update", "body": message[:200]}
    if tool_name == "create_calendar_event":
        return {"title": "Meeting", "start_iso": "2026-09-20T10:00:00", "duration_minutes": 30}
    if tool_name == "translate_text":
        m = re.search(r"to ([A-Za-z]+)", message, re.I)
        return {"text": message, "target_language": (m.group(1) if m else "Spanish")}
    if tool_name == "summarize_text":
        return {"text": message, "max_sentences": 2}
    if tool_name == "sentiment_analysis":
        return {"text": message}
    if tool_name == "stock_price_lookup":
        m = re.search(r"\b([A-Z]{2,5})\b", message)
        return {"ticker": (m.group(1) if m else "AAPL")}
    if tool_name == "database_query":
        table = "customers" if "customer" in message.lower() or "mrr" in message.lower() else "tickets"
        return {"table": table, "filters": {}}
    if tool_name == "code_executor":
        return {"python_expression": "mean([1, 2, 3, 4, 5])"}
    if tool_name == "file_reader":
        fname = "pricing.md" if "pricing" in message.lower() else "onboarding.md"
        return {"file_name": fname}
    if tool_name == "geolocation_lookup":
        m = re.search(r"is ([A-Za-z ]+?)(?:[.?!]|$)", message)
        return {"ip_or_city": (m.group(1).strip() if m else "Berlin")}
    if tool_name == "web_search":
        return {"query": message[:120], "max_results": 3}
    return {}


def _match_tools(message: str, registry: ToolRegistry, limit: int) -> list[str]:
    matched: list[str] = []
    for pattern, tool_name in _KEYWORD_TOOL_MAP:
        if pattern.search(message) and tool_name not in matched:
            matched.append(tool_name)
        if len(matched) >= limit:
            break
    if not matched:
        matched = ["web_search"]
    # Filter to tools that actually exist in the registry passed in.
    available = {s.name for s in registry.list_specs()}
    return [t for t in matched if t in available] or ["web_search"]


class MockLLMClient:
    """Deterministic stand-in for StructuredLLMClient. Same public method
    signature (`get_structured`) so pipeline stages never need to know
    which implementation is behind `self.llm`."""

    def __init__(self, registry: ToolRegistry, max_plan_steps: int = 8):
        self._registry = registry
        self._max_plan_steps = max_plan_steps

    async def get_structured(self, system_prompt: str, user_prompt: str, schema: Type[T]) -> T:
        context = self._parse_context(user_prompt)

        if schema is IntentResult:
            return self._intent(context)  # type: ignore[return-value]
        if schema is ExecutionPlan:
            return self._plan(context)  # type: ignore[return-value]
        if schema is ValidationResult:
            return self._validate(context)  # type: ignore[return-value]
        if schema is FinalResponse:
            return self._final(context)  # type: ignore[return-value]
        raise ValueError(f"MockLLMClient has no handler for schema {schema}")

    @staticmethod
    def _parse_context(user_prompt: str) -> dict:
        try:
            return json.loads(user_prompt)
        except json.JSONDecodeError:
            return {"message": user_prompt}

    def _intent(self, context: dict) -> IntentResult:
        message = context.get("message", "")
        tools_needed = _match_tools(message, self._registry, limit=self._max_plan_steps) != ["web_search"] or True
        return IntentResult(
            intent=(message[:60] or "general_request"),
            entities={"raw_message": message},
            requires_tools=tools_needed,
            confidence=0.9,
        )

    def _plan(self, context: dict) -> ExecutionPlan:
        message = context.get("message", "")
        tool_names = _match_tools(message, self._registry, limit=min(3, self._max_plan_steps))
        steps = []
        for i, tool_name in enumerate(tool_names, start=1):
            steps.append(
                PlanStep(
                    step_id=i,
                    description=f"Use {tool_name} to address part of the request",
                    tool_name=tool_name,
                    tool_arguments=_default_args_for(tool_name, message),
                    depends_on=[],
                )
            )
        return ExecutionPlan(goal=message[:120] or "Fulfil user request", steps=steps)

    def _validate(self, context: dict) -> ValidationResult:
        results = context.get("tool_results", [])
        issues = [r["error"] for r in results if not r.get("success") and r.get("error")]
        return ValidationResult(
            all_steps_valid=len(issues) == 0,
            issues=issues,
            needs_replan=False,
        )

    def _final(self, context: dict) -> FinalResponse:
        message = context.get("message", "")
        results = context.get("tool_results", [])
        lines = []
        citations = []
        for r in results:
            tool = r.get("tool_name")
            output = r.get("output")
            if r.get("success"):
                lines.append(f"- **{tool}** returned: {json.dumps(output)}")
                citations.append(tool)
            else:
                lines.append(f"- **{tool}** failed after {r.get('attempts', 1)} attempt(s): {r.get('error')}")
        details = "\n".join(lines) if lines else "No tools were required for this request."
        summary = f"Completed your request: \"{message[:80]}\"." if message else "Request completed."
        return FinalResponse(summary=summary, details=details, citations=citations)
