"""Core task models using Pydantic."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    PLANNING = "planning"
    PLANNED = "planned"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Step execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskStep(BaseModel):
    """A single step in a task plan."""
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    description: str
    status: StepStatus = StepStatus.PENDING
    plugin: Optional[str] = None
    action: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    depends_on: List[str] = Field(default_factory=list)
    
    model_config = ConfigDict(use_enum_values=True)


class Plan(BaseModel):
    """Execution plan for a task."""
    steps: List[TaskStep] = Field(default_factory=list)
    estimated_duration: Optional[int] = None  # in seconds
    required_plugins: List[str] = Field(default_factory=list)
    
    def get_progress(self) -> tuple[int, int]:
        """Returns (completed_steps, total_steps)."""
        completed = sum(1 for s in self.steps if s.status == StepStatus.COMPLETED)
        return completed, len(self.steps)
    
    @property
    def percent_complete(self) -> float:
        """Calculate percentage completion."""
        if not self.steps:
            return 0.0
        completed, total = self.get_progress()
        return (completed / total) * 100


class Task(BaseModel):
    """Main task model."""
    id: str = Field(default_factory=lambda: str(uuid4())[:8])
    description: str
    status: TaskStatus = TaskStatus.PENDING
    plan: Optional[Plan] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    parent_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    priority: int = 5  # 1-10, higher = more important
    
    model_config = ConfigDict(use_enum_values=True)


class TaskEvent(BaseModel):
    """Event for task updates (for WebSocket/CLI)."""
    task_id: str
    event_type: str  # "status_change", "step_complete", "progress", "error"
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
