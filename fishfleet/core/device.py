"""
FishFleet — 设备管理

负责：
  · 设备发现（adb devices）
  · 连接管理（USB / WiFi）
  · 健康检查（电量/温度/存储/网络）
  · 自动重连
  · 并发锁（同一设备同一时刻只允许一个操作）
"""

from __future__ import annotations

import subprocess
import re
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .config import get_config


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

class DeviceState(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"
    UNAUTHORIZED = "unauthorized"


@dataclass
class DeviceHealth:
    battery: Optional[int] = None      # 0-100
    battery_temp: Optional[float] = None  # 摄氏度
    storage_free_mb: Optional[int] = None
    wifi_connected: Optional[bool] = None
    screen_on: Optional[bool] = None
    checked_at: float = 0.0

    def is_healthy(self) -> bool:
        if self.battery is not None and self.battery < 10:
            return False
        if self.battery_temp is not None and self.battery_temp > 45:
            return False
        if self.storage_free_mb is not None and self.storage_free_mb < 200:
            return False
        return True


@dataclass
class Device:
    serial: str
    identity: str = ""
    connection: str = "usb"
    accessible: Optional[bool] = None
    mode: int = 0
    is_accessible: bool = False
    accessible_port: int = 0
    index: int = 0
    model: str = ""
    state: DeviceState = DeviceState.OFFLINE
    health: DeviceHealth = field(default_factory=DeviceHealth)
    account: dict = field(default_factory=dict)
    last_error: str = ""
    last_seen: float = 0.0
    width: int = 1080
    height: int = 2280
    _size_ok: bool = field(default=False, repr=False)
    _size_ts: float = field(default=0.0, repr=False)

    # 运行时（不持久化）
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _current_task: Optional[str] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "serial": self.serial,
            "index": self.index,
            "model": self.model,
            "state": self.state.value,
            "battery": self.health.battery,
            "account": self.account,
            "last_error": self.last_error,
            "current_task": self._current_task,
            "width": self.width,
            "height": self.height,
        }


# ---------------------------------------------------------------------------
# ADB 封装
# ---------------------------------------------------------------------------

class ADB:
    """极简 ADB 封装。"""

    def __init__(self, adb_path: str = "adb"):
        self.adb = adb_path

    def _run(self, args: list[str], timeout: int = 30, serial: str | None = None) -> tuple[int, str, str]:
        cmd = [self.adb]
        if serial:
            cmd += ["-s", serial]
        cmd += args
        try:
            p = subprocess.run(
                cmd, capture_output=True, timeout=timeout,
                text=True, encoding="utf-8", errors="replace",
            )
            return p.returncode, p.stdout or "", p.stderr or ""
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except FileNotFoundError:
            return -2, "", f"adb 不存在: {self.adb}"
        except Exception as e:  # noqa: BLE001
            return -3, "", str(e)

    def devices(self) -> list[tuple[str, str]]:
        """返回 [(serial, state)]"""
        code, out, _ = self._run(["devices"])
        if code != 0:
            return []
        result = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, state = line.split("\t", 1)
            result.append((serial.strip(), state.strip()))
        return result

    def shell(self, serial: str, cmd: str, timeout: int = 30) -> tuple[int, str]:
        code, out, err = self._run(["shell", cmd], timeout=timeout, serial=serial)
        return code, (out if code == 0 else err)

    def getprop(self, serial: str, prop: str) -> str:
        code, out = self.shell(serial, f"getprop {prop}")
        return out.strip() if code == 0 else ""


# ---------------------------------------------------------------------------
# 设备管理器
# ---------------------------------------------------------------------------

class DeviceManager:
    def __init__(self, adb_path: str | None = None):
        cfg = get_config()
        self.adb = ADB(adb_path or cfg.devices.adb_path)
        self._devices: dict[str, Device] = {}
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._transport_identity: dict[str, str] = {}
        self._transport_kind: dict[str, str] = {}
        self._access_cache: dict[str, tuple[float, Optional[bool]]] = {}

    def _identify_transport(self, serial: str) -> str:
        if ':' not in serial:
            return serial
        code, output = self.adb.shell(serial, 'getprop ro.serialno', timeout=4)
        identity = output.strip()
        if code == 0 and re.fullmatch(r'[A-Za-z0-9._-]{4,128}', identity) and identity.lower() not in ('unknown', 'null', '0123456789abcdef'):
            return identity
        return ''

    def _identify_network_kind(self, serial: str) -> str:
        host = serial.rsplit(':', 1)[0]
        code, routes = self.adb.shell(serial, 'ip -4 route', timeout=4)
        if code == 0:
            interfaces = {m.group(1) for line in routes.splitlines()
                          if re.search(r'\bsrc\s+' + re.escape(host) + r'(?:\s|$)', line)
                          and (m := re.search(r'\bdev\s+(\S+)', line))}
            if any(name.startswith(('eth', 'en', 'usb')) for name in interfaces):
                return 'otg'
            if any(name.startswith(('wlan', 'wifi')) for name in interfaces):
                return 'wifi'
        return 'network'

    def _read_accessible(self, serial: str) -> Optional[bool]:
        from .device_ext import accessible_is_granted
        try:
            result = accessible_is_granted(serial)
            return bool(result['granted']) if result.get('ok') else None
        except Exception:
            return None

    # ---- 发现 ----

    def refresh(self) -> list[Device]:
        """重新扫描设备；仅在能验证硬件序列号时合并不同传输。"""
        with self._refresh_lock:
            return self._refresh_devices()

    def _refresh_devices(self) -> list[Device]:
        found = self.adb.devices()
        now = time.time()
        live_serials = {s for s, state in found if state == 'device'}
        self._transport_identity = {s: identity for s, identity in self._transport_identity.items() if s in live_serials}
        self._transport_kind = {s: kind for s, kind in self._transport_kind.items() if s in live_serials}
        unknown_kind = [s for s in live_serials if ':' in s and s not in self._transport_kind]
        if unknown_kind:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for serial, kind in zip(unknown_kind, pool.map(self._identify_network_kind, unknown_kind)):
                    if kind != 'network':
                        self._transport_kind[serial] = kind
        unknown = [s for s, state in found if state == 'device' and s not in self._transport_identity]
        if unknown:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for serial, identity in zip(unknown, pool.map(self._identify_transport, unknown)):
                    if identity:
                        self._transport_identity[serial] = identity
        groups = {}
        for serial, state in found:
            identity = self._transport_identity.get(serial, serial)
            groups.setdefault(identity, []).append((serial, state))
        # 同时存在时优先 USB；未识别身份的网络设备独立保留。
        chosen = {identity: sorted(entries, key=lambda x: (x[1] != 'device', ':' in x[0], x[0]))[0] for identity, entries in groups.items()}
        stale_access = [serial for serial, state in chosen.values() if state == 'device' and now - self._access_cache.get(serial, (0, None))[0] > 30]
        if stale_access:
            with ThreadPoolExecutor(max_workers=8) as pool:
                for serial, value in zip(stale_access, pool.map(self._read_accessible, stale_access)):
                    self._access_cache[serial] = (now, value)

        with self._lock:
            seen = set()
            for identity, (serial, state) in chosen.items():
                seen.add(serial)
                dev = next((d for d in self._devices.values() if (d.identity or d.serial) == identity), None)
                if dev is None:
                    dev = self._devices.get(serial)
                for old_serial, old_dev in list(self._devices.items()):
                    if old_dev is not dev and (old_dev.identity or old_dev.serial) == identity:
                        del self._devices[old_serial]
                if dev is None:
                    dev = Device(serial=serial, identity=identity, index=len(self._devices) + 1)
                else:
                    for old_serial, old_dev in list(self._devices.items()):
                        if old_dev is dev and old_serial != serial:
                            del self._devices[old_serial]
                dev.serial = serial
                dev.identity = identity
                dev.connection = self._transport_kind.get(serial, 'network') if ':' in serial else 'usb'
                dev.accessible = self._access_cache.get(serial, (0, None))[1] if state == 'device' else None
                try:
                    from .device_ext import accessible_runtime
                    runtime = accessible_runtime(serial) or {}
                    if runtime.get("is_accessible") or self._accessible_session_alive(serial):
                        dev.mode = 3
                        dev.is_accessible = True
                        dev.accessible = True
                        try:
                            dev.accessible_port = int(runtime.get("port") or 0)
                        except (TypeError, ValueError):
                            dev.accessible_port = 0
                    else:
                        dev.mode = 0
                        dev.is_accessible = False
                        dev.accessible_port = 0
                except Exception:
                    # 兜底也不许用“已授权”冒充“已连接”：标签只认真实通道。
                    dev.mode = 0
                    dev.is_accessible = False
                    dev.accessible_port = 0
                self._devices[serial] = dev

                if state == "device":
                    dev.state = DeviceState.ONLINE
                    dev.last_error = ""
                    # 首次上线时读一次真实分辨率（wm size 有 override 就用 override）
                    # 分辨率：首次立即读，之后每 30 秒刷新一次（wm size 可能被改）
                    need = (not dev._size_ok) or (now - getattr(dev, "_size_ts", 0) > 30)
                    if need:
                        try:
                            code, out = self.adb.shell(serial, "wm size", timeout=3)
                            if code == 0 and out:
                                import re as _re
                                m = _re.search(r"Override size:\s*(\d+)x(\d+)", out)
                                if not m:
                                    m = _re.search(r"Physical size:\s*(\d+)x(\d+)", out)
                                if m:
                                    dev.width = int(m.group(1))
                                    dev.height = int(m.group(2))
                                    dev._size_ok = True
                                    dev._size_ts = now
                        except Exception:
                            pass
                elif state == "unauthorized":
                    dev.state = DeviceState.UNAUTHORIZED
                    dev.last_error = "未授权（需在手机上点允许）"
                else:
                    dev.state = DeviceState.OFFLINE
                    dev.last_error = state
                dev.last_seen = now

            # 消失的设备
            for serial, dev in self._devices.items():
                if serial not in seen:
                    # 无障碍模式只要画面还在跑，就不能因为 adb 掉线而降级：
                    # 手机是通过内网 TCP 回连的，与 adbd 无关。
                    if self._accessible_alive(serial):
                        dev.state = DeviceState.ONLINE
                        dev.is_accessible = True
                        dev.mode = 3
                        dev.last_error = ""
                        continue
                    dev.state = DeviceState.OFFLINE
                    dev.last_error = "未连接"

        return list(self._devices.values())

    @staticmethod
    def _accessible_session_alive(serial: str) -> bool:
        """无障碍会话是否活着（只看投屏 worker，不依赖运行表也不依赖 adb）。"""
        try:
            from ..web.server import _streams  # 运行期查询避免循环导入
            w = _streams.get(serial)
            return bool(w is not None and getattr(w, "accessible_mode", False)
                        and getattr(w, "running", False))
        except Exception:
            return False

    @staticmethod
    def _accessible_alive(serial: str) -> bool:
        """无障碍通道是否仍然活着（不依赖 adb）。"

        判据（二选一）：无障碍投屏会话仍在收流或运行表标记为无障碍。
        前者覆盖运行表丢失但会话还活着的僵尸态。
        """
        if DeviceManager._accessible_session_alive(serial):
            return True
        try:
            from .device_ext import accessible_runtime
            if not (accessible_runtime(serial) or {}).get("is_accessible"):
                return False
        except Exception:
            return False
        try:
            from ..web.server import _streams  # 运行期查询，避免循环导入
            w = _streams.get(serial)
            return bool(w is not None and getattr(w, "running", False))
        except Exception:
            return False

    # ---- 查询 ----

    def get(self, serial: str) -> Optional[Device]:
        return self._devices.get(serial)

    def all(self) -> list[Device]:
        return list(self._devices.values())

    def online(self) -> list[Device]:
        return [d for d in self._devices.values() if d.state == DeviceState.ONLINE]

    def by_index(self, index: int) -> Optional[Device]:
        for d in self._devices.values():
            if d.index == index:
                return d
        return None

    # ---- 健康检查 ----

    def check_health(self, serial: str) -> DeviceHealth:
        h = DeviceHealth(checked_at=time.time())

        # 电量
        code, out = self.adb.shell(serial, "dumpsys battery")
        if code == 0:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("level:"):
                    try:
                        h.battery = int(line.split(":")[1].strip())
                    except (ValueError, IndexError):
                        pass
                elif line.startswith("temperature:"):
                    try:
                        h.battery_temp = int(line.split(":")[1].strip()) / 10.0
                    except (ValueError, IndexError):
                        pass

        # 存储
        code, out = self.adb.shell(serial, "df /data")
        if code == 0:
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        h.storage_free_mb = int(parts[3]) // 1024
                    except (ValueError, IndexError):
                        pass

        # 屏幕
        code, out = self.adb.shell(serial, "dumpsys power | grep 'Display Power'")
        if code == 0:
            h.screen_on = "state=ON" in out

        # 更新
        dev = self._devices.get(serial)
        if dev:
            dev.health = h

        return h

    # ---- 并发锁 ----

    def acquire(self, serial: str, timeout: float = 60.0) -> bool:
        """尝试占用设备（同一时刻只允许一个操作）。"""
        dev = self._devices.get(serial)
        if dev is None:
            return False
        ok = dev._lock.acquire(timeout=timeout)
        if ok:
            dev.state = DeviceState.BUSY
        return ok

    def release(self, serial: str) -> None:
        dev = self._devices.get(serial)
        if dev is None:
            return
        try:
            dev._lock.release()
        except RuntimeError:
            pass
        if dev.state == DeviceState.BUSY:
            dev.state = DeviceState.ONLINE
        dev._current_task = None

    # ---- 自动重连 ----

    def ensure_online(self, serial: str, retries: int = 3, delay: float = 2.0) -> bool:
        """确保设备在线，失败则尝试重连。"""
        for i in range(retries):
            self.refresh()
            dev = self._devices.get(serial)
            if dev and dev.state == DeviceState.ONLINE:
                return True
            if i < retries - 1:
                # 尝试重连
                self.adb._run(["reconnect", "offline"], timeout=5)
                time.sleep(delay)
        return False


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_manager: Optional[DeviceManager] = None
_manager_lock = threading.Lock()


def get_manager() -> DeviceManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = DeviceManager()
        return _manager
