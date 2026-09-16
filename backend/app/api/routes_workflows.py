from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Workflow
from app.observability import METRICS
from app.pipeline.orchestrator import WorkflowOrchestrator
from app.schemas import WorkflowRequest, WorkflowView

router = APIRouter(prefix="/workflows", tags=["workflows"])
_orchestrator = WorkflowOrchestrator()


@router.post("", response_model=WorkflowView, status_code=201)
async def create_workflow(payload: WorkflowRequest, db: AsyncSession = Depends(get_db)) -> Workflow:
    """Submit a natural-language request; runs it through the full 8-stage
    agent pipeline synchronously and returns the completed (or failed)
    workflow with every stage and tool call recorded."""
    workflow = await _orchestrator.run(db, payload.conversation_id, payload.message)
    return workflow


@router.get("/{workflow_id}", response_model=WorkflowView)
async def get_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)) -> Workflow:
    result = await db.execute(
        select(Workflow)
        .options(selectinload(Workflow.steps), selectinload(Workflow.tool_calls))
        .where(Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.get("", response_model=list[WorkflowView])
async def list_workflows(conversation_id: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Workflow).options(selectinload(Workflow.steps), selectinload(Workflow.tool_calls))
    if conversation_id:
        stmt = stmt.where(Workflow.conversation_id == conversation_id)
    stmt = stmt.order_by(Workflow.created_at.desc()).limit(50)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/_meta/metrics")
async def get_metrics():
    """Exposes in-process observability counters/histograms (cache hit
    rate, tool latency, retry counts, etc.) — see app/observability.py."""
    return METRICS.snapshot()
