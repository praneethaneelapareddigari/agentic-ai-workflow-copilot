from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from app.tools import implementations as impl


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    func: Callable[..., Coroutine[Any, Any, dict[str, Any]]]

    @property
    def parameters(self) -> list[str]:
        return [
            p.name
            for p in inspect.signature(self.func).parameters.values()
            if p.name != "self"
        ]

    def as_llm_schema(self) -> dict[str, Any]:
        """Render an OpenAI-function-calling-style schema for prompting."""
        sig = inspect.signature(self.func)
        props = {}
        required = []
        for name, param in sig.parameters.items():
            ptype = "string"
            if param.annotation in (int, float):
                ptype = "number"
            elif param.annotation is bool:
                ptype = "boolean"
            elif param.annotation is dict:
                ptype = "object"
            props[name] = {"type": ptype}
            if param.default is inspect._empty:
                required.append(name)
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": props, "required": required},
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, name: str, description: str, func) -> None:
        self._tools[name] = ToolSpec(name=name, description=description, func=func)

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def list_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def catalog_for_prompt(self) -> str:
        lines = []
        for spec in self._tools.values():
            lines.append(f"- {spec.name}({', '.join(spec.parameters)}): {spec.description}")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._tools)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("web_search", "Search the web for current information", impl.web_search)
    registry.register("weather_lookup", "Get current weather for a location", impl.weather_lookup)
    registry.register("calculator", "Evaluate an arithmetic expression", impl.calculator)
    registry.register("currency_converter", "Convert an amount between currencies", impl.currency_converter)
    registry.register("unit_converter", "Convert a value between units (km/mi, kg/lb, C/F)", impl.unit_converter)
    registry.register("send_email", "Send an email to a recipient", impl.send_email)
    registry.register("create_calendar_event", "Create a calendar event", impl.create_calendar_event)
    registry.register("translate_text", "Translate text into a target language", impl.translate_text)
    registry.register("summarize_text", "Summarize a block of text", impl.summarize_text)
    registry.register("sentiment_analysis", "Classify sentiment of a piece of text", impl.sentiment_analysis)
    registry.register("stock_price_lookup", "Look up a stock's current price", impl.flaky_stock_price_lookup)
    registry.register("database_query", "Query internal customer/ticket tables", impl.database_query)
    registry.register("code_executor", "Evaluate a restricted numeric/statistics expression", impl.code_executor)
    registry.register("file_reader", "Read the contents of a known document", impl.file_reader)
    registry.register("geolocation_lookup", "Resolve a city or IP to coordinates/timezone", impl.geolocation_lookup)
    return registry


default_registry = build_default_registry()
