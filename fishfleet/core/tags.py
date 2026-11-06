"""
FishFleet — 设备标签（分组）

模仿汇城的"手机标签"：
  · 全部手机 (20/20)
  · 未标签手机 (2/20)
  · 自定义标签（抖音组、小红书组...）

存储：data/tags.json
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DATA_DIR = Path("data")
TAG_FILE = DATA_DIR / "tags.json"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class Tag:
    id: str
    name: str
    color: str = "#2f81f7"
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# 标签管理器
# ---------------------------------------------------------------------------

class TagManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._tags: dict[str, Tag] = {}
        self._assignments: dict[str, list[str]] = {}   # serial -> [tag_id]
        self._load()

    # ---- 持久化 ----

    def _load(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not TAG_FILE.exists():
            # 创建默认标签
            self._tags = {
                "douyin": Tag(id="douyin", name="抖音组", color="#fe2c55", created_at=time.time()),
                "xhs": Tag(id="xhs", name="小红书组", color="#ff2442", created_at=time.time()),
                "idle": Tag(id="idle", name="备用", color="#8b949e", created_at=time.time()),
            }
            self._save()
            return

        try:
            data = json.loads(TAG_FILE.read_text(encoding="utf-8"))
            self._tags = {
                t["id"]: Tag(
                    id=t["id"], name=t["name"],
                    color=t.get("color", "#2f81f7"),
                    created_at=t.get("created_at", 0),
                )
                for t in data.get("tags", [])
            }
            self._assignments = data.get("assignments", {})
        except (json.JSONDecodeError, KeyError, OSError):
            self._tags = {}
            self._assignments = {}

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "tags": [t.to_dict() for t in self._tags.values()],
            "assignments": self._assignments,
        }
        TAG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---- 标签 CRUD ----

    def list_tags(self) -> list[dict]:
        return [t.to_dict() for t in self._tags.values()]

    def create_tag(self, name: str, color: str = "#2f81f7") -> dict:
        with self._lock:
            tag_id = f"t{int(time.time() * 1000) % 1000000}"
            tag = Tag(id=tag_id, name=name, color=color, created_at=time.time())
            self._tags[tag_id] = tag
            self._save()
            return tag.to_dict()

    def update_tag(self, tag_id: str, name: str = "", color: str = "") -> bool:
        with self._lock:
            t = self._tags.get(tag_id)
            if t is None:
                return False
            if name:
                t.name = name
            if color:
                t.color = color
            self._save()
            return True

    def delete_tag(self, tag_id: str) -> bool:
        with self._lock:
            if tag_id not in self._tags:
                return False
            del self._tags[tag_id]
            # 从所有设备移除
            for serial in list(self._assignments.keys()):
                if tag_id in self._assignments[serial]:
                    self._assignments[serial].remove(tag_id)
                if not self._assignments[serial]:
                    del self._assignments[serial]
            self._save()
            return True

    # ---- 设备分配 ----

    def get_tags(self, serial: str) -> list[str]:
        return list(self._assignments.get(serial, []))

    def set_tags(self, serial: str, tag_ids: list[str]) -> None:
        with self._lock:
            valid = [t for t in tag_ids if t in self._tags]
            if valid:
                self._assignments[serial] = valid
            else:
                self._assignments.pop(serial, None)
            self._save()

    def add_tag(self, serial: str, tag_id: str) -> bool:
        if tag_id not in self._tags:
            return False
        with self._lock:
            tags = self._assignments.setdefault(serial, [])
            if tag_id not in tags:
                tags.append(tag_id)
            self._save()
            return True

    def remove_tag(self, serial: str, tag_id: str) -> bool:
        with self._lock:
            tags = self._assignments.get(serial, [])
            if tag_id in tags:
                tags.remove(tag_id)
            if not tags:
                self._assignments.pop(serial, None)
            self._save()
            return True

    # ---- 查询 ----

    def devices_with_tag(self, tag_id: str) -> list[str]:
        return [s for s, tags in self._assignments.items() if tag_id in tags]

    def devices_without_tags(self, all_serials: list[str]) -> list[str]:
        return [s for s in all_serials if not self._assignments.get(s)]

    def summary(self, all_serials: list[str]) -> dict:
        """返回各分组的统计。"""
        result = {
            "all": {
                "id": "all",
                "name": "全部手机",
                "count": len(all_serials),
                "total": len(all_serials),
                "color": "#2f81f7",
            },
            "untagged": {
                "id": "untagged",
                "name": "未标签手机",
                "count": len(self.devices_without_tags(all_serials)),
                "total": len(all_serials),
                "color": "#8b949e",
            },
            "tags": [],
        }

        for t in self._tags.values():
            devs = self.devices_with_tag(t.id)
            result["tags"].append({
                "id": t.id,
                "name": t.name,
                "color": t.color,
                "count": len(devs),
                "total": len(all_serials),
                "serials": devs,
            })

        return result


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_manager: Optional[TagManager] = None
_lock = threading.Lock()


def get_tag_manager() -> TagManager:
    global _manager
    with _lock:
        if _manager is None:
            _manager = TagManager()
        return _manager
