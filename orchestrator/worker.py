from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, List, Optional

from .models import TaskRecord, TaskResult
from .result_store import write_attempt_record, write_attempt_trace


def has_valid_action(response: Any) -> bool:
    """Placeholder action validity check.

    For real android_world integration, replace with execution-confirmed signal.
    """
    return bool(getattr(response, "action", None))


def is_run_completed(step_results: List[Dict[str, Any]]) -> bool:
    if not step_results:
        return False
    if step_results[-1].get("done"):
        return True
    return all(bool(s.get("has_valid_action")) for s in step_results)


def execute_loop(
    task: Any,
    env: Any,
    agent: Any,
    max_steps: int,
    heartbeat_fn: Callable[[], bool],
    reclaimed_fn: Callable[[], bool],
) -> Dict[str, Any]:
    step_results: List[Dict[str, Any]] = []

    # task goal expected by android_world agents
    goal = getattr(task, "goal", "")

    for step_idx in range(max_steps):
        if not heartbeat_fn() or reclaimed_fn():
            return {
                "run_completed": False,
                "is_successful": None,
                "final_status": "failed",
                "last_step": step_idx,
                "error_code": "E_LEASE_TIMEOUT",
                "incomplete_reason": "infra_timeout",
                "step_results": step_results,
            }

        response = agent.step(goal)
        step_results.append(
            {
                "step": step_idx,
                "done": bool(getattr(response, "done", False)),
                "action": getattr(response, "action", None),
                "has_valid_action": has_valid_action(response),
            }
        )

        if getattr(response, "done", False):
            break

    completed = is_run_completed(step_results)
    successful = bool(task.is_successful(env) == 1) if completed else None

    return {
        "run_completed": completed,
        "is_successful": successful,
        "final_status": "succeeded" if successful else "failed",
        "last_step": step_results[-1]["step"] if step_results else 0,
        "error_code": None,
        "incomplete_reason": None if completed else "program_error",
        "step_results": step_results,
    }


def run_task(
    root_dir: Path,
    task_record: TaskRecord,
    task: Any,
    env: Any,
    agent: Any,
    max_steps: int,
    heartbeat_fn: Callable[[], bool],
    reclaimed_fn: Callable[[], bool],
) -> TaskResult:
    result = execute_loop(task, env, agent, max_steps, heartbeat_fn, reclaimed_fn)
    step_results = result["step_results"]
    trace_summary = {
        "step_count": len(step_results),
        "valid_action_count": sum(1 for s in step_results if s.get("has_valid_action")),
        "done_step": next((s["step"] for s in step_results if s.get("done")), None),
    }
    trace_path = write_attempt_trace(
        root_dir=root_dir,
        run_id=task_record.run_id,
        task_id=task_record.task_id,
        attempt_index=task_record.attempt_index,
        trace={
            "task_id": task_record.task_id,
            "run_id": task_record.run_id,
            "task_name": task_record.task_name,
            "attempt_index": task_record.attempt_index,
            "goal": getattr(task, "goal", ""),
            "summary": trace_summary,
            "step_results": step_results,
        },
    )

    model = TaskResult(
        run_completed=result["run_completed"],
        is_successful=result["is_successful"],
        final_status=result["final_status"],
        last_step=result["last_step"],
        error_code=result["error_code"],
        incomplete_reason=result["incomplete_reason"],
    )

    record = {
        "task_id": task_record.task_id,
        "run_id": task_record.run_id,
        "task_name": task_record.task_name,
        "attempt_index": task_record.attempt_index,
        "trace_path": str(trace_path),
        **trace_summary,
        **asdict(model),
    }
    write_attempt_record(root_dir, record)
    return model


# ------- adb-only e2e path -------
def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class _AdbOnlyEnv:
    def __init__(self, serial: str):
        self.serial = serial

    def _run(self, args: List[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
        cmd = ["adb", "-s", self.serial] + args
        return subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def ensure_device(self) -> None:
        subprocess.run(["adb", "connect", self.serial], capture_output=True, text=True, check=False)
        state = self._run(["get-state"], timeout=8)
        if state.returncode != 0 or "device" not in (state.stdout or "").strip():
            raise RuntimeError(f"adb serial not ready: {self.serial}")

    def press_home(self) -> None:
        self._run(["shell", "input", "keyevent", "3"])

    def start_settings(self) -> None:
        self._run(["shell", "am", "start", "-W", "-n", "com.android.settings/.Settings"])

    def get_current_focus(self) -> str:
        proc = self._run(["shell", "dumpsys", "window", "windows"])
        if proc.returncode != 0:
            return ""
        return (proc.stdout or "").replace("\r", "")

    def capture_artifacts(self, out_dir: Path) -> Dict[str, str]:
        out_dir.mkdir(parents=True, exist_ok=True)
        screenshot = out_dir / "screen.png"
        xml_dump = out_dir / "window_dump.xml"
        focus_txt = out_dir / "window_focus.txt"

        with screenshot.open("wb") as f:
            shot = subprocess.run(
                ["adb", "-s", self.serial, "exec-out", "screencap", "-p"],
                stdout=f,
                stderr=subprocess.PIPE,
                check=False,
            )
        _ = shot  # keep command for future extension
        self._run(["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"])
        cat_xml = self._run(["shell", "cat", "/sdcard/window_dump.xml"])
        xml_dump.write_text(cat_xml.stdout or "", encoding="utf-8")

        focus_txt.write_text(self.get_current_focus(), encoding="utf-8")
        return {
            "screenshot": str(screenshot),
            "window_dump_xml": str(xml_dump),
            "window_focus": str(focus_txt),
        }


class _AdbResp:
    def __init__(self, done: bool, action: Optional[str]):
        self.done = done
        self.action = action


class _AdbOnlyAgent:
    def __init__(self, env: _AdbOnlyEnv):
        self._env = env
        self._done = False

    def step(self, _goal: str) -> _AdbResp:
        if not self._done:
            self._env.start_settings()
            self._done = True
            return _AdbResp(done=True, action="am_start_settings")
        return _AdbResp(done=True, action="noop")


class _AdbOnlyTask:
    goal = "Open Android Settings app"

    def is_successful(self, env: _AdbOnlyEnv) -> int:
        focus = env.get_current_focus().lower()
        return 1 if "com.android.settings" in focus else 0


def _adb_only_e2e(root_dir: Path, serial: str, run_id: Optional[str]) -> int:
    env = _AdbOnlyEnv(serial=serial)
    env.ensure_device()
    env.press_home()

    real_run_id = run_id or f"run-adb-only-{_utc_now_compact()}"
    task_record = TaskRecord(
        task_id=f"task-adb-only-{_utc_now_compact()}",
        run_id=real_run_id,
        task_name="AdbOnlyOpenSettings",
        payload={"serial": serial},
        attempt_index=1,
    )

    task = _AdbOnlyTask()
    agent = _AdbOnlyAgent(env)

    result = run_task(
        root_dir=root_dir,
        task_record=task_record,
        task=task,
        env=env,
        agent=agent,
        max_steps=3,
        heartbeat_fn=lambda: True,
        reclaimed_fn=lambda: False,
    )

    artifact_dir = root_dir / "runs" / "logs" / "m2-adb-only" / real_run_id
    artifacts = env.capture_artifacts(artifact_dir)

    print(
        "M2_ADB_ONLY",
        f"run_id={real_run_id}",
        f"serial={serial}",
        f"run_completed={result.run_completed}",
        f"is_successful={result.is_successful}",
        f"artifacts={artifact_dir}",
    )
    for k, v in artifacts.items():
        print(f"M2_ADB_ONLY_ARTIFACT {k}={v}")

    return 0 if (result.run_completed and bool(result.is_successful)) else 1


# ------- local self test -------
class _Resp:
    def __init__(self, done: bool, action: Optional[str]):
        self.done = done
        self.action = action


class _Agent:
    def __init__(self, steps: List[_Resp]):
        self._steps = steps
        self._i = 0

    def step(self, _goal: str) -> _Resp:
        s = self._steps[min(self._i, len(self._steps) - 1)]
        self._i += 1
        return s


class _Task:
    goal = "demo"

    def is_successful(self, _env: Any) -> int:
        return 1


def _self_test(root_dir: Path) -> int:
    tr = TaskRecord(
        task_id="task-selftest-1",
        run_id="run-selftest",
        task_name="SelfTestTask",
        payload={},
        attempt_index=1,
    )
    agent = _Agent([_Resp(False, "tap"), _Resp(True, "tap")])
    result = run_task(
        root_dir=root_dir,
        task_record=tr,
        task=_Task(),
        env=object(),
        agent=agent,
        max_steps=5,
        heartbeat_fn=lambda: True,
        reclaimed_fn=lambda: False,
    )
    print(
        "M2_SELF_TEST",
        f"run_completed={result.run_completed}",
        f"is_successful={result.is_successful}",
        f"final_status={result.final_status}",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--adb-only-e2e", action="store_true")
    parser.add_argument("--serial", default="127.0.0.1:15500")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()

    root_dir = Path(args.root)
    if args.self_test:
        return _self_test(root_dir)
    if args.adb_only_e2e:
        return _adb_only_e2e(root_dir, serial=args.serial, run_id=(args.run_id or None))

    print("Use --self-test for local validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
