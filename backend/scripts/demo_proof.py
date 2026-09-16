"""
Standalone proof-of-functionality script. Run with:

    python3 scripts/demo_proof.py

Exercises the full pipeline against the mock LLM (no API key needed) and
prints clear, labeled evidence of:
  1. All 8 pipeline stages executing in order and persisting to the DB
  2. Multi-tool dynamic invocation (>1 of the 15 registered tools)
  3. Automatic retry-with-backoff recovering a transient tool failure
  4. Redis-fallback caching turning a repeated call into a cache hit
  5. Structured-output validation rejecting an over-long plan
  6. In-process observability metrics (latency, retries, cache hit rate)
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("USE_MOCK_LLM", "true")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./demo_proof.db")


async def main():
    from app.database import init_db
    from app.observability import METRICS
    from app.pipeline.orchestrator import WorkflowOrchestrator
    from app.database import AsyncSessionLocal
    from app.tools.registry import default_registry

    await init_db()
    orch = WorkflowOrchestrator()

    def header(title):
        print("\n" + "=" * 70)
        print(title)
        print("=" * 70)

    header(f"TOOL REGISTRY: {len(default_registry)} tools registered (requirement: 12+)")
    for spec in default_registry.list_specs():
        print(f"  - {spec.name}")

    header("PROOF 1: Full 8-stage pipeline, multi-tool dynamic invocation")
    async with AsyncSessionLocal() as db:
        wf = await orch.run(db, None, "What's the weather in Tokyo and convert 200 USD to GBP")
    print(f"Workflow status: {wf.status.value}")
    print("Stages executed, in order:")
    for s in wf.steps:
        print(f"  {s.sequence}. {s.name:<22} status={s.status.value:<10} duration_ms={s.duration_ms}")
    print("Tools dynamically invoked:")
    for t in wf.tool_calls:
        print(f"  - {t.tool_name}: attempt={t.attempt} cache_hit={t.cache_hit} duration_ms={t.duration_ms}")
    print("\nFinal response:\n" + wf.final_response)

    header("PROOF 2: Retry-with-backoff recovers a transient tool failure")
    from app.tools import implementations as impl
    impl._ATTEMPT_COUNTERS.pop("DEMO", None)
    async with AsyncSessionLocal() as db:
        wf = await orch.run(db, None, "stock price of DEMO")
    call = wf.tool_calls[0]
    print(f"Tool: {call.tool_name}")
    print(f"Final status: {call.status.value} after {call.attempt} attempt(s)")
    print("(attempt 1 raises ToolError by design; attempt 2 succeeds — see app/tools/implementations.py)")

    header("PROOF 3: Redis-fallback caching turns a repeat call into a cache hit")
    async with AsyncSessionLocal() as db:
        t0 = time.perf_counter()
        wf1 = await orch.run(db, None, "weather in Singapore")
        t1 = time.perf_counter()
        wf2 = await orch.run(db, None, "weather in Singapore")
        t2 = time.perf_counter()
    print(f"First call:  cache_hit={wf1.tool_calls[0].cache_hit}  wall_time_ms={round((t1 - t0) * 1000, 2)}")
    print(f"Second call: cache_hit={wf2.tool_calls[0].cache_hit}  wall_time_ms={round((t2 - t1) * 1000, 2)}")

    header("PROOF 4: Structured-output validation rejects malformed plans")
    from pydantic import ValidationError
    from app.schemas import ExecutionPlan
    try:
        ExecutionPlan(goal="x", steps=[
            {"step_id": i, "description": "x", "tool_name": "web_search", "tool_arguments": {}}
            for i in range(20)
        ])
        print("UNEXPECTED: validation did not reject the oversized plan")
    except ValidationError as e:
        print("Correctly rejected a 20-step plan (limit is 12):")
        print(" ", str(e).splitlines()[0])

    header("PROOF 5: Observability — in-process metrics snapshot")
    snapshot = METRICS.snapshot()
    print("Counters:")
    for k, v in snapshot["counters"].items():
        print(f"  {k} = {v}")
    print("Histograms (latency):")
    for k, v in snapshot["histograms"].items():
        print(f"  {k}: {v}")

    header("DONE — all proofs executed successfully")


if __name__ == "__main__":
    asyncio.run(main())
