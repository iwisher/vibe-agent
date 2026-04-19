"""Task planner using LLM for natural language → step-by-step plan."""

import json
from typing import Any, Dict, List

from .llm import LLMClient, Message
from .models import Plan, Task, TaskStep


# JSON schema for plan responses
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "plugin": {"type": ["string", "null"]},
                    "action": {"type": ["string", "null"]},
                    "params": {"type": "object"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["description"],
            },
        },
        "estimated_duration": {"type": "integer"},
        "required_plugins": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["steps"],
}

PLANNING_PROMPT = """You are a task planning assistant. Break down the user's task into clear, executable steps.

Available plugins and their actions:
- personal_assistant: respond, ask_clarification, summarize
- web_search: search, search_news, get_webpage
- browser: navigate, click, type, screenshot, extract_text, run_js
- file_ops: read, write, append, delete, list_dir, search_files
- http_client: get, post, put, delete, patch
- email: send, read, list_inbox
- calendar: add_event, list_events, find_free_time
- data_processing: parse_csv, parse_json, transform, analyze

For each step, specify:
1. A clear description
2. The plugin to use (or null if no plugin needed)
3. The action to take
4. Required parameters
5. Any dependencies on previous steps

Respond with a JSON object matching this schema:
{
    "steps": [
        {
            "description": "Step description",
            "plugin": "plugin_name or null",
            "action": "action_name or null", 
            "params": {"key": "value"},
            "depends_on": ["step_id"] // IDs of steps this depends on
        }
    ],
    "estimated_duration": 300, // estimated seconds
    "required_plugins": ["plugin1", "plugin2"]
}

Task to plan: {task_description}

Context: {context}
"""


class TaskPlanner:
    """LLM-based task planner."""
    
    def __init__(self, llm: LLMClient):
        self.llm = llm
    
    async def create_plan(self, task: Task) -> Plan:
        """Create execution plan for a task."""
        messages = [
            Message(
                role="user",
                content=PLANNING_PROMPT.format(
                    task_description=task.description,
                    context=json.dumps(task.context, indent=2),
                ),
            )
        ]
        
        try:
            result = await self.llm.structured_output(messages, PLAN_SCHEMA)
            
            # Generate step IDs and build steps
            steps = []
            for i, step_data in enumerate(result.get("steps", [])):
                step_id = f"step_{i}"
                step = TaskStep(
                    id=step_id,
                    description=step_data.get("description", ""),
                    plugin=step_data.get("plugin"),
                    action=step_data.get("action"),
                    params=step_data.get("params", {}),
                    depends_on=step_data.get("depends_on", []),
                )
                steps.append(step)
            
            return Plan(
                steps=steps,
                estimated_duration=result.get("estimated_duration"),
                required_plugins=result.get("required_plugins", []),
            )
        
        except Exception as e:
            # Fallback: create a simple single-step plan
            return Plan(
                steps=[
                    TaskStep(
                        id="step_0",
                        description=f"Execute: {task.description}",
                        plugin="personal_assistant",
                        action="respond",
                        params={"task": task.description},
                    )
                ],
                estimated_duration=60,
                required_plugins=["personal_assistant"],
            )
    
    async def refine_plan(
        self,
        task: Task,
        current_plan: Plan,
        error_step_id: str,
        error_message: str,
    ) -> Plan:
        """Refine plan when a step fails."""
        messages = [
            Message(
                role="user",
                content=f"""The following plan failed at step {error_step_id}:

Error: {error_message}

Current plan:
{json.dumps(current_plan.model_dump(), indent=2)}

Please provide a refined plan that addresses the error. Respond with the same JSON format.""",
            )
        ]
        
        try:
            result = await self.llm.structured_output(messages, PLAN_SCHEMA)
            
            steps = []
            for i, step_data in enumerate(result.get("steps", [])):
                step = TaskStep(
                    id=f"step_{i}",
                    description=step_data.get("description", ""),
                    plugin=step_data.get("plugin"),
                    action=step_data.get("action"),
                    params=step_data.get("params", {}),
                    depends_on=step_data.get("depends_on", []),
                )
                steps.append(step)
            
            return Plan(
                steps=steps,
                estimated_duration=result.get("estimated_duration"),
                required_plugins=result.get("required_plugins", []),
            )
        
        except Exception:
            # Return original plan if refinement fails
            return current_plan
