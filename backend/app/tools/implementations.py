"""
Concrete tool implementations the agent pipeline can invoke.

Every tool is a small async function with a JSON-serializable input/output
contract. They are intentionally deterministic mocks (no outbound network
calls) so the project runs and is testable with zero external
dependencies/API keys — but each one documents exactly where a real
integration (a web search API, a weather API, an SMTP relay, etc.) would be
wired in for production. Swapping a mock for a real call is a one-function
change; the pipeline, retries, caching and validation around it are
unaffected.

One tool (`flaky_stock_price_lookup`) deliberately fails on its first
attempt so the pipeline's retry/error-handling stage has something real to
demonstrate.
"""
from __future__ import annotations

import asyncio
import random
import statistics
from datetime import datetime, timedelta
from typing import Any


class ToolError(Exception):
    """Raised by tools on failure; caught & retried by the executor stage."""


# --- naive in-memory "database" for the database_query tool -----------------
_MOCK_DB = {
    "customers": [
        {"id": 1, "name": "Acme Corp", "plan": "enterprise", "mrr": 12000},
        {"id": 2, "name": "Globex", "plan": "pro", "mrr": 800},
        {"id": 3, "name": "Initech", "plan": "starter", "mrr": 99},
    ],
    "tickets": [
        {"id": 101, "customer_id": 1, "status": "open", "priority": "high"},
        {"id": 102, "customer_id": 2, "status": "closed", "priority": "low"},
    ],
}

_ATTEMPT_COUNTERS: dict[str, int] = {}


async def web_search(query: str, max_results: int = 3) -> dict[str, Any]:
    """Mocked web search. Production: wire to Bing/Google/Tavily/Serper API."""
    await asyncio.sleep(0.05)
    results = [
        {
            "title": f"Result {i + 1} for '{query}'",
            "url": f"https://example.com/search?q={query.replace(' ', '+')}&r={i}",
            "snippet": f"Relevant information about {query} (mock result #{i + 1}).",
        }
        for i in range(max_results)
    ]
    return {"query": query, "results": results}


async def weather_lookup(location: str, units: str = "metric") -> dict[str, Any]:
    """Mocked weather API. Production: wire to OpenWeatherMap/NWS."""
    await asyncio.sleep(0.05)
    rng = random.Random(location.lower())
    temp_c = round(rng.uniform(5, 32), 1)
    temp = temp_c if units == "metric" else round(temp_c * 9 / 5 + 32, 1)
    conditions = rng.choice(["clear", "partly cloudy", "overcast", "light rain"])
    return {
        "location": location,
        "temperature": temp,
        "units": "C" if units == "metric" else "F",
        "conditions": conditions,
    }


async def calculator(expression: str) -> dict[str, Any]:
    """Safe arithmetic evaluator (no eval of arbitrary code)."""
    import ast
    import operator as op

    ops = {
        ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
        ast.Pow: op.pow, ast.Mod: op.mod, ast.USub: op.neg, ast.UAdd: op.pos,
    }

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.operand))
        raise ToolError(f"Unsupported expression: {expression!r}")

    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval(tree.body)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Could not evaluate expression: {exc}") from exc
    return {"expression": expression, "result": result}


_FX_RATES_USD = {"USD": 1.0, "EUR": 0.92, "GBP": 0.78, "INR": 83.1, "JPY": 147.5}


async def currency_converter(amount: float, from_currency: str, to_currency: str) -> dict[str, Any]:
    """Mocked FX conversion. Production: wire to exchangerate.host / OpenFX."""
    await asyncio.sleep(0.02)
    frm, to = from_currency.upper(), to_currency.upper()
    if frm not in _FX_RATES_USD or to not in _FX_RATES_USD:
        raise ToolError(f"Unsupported currency pair: {frm}->{to}")
    usd = amount / _FX_RATES_USD[frm]
    converted = round(usd * _FX_RATES_USD[to], 2)
    return {"amount": amount, "from": frm, "to": to, "converted": converted}


_UNIT_TABLE = {
    ("km", "mi"): 0.621371, ("mi", "km"): 1.60934,
    ("kg", "lb"): 2.20462, ("lb", "kg"): 0.453592,
    ("c", "f"): None, ("f", "c"): None,
}


async def unit_converter(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
    fu, tu = from_unit.lower(), to_unit.lower()
    if fu == "c" and tu == "f":
        result = value * 9 / 5 + 32
    elif fu == "f" and tu == "c":
        result = (value - 32) * 5 / 9
    elif (fu, tu) in _UNIT_TABLE and _UNIT_TABLE[(fu, tu)] is not None:
        result = value * _UNIT_TABLE[(fu, tu)]
    else:
        raise ToolError(f"Unsupported unit conversion: {fu}->{tu}")
    return {"value": value, "from": fu, "to": tu, "result": round(result, 4)}


async def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Mocked email send. Production: wire to SES/SendGrid/SMTP."""
    await asyncio.sleep(0.03)
    return {"to": to, "subject": subject, "status": "queued", "message_id": f"msg_{random.randint(10000, 99999)}"}


async def create_calendar_event(title: str, start_iso: str, duration_minutes: int = 30) -> dict[str, Any]:
    """Mocked calendar creation. Production: wire to Google Calendar/Outlook API."""
    await asyncio.sleep(0.03)
    start = datetime.fromisoformat(start_iso) if start_iso else datetime.utcnow()
    end = start + timedelta(minutes=duration_minutes)
    return {
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "event_id": f"evt_{random.randint(10000, 99999)}",
    }


async def translate_text(text: str, target_language: str) -> dict[str, Any]:
    """Mocked translation. Production: wire to DeepL/Google Translate API."""
    await asyncio.sleep(0.03)
    return {
        "source_text": text,
        "target_language": target_language,
        "translated_text": f"[{target_language}] {text}",
    }


async def summarize_text(text: str, max_sentences: int = 2) -> dict[str, Any]:
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if s.strip()]
    summary = ". ".join(sentences[:max_sentences])
    if summary and not summary.endswith("."):
        summary += "."
    return {"original_length": len(text), "summary": summary or text[:140]}


async def sentiment_analysis(text: str) -> dict[str, Any]:
    positive_words = {"good", "great", "excellent", "happy", "love", "amazing", "positive"}
    negative_words = {"bad", "terrible", "sad", "hate", "poor", "awful", "negative"}
    tokens = {w.strip(".,!?").lower() for w in text.split()}
    score = len(tokens & positive_words) - len(tokens & negative_words)
    label = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    return {"text": text, "label": label, "score": score}


async def flaky_stock_price_lookup(ticker: str) -> dict[str, Any]:
    """Mocked stock price API that fails on the first attempt per ticker to
    exercise the pipeline's retry-with-backoff logic. Production: wire to
    a real market-data API (IEX, Polygon, Alpha Vantage)."""
    _ATTEMPT_COUNTERS[ticker] = _ATTEMPT_COUNTERS.get(ticker, 0) + 1
    if _ATTEMPT_COUNTERS[ticker] == 1:
        raise ToolError(f"Upstream market-data provider timed out for {ticker}")
    rng = random.Random(ticker.upper())
    price = round(rng.uniform(20, 500), 2)
    return {"ticker": ticker.upper(), "price": price, "currency": "USD", "attempt": _ATTEMPT_COUNTERS[ticker]}


async def database_query(table: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mocked internal DB query against seed data (see _MOCK_DB above)."""
    filters = filters or {}
    rows = _MOCK_DB.get(table)
    if rows is None:
        raise ToolError(f"Unknown table: {table}")
    filtered = [r for r in rows if all(r.get(k) == v for k, v in filters.items())]
    return {"table": table, "filters": filters, "rows": filtered, "count": len(filtered)}


async def code_executor(python_expression: str) -> dict[str, Any]:
    """Restricted execution of a single arithmetic/statistics expression —
    NOT a general sandbox. Production would isolate this in a gVisor/Firecracker
    microVM or a hosted code-interpreter service."""
    allowed_names = {"mean": statistics.mean, "median": statistics.median, "sum": sum, "len": len}
    try:
        code = compile(python_expression, "<code_executor>", "eval")
        for name in code.co_names:
            if name not in allowed_names:
                raise ToolError(f"Use of '{name}' is not permitted in code_executor")
        result = eval(code, {"__builtins__": {}}, allowed_names)  # noqa: S307
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"Execution failed: {exc}") from exc
    return {"expression": python_expression, "result": result}


async def file_reader(file_name: str) -> dict[str, Any]:
    """Mocked document reader. Production: wire to S3/GDrive/local FS + a
    real parser (the docx/pdf skills in this environment, for example)."""
    await asyncio.sleep(0.02)
    mock_contents = {
        "onboarding.md": "Welcome to the platform. Step 1: connect your data. Step 2: invite your team.",
        "pricing.md": "Starter: $99/mo. Pro: $800/mo. Enterprise: custom pricing.",
    }
    content = mock_contents.get(file_name, f"(mock) contents of {file_name}")
    return {"file_name": file_name, "content": content}


async def geolocation_lookup(ip_or_city: str) -> dict[str, Any]:
    """Mocked geolocation. Production: wire to ipinfo.io / MaxMind."""
    rng = random.Random(ip_or_city)
    return {
        "query": ip_or_city,
        "lat": round(rng.uniform(-60, 60), 4),
        "lon": round(rng.uniform(-180, 180), 4),
        "timezone": rng.choice(["UTC", "America/New_York", "Asia/Kolkata", "Europe/Berlin"]),
    }
