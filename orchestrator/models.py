from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class TaskRecord:
    task_id: str
    run_id: str
    task_name: str
    payload: Dict[str, Any]
    attempt_index: int = 1


@dataclass
class TaskResult:
    run_completed: bool
    is_successful: Optional[bool]
    final_status: str
    last_step: int
    error_code: Optional[str] = None
    incomplete_reason: Optional[str] = None
