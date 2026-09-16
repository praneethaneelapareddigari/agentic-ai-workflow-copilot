from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Structured LLM outputs (used with response_format / JSON-mode + validated
# on the way in — this is the "structured outputs" hardening).
# ---------------------------------------------------------------------------
class IntentResult(BaseModel):
    """Output of Stage 1 — Intent Parsing."""

    intent: str = Field(description="Short canonical name of the user's goal")
    entities: dict[str, Any] = Field(default_factory=dict)
    requires_tools: bool = True
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)


class PlanStep(BaseModel):
    """A single step in the execution plan produced by Stage 2 — Planning."""

    step_id: int
    description: str
    tool_name: Optional[str] = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)

    @field_validator("tool_arguments", mode="before")
    @classmethod
    def _default_args(cls, v):
        return v or {}


class ExecutionPlan(BaseModel):
    """Output of Stage 2 — Planning."""

    goal: str
    steps: list[PlanStep]

    @field_validator("steps")
    @classmethod
    def _limit_steps(cls, v: list[PlanStep]) -> list[PlanStep]:
        if len(v) == 0:
            raise ValueError("plan must contain at least one step")
        if len(v) > 12:
            raise ValueError("plan exceeds maximum allowed steps (12)")
        return v


class ToolSelection(BaseModel):
    """Output of Stage 3 — Tool Selection for a specific plan step."""

    step_id: int
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class ToolExecutionResult(BaseModel):
    step_id: int
    tool_name: str
    arguments: dict[str, Any]
    success: bool
    output: Any = None
    error: Optional[str] = None
    attempts: int = 1
    cache_hit: bool = False
    duration_ms: int = 0


class ValidationResult(BaseModel):
    """Output of Stage 5 — Validation."""

    all_steps_valid: bool
    issues: list[str] = Field(default_factory=list)
    needs_replan: bool = False


class FinalResponse(BaseModel):
    """Output of Stage 8 — Response Generation."""

    summary: str
    details: str
    citations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API request / response contracts
# ---------------------------------------------------------------------------
class WorkflowRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: str = Field(min_length=1, max_length=4000)


class StepView(BaseModel):
    sequence: int
    name: str
    status: str
    duration_ms: Optional[int] = None
    output_summary: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    model_config = {"from_attributes": True}


class ToolCallView(BaseModel):
    tool_name: str
    arguments: dict[str, Any]
    status: str
    attempt: int
    cache_hit: bool
    duration_ms: Optional[int] = None
    result: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}


class WorkflowView(BaseModel):
    id: str
    conversation_id: str
    trace_id: str
    user_request: str
    status: str
    plan: Optional[dict[str, Any]] = None
    final_response: Optional[str] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    steps: list[StepView] = Field(default_factory=list)
    tool_calls: list[ToolCallView] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    llm_mode: str
    cache_backend: str
