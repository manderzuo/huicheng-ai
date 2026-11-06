"""
FishFleet — 系统设置

模仿汇城的"系统设置"面板：
  · 软件界面（语言/控制板位置/显示模式/排列方式）
  · 手机界面（编号/颜色/间距/单排数量）
  · 显示参数（大屏尺寸/小屏尺寸/画质/帧率）
  · 连接筛选

存储：data/settings.json
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

SETTINGS_FILE = Path("data/settings.json")


# ---------------------------------------------------------------------------
# 默认设置
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    # 软件界面
    "ui": {
        "language": "zh-CN",
        "panel_position": "right",      # left | right
        "theme": "dark",                # light | dark
        "sort_mode": "index",           # index | tag
        "multi_select": True,           # Ctrl 多选
    },

    # 手机界面
    "phone": {
        "number_opacity": 1.0,
        "number_color": "#2f81f7",
        "grid_gap": 10,
        "per_row": 0,                   # 0 = 自动
        "show_magnifier": True,         # 放大镜
        "show_remark": True,            # 备注框
        "show_mode": True,              # 显示模式标记
        "show_number": True,
        "show_name": True,
        "show_tag": True,               # 显示连接类型（USB/WiFi）
    },

    # 显示参数
    "display": {
        "big_width": 480,               # 大屏宽度
        "small_width": 320,             # 小屏宽度（缩略图）
        "quality": 55,                  # 画质（JPEG 质量）
        "fps": 0.33,                    # 帧率（每秒帧数，0.33 = 3秒一帧）
        "mode": "small",                # small | big | control
    },

    # 连接筛选
    "filter": {
        "connection": "all",            # all | usb | wifi | otg | accessibility
        "type": "local",                # local | cloud | virtual
        "group": "all",                 # all | untagged | <tag_id>
    },

    # 行为
    "behavior": {
        "auto_sleep": False,            # 自动熄屏
        "check_health_interval": 60,
        "screenshot_interval": 3,
    },
}


# ---------------------------------------------------------------------------
# 设置管理
# ---------------------------------------------------------------------------

@contextmanager
def _process_lock():
    """在 Windows 使用系统释放的字节锁，避免不同新版进程交错写设置。"""
    if os.name != 'nt':
        yield
        return
    import msvcrt
    lock_path = SETTINGS_FILE.with_name(SETTINGS_FILE.name + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a+b') as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b'0'); lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


class Settings:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        with _process_lock():
            self._load()

    def _load(self) -> None:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_FILE.exists():
            self._data = json.loads(json.dumps(DEFAULTS))   # 深拷贝
            self._save()
            return

        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # 文件不可读时不得用默认值覆盖已有用户设置。
            raise RuntimeError('设置文件无法读取，拒绝覆盖') from exc
        if not isinstance(saved, dict):
            raise RuntimeError('设置文件格式无效，拒绝覆盖')

        # 合并（保证新版本新增的项有默认值）
        self._data = self._merge(DEFAULTS, saved)

    @staticmethod
    def _merge(default: dict, saved: dict) -> dict:
        result = dict(default)
        for k, v in saved.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = Settings._merge(result[k], v)
            else:
                result[k] = v
        return result

    def _save(self) -> None:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        fd, temp_path = tempfile.mkstemp(prefix=SETTINGS_FILE.name + '.', suffix='.tmp', dir=SETTINGS_FILE.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, SETTINGS_FILE)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    # ---- 读写 ----

    def get(self, section: str = "", key: str = "", default: Any = None) -> Any:
        if not section:
            return self._data
        sec = self._data.get(section, {})
        if not key:
            return sec
        return sec.get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        with self._lock, _process_lock():
            self._load()
            previous = json.loads(json.dumps(self._data))
            self._data.setdefault(section, {})[key] = value
            try:
                self._save()
            except Exception:
                self._data = previous
                raise

    def update(self, section: str, values: dict) -> None:
        with self._lock, _process_lock():
            self._load()
            previous = json.loads(json.dumps(self._data))
            self._data.setdefault(section, {}).update(values)
            try:
                self._save()
            except Exception:
                self._data = previous
                raise

    def reset(self, section: str = "") -> None:
        with self._lock, _process_lock():
            self._load()
            previous = json.loads(json.dumps(self._data))
            if section:
                self._data[section] = json.loads(json.dumps(DEFAULTS.get(section, {})))
            else:
                self._data = json.loads(json.dumps(DEFAULTS))
            try:
                self._save()
            except Exception:
                self._data = previous
                raise

    def to_dict(self) -> dict:
        # 读取时刷新磁盘，使其他遵守相同锁的进程更新可见。
        with self._lock, _process_lock():
            self._load()
            return json.loads(json.dumps(self._data))


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_settings: Optional[Settings] = None
_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings
    with _lock:
        if _settings is None:
            _settings = Settings()
        return _settings
