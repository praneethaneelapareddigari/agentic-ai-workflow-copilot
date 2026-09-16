from __future__ import annotations

from enum import Enum


class Stage(str, Enum):
    """The 8 stages of the agent pipeline, in execution order."""

    INTENT_PARSING = "intent_parsing"
    PLANNING = "planning"
    TOOL_SELECTION = "tool_selection"
    EXECUTION = "execution"
    VALIDATION = "validation"
    ERROR_HANDLING = "error_handling"
    AGGREGATION = "aggregation"
    RESPONSE_GENERATION = "response_generation"


STAGE_ORDER: list[Stage] = [
    Stage.INTENT_PARSING,
    Stage.PLANNING,
    Stage.TOOL_SELECTION,
    Stage.EXECUTION,
    Stage.VALIDATION,
    Stage.ERROR_HANDLING,
    Stage.AGGREGATION,
    Stage.RESPONSE_GENERATION,
]
