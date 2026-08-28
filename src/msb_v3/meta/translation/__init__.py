"""META-1A: Translation engine — the bridge between MetaTask and worker execution.

The translation layer is the most important missing component (blueprint §4,
§8).  A powerful model and a small model receive *different representations of
the same task*; translation is *semantic-preserving compilation* — it may
modify wording, structure, verbosity, examples, tool syntax, and instruction
order, but it may NOT modify objective, constraints, authorization, success
criteria, verification requirements, or safety requirements.

Architecture law (blueprint §12):
    Never give a worker more context merely because more context is available.

    AVAILABLE CONTEXT
           ↓
    RELEVANCE FILTER
           ↓
    TASK CONTEXT
           ↓
    MODEL CONTEXT
"""

from msb_v3.meta.translation.context_compiler import (
    ContextBudget,
    ContextCompiler,
    ContextSelection,
)
from msb_v3.meta.translation.import_graph import GraphStats, ImportGraph
from msb_v3.meta.translation.model_task import ModelTask, ToolPolicy
from msb_v3.meta.translation.task_translator import TaskTranslator, WorkerProfile

__all__ = [
    "ContextBudget",
    "ContextCompiler",
    "ContextSelection",
    "GraphStats",
    "ImportGraph",
    "ModelTask",
    "TaskTranslator",
    "ToolPolicy",
    "WorkerProfile",
]
