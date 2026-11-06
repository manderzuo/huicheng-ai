"""FishFleet 自动发布器 —— 小红书 / 抖音 / 哔哩哔哩。

设计要点
--------
1. **视频必须先到相册**：发布时 App 只认相册，不认电脑路径。
   所以流程固定为 `MediaPusher.ensure_video()` → 等 MediaStore 可见 → 再发布。
2. **坐标来自实机探测**：三个平台都是自绘控件，uiautomator 拿不到
   bounds 的必须用固定坐标（后面跟一次 dump 校验）。
3. **每一步都截图留证**：失败时能看出卡在哪一屏。
4. **文案走 XWKeyboard 广播**（XW_INPUT_TEXT），中文可靠。

⚠️ 真实发布有副作用。调用前应确认：账号已登录、文案已审核、视频内容合规。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

ADB = os.environ.get(
    "ADB_PATH", __import__("fishfleet.paths", fromlist=["adb_path"]).adb_path()
)

SHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "publish_logs")
SHOT_DIR = os.path.normpath(SHOT_DIR)


# ---------------------------------------------------------------------------
# 设备操作封装
# ---------------------------------------------------------------------------

class Phone:
    def __init__(self, serial: str, tag: str = ""):
        self.serial = serial
        self.tag = tag or serial[-6:]
        self.w, self.h = self._screen_size()
        os.makedirs(SHOT_DIR, exist_ok=True)

    # ---- 基础 ----

    def _run(self, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            [ADB, "-s", self.serial] + args,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="ignore",
        )

    def _screen_size(self) -> tuple[int, int]:
        """真实屏幕尺寸。

        ★ 不能用 `wm size`：它返回的是「应用可用区」（如 1080x1920），
          不含底部导航栏；而截图是完整屏幕（1080x2280）。
          用 1920 当基准算出来的比例，点击会整体偏上，根本点不到底部按钮。
        """
        # 1) 首选：截图 PNG 的真实像素（最可靠）
        try:
            r = subprocess.run(
                [ADB, "-s", self.serial, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=40,
            )
            raw = r.stdout
            if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
                w = int.from_bytes(raw[16:20], "big")
                h = int.from_bytes(raw[20:24], "big")
                if 200 <= w <= 5000 and 200 <= h <= 5000:
                    return w, h
        except Exception:
            pass
        # 2) 兜底：wm size
        try:
            r = self._run(["shell", "wm size"], timeout=20)
            m = re.search(r"(\d+)x(\d+)", r.stdout or "")
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return 1080, 2280

    def shot(self, tag: str) -> str:
        p = subprocess.run(
            [ADB, "-s", self.serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=40,
        )
        f = os.path.join(SHOT_DIR, f"{self.tag}_{int(time.time())}_{tag}.png")
        with open(f, "wb") as fh:
            fh.write(p.stdout)
        return f

    # ---- 收键盘（不可靠的 back 的替代）----

    def hide_keyboard(self) -> None:
        """收起软键盘。

        坑：`input keyevent 4`（BACK）会被某些输入法吞掉，
        键盘还在，导致底部的发布按钮被顶上去、甚至被完全挡住。
        更可靠的做法是点一下标题上方的空白区（那里没有输入框）。
        """
        # 点标题上方空白（发布页布局稳定：y≈0.13 是预览图顶部）
        self.tap(int(self.w * 0.5), int(self.h * 0.13), 1.2)
        time.sleep(0.8)
        # 再补一次 BACK
        self._run(["shell", "input", "keyevent", "4"], timeout=20)
        time.sleep(1.2)

    def keyboard_shown(self) -> bool:
        """判断软键盘是否显示（用 dumpsys input_method）。"""
        r = self._run(["shell", "dumpsys input_method | grep mInputShown"], timeout=20)
        return "mInputShown=true" in (r.stdout or "")

    # ---- 让指定视频排到相册第一位 ----

    def bring_to_front(self, video_name: str) -> bool:
        """把指定视频排到相册第一格。

        原理：相册按 MediaStore 的 date_modified 降序排。
        `content update` 因权限拿不到，但 `touch` 文件 mtime
        + MEDIA_SCANNER_SCAN_FILE 会让扫描器把 date_modified 刷成当前时间，
        于是它自然排到最前面。实测有效。

        路径直接按 MediaPusher 的约定：/sdcard/Download/{name}
        （find 在某些机型上被 SELinux 限制，不能用）
        """
        candidates = [
            f"/sdcard/Download/{video_name}",
            f"/sdcard/DCIM/Camera/{video_name}",
        ]
        path = None
        for c in candidates:
            r = self._run(["shell", f"ls {c}"], timeout=20)
            out = (r.stdout or "") + (r.stderr or "")
            if video_name in out and "No such file" not in out:
                path = c
                break
        if not path:
            print(f"    [bring_to_front] 找不到 {video_name}")
            return False

        self._run(["shell", f"touch {path}"], timeout=25)
        time.sleep(0.5)
        self._run(["shell",
                   f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                   f"-d file://{path}"], timeout=25)
        time.sleep(3)
        print(f"    [bring_to_front] {path} 已刷新时间")
        return True

    # ---- 像素级按钮定位（自绘控件没有 bounds）----

    def find_colored_button(self, rgb_ranges: list, y_from: float = 0.80,
                            min_pixels: int = 800) -> Optional[tuple[int, int, int]]:
        """扫描截图找「大块纯色区域」，返回 (cx, cy, 像素数)。

        rgb_ranges: [(rmin, rmax, gmin, gmax, bmin, bmax), ...]
        抖音/小红书的发布按钮都是自绘圆角矩形，uiautomator 返回 bounds=0，
        只能靠颜色找。取最大连通块的中心作为点击点。
        """
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            return None
        try:
            shot = self.shot("_find_btn")
            a = np.array(Image.open(shot).convert("RGB"))
            h, w = a.shape[:2]
            y0 = int(h * y_from)
            sub = a[y0:, :, :]
            r = sub[:, :, 0].astype(int)
            g = sub[:, :, 1].astype(int)
            b = sub[:, :, 2].astype(int)
            mask = np.zeros(r.shape, dtype=bool)
            for (r1, r2, g1, g2, b1, b2) in rgb_ranges:
                mask |= ((r >= r1) & (r <= r2) & (g >= g1) & (g <= g2) &
                         (b >= b1) & (b <= b2))
            ys, xs = np.where(mask)
            if len(xs) < min_pixels:
                return None
            # 用中位数抗噪（避免零星同色像素拉偏）
            cx = int(np.median(xs))
            cy = int(np.median(ys)) + y0
            return cx, cy, len(xs)
        except Exception:
            return None

    def tap_colored_button(self, rgb_ranges: list, y_from: float = 0.80,
                           min_pixels: int = 800, wait: float = 2.5) -> bool:
        r = self.find_colored_button(rgb_ranges, y_from, min_pixels)
        if not r:
            return False
        cx, cy, n = r
        print(f"    找到按钮 ({cx},{cy}) {n} 像素")
        self.tap(cx, cy, wait)
        return True

    # ---- 比例坐标（适配 1080x1920 / 1080x2280）----

    def tp(self, rx: float, ry: float, wait: float = 2.5) -> None:
        """按屏幕比例点击。不同机型高度不同（1920 / 2280），固定像素会错位。"""
        self.tap(int(self.w * rx), int(self.h * ry), wait)

    def tap(self, x: int, y: int, wait: float = 2.5) -> None:
        self._run(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=25)
        time.sleep(wait)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
        self._run(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms)],
                  timeout=25)
        time.sleep(1.0)

    def back(self) -> None:
        self._run(["shell", "input", "keyevent", "4"], timeout=20)
        time.sleep(1.2)

    def home(self) -> None:
        self._run(["shell", "input", "keyevent", "3"], timeout=20)
        time.sleep(1.2)

    def launch(self, pkg: str, wait: float = 7.0) -> bool:
        self._run(["shell", "am", "force-stop", pkg], timeout=25)
        time.sleep(0.6)
        r = self._run(["shell", "monkey", "-p", pkg,
                       "-c", "android.intent.category.LAUNCHER", "1"], timeout=40)
        time.sleep(wait)
        return "No activities found" not in (r.stdout or "")

    def focus(self) -> str:
        r = self._run(["shell", "dumpsys window | grep mCurrentFocus"], timeout=25)
        return (r.stdout or "").strip()

    def dump(self) -> str:
        self._run(["shell", "uiautomator dump /sdcard/ui.xml"], timeout=30)
        r = self._run(["shell", "cat /sdcard/ui.xml"], timeout=30)
        return r.stdout or ""

    def texts(self) -> list[str]:
        xml = self.dump()
        out, seen = [], set()
        for m in re.finditer(r'(?:text|content-desc)="([^"]+)"', xml):
            t = m.group(1).strip()
            if t and t not in seen and len(t) < 120:
                seen.add(t)
                out.append(t)
        return out

    def find(self, keyword: str, prefer_clickable: bool = True) -> Optional[tuple[int, int]]:
        """按文字找元素中心点。

        ★ 关键教训：不能只取第一个匹配。
          小红书的删除确认框里，「确定删除该草稿吗？」和按钮「确定」都含「确定」，
          取第一个就点到了提示文字上，永远删不掉。
          所以：优先返回 clickable=true 的节点，没有才退回第一个。

        也支持 content-desc 匹配（很多图标按钮没有 text 只有 desc）。
        """
        xml = self.dump()
        pattern = re.compile(
            r'<node[^>]*?text="([^"]*)"[^>]*?content-desc="([^"]*)"'
            r'[^>]*?clickable="(\w+)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
        )
        clickable_hits = []
        any_hits = []
        for m in pattern.finditer(xml):
            t, d, clk, x1, y1, x2, y2 = m.groups()
            label = (t or "") + "|" + (d or "")
            if keyword not in label:
                continue
            cx = (int(x1) + int(x2)) // 2
            cy = (int(y1) + int(y2)) // 2
            if int(x2) - int(x1) <= 1 or int(y2) - int(y1) <= 1:
                continue                      # 跳过 0 尺寸
            entry = (cx, cy)
            any_hits.append(entry)
            if clk == "true":
                clickable_hits.append(entry)

        if prefer_clickable and clickable_hits:
            return clickable_hits[0]
        if any_hits:
            return any_hits[0]
        return None

    # ---- 文字输入（XWKeyboard）----

    def send_text(self, text: str) -> bool:
        safe = text.replace("'", "'\\''")
        r = self._run(["shell", f"am broadcast -a XW_INPUT_TEXT --es msg '{safe}'"], timeout=30)
        ok = "Broadcast completed" in ((r.stdout or "") + (r.stderr or ""))
        time.sleep(0.8)
        return ok

    def wait_text(self, keyword: str, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(keyword in t for t in self.texts()):
                return True
            time.sleep(1.0)
        return False

    def wait_gone(self, keyword: str, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not any(keyword in t for t in self.texts()):
                return True
            time.sleep(1.0)
        return False


# ---------------------------------------------------------------------------
# 发布结果
# ---------------------------------------------------------------------------

@dataclass
class PublishResult:
    ok: bool
    platform: str
    serial: str
    video: str = ""
    title: str = ""
    steps: list = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0
    shots: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "platform": self.platform, "serial": self.serial,
            "video": self.video, "title": self.title,
            "steps": self.steps, "error": self.error,
            "elapsed": round(self.elapsed, 1), "shots": self.shots,
        }


class BasePublisher:
    platform = "base"
    pkg = ""
    # 视频格子在相册里的相对位置（第几行第几列，0 基）
    GRID_ORIGIN = (168, 383)
    GRID_STEP = (371, 367)          # 列间距、行间距（1080x2280 实测）

    def __init__(self, phone: Phone):
        self.p = phone
        self.steps: list[str] = []
        self.shots: list[str] = []

    def _step(self, name: str, ok: bool = True) -> None:
        self.steps.append(("OK " if ok else "ERR") + " " + name)
        self.shots.append(self.p.shot(re.sub(r"[^\w]+", "_", name)[:24]))

    def grid_xy(self, row: int, col: int) -> tuple[int, int]:
        ox, oy = self.GRID_ORIGIN
        sx, sy = self.GRID_STEP
        return ox + col * sx, oy + row * sy

    def publish(self, video_name: str, title: str, desc: str,
                row: int = 0, col: int = 1) -> PublishResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 小红书
# ---------------------------------------------------------------------------

class XhsPublisher(BasePublisher):
    platform = "小红书"
    pkg = "com.xingin.xhs"

    # 小红书红 #FF2442（下一步 / 发布笔记 都是这个红）
    RED_RANGES = [(200, 255, 20, 90, 40, 110)]

    def _clear_dialogs(self) -> None:
        """清掉小红书的草稿恢复弹窗。

        ★ 这个弹窗是最大的坑：上一次中断（或者上一次发布失败）后，
          下次启动会弹「继续编辑视频笔记吗？」，挡住整个界面。
          点「存草稿」能正常退出。
        """
        for _ in range(4):
            ts = " ".join(self.p.texts())
            if "继续编辑" not in ts:
                break
            for kw in ("存草稿", "去编辑"):
                pos = self.p.find(kw)
                if pos:
                    print(f"    [清理] 草稿弹窗 → 点「{kw}」")
                    self.p.tap(*pos, 3)
                    break
            else:
                break

        # 其他提示
        for kw in ("以后再说", "我知道了", "暂不", "跳过", "取消"):
            if kw == "取消":
                continue
            pos = self.p.find(kw)
            if pos:
                self.p.tap(*pos, 1.5)

    def publish(self, video_name: str, title: str, desc: str,
                row: int = 0, col: int = 1) -> PublishResult:
        t0 = time.time()
        r = PublishResult(ok=False, platform=self.platform, serial=self.p.serial,
                          video=video_name, title=title)
        try:
            # ---- 1. 冷启动 ----
            self.p.home()
            time.sleep(1)
            self.p._run(["shell", "am force-stop", self.pkg], timeout=25)
            time.sleep(1.5)
            if not self.p.launch(self.pkg, wait=9):
                r.error = "小红书未安装"
                return r
            self._clear_dialogs()
            self._step("打开小红书")

            # ---- 2. 底部 + ----
            self.p.tap(546, 2081, 4)
            self._clear_dialogs()
            self._step("点发布入口")

            # ---- 3. 从相册选择 ----
            pos = self.p.find("从相册选择")
            if not pos:
                r.error = "未找到「从相册选择」"
                r.steps = self.steps
                return r
            self.p.tap(*pos, 4)
            self._step("打开相册")

            # ---- 4. 选视频（相册格子）----
            gx, gy = self.grid_xy(row, col)
            self.p.tap(gx, gy, 3)
            self._step(f"选视频 r{row}c{col}")

            # ---- 5. 下一步（红按钮，像素定位）----
            time.sleep(2)
            nxt = False
            for y_from in (0.88, 0.84):
                if self.p.tap_colored_button(self.RED_RANGES, y_from=y_from,
                                             min_pixels=3000, wait=6):
                    nxt = True
                    break
            if not nxt:
                pos = self.p.find("下一步")
                if pos:
                    self.p.tap(*pos, 6)
                    nxt = True
                else:
                    self.p.tap(int(self.p.w * 0.864), int(self.p.h * 0.912), 6)
            self._step("进入编辑页", nxt)

            # ---- 6. 确认在编辑页 ----
            ts = " ".join(self.p.texts())
            if "下一步" not in ts and "添加文字" not in ts and "特效" not in ts:
                # 可能直接跳到了填写页
                pass

            # ---- 7. 编辑页再点下一步（进填写页）----
            time.sleep(2)
            went = False
            for y_from in (0.88, 0.84):
                if self.p.tap_colored_button(self.RED_RANGES, y_from=y_from,
                                             min_pixels=3000, wait=6):
                    went = True
                    break
            if not went:
                pos = self.p.find("下一步")
                if pos:
                    self.p.tap(*pos, 6)
                    went = True
            self._step("进入填写页", went)

            # ---- 8. 确认在填写页 ----
            ts = " ".join(self.p.texts())
            if "添加标题" not in ts and "发布笔记" not in ts:
                r.error = f"未到填写页（当前：{ts[:60]}）"
                r.steps = self.steps
                return r

            # ---- 9. 填标题 ----
            pos = self.p.find("添加标题")
            if pos:
                self.p.tap(*pos, 2.5)
                time.sleep(1)
                self.p.send_text(title)
                time.sleep(1.5)
                self._step("填标题")

            # ---- 10. 填描述 ----
            pos = self.p.find("添加作品描述")
            if pos:
                self.p.tap(*pos, 2.5)
                time.sleep(1)
                self.p.send_text(desc)
                time.sleep(2)
                self._step("填描述")

            # ---- 11. 收键盘 ----
            self.p.hide_keyboard()
            time.sleep(1)
            self._step("收键盘")

            # ---- 11.5 声明「笔记含 AI 合成内容」----
            # ★★★ 平台硬性要求 ★★★
            #   不声明会被判违规（提示"使用AI但未标注"），可能被限流甚至删稿。
            #   路径：高级选项 → 内容类型声明 → 自主声明 → 勾选「笔记含AI合成内容」
            #          → 返回两次回填写页
            ai_declared = False
            pos = self.p.find("高级选项")
            if pos:
                self.p.tap(*pos, 3)
                pos = self.p.find("内容类型声明")
                if pos:
                    self.p.tap(*pos, 3)
                    pos = self.p.find("笔记含AI合成内容")
                    if pos:
                        self.p.tap(*pos, 2)
                        time.sleep(1)
                        # 标记位会从 icon_unselect_grey_20 变为 Oval;ridio;17px
                        chk = " ".join(self.p.texts())
                        ai_declared = "Oval;ridio" in chk or "Oval;radio" in chk
                    # 返回：声明页 → 高级选项
                    self.p.back()
                    time.sleep(1.5)
                # 返回：高级选项 → 填写页
                self.p.back()
                time.sleep(2)
            self._step("声明AI合成", ai_declared)
            print(f"    [AI声明] {'已勾选' if ai_declared else '未勾选（可能被判违规）'}")

            # ---- 12. 点发布笔记（红按钮）----
            posted = False
            for y_from in (0.86, 0.82):
                if self.p.tap_colored_button(self.RED_RANGES, y_from=y_from,
                                             min_pixels=3000, wait=12):
                    posted = True
                    break
            if not posted:
                pos = self.p.find("发布笔记")
                if pos:
                    self.p.tap(*pos, 12)
                    posted = True
            self._step("点发布", posted)
            if not posted:
                r.error = "未找到「发布笔记」按钮"
                r.steps = self.steps
                return r

            # ---- 13. 严格判定 ----
            # 小红书成功的标志：
            #   a) 出现「发布成功」提示
            #   b) 回到首页 / 个人页（IndexActivity）
            #   c) 出现「笔记已发布」
            # 失败标志：
            #   a) 还在填写页（有「发布笔记」文字）
            #   b) 弹出「保存草稿」对话框
            ok = False
            for i in range(15):
                ts = " ".join(self.p.texts())
                focus = self.p.focus()

                if "发布成功" in ts or "笔记已发布" in ts:
                    ok = True
                    break
                if "保存草稿" in ts or "存草稿" in ts:
                    # 弹了草稿框，说明发布没走通
                    pos = self.p.find("取消")
                    if pos:
                        self.p.tap(*pos, 1.5)
                    r.error = "发布时弹出草稿对话框（可能被风控）"
                    break
                if "发布笔记" not in ts and "添加标题" not in ts:
                    # 离开填写页
                    if "IndexActivity" in focus or "MainActivity" in focus:
                        ok = True
                        break
                    ok = True
                    break
                time.sleep(1.5)

            r.ok = ok
            r.steps = self.steps
            r.shots = self.shots
            if not ok and not r.error:
                r.error = "发布后未确认成功"
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
            r.steps = self.steps
            r.shots = self.shots
        r.elapsed = time.time() - t0
        return r


# ---------------------------------------------------------------------------
# 抖音（自动发布入口因版本差异较大，走"+"→ 相册 → 编辑 → 发布）
# ---------------------------------------------------------------------------

class DouyinPublisher(BasePublisher):
    platform = "抖音"
    pkg = "com.ss.android.ugc.aweme"

    # 抖音红（发作品按钮）
    RED_RANGES = [
        (200, 255, 20, 110, 40, 150),
    ]

    def _clear_dialogs(self) -> None:
        """清掉抖音的恢复/提示对话框。

        最讨厌的是「继续编辑作品吗？」——上次中断留下的草稿弹窗。
        ★ 注意：这个弹窗只有「存草稿」和「去编辑」两个按钮，
          那个「取消」是右上角的 X，不是对话框按钮。
          实测点「存草稿」能正常退出并进入拍摄页。
        """
        for _ in range(4):
            ts = " ".join(self.p.texts())
            if "继续编辑" not in ts and "去编辑" not in ts:
                break
            # 优先「存草稿」（不丢内容，能正常退出）
            for kw in ("存草稿", "去编辑"):
                pos = self.p.find(kw)
                if pos:
                    print(f"    [清理] 草稿弹窗 → 点「{kw}」{pos}")
                    self.p.tap(*pos, 3)
                    time.sleep(1.5)
                    break
            else:
                break

        # 其他常见弹窗
        for kw in ("以后再说", "不再提醒", "我知道了", "暂不更新", "跳过", "稍后"):
            pos = self.p.find(kw)
            if pos:
                print(f"    [清理] 点「{kw}」")
                self.p.tap(*pos, 1.5)

    def _clear_permissions(self, rounds: int = 6) -> None:
        """清掉相机/相册权限弹窗。"""
        for _ in range(rounds):
            ts = " ".join(self.p.texts())
            if "允許" not in ts and "允许" not in ts:
                return
            for kw in ("使用應用程式時", "僅允許這一次", "允許", "允许",
                       "始终允许", "全部允许"):
                pos = self.p.find(kw)
                if pos:
                    self.p.tap(*pos, 2.0)
                    break
            else:
                return

    def publish(self, video_name: str, title: str, desc: str,
                row: int = 0, col: int = 1) -> PublishResult:
        t0 = time.time()
        r = PublishResult(ok=False, platform=self.platform, serial=self.p.serial,
                          video=video_name, title=title)
        try:
            # ★ 每次发布前强制回到桌面再冷启动，避免上一次残留状态
            self.p.home()
            time.sleep(1)
            self.p._run(["shell", "am force-stop", self.pkg], timeout=25)
            time.sleep(1)
            if not self.p.launch(self.pkg, wait=10):
                r.error = "抖音未安装"
                return r
            self._clear_dialogs()          # ★ 先清草稿恢复弹窗
            self._step("打开抖音")

            # 底部 +（1080x2280 实测 546,2110）
            self.p.tap(546, 2110, 4)
            self._clear_permissions()
            self._clear_dialogs()
            self._step("点发布入口")

            # 拍摄页 → 相册
            pos = self.p.find("相册")
            if not pos:
                # 没到拍摄页，重试一次
                self.p.tap(546, 2110, 4)
                self._clear_permissions()
                pos = self.p.find("相册")
            if not pos:
                r.error = "未进入拍摄页"
                r.steps = self.steps
                return r
            self.p.tap(*pos, 5)
            self._clear_permissions(2)
            self._step("打开相册")

            # 相册格子：357x357，首格 (178, 711)，列距 361，行距 360
            gx = 178 + col * 361
            gy = 711 + row * 360
            self.p.tap(gx, gy, 3)
            self._step(f"选视频 r{row}c{col}")

            # 预览页「下一步」→ (786, 2077)
            self.p.tap(786, 2077, 7)
            self._step("进入编辑页")

            # 编辑页「下一步」→ 同一位置
            self.p.tap(786, 2077, 7)
            self._step("进入发布页")

            # 确认在发布页
            ts = " ".join(self.p.texts())
            if "添加作品描述" not in ts and "添加标题" not in ts:
                r.error = f"未到发布页（当前：{ts[:60]}）"
                r.steps = self.steps
                return r

            # 标题
            pos = self.p.find("添加标题")
            if pos:
                self.p.tap(*pos, 2)
                self.p.send_text(title)
                self._step("填标题")

            time.sleep(1)

            # 描述（★ 绝不能带 #，会跳话题搜索打断流程）
            pos = self.p.find("添加作品描述")
            if pos:
                self.p.tap(*pos, 3)
                # 等键盘弹出再发字，否则广播文字会丢
                for _ in range(6):
                    if self.p.keyboard_shown():
                        break
                    time.sleep(0.5)
                ok_desc = self.p.send_text(desc)
                time.sleep(2)
                # 验证描述是否真的进去
                ts_chk = " ".join(self.p.texts())
                if desc[:6] not in ts_chk:
                    print("    [警告] 描述可能未填入，重试一次")
                    self.p.send_text(desc)
                    time.sleep(2)
                self._step("填描述", ok_desc)

            # ★ 可靠收键盘：点空白 + BACK，再确认
            self.p.hide_keyboard()
            if self.p.keyboard_shown():
                print("    [警告] 键盘仍在，再收一次")
                self.p.hide_keyboard()
            self._step("收键盘")

            # 像素定位「发作品」并点击
            # 先试窄范围（更准），找不到再放宽
            clicked = self.p.tap_colored_button(
                self.RED_RANGES, y_from=0.86, min_pixels=3000, wait=9
            )
            if not clicked:
                self._step("窄范围未找到，放宽重试", False)
                clicked = self.p.tap_colored_button(
                    self.RED_RANGES, y_from=0.70, min_pixels=500, wait=9
                )
            self._step("点发作品", clicked)
            if not clicked:
                r.error = "未找到发作品按钮"
                r.steps = self.steps
                return r

            # 判定：只要离开「发布页」就算发出去（抖音发完会跳播放页或首页，
            # 有时 Activity 会重启成 SplashActivity，所以不能只看文字）
            ok = False
            for i in range(15):
                ts = " ".join(self.p.texts())
                focus = self.p.focus()

                # 强信号：出现播放页时间戳
                if any(k in ts for k in ("秒前", "刚刚", "上传成功", "已发布")):
                    ok = True
                    break
                # 中信号：发布页特征消失
                if ("添加作品描述" not in ts) and ("发作品" not in ts):
                    ok = True
                    break
                # 弱信号：前台不是拍摄/发布 Activity
                if "shortvideo" not in focus and "SplashActivity" in focus:
                    ok = True
                    break
                time.sleep(1.5)

            r.ok = ok
            r.steps = self.steps
            r.shots = self.shots
            if not ok:
                r.error = "发布后未确认成功"
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
            r.steps = self.steps
            r.shots = self.shots
        r.elapsed = time.time() - t0
        return r


# ---------------------------------------------------------------------------
# 哔哩哔哩
# ---------------------------------------------------------------------------

class BiliPublisher(BasePublisher):
    platform = "哔哩哔哩"
    pkg = "tv.danmaku.bili"

    # B站粉 #FB7299
    PINK_RANGES = [(230, 255, 80, 160, 120, 200)]

    # ★★★ B站「最近项目」的格子 → 文件名 映射（实测）
    #
    # B站的「最近项目」其实就是相册，但它【不按 date_modified 排序】，
    # 而是按内部的项目顺序，所以 bring_to_front() 对它无效。
    # 实测 7 个视频的固定位置（358x358 格子，首格中心 (180,501)）：
    #   第1格(180,501)=7.mp4  第2格(540,501)=4.mp4  第3格(900,501)=5.mp4
    #   第4格(180,862)=6.mp4  第5格(540,862)=3.mp4  第6格(900,862)=2.mp4
    #   第7格(180,1223)=1.mp4
    # 注意：如果手机里新增/删除了视频，这个顺序会变，需要重新 dump 校准。
    GRID_MAP = {
        "7.mp4": (0, 0),
        "4.mp4": (0, 1),
        "5.mp4": (0, 2),
        "6.mp4": (1, 0),
        "3.mp4": (1, 1),
        "2.mp4": (1, 2),
        "1.mp4": (2, 0),
    }
    GRID_ORIGIN = (180, 501)      # 第1格中心
    GRID_COLS = 360               # 列间距
    GRID_ROWS = 361               # 行间距

    def _clear_dialogs(self) -> None:
        """清掉 B站 的各种提示弹窗。"""
        # 智能成片的引导
        for _ in range(3):
            pos = self.p.find("我知道了")
            if pos:
                self.p.tap(*pos, 2)
                continue
            break
        # 编辑器小贴士（tooltip，会挡住「下一步」）
        for kw in ("编辑器使用小贴士",):
            if any(kw in t for t in self.p.texts()):
                # tooltip 的 X 在左上
                self.p.tap(int(self.p.w * 0.07), int(self.p.h * 0.115), 1.5)
                break

    def publish(self, video_name: str, title: str, desc: str,
                row: int = 0, col: int = 1) -> PublishResult:
        t0 = time.time()
        r = PublishResult(ok=False, platform=self.platform, serial=self.p.serial,
                          video=video_name, title=title)
        try:
            # ---- 1. 冷启动 ----
            self.p.home()
            time.sleep(1)
            self.p._run(["shell", "am force-stop", self.pkg], timeout=25)
            time.sleep(1.5)
            if not self.p.launch(self.pkg, wait=10):
                r.error = "B站未安装"
                return r
            self._step("打开B站")
            self._clear_dialogs()

            # ---- 2. 底部 + ----
            self.p.tap(546, 2110, 5)
            self._clear_dialogs()
            self._step("点发布入口")

            # ---- 3. 创作中心 → 点底部「上传」tab 打开相册 ----
            #
            # ★★★ 血泪教训 ★★★
            #   「最近项目」列表里那些视频是【草稿箱】里的历史项目，不是相册！
            #   点它们的「+」只是把那个草稿重新拉进编辑流，
            #   结果就是：不管发几次，发出去的全是同一个视频。
            #   正确做法：点底部「上传」tab，打开系统相册，选真正要发的那个。
            ts = " ".join(self.p.texts())
            if "非正式会员单日只能投递五个稿件" in ts:
                r.error = "B站日限额已满（非正式会员每日 5 条），请明天再发或先答题转正"
                r.steps = self.steps
                return r

            # 底部四个 tab：拍摄 / 上传 / 开直播 / 发图文
            # 1080x2280 实测 y=2183，上传在 x=540
            self.p.tap(540, 2183, 5)
            self._step("点上传tab")

            ts = " ".join(self.p.texts())
            # 打开相册后应该能看到「所有照片」「视频」「图片」这类文字
            in_album = any(k in ts for k in ("所有照片", "所有视频", "最近项目", "视频", "图片"))
            if not in_album:
                r.error = f"未打开相册（当前：{ts[:60]}）"
                r.steps = self.steps
                return r

            # ★ 按文件名查映射表选格子（不能用 row/col，因为 B站排序特殊）
            pos = self.GRID_MAP.get(video_name)
            if not pos:
                r.error = f"B站格子映射里没有 {video_name}，需要重新校准"
                r.steps = self.steps
                return r
            grow, gcol = pos
            gx = self.GRID_ORIGIN[0] + gcol * self.GRID_COLS
            gy = self.GRID_ORIGIN[1] + grow * self.GRID_ROWS
            print(f"    [B站选格] {video_name} → r{grow}c{gcol} ({gx},{gy})")
            self.p.tap(gx, gy, 4)
            self._step(f"选{video_name}")

            # ---- 4. 去编辑 ----
            # 底部两个按钮：智能成片(左) 去编辑(右粉红)
            # 去编辑在 (786, 2205) 附近，用像素定位更稳
            edited = False
            for y_from in (0.94, 0.90):
                if self.p.tap_colored_button(self.PINK_RANGES, y_from=y_from,
                                             min_pixels=2000, wait=8):
                    edited = True
                    break
            self._step("进入编辑器", edited)
            if not edited:
                r.error = "未找到去编辑按钮"
                r.steps = self.steps
                return r

            time.sleep(2)
            self._clear_dialogs()

            # ---- 5. 编辑器 → 右上角「下一步」----
            pos = self.p.find("下一步")
            if pos:
                self.p.tap(*pos, 10)
            else:
                self.p.tap(int(self.p.w * 0.92), int(self.p.h * 0.084), 10)
            self._step("进入投稿页")

            # ---- 6. 确认在投稿页 ----
            ts = " ".join(self.p.texts())
            if "发布视频" not in ts and "分区和标签" not in ts:
                r.error = f"未到投稿页（当前：{ts[:60]}）"
                r.steps = self.steps
                return r

            # ---- 7. 填标题 ----
            pos = self.p.find("合适的标题")
            if pos:
                self.p.tap(*pos, 2.5)
                time.sleep(1)
                self.p.send_text(title)
                time.sleep(2)
                self._step("填标题")

            # ---- 8. 创作声明（必填）----
            ts = " ".join(self.p.texts())
            if "(必填)" in ts and "内容无需标注" not in ts:
                pos = self.p.find("创作声明")
                if pos:
                    self.p.tap(*pos, 4)
                    # 选「内容无需标注」
                    pos2 = self.p.find("内容无需标注")
                    if pos2:
                        self.p.tap(*pos2, 2)
                    time.sleep(1)
                    pos3 = self.p.find("确定")
                    if pos3:
                        self.p.tap(*pos3, 4)
                    self._step("选创作声明")

            # ---- 9. 收键盘 ----
            self.p.hide_keyboard()
            time.sleep(1)
            self._step("收键盘")

            # ---- 10. 点发布（底部粉红大按钮）----
            # 发布按钮 y 在 0.89-0.94 之间，比去编辑更靠上
            clicked = False
            for y_from in (0.87, 0.85):
                if self.p.tap_colored_button(self.PINK_RANGES, y_from=y_from,
                                             min_pixels=5000, wait=10):
                    clicked = True
                    break
            self._step("点发布", clicked)
            if not clicked:
                r.error = "未找到发布按钮"
                r.steps = self.steps
                return r

            # ---- 11. 判定 ----
            ok = False
            for _ in range(15):
                ts = " ".join(self.p.texts())
                focus = self.p.focus()
                if any(k in ts for k in ("投稿成功", "已投稿", "审核中", "发布成功", "秒前")):
                    ok = True
                    break
                if "发布视频" not in ts and "分区和标签" not in ts:
                    ok = True
                    break
                if "MainActivityV2" in focus:
                    ok = True
                    break
                time.sleep(1.5)

            r.ok = ok
            r.steps = self.steps
            r.shots = self.shots
            if not ok:
                r.error = "发布后未确认成功"
        except Exception as e:  # noqa: BLE001
            r.error = str(e)
            r.steps = self.steps
            r.shots = self.shots
        r.elapsed = time.time() - t0
        return r


PUBLISHERS = {
    "xhs": XhsPublisher,
    "dy": DouyinPublisher,
    "bili": BiliPublisher,
}


def publish_one(serial: str, platform: str, video_name: str,
                title: str, desc: str, row: int = 0, col: int = 1) -> dict:
    """发布单条。platform: xhs | dy | bili"""
    cls = PUBLISHERS.get(platform)
    if not cls:
        return {"ok": False, "error": f"未知平台 {platform}"}
    phone = Phone(serial)
    pub = cls(phone)
    return pub.publish(video_name, title, desc, row, col).to_dict()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="FishFleet 自动发布器")
    ap.add_argument("--serial", required=True)
    ap.add_argument("--platform", required=True, choices=list(PUBLISHERS))
    ap.add_argument("--title", default="")
    ap.add_argument("--desc", default="")
    ap.add_argument("--row", type=int, default=0)
    ap.add_argument("--col", type=int, default=1)
    ap.add_argument("--video", default="")
    a = ap.parse_args()
    res = publish_one(a.serial, a.platform, a.video, a.title, a.desc, a.row, a.col)
    print(json.dumps(res, ensure_ascii=False, indent=2))
