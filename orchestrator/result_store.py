import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_attempt_record(root_dir: Path, record: Dict[str, Any]) -> Path:
    """Append a task attempt record to runs/results/<run_id>.jsonl."""
    run_id = record["run_id"]
    out_dir = root_dir / "runs" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{run_id}.jsonl"

    line = dict(record)
    line.setdefault("ts_utc", _utc_now_iso())

    with out_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=True) + "\n")
    return out_file


def write_attempt_trace(
    root_dir: Path,
    run_id: str,
    task_id: str,
    attempt_index: int,
    trace: Dict[str, Any],
) -> Path:
    """Write per-attempt execution trace to runs/logs/m2-trace/."""
    out_dir = root_dir / "runs" / "logs" / "m2-trace" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{task_id}-attempt-{attempt_index}.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(trace, f, ensure_ascii=True, indent=2)
        f.write("\n")
    return out_file
