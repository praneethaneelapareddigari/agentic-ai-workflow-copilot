import pytest

from app.tools import implementations as impl
from app.tools.registry import build_default_registry


class TestToolImplementations:
    async def test_calculator_basic(self):
        result = await impl.calculator("2 + 3 * 4")
        assert result["result"] == 14

    async def test_calculator_rejects_unsafe_input(self):
        with pytest.raises(impl.ToolError):
            await impl.calculator("__import__('os').system('echo hi')")

    async def test_currency_converter(self):
        result = await impl.currency_converter(100, "USD", "EUR")
        assert result["from"] == "USD"
        assert result["to"] == "EUR"
        assert result["converted"] > 0

    async def test_currency_converter_unsupported_pair(self):
        with pytest.raises(impl.ToolError):
            await impl.currency_converter(100, "USD", "ZZZ")

    async def test_unit_converter_celsius_to_fahrenheit(self):
        result = await impl.unit_converter(0, "c", "f")
        assert result["result"] == 32.0

    async def test_weather_lookup_deterministic_per_location(self):
        r1 = await impl.weather_lookup("Tokyo")
        r2 = await impl.weather_lookup("Tokyo")
        assert r1 == r2  # deterministic mock, same seed

    async def test_database_query_filters(self):
        result = await impl.database_query("customers", {"plan": "enterprise"})
        assert result["count"] == 1
        assert result["rows"][0]["name"] == "Acme Corp"

    async def test_database_query_unknown_table(self):
        with pytest.raises(impl.ToolError):
            await impl.database_query("nonexistent_table")

    async def test_code_executor_allows_statistics(self):
        result = await impl.code_executor("mean([1, 2, 3])")
        assert result["result"] == 2

    async def test_code_executor_blocks_disallowed_names(self):
        with pytest.raises(impl.ToolError):
            await impl.code_executor("open('/etc/passwd')")

    async def test_flaky_stock_price_fails_then_succeeds(self):
        impl._ATTEMPT_COUNTERS.pop("FAILTEST", None)
        with pytest.raises(impl.ToolError):
            await impl.flaky_stock_price_lookup("FAILTEST")
        result = await impl.flaky_stock_price_lookup("FAILTEST")
        assert result["ticker"] == "FAILTEST"
        assert result["attempt"] == 2

    async def test_sentiment_analysis(self):
        positive = await impl.sentiment_analysis("This is great and amazing")
        negative = await impl.sentiment_analysis("This is terrible and bad")
        assert positive["label"] == "positive"
        assert negative["label"] == "negative"


class TestToolRegistry:
    def test_registry_has_at_least_twelve_tools(self):
        registry = build_default_registry()
        assert len(registry) >= 12

    def test_all_tools_expose_llm_schema(self):
        registry = build_default_registry()
        for spec in registry.list_specs():
            schema = spec.as_llm_schema()
            assert schema["name"] == spec.name
            assert "parameters" in schema

    def test_unknown_tool_raises(self):
        registry = build_default_registry()
        with pytest.raises(KeyError):
            registry.get("does_not_exist")
