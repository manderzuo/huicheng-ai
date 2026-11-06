"""
FishFleet — 原子操作

封装 uiautomator2 的低层操作，提供：
  · 截图 / UI 树
  · 点击 / 滑动 / 输入 / 按键
  · 应用启动/停止
  · 拟人化延迟
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Optional

try:
    import uiautomator2 as u2
except ImportError:  # pragma: no cover
    u2 = None

from .config import get_config


# ---------------------------------------------------------------------------
# 拟人化工具
# ---------------------------------------------------------------------------

class Humanizer:
    """模拟真人操作的随机延迟和轨迹。"""

    def __init__(self, min_delay: float = 0.5, max_delay: float = 3.0):
        self.min_delay = min_delay
        self.max_delay = max_delay

    def delay(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def short_delay(self) -> None:
        time.sleep(random.uniform(0.1, 0.4))

    # 手感曲线：拖得短按短距离，拖得长按长距离但收敛封顶。
    # 分段连续单调：0-80按1:1，80-400约0.7，400-800约0.5，800以上趋近封顶。
    SWIPE_CURVE = ((80, 1.0), (400, 0.7), (800, 0.5))
    SWIPE_MAX_DISTANCE = 600

    @staticmethod
    def swipe_feel(distance: int) -> int:
        """把用户拖动距离换算成实际发送距离，保持方向由调用方决定。"""
        d = max(0, int(distance))
        if d <= 0:
            return 0
        out, prev = 0.0, 0
        for edge, ratio in Humanizer.SWIPE_CURVE:
            if d <= prev:
                break
            out += (min(d, edge) - prev) * ratio
            prev = edge
            if d <= edge:
                break
        # 曲线本身不封顶：超过最后一段仍按最后一段斜率延续；
        # 钳制只发生在最终发送距离上，保证拖得越长走得越远。
        if d > prev:
            out += (d - prev) * Humanizer.SWIPE_CURVE[-1][1]
        return max(1, min(Humanizer.SWIPE_MAX_DISTANCE, int(round(out))))

    @staticmethod
    def swipe_shorten(x1: int, y1: int, x2: int, y2: int,
                      width: int = 0, height: int = 0) -> tuple[int, int, int, int]:
        """按手感曲线缩短滑动向量并钳制到屏幕内，方向不变。"""
        dx, dy = x2 - x1, y2 - y1
        dist = abs(dx) + abs(dy)
        if dist <= 0:
            return x1, y1, x2, y2
        feel = Humanizer.swipe_feel(dist)
        scale = feel / dist
        nx2 = int(round(x1 + dx * scale))
        ny2 = int(round(y1 + dy * scale))
        if width > 0:
            nx2 = max(0, min(width - 1, nx2))
        if height > 0:
            ny2 = max(0, min(height - 1, ny2))
        return x1, y1, nx2, ny2

    @staticmethod
    def swipe_points(x1: int, y1: int, x2: int, y2: int, steps: int = 8) -> list[tuple[int, int]]:
        """生成带抖动的滑动轨迹（模拟真人手指）。"""
        pts = []
        for i in range(steps + 1):
            t = i / steps
            # 缓动（先快后慢）
            t = 1 - (1 - t) ** 2
            x = int(x1 + (x2 - x1) * t + random.randint(-6, 6))
            y = int(y1 + (y2 - y1) * t + random.randint(-6, 6))
            pts.append((x, y))
        return pts


# ---------------------------------------------------------------------------
# 设备控制器
# ---------------------------------------------------------------------------

@dataclass
class ScreenInfo:
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.width // 2, self.height // 2


class DeviceController:
    """单台设备的高层操作封装。"""

    def __init__(self, serial: str, humanize: bool = True):
        if u2 is None:
            raise RuntimeError("需要 uiautomator2：pip install uiautomator2")

        cfg = get_config()
        self.serial = serial
        self.d = u2.connect(serial)
        human_cfg = cfg.guard.get("humanize", {}) if cfg.guard else {}
        self.humanize = humanize
        self.human = Humanizer(
            min_delay=human_cfg.get("min_delay", 0.5),
            max_delay=human_cfg.get("max_delay", 3.0),
        )
        self._screen: Optional[ScreenInfo] = None

    # ---- 屏幕 ----

    @property
    def screen(self) -> ScreenInfo:
        if self._screen is None:
            w, h = self.d.window_size()
            self._screen = ScreenInfo(w, h)
        return self._screen

    def screenshot(self, path: str | None = None) -> str:
        """截图，返回文件路径。"""
        if path is None:
            os.makedirs("data/shots", exist_ok=True)
            path = f"data/shots/{self.serial}_{int(time.time()*1000)}.png"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.d.screenshot(path)
        return path

    def ui_dump(self) -> str:
        """获取 UI 层级 XML。"""
        try:
            return self.d.dump_hierarchy()
        except Exception:  # noqa: BLE001
            return ""

    # ---- 前台应用 ----

    def current_app(self) -> dict:
        try:
            return self.d.app_current()
        except Exception:  # noqa: BLE001
            return {"package": "", "activity": ""}

    def current_package(self) -> str:
        return self.current_app().get("package", "")

    # ---- 点击 ----

    def tap(self, x: int, y: int, humanize: bool | None = None) -> bool:
        try:
            self.d.click(x, y)
            if humanize is None:
                humanize = self.humanize
            if humanize:
                self.human.short_delay()
            return True
        except Exception:  # noqa: BLE001
            return False

    def tap_text(self, text: str, timeout: float = 3.0) -> bool:
        """按文字点击（简单版，复杂定位用 locator）。"""
        try:
            el = self.d(text=text)
            if el.wait(timeout=timeout):
                el.click()
                return True
            return False
        except Exception:  # noqa: BLE001
            return False

    def long_press(self, x: int, y: int, duration: float = 1.0) -> bool:
        try:
            self.d.long_click(x, y, duration)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---- 滑动 ----

    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration: float = 0.3, humanize: bool | None = None) -> bool:
        try:
            if humanize is None:
                humanize = self.humanize
            if humanize:
                # 用轨迹点模拟
                pts = self.human.swipe_points(x1, y1, x2, y2)
                self.d.swipe_points(pts, duration / len(pts))
            else:
                self.d.swipe(x1, y1, x2, y2, duration)
            return True
        except Exception:  # noqa: BLE001
            try:
                self.d.swipe(x1, y1, x2, y2, duration)
                return True
            except Exception:  # noqa: BLE001
                return False

    def swipe_up(self, ratio_from: float = 0.75, ratio_to: float = 0.25) -> bool:
        """向上滑（看下一条）。"""
        w, h = self.screen.width, self.screen.height
        x = w // 2 + random.randint(-30, 30)
        return self.swipe(x, int(h * ratio_from), x, int(h * ratio_to), 0.25)

    def swipe_down(self, ratio_from: float = 0.25, ratio_to: float = 0.75) -> bool:
        w, h = self.screen.width, self.screen.height
        x = w // 2 + random.randint(-30, 30)
        return self.swipe(x, int(h * ratio_from), x, int(h * ratio_to), 0.25)

    # ---- 输入 ----

    def input_text(self, text: str) -> bool:
        """输入文字：默认熊猫键盘（XWKeyboard）广播，中文可靠；不可用回退 u2。"""
        try:
            import base64 as _b64, subprocess as _sp
            from .config import get_config as _get_cfg
            _adb = _get_cfg().devices.adb_path
            _raw = (text or '').encode('utf-8', errors='ignore')
            if _raw:
                _b = _b64.b64encode(_raw).decode('ascii')
                _r = _sp.run([_adb, "-s", self.serial, "shell",
                              f"am broadcast -a XW_INPUT_B64 --es msg '{_b}'"],
                             capture_output=True, text=True, timeout=20)
                if "Broadcast completed" in ((_r.stdout or '') + (_r.stderr or '')):
                    return True
        except Exception:  # noqa: BLE001
            pass
        try:
            el = self.d(className="android.widget.EditText")
            if el.exists:
                el.set_text(text)
                return True
            # 兜底：当前焦点输入
            self.d.send_keys(text)
            return True
        except Exception:  # noqa: BLE001
            return False

    def press_key(self, key: str) -> bool:
        """按键：back / home / enter / delete ..."""
        try:
            self.d.press(key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def back(self) -> bool:
        return self.press_key("back")

    def home(self) -> bool:
        return self.press_key("home")

    # ---- 应用 ----

    def launch_app(self, package: str) -> bool:
        """启动应用（走 adb，不用 uiautomator2）。

        ★ 2026-10-28 修正：原来用 self.d.app_start()，那是 uiautomator2 的
          方法，每台要 3-5 秒且并发时抢服务；批量 2 台经常只成 1 台。
          改用 monkey（和右键菜单一致），单台 ~200ms，并发不冲突。
        """
        try:
            from ..core.config import get_config
            adb = get_config().devices.adb_path
            import subprocess
            r = subprocess.run(
                [adb, "-s", self.serial, "shell", "monkey",
                 "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="ignore",
            )
            out = (r.stdout or "") + (r.stderr or "")
            # monkey 成功会有 "Events injected: 1"
            if "Events injected" in out:
                return True
            # 兜底：用 am start 试一次
            r2 = subprocess.run(
                [adb, "-s", self.serial, "shell", "monkey", "-p", package, "1"],
                capture_output=True, text=True, timeout=20,
                encoding="utf-8", errors="ignore",
            )
            return "Events injected" in ((r2.stdout or "") + (r2.stderr or ""))
        except Exception:  # noqa: BLE001
            return False

    def stop_app(self, package: str) -> bool:
        try:
            self.d.app_stop(package)
            return True
        except Exception:  # noqa: BLE001
            return False

    def is_app_running(self, package: str) -> bool:
        try:
            return self.d.app_current().get("package") == package
        except Exception:  # noqa: BLE001
            return False

    # ---- 等待 ----

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait_for_text(self, text: str, timeout: float = 10.0) -> bool:
        try:
            return bool(self.d(textContains=text).wait(timeout=timeout))
        except Exception:  # noqa: BLE001
            return False

    def wait_for_gone(self, text: str, timeout: float = 10.0) -> bool:
        try:
            return bool(self.d(textContains=text).wait_gone(timeout=timeout))
        except Exception:  # noqa: BLE001
            return False

    # ---- 弹窗处理 ----

    def dismiss_popups(self, texts: list[str] | None = None) -> int:
        """关闭常见弹窗，返回关闭数量。"""
        if texts is None:
            texts = ["取消", "稍后", "以后再说", "跳过", "我知道了", "关闭", "不允许", "暂不"]

        closed = 0
        for t in texts:
            try:
                el = self.d(text=t)
                if el.exists:
                    el.click()
                    closed += 1
                    self.human.short_delay()
            except Exception:  # noqa: BLE001
                continue
        return closed
