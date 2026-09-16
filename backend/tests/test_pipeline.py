import pytest

from app.database import AsyncSessionLocal, init_db
from app.models import WorkflowStatus
from app.pipeline.orchestrator import WorkflowOrchestrator
from app.pipeline.stages import STAGE_ORDER
from app.tools import implementations as impl


@pytest.fixture(autouse=True, scope="module")
async def _setup_db():
    await init_db()
    yield


class TestPipelineHappyPath:
    async def test_runs_all_eight_stages_in_order(self):
        orch = WorkflowOrchestrator()
        async with AsyncSessionLocal() as db:
            wf = await orch.run(db, None, "weather in Rome and convert 50 USD to GBP")

        assert wf.status == WorkflowStatus.COMPLETED
        assert [s.name for s in wf.steps] == [s.value for s in STAGE_ORDER]
        assert all(s.status.value == "succeeded" for s in wf.steps)

    async def test_persists_tool_calls(self):
        orch = WorkflowOrchestrator()
        async with AsyncSessionLocal() as db:
            wf = await orch.run(db, None, "weather in Oslo")
        assert len(wf.tool_calls) == 1
        assert wf.tool_calls[0].tool_name == "weather_lookup"
        assert wf.tool_calls[0].status.value == "succeeded"

    async def test_multi_tool_request_invokes_multiple_tools(self):
        orch = WorkflowOrchestrator()
        async with AsyncSessionLocal() as db:
            wf = await orch.run(
                db, None, "What's the weather in Madrid and calculate 8 * 9"
            )
        tool_names = {t.tool_name for t in wf.tool_calls}
        assert "weather_lookup" in tool_names
        assert "calculator" in tool_names

    async def test_final_response_is_populated(self):
        orch = WorkflowOrchestrator()
        async with AsyncSessionLocal() as db:
            wf = await orch.run(db, None, "weather in Lisbon")
        assert wf.final_response
        assert "weather_lookup" in wf.final_response

    async def test_conversation_created_and_reused(self):
        orch = WorkflowOrchestrator()
        async with AsyncSessionLocal() as db:
            wf1 = await orch.run(db, None, "weather in Dublin")
        async with AsyncSessionLocal() as db:
            wf2 = await orch.run(db, wf1.conversation_id, "weather in Dublin again")
        assert wf1.conversation_id == wf2.conversation_id


class TestPipelineRetryAndCaching:
    async def test_flaky_tool_recovers_via_retry(self):
        impl._ATTEMPT_COUNTERS.pop("RETRYCO", None)
        orch = WorkflowOrchestrator()
        async with AsyncSessionLocal() as db:
            wf = await orch.run(db, None, "stock price of RETRYCO")
        call = wf.tool_calls[0]
        assert call.status.value == "succeeded"
        assert call.attempt == 2  # failed once, succeeded on 2nd attempt

    async def test_repeated_identical_request_hits_cache(self):
        orch = WorkflowOrchestrator()
        async with AsyncSessionLocal() as db:
            wf1 = await orch.run(db, None, "weather in Vienna")
        async with AsyncSessionLocal() as db:
            wf2 = await orch.run(db, None, "weather in Vienna")
        assert wf1.tool_calls[0].cache_hit is False
        assert wf2.tool_calls[0].cache_hit is True
        # Cached call should be at least as fast, typically much faster.
        assert wf2.tool_calls[0].duration_ms <= wf1.tool_calls[0].duration_ms + 5


class TestPipelinePlanValidation:
    async def test_plan_step_limit_enforced_by_schema(self):
        from pydantic import ValidationError

        from app.schemas import ExecutionPlan

        too_many = [
            {"step_id": i, "description": "x", "tool_name": "web_search", "tool_arguments": {}}
            for i in range(13)
        ]
        with pytest.raises(ValidationError):
            ExecutionPlan(goal="test", steps=too_many)

    async def test_unknown_tool_in_plan_is_repaired(self):
        orch = WorkflowOrchestrator()
        # Monkeypatch the mock LLM to hallucinate a bogus tool name for planning.
        from app.schemas import ExecutionPlan, PlanStep

        async def fake_get_structured(system_prompt, user_prompt, schema):
            if schema is ExecutionPlan:
                return ExecutionPlan(
                    goal="test",
                    steps=[PlanStep(step_id=1, description="test step", tool_name="totally_made_up_tool", tool_arguments={})],
                )
            return await original_get_structured(system_prompt, user_prompt, schema)

        original_get_structured = orch._llm.get_structured
        orch._llm.get_structured = fake_get_structured  # type: ignore[method-assign]

        async with AsyncSessionLocal() as db:
            wf = await orch.run(db, None, "irrelevant request")

        assert wf.status == WorkflowStatus.COMPLETED
        assert wf.tool_calls[0].tool_name == "web_search"  # repaired fallback
