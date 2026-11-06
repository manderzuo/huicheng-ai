"""
FishFleet — 任务队列

模仿汇城的"执行任务 / 结束任务 / 执行列表"：
  · 任务入队
  · 按设备并发执行
  · 状态跟踪
  · 可中断

持久化：data/tasks.json
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

DATA_DIR = Path("data")
TASK_FILE = DATA_DIR / "tasks.json"


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str
    type: str                       # publish / patrol / reply / adb / ...
    serial: str
    params: dict = field(default_factory=dict)
    state: TaskState = TaskState.PENDING
    result: str = ""
    error: str = ""
    progress: float = 0.0           # 0-1
    steps: list[dict] = field(default_factory=list)
    created_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    retries: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "serial": self.serial,
            "params": self.params,
            "state": self.state.value,
            "result": self.result,
            "error": self.error,
            "progress": round(self.progress, 2),
            "steps": self.steps[-20:],       # 只返回最近 20 步
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retries": self.retries,
            "duration": round(
                (self.finished_at or time.time()) - self.started_at, 1
            ) if self.started_at else 0,
        }


# ---------------------------------------------------------------------------
# 执行器注册
# ---------------------------------------------------------------------------

# type -> 执行函数 (task) -> (ok, result)
EXECUTORS: dict[str, Callable[[Task], tuple[bool, str]]] = {}


def register_executor(task_type: str):
    """注册任务执行器。"""
    def deco(fn: Callable[[Task], tuple[bool, str]]):
        EXECUTORS[task_type] = fn
        return fn
    return deco


# ---------------------------------------------------------------------------
# 队列
# ---------------------------------------------------------------------------

class TaskQueue:
    def __init__(self, max_concurrency: int = 4):
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self.max_concurrency = max_concurrency
        self._running_count = 0

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        if not TASK_FILE.exists():
            return
        try:
            data = json.loads(TASK_FILE.read_text(encoding="utf-8"))
            for t in data.get("tasks", []):
                task = Task(
                    id=t["id"],
                    type=t["type"],
                    serial=t["serial"],
                    params=t.get("params", {}),
                    state=TaskState(t.get("state", "pending")),
                    result=t.get("result", ""),
                    error=t.get("error", ""),
                    created_at=t.get("created_at", 0),
                )
                # 重启后，之前 running 的标记为 failed
                if task.state == TaskState.RUNNING:
                    task.state = TaskState.FAILED
                    task.error = "服务重启，任务中断"
                self._tasks[task.id] = task
        except (json.JSONDecodeError, KeyError):
            pass

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {"tasks": [t.to_dict() for t in self._tasks.values()]}
        try:
            TASK_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ---- 创建 ----

    def create(self, task_type: str, serial: str, params: dict | None = None,
               auto_start: bool = True) -> Task:
        task = Task(
            id=f"t{uuid.uuid4().hex[:10]}",
            type=task_type,
            serial=serial,
            params=params or {},
            created_at=time.time(),
        )
        with self._lock:
            self._tasks[task.id] = task
            self._save()

        if auto_start:
            self.start(task.id)
        return task

    def create_batch(self, task_type: str, serials: list[str],
                     params: dict | None = None) -> list[Task]:
        """批量创建任务。"""
        tasks = []
        for s in serials:
            t = self.create(task_type, s, params, auto_start=False)
            tasks.append(t)
        # 批量启动
        for t in tasks:
            self.start(t.id)
        return tasks

    # ---- 启动 ----

    def start(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.state != TaskState.PENDING:
                return False

            if self._running_count >= self.max_concurrency:
                # 排队，稍后由 worker 拉起
                return True

            task.state = TaskState.RUNNING
            task.started_at = time.time()
            self._running_count += 1

            cancel = threading.Event()
            self._cancel_flags[task_id] = cancel
            self._save()

        th = threading.Thread(
            target=self._run, args=(task, cancel), daemon=True
        )
        self._threads[task_id] = th
        th.start()
        return True

    def _run(self, task: Task, cancel: threading.Event) -> None:
        executor = EXECUTORS.get(task.type)
        if executor is None:
            task.state = TaskState.FAILED
            task.error = f"未知任务类型: {task.type}"
        else:
            try:
                ok, result = executor(task)
                if cancel.is_set():
                    task.state = TaskState.CANCELLED
                    task.result = "已取消"
                elif ok:
                    task.state = TaskState.DONE
                    task.result = result
                    task.progress = 1.0
                else:
                    task.state = TaskState.FAILED
                    task.error = result
            except Exception as e:  # noqa: BLE001
                task.state = TaskState.FAILED
                task.error = f"{type(e).__name__}: {e}"

        task.finished_at = time.time()

        with self._lock:
            self._running_count = max(0, self._running_count - 1)
            self._save()
            # 拉起下一个 pending
            self._next_pending()

    def _next_pending(self) -> None:
        if self._running_count >= self.max_concurrency:
            return
        for t in self._tasks.values():
            if t.state == TaskState.PENDING:
                self.start(t.id)
                break

    # ---- 取消 ----

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            flag = self._cancel_flags.get(task_id)
            if flag:
                flag.set()
            task = self._tasks.get(task_id)
            if task and task.state == TaskState.PENDING:
                task.state = TaskState.CANCELLED
                task.finished_at = time.time()
                self._save()
                return True
        return flag is not None

    def cancel_all(self, serial: str = "") -> int:
        count = 0
        for t in list(self._tasks.values()):
            if t.state in (TaskState.PENDING, TaskState.RUNNING):
                if not serial or t.serial == serial:
                    if self.cancel(t.id):
                        count += 1
        return count

    # ---- 查询 ----

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list(self, serial: str = "", state: str = "", limit: int = 50) -> list[dict]:
        items = []
        for t in self._tasks.values():
            if serial and t.serial != serial:
                continue
            if state and t.state.value != state:
                continue
            items.append(t)

        items.sort(key=lambda x: x.created_at, reverse=True)
        return [t.to_dict() for t in items[:limit]]

    def stats(self) -> dict:
        counts = {
            "pending": 0, "running": 0, "done": 0,
            "failed": 0, "cancelled": 0,
        }
        for t in self._tasks.values():
            counts[t.state.value] = counts.get(t.state.value, 0) + 1
        counts["total"] = len(self._tasks)
        counts["active"] = counts["pending"] + counts["running"]
        return counts

    def clear_finished(self) -> int:
        with self._lock:
            to_remove = [
                tid for tid, t in self._tasks.items()
                if t.state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)
            ]
            for tid in to_remove:
                del self._tasks[tid]
            self._save()
            return len(to_remove)


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_queue: Optional[TaskQueue] = None
_q_lock = threading.Lock()


def get_queue() -> TaskQueue:
    global _queue
    with _q_lock:
        if _queue is None:
            _queue = TaskQueue(max_concurrency=4)
        return _queue


# ---------------------------------------------------------------------------
# 内置执行器
# ---------------------------------------------------------------------------

@register_executor("adb")
def _exec_adb(task: Task) -> tuple[bool, str]:
    import subprocess
    from ..core.config import get_config

    cmd = task.params.get("command", "")
    if not cmd:
        return False, "缺少 command 参数"

    adb = get_config().devices.adb_path
    p = subprocess.run(
        [adb, "-s", task.serial, "shell", cmd],
        capture_output=True, text=True, timeout=120,
    )
    task.steps.append({"cmd": cmd, "rc": p.returncode})
    return p.returncode == 0, (p.stdout or "")[:500]


@register_executor("launch")
def _exec_launch(task: Task) -> tuple[bool, str]:
    from ..core.actions import DeviceController

    pkg = task.params.get("package", "")
    if not pkg:
        return False, "缺少 package 参数"

    ctrl = DeviceController(task.serial)
    ok = ctrl.launch_app(pkg)
    return ok, f"启动 {pkg}: {'成功' if ok else '失败'}"


@register_executor("key")
def _exec_key(task: Task) -> tuple[bool, str]:
    from ..core.actions import DeviceController

    key = task.params.get("key", "home")
    ctrl = DeviceController(task.serial)
    ok = ctrl.press_key(key)
    return ok, f"按键 {key}: {'成功' if ok else '失败'}"


@register_executor("text")
def _exec_text(task: Task) -> tuple[bool, str]:
    from ..core.actions import DeviceController

    text = task.params.get("text", "")
    ctrl = DeviceController(task.serial)
    ok = ctrl.input_text(text)
    return ok, f"输入: {'成功' if ok else '失败'}"


@register_executor("swipe")
def _exec_swipe(task: Task) -> tuple[bool, str]:
    from ..core.actions import DeviceController

    direction = task.params.get("direction", "up")
    count = int(task.params.get("count", 1))
    interval = float(task.params.get("interval", 3.0))

    ctrl = DeviceController(task.serial)
    fn = {"up": ctrl.swipe_up, "down": ctrl.swipe_down}.get(direction)
    if fn is None:
        return False, f"未知方向: {direction}"

    for i in range(count):
        if fn():
            task.progress = (i + 1) / count
            task.steps.append({"swipe": i + 1, "ok": True})
        if i < count - 1:
            time.sleep(interval)

    return True, f"滑动 {count} 次"


@register_executor("publish")
def _exec_publish(task: Task) -> tuple[bool, str]:
    from ..apps.douyin import DouyinAdapter
    from ..flows.publish import PublishFlow

    video = task.params.get("video", "")
    if not video:
        return False, "缺少 video 参数"

    flow = PublishFlow(task.serial, DouyinAdapter)
    r = flow.run(
        video,
        title=task.params.get("title", ""),
        description=task.params.get("desc", ""),
        tags=task.params.get("tags", []),
    )
    task.steps.append(r.to_dict())
    return r.ok, r.error or "发布成功"


@register_executor("agent")
def _exec_agent(task: Task) -> tuple[bool, str]:
    from ..llm.agent import Agent

    goal = task.params.get("goal", "")
    if not goal:
        return False, "缺少 goal 参数"

    agent = Agent(task.serial)
    state = agent.run(goal, max_steps=int(task.params.get("max_steps", 20)))
    task.steps = state.history[-10:]
    return state.done, state.result or state.error
