"""
FishFleet — 动作录制与回放

模仿汇城的"录制动作 / 执行动作"：
  · 录制：把一系列操作记录下来
  · 回放：按顺序重放（支持变速、循环、跨设备）

存储：data/actions/*.json
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

ACTIONS_DIR = Path("data/actions")


# ---------------------------------------------------------------------------
# 动作
# ---------------------------------------------------------------------------

@dataclass
class Action:
    type: str                       # tap / swipe / text / key / launch / wait
    params: dict = field(default_factory=dict)
    delay: float = 0.5              # 执行前的等待（秒）
    comment: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "params": self.params,
            "delay": self.delay,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Action":
        return cls(
            type=d["type"],
            params=d.get("params", {}),
            delay=d.get("delay", 0.5),
            comment=d.get("comment", ""),
        )


@dataclass
class ActionScript:
    id: str
    name: str
    actions: list[Action] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "actions": [a.to_dict() for a in self.actions],
            "count": len(self.actions),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------

class ActionPlayer:
    """回放动作脚本。"""

    def __init__(self, serial: str):
        from ..core.actions import DeviceController
        self.serial = serial
        self.ctrl = DeviceController(serial)

    def play(
        self,
        script: ActionScript,
        speed: float = 1.0,
        repeat: int = 1,
        stop_flag: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> dict:
        """
        回放脚本。

        speed:   速度倍率（2.0 = 两倍速）
        repeat:  重复次数
        stop_flag: 返回 True 时中止
        """
        t0 = time.time()
        total = len(script.actions) * repeat
        done = 0
        errors = []

        for r in range(repeat):
            for i, action in enumerate(script.actions):
                if stop_flag and stop_flag():
                    return {
                        "ok": False, "done": done, "total": total,
                        "error": "已中止", "elapsed": time.time() - t0,
                    }

                # 延迟
                delay = max(0.0, action.delay / max(0.1, speed))
                if delay > 0:
                    time.sleep(delay)

                # 执行
                try:
                    ok = self._exec(action)
                except Exception as e:  # noqa: BLE001
                    ok = False
                    errors.append(f"第{r+1}轮 第{i+1}步: {e}")

                done += 1
                if progress_cb:
                    progress_cb(done, total, f"{action.type}")

        return {
            "ok": len(errors) == 0,
            "done": done,
            "total": total,
            "errors": errors[:5],
            "elapsed": round(time.time() - t0, 1),
        }

    def _exec(self, a: Action) -> bool:
        p = a.params
        if a.type == "tap":
            return self.ctrl.tap(int(p["x"]), int(p["y"]))
        if a.type == "swipe":
            return self.ctrl.swipe(
                int(p["x1"]), int(p["y1"]), int(p["x2"]), int(p["y2"]),
                float(p.get("duration", 0.3)),
            )
        if a.type == "text":
            return self.ctrl.input_text(p.get("text", ""))
        if a.type == "key":
            return self.ctrl.press_key(p.get("key", "back"))
        if a.type == "launch":
            return self.ctrl.launch_app(p.get("package", ""))
        if a.type == "wait":
            time.sleep(float(p.get("seconds", 1)))
            return True
        if a.type == "swipe_up":
            return self.ctrl.swipe_up()
        if a.type == "swipe_down":
            return self.ctrl.swipe_down()
        if a.type == "back":
            return self.ctrl.back()
        if a.type == "home":
            return self.ctrl.home()
        return False


# ---------------------------------------------------------------------------
# 脚本管理
# ---------------------------------------------------------------------------

class ScriptManager:
    def __init__(self):
        ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ActionScript] = {}
        self._load_all()

    def _path(self, script_id: str) -> Path:
        return ACTIONS_DIR / f"{script_id}.json"

    def _load_all(self) -> None:
        for f in ACTIONS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                s = ActionScript(
                    id=data["id"],
                    name=data["name"],
                    description=data.get("description", ""),
                    actions=[Action.from_dict(a) for a in data.get("actions", [])],
                    created_at=data.get("created_at", 0),
                    updated_at=data.get("updated_at", 0),
                )
                self._cache[s.id] = s
            except (json.JSONDecodeError, KeyError):
                continue

    def list(self) -> list[dict]:
        items = sorted(self._cache.values(), key=lambda x: x.updated_at, reverse=True)
        return [s.to_dict() for s in items]

    def get(self, script_id: str) -> Optional[ActionScript]:
        return self._cache.get(script_id)

    def create(self, name: str, description: str = "") -> ActionScript:
        s = ActionScript(
            id=f"s{uuid.uuid4().hex[:8]}",
            name=name,
            description=description,
            created_at=time.time(),
            updated_at=time.time(),
        )
        self._cache[s.id] = s
        self._save(s)
        return s

    def add_action(self, script_id: str, action: Action) -> bool:
        s = self._cache.get(script_id)
        if s is None:
            return False
        s.actions.append(action)
        s.updated_at = time.time()
        self._save(s)
        return True

    def remove_action(self, script_id: str, index: int) -> bool:
        s = self._cache.get(script_id)
        if s is None or index < 0 or index >= len(s.actions):
            return False
        s.actions.pop(index)
        s.updated_at = time.time()
        self._save(s)
        return True

    def delete(self, script_id: str) -> bool:
        if script_id in self._cache:
            del self._cache[script_id]
        p = self._path(script_id)
        if p.exists():
            p.unlink()
        return True

    def _save(self, s: ActionScript) -> None:
        p = self._path(s.id)
        p.write_text(
            json.dumps(s.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- 导入/导出 ----

    def export_text(self, script_id: str) -> str:
        s = self._cache.get(script_id)
        if s is None:
            return ""
        return json.dumps(s.to_dict(), ensure_ascii=False, indent=2)

    def import_text(self, text: str) -> Optional[ActionScript]:
        try:
            data = json.loads(text)
            s = ActionScript(
                id=f"s{uuid.uuid4().hex[:8]}",
                name=data.get("name", "导入脚本"),
                description=data.get("description", ""),
                actions=[Action.from_dict(a) for a in data.get("actions", [])],
                created_at=time.time(),
                updated_at=time.time(),
            )
            self._cache[s.id] = s
            self._save(s)
            return s
        except (json.JSONDecodeError, KeyError):
            return None


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_manager: Optional[ScriptManager] = None


def get_script_manager() -> ScriptManager:
    global _manager
    if _manager is None:
        _manager = ScriptManager()
    return _manager


# ---------------------------------------------------------------------------
# 录制器（监听屏幕操作）
# ---------------------------------------------------------------------------

class Recorder:
    """
    录制器：把用户的操作记录下来。

    两种模式：
      1. 手动模式：用户调用 record() 添加动作
      2. 自动模式：轮询 UI 变化（较复杂，暂不实现）
    """

    def __init__(self, script_id: str):
        self.script_id = script_id
        self._recording = False
        self._start_time = 0.0
        self._last_ts = 0.0

    def start(self) -> None:
        self._recording = True
        self._start_time = time.time()
        self._last_ts = self._start_time

    def stop(self) -> None:
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    def record(self, action_type: str, params: dict, comment: str = "") -> bool:
        """记录一个动作（自动计算与上一个动作的间隔）。"""
        if not self._recording:
            return False

        now = time.time()
        delay = max(0.1, now - self._last_ts)
        self._last_ts = now

        action = Action(type=action_type, params=params, delay=round(delay, 2), comment=comment)
        return get_script_manager().add_action(self.script_id, action)
