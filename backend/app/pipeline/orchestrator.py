from __future__ import annotations

import json
import time
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.factory import LLMClient, build_llm_client
from app.models import (
    Conversation,
    Message,
    StepStatus,
    ToolCallRecord,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from app.observability import get_logger, log_event, new_trace_id, timed
from app.pipeline.executor import ToolExecutor
from app.pipeline.stages import Stage
from app.schemas import ExecutionPlan, FinalResponse, IntentResult, ValidationResult
from app.tools.registry import ToolRegistry, default_registry

logger = get_logger(__name__)


class PipelineError(Exception):
    pass


class WorkflowOrchestrator:
    """Runs a user request through the 8-stage agent pipeline:

    1. Intent Parsing        -> IntentResult
    2. Planning              -> ExecutionPlan (decomposes request into steps)
    3. Tool Selection        -> confirms/repairs each step's tool + arguments
    4. Execution             -> ToolExecutor runs steps (cache + retry + async)
    5. Validation            -> ValidationResult over tool outputs
    6. Error Handling        -> retries/drops failed steps, may trigger replan
    7. Aggregation           -> collapses results into a single context blob
    8. Response Generation   -> FinalResponse returned to the user

    Every stage's status/timing is persisted as a WorkflowStep row, and every
    tool invocation as a ToolCallRecord row, so the whole run is fully
    auditable after the fact via GET /workflows/{id}.
    """

    def __init__(self, registry: ToolRegistry = default_registry, llm: LLMClient | None = None):
        self._registry = registry
        self._llm = llm or build_llm_client(registry)
        self._executor = ToolExecutor(registry)

    async def run(self, db: AsyncSession, conversation_id: str | None, user_message: str) -> Workflow:
        trace_id = new_trace_id()
        conversation = await self._get_or_create_conversation(db, conversation_id, user_message)

        workflow = Workflow(
            conversation_id=conversation.id,
            trace_id=trace_id,
            user_request=user_message,
            status=WorkflowStatus.PENDING,
        )
        db.add(workflow)
        db.add(Message(conversation_id=conversation.id, role="user", content=user_message))
        await db.flush()

        pipeline_start = time.perf_counter()
        context: dict = {"message": user_message}

        try:
            workflow.status = WorkflowStatus.PLANNING
            intent = await self._run_stage(db, workflow, 1, Stage.INTENT_PARSING, self._stage_intent, context)
            context["intent"] = intent.model_dump()

            plan = await self._run_stage(db, workflow, 2, Stage.PLANNING, self._stage_planning, context)
            workflow.plan = plan.model_dump()
            context["plan"] = plan.model_dump()

            plan = await self._run_stage(db, workflow, 3, Stage.TOOL_SELECTION, self._stage_tool_selection, context, plan=plan)
            context["plan"] = plan.model_dump()

            workflow.status = WorkflowStatus.EXECUTING
            tool_results = await self._run_stage(
                db, workflow, 4, Stage.EXECUTION, self._stage_execution, context, plan=plan, workflow=workflow, db=db
            )
            context["tool_results"] = [r.model_dump() for r in tool_results]

            workflow.status = WorkflowStatus.VALIDATING
            validation = await self._run_stage(db, workflow, 5, Stage.VALIDATION, self._stage_validation, context)

            tool_results = await self._run_stage(
                db, workflow, 6, Stage.ERROR_HANDLING, self._stage_error_handling,
                context, plan=plan, tool_results=tool_results, validation=validation, workflow=workflow, db=db,
            )
            context["tool_results"] = [r.model_dump() for r in tool_results]

            aggregated = await self._run_stage(db, workflow, 7, Stage.AGGREGATION, self._stage_aggregation, context)
            context["aggregated"] = aggregated

            final = await self._run_stage(db, workflow, 8, Stage.RESPONSE_GENERATION, self._stage_response, context)

            workflow.final_response = final.summary + "\n\n" + final.details
            workflow.status = WorkflowStatus.COMPLETED
            db.add(Message(conversation_id=conversation.id, role="assistant", content=workflow.final_response))

        except Exception as exc:  # noqa: BLE001 - top-level safety net
            workflow.status = WorkflowStatus.FAILED
            workflow.error = str(exc)
            log_event(logger, "ERROR", "Workflow failed", trace_id=trace_id, error=str(exc))

        workflow.duration_ms = int((time.perf_counter() - pipeline_start) * 1000)
        await db.commit()
        await db.refresh(workflow, attribute_names=["steps", "tool_calls"])
        return workflow

    # -- persistence-aware stage runner --------------------------------------
    async def _run_stage(self, session, workflow_row, sequence, stage: Stage, fn, context, **kwargs):
        step_row = WorkflowStep(
            workflow_id=workflow_row.id, sequence=sequence, name=stage.value,
            status=StepStatus.RUNNING, started_at=datetime.utcnow(),
            input_summary=self._safe_summary(context),
        )
        session.add(step_row)
        await session.flush()

        start = time.perf_counter()
        try:
            with timed("pipeline.stage", stage=stage.value):
                result = await fn(context, **kwargs)
            step_row.status = StepStatus.SUCCEEDED
            step_row.output_summary = self._safe_summary(result)
            return result
        except Exception as exc:  # noqa: BLE001
            step_row.status = StepStatus.FAILED
            step_row.error = str(exc)
            raise
        finally:
            step_row.finished_at = datetime.utcnow()
            step_row.duration_ms = int((time.perf_counter() - start) * 1000)

    @staticmethod
    def _safe_summary(obj) -> dict:
        try:
            if hasattr(obj, "model_dump"):
                data = obj.model_dump()
            elif isinstance(obj, list):
                data = {"items": [o.model_dump() if hasattr(o, "model_dump") else o for o in obj]}
            elif isinstance(obj, dict):
                data = obj
            else:
                data = {"value": str(obj)}
            # Round-trip through json to guarantee the value is DB-serializable.
            return json.loads(json.dumps(data, default=str))
        except Exception:  # noqa: BLE001
            return {"value": str(obj)[:500]}

    async def _get_or_create_conversation(self, db: AsyncSession, conversation_id: str | None, user_message: str) -> Conversation:
        if conversation_id:
            conv = await db.get(Conversation, conversation_id)
            if conv:
                return conv
        conv = Conversation(title=user_message[:60])
        db.add(conv)
        await db.flush()
        return conv

    # -- the 8 stages ---------------------------------------------------------
    async def _stage_intent(self, context: dict) -> IntentResult:
        return await self._llm.get_structured(
            system_prompt="You extract the user's intent and entities.",
            user_prompt=json.dumps({"message": context["message"]}),
            schema=IntentResult,
        )

    async def _stage_planning(self, context: dict) -> ExecutionPlan:
        prompt = json.dumps({
            "message": context["message"],
            "intent": context.get("intent"),
            "available_tools": self._registry.catalog_for_prompt(),
        })
        return await self._llm.get_structured(
            system_prompt="You decompose the user's request into an ordered plan of tool-using steps.",
            user_prompt=prompt,
            schema=ExecutionPlan,
        )

    async def _stage_tool_selection(self, context: dict, plan: ExecutionPlan) -> ExecutionPlan:
        # Repairs any step whose tool_name doesn't exist in the registry by
        # asking the LLM to reselect just that step. Cheap sanity pass that
        # keeps a hallucinated tool name from blowing up execution.
        available = {s.name for s in self._registry.list_specs()}
        fixed_steps = []
        for step in plan.steps:
            if step.tool_name and step.tool_name not in available:
                log_event(logger, "INFO", "Repairing unknown tool in plan step", tool=step.tool_name)
                step = step.model_copy(update={"tool_name": "web_search", "tool_arguments": {"query": context["message"][:120]}})
            fixed_steps.append(step)
        return plan.model_copy(update={"steps": fixed_steps})

    async def _stage_execution(self, context: dict, plan: ExecutionPlan, workflow: Workflow, db: AsyncSession):
        results = await self._executor.run_plan(plan.steps)
        for r in results:
            db.add(
                ToolCallRecord(
                    workflow_id=workflow.id,
                    tool_name=r.tool_name,
                    arguments=r.arguments,
                    result={"output": r.output} if r.success else None,
                    status=StepStatus.SUCCEEDED if r.success else StepStatus.FAILED,
                    attempt=r.attempts,
                    cache_hit=r.cache_hit,
                    duration_ms=r.duration_ms,
                    error=r.error,
                )
            )
        await db.flush()
        return results

    async def _stage_validation(self, context: dict) -> ValidationResult:
        return await self._llm.get_structured(
            system_prompt="You validate whether tool execution results satisfy the user's request.",
            user_prompt=json.dumps({"message": context["message"], "tool_results": context["tool_results"]}),
            schema=ValidationResult,
        )

    async def _stage_error_handling(self, context: dict, plan: ExecutionPlan, tool_results, validation: ValidationResult, workflow: Workflow, db: AsyncSession):
        if validation.all_steps_valid:
            return tool_results

        failed_steps = [
            s for s, r in zip(plan.steps, tool_results)
            if not r.success and s.tool_name
        ]
        if not failed_steps:
            return tool_results

        log_event(logger, "INFO", "Re-running failed steps after validation flagged issues",
                  failed_count=len(failed_steps))
        retried = await self._executor.run_plan(failed_steps)
        retried_by_id = {r.step_id: r for r in retried}

        for r in retried:
            db.add(
                ToolCallRecord(
                    workflow_id=workflow.id, tool_name=r.tool_name, arguments=r.arguments,
                    result={"output": r.output} if r.success else None,
                    status=StepStatus.SUCCEEDED if r.success else StepStatus.FAILED,
                    attempt=r.attempts, cache_hit=r.cache_hit, duration_ms=r.duration_ms,
                    error=r.error,
                )
            )
        await db.flush()

        merged = [retried_by_id.get(r.step_id, r) for r in tool_results]
        return merged

    async def _stage_aggregation(self, context: dict) -> dict:
        results = context["tool_results"]
        return {
            "total_steps": len(results),
            "succeeded": sum(1 for r in results if r["success"]),
            "failed": sum(1 for r in results if not r["success"]),
            "cache_hits": sum(1 for r in results if r.get("cache_hit")),
        }

    async def _stage_response(self, context: dict) -> FinalResponse:
        return await self._llm.get_structured(
            system_prompt="You write the final natural-language response to the user, "
                          "incorporating tool results and being explicit about any failures.",
            user_prompt=json.dumps({"message": context["message"], "tool_results": context["tool_results"], "aggregated": context["aggregated"]}),
            schema=FinalResponse,
        )
