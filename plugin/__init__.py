"""Agent Batch — agent-side registration.

The desktop panel is the operator console; these two tools are Hermes' own hands on the
same plan, which is what makes "paste a task list, let Hermes phase it" an actual
workflow rather than a caption:

  agent_batch_plan   read the stored tasks / repo / model / current phases
  agent_batch_phase  write the parallel groups Hermes decided (indices or task text)

Dispatching a phase stays a deliberate click in the panel — it launches real CI on the
user's GitHub account.
"""
from __future__ import annotations

import json
import logging

from . import agent_batch_state as st

logger = logging.getLogger(__name__)

TOOLSET = "agent-batch"

PLAN_SCHEMA = {
    "name": "agent_batch_plan",
    "description": (
        "Read the Agent Batch plan: the stored task list, target repo, model, and the "
        "current phases with their status. Call this before agent_batch_phase so the "
        "phases you propose cover the tasks that are actually there."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

PHASE_SCHEMA = {
    "name": "agent_batch_phase",
    "description": (
        "Save a phasing decision for the Agent Batch plan: groups of tasks that can run "
        "in parallel, ordered by dependency. Tasks inside one group dispatch together as "
        "independent agents on their own branches; a later group starts after the earlier "
        "one lands. Put every stored task in exactly one group. Reference tasks by their "
        "index from agent_batch_plan (e.g. [[0, 1], [2]])."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phases": {
                "type": "array",
                "description": "Parallel groups, in execution order.",
                "items": {
                    "type": "array",
                    "items": {"type": ["integer", "string"]},
                    "minItems": 1,
                },
                "minItems": 1,
            },
        },
        "required": ["phases"],
    },
}


def _fail(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def handle_plan(args: dict, **kwargs) -> str:
    try:
        return json.dumps(st.load_plan(), ensure_ascii=False)
    except Exception as e:  # never raise: the model reads the JSON back
        logger.exception("agent_batch_plan failed")
        return _fail(f"could not read the plan: {e}")


def handle_phase(args: dict, **kwargs) -> str:
    try:
        plan = st.load_plan()
        plan = st.set_phases(plan, (args or {}).get("phases"))
        return json.dumps(
            {
                "ok": True,
                "phase_count": len(plan["phases"]),
                "phases": plan["phases"],
                "next": "Open the Agent Batch panel in Desktop to dispatch a phase.",
            },
            ensure_ascii=False,
        )
    except st.AgentBatchError as e:
        return _fail(str(e))
    except Exception as e:
        logger.exception("agent_batch_phase failed")
        return _fail(f"could not save the phases: {e}")


def register(ctx) -> None:
    """Wire the plan tools to the shared state module. Called once at startup."""
    ctx.register_tool(name="agent_batch_plan", toolset=TOOLSET, schema=PLAN_SCHEMA, handler=handle_plan)
    ctx.register_tool(name="agent_batch_phase", toolset=TOOLSET, schema=PHASE_SCHEMA, handler=handle_phase)
