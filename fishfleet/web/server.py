"""
FishFleet — Web 服务

模仿汇城群控的界面：
  · 左侧：20 台手机缩略图网格（实时截图）
  · 右侧：设备详情 / 任务 / 日志
  · 顶部：全局操作

启动：
    python -m fishfleet.web.server
    然后浏览器打开 http://127.0.0.1:8765
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional, Literal

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator

from ..core.device import DeviceState, get_manager
from .api import router as general_router
from .menu_recording import capture as record_input
from .batch import router as batch_router
from .api_proxy import detect_proxy, open_api, public_proxy, validate_kind, validate_proxy


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="FishFleet", version="0.1.0")


# ---------------------------------------------------------------------------
# 2026-11-06 主人要求：加日志，出 bug 时要能查清「对方操作了什么」。
#
# 这里做两件事：
#   1. 请求日志 —— 每个 API 调用记一条（方法/路径/状态/耗时），
#      `/static/` 噪音太大直接跳过。用户点一次按钮往往会触发几条，
#      连起来就能还原操作序列。
#   2. 未捕获异常 —— 记完整 traceback 到 error.log。
# ---------------------------------------------------------------------------

try:
    from fishfleet.logsetup import get_logger as _get_log
    _req_log = _get_log('http')
except Exception:                                       # pragma: no cover
    import logging as _logging
    _req_log = _logging.getLogger('http')


@app.middleware('http')
async def _log_requests(request, call_next):
    """记录 API 调用。绝不影响主流程（全部 try/except）。"""
    import time as _t
    t0 = _t.perf_counter()
    path = request.url.path
    quiet = path.startswith('/static') or path in ('/favicon.ico',)
    try:
        response = await call_next(request)
    except Exception:                                   # noqa: BLE001
        if not quiet:
            try:
                _req_log.exception('请求异常 %s %s', request.method, path)
            except Exception:
                pass
        raise
    if not quiet:
        try:
            cost = (_t.perf_counter() - t0) * 1000
            lvl = 30 if response.status_code >= 400 else 20      # WARNING/INFO
            _req_log.log(lvl, '%s %s -> %s  %.0fms',
                         request.method, path, response.status_code, cost)
        except Exception:
            pass
    return response


@app.post('/api/log/event')
async def log_client_event(payload: dict):
    """前端主动上报操作事件。

    前端在关键动作（点发布、开始采集、选批次…）调用这里，
    这样日志里就能看到「对方到底点了什么」，而不是只有接口名。
    """
    try:
        from fishfleet.logsetup import log_event
        name = str(payload.get('event') or 'client.event')[:120]
        data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
        log_event(name, data)
        return {'ok': True}
    except Exception:                                   # noqa: BLE001
        return {'ok': False}


@app.get('/api/log/diagnose')
def log_diagnose():
    """返回最近的错误日志 + 环境快照，方便技术支持远程判断。"""
    try:
        from fishfleet.logsetup import log_dir, snapshot, tail
        return {
            'ok': True,
            'log_dir': str(log_dir()),
            'snapshot': snapshot(),
            'error_tail': tail('error.log', 200),
            'app_tail': tail('app.log', 300),
        }
    except Exception as exc:                            # noqa: BLE001
        return {'ok': False, 'error': str(exc)}


@app.post('/api/log/export')
def log_export():
    """把诊断日志打包成 zip（默认放到桌面），让用户直接发回来。"""
    try:
        from fishfleet.logsetup import export_bundle
        p = export_bundle()
        return {'ok': bool(p), 'path': str(p) if p else ''}
    except Exception as exc:                            # noqa: BLE001
        return {'ok': False, 'error': str(exc)}


@app.on_event("startup")
def _prewarm_mcp_channels():
    """2026-11-06 主人要求：「dump 慢可以，但至少启动那一下要快」。

    以前每个 MCP 通道是**第一个任务来了才创建**（启动 Node 进程 + 连 adb 要几秒），
    表现就是「点了开始，手机半天没反应」。这里在服务启动时预建几个通道放回池里，
    任务一到直接取用。

    约束：后台线程执行、失败静默，绝不阻塞服务启动；
    数量可用环境变量 FISHFLEET_PREWARM 覆盖（默认 8）。
    """
    import threading

    def _run():
        try:
            from . import mobile_gateway as gw
            made = gw.prewarm()
            print('[prewarm] MCP 通道预建完成：%d 个' % made)
        except Exception as exc:                        # noqa: BLE001
            print('[prewarm] 预建失败（不影响启动）：%s' % type(exc).__name__)

    threading.Thread(target=_run, name='mcp-prewarm', daemon=True).start()


@app.on_event("startup")
def _disable_screen_autorotate():
    """2026-11-06 主人要求：**每次重启默认关闭所有手机的屏幕自动旋转**。

    为什么必须做：自动旋转会让同一控件的坐标在横竖屏之间漂移
    （实测校对里那些 dy=0.1~0.4 的大偏移，有一部分就是屏幕转过去造成的），
    链路跑一半屏幕一转，dump 出来的坐标就全对不上。

    约束：
      · 只改 `accelerometer_rotation`（关自动旋转）+ `user_rotation=0`（固定竖屏），
        不碰任何网络/账号/应用设置；
      · **41号机 SERIAL0001 绝不触碰**；
      · 后台线程执行、失败静默，绝不阻塞服务启动。
    """
    import threading
    import time as _time

    NEVER = 'SERIAL0001'

    def _run():
        _time.sleep(8)                      # 等 adb server / 设备枚举就绪
        try:
            adb = _adb_path()
        except Exception:
            return
        try:
            out = subprocess.run([adb, 'devices'], capture_output=True, text=True,
                                 timeout=25).stdout
        except Exception:
            return
        for line in (out or '').splitlines():
            parts = line.split()
            if len(parts) != 2 or parts[1] != 'device':
                continue
            serial = parts[0]
            if serial == NEVER:
                continue
            for cmd in (['settings', 'put', 'system', 'accelerometer_rotation', '0'],
                        ['settings', 'put', 'system', 'user_rotation', '0']):
                try:
                    subprocess.run([adb, '-s', serial, 'shell'] + cmd,
                                   capture_output=True, timeout=10)
                except Exception:
                    pass

    try:
        threading.Thread(target=_run, name='disable-autorotate', daemon=True).start()
    except Exception:
        pass


# 批量操作 API
app.include_router(batch_router)

from .lead_pool_api import router as lead_pool_router
app.include_router(lead_pool_router)

from .live_control import make_router as _make_live_control_router
from .menu_recording import ACTIVE as _recording_active
# Getter is evaluated per packet; never starts/reconnects a stream.
app.include_router(_make_live_control_router(
    lambda serial: _live_session(serial),
    lambda serial: serial in _recording_active,
))

# ★ 设备扩展能力 API（对齐汇城 34 个命令）
from .ext_api import router as ext_router  # noqa: E402
app.include_router(ext_router)

# 扩展 API（标签/任务/媒体/文件/短语）
app.include_router(general_router)
from .execution import router as execution_router
app.include_router(execution_router)
from .workspace_assets import router as workspace_assets_router
app.include_router(workspace_assets_router)
from .menu_tools import router as menu_tools_router
app.include_router(menu_tools_router)
from .otg import router as otg_router
app.include_router(otg_router)

# 视频创作流水线（提示词生成 + 视频 MCP）：同进程挂载，避免跨站拦截和额外端口。
try:
    import sys as _sys
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parents[2]
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    from creative_studio.backend import app as studio_app, resume_known_tasks  # noqa: E402
    app.mount("/studio", studio_app)
    app.router.add_event_handler('startup', resume_known_tasks)
except Exception as _exc:  # 未安装或依赖缺失时不影响主服务
    import logging as _logging
    _logging.getLogger(__name__).warning("视频创作模块未挂载：%s", _exc)

if STATIC_DIR.exists():
    class _NoCacheStatic(StaticFiles):
        """静态文件禁止缓存（开发期保证改了就生效）。"""

        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
            return resp

    app.mount("/static", _NoCacheStatic(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

class FleetState:
    def __init__(self):
        self.screenshots: dict[str, bytes] = {}      # serial -> 压缩后 JPEG bytes
        self.raw_png: dict[str, bytes] = {}          # serial -> 原始 PNG（大图预览用）
        self._medium_cache: dict = {}                 # 中尺寸缓存
        self.shot_ts: dict[str, float] = {}          # serial -> 时间戳
        self.logs: deque = deque(maxlen=500)         # 日志
        self.tasks: dict[str, dict] = {}             # 运行中的任务
        self._shot_thread: Optional[threading.Thread] = None
        self._running = False
        self._subscribers: list[WebSocket] = []
        self.active_serial: str = ""      # 用户正在操控的设备（优先刷新）

    def log(self, msg: str, level: str = "info") -> None:
        entry = {"ts": time.time(), "level": level, "msg": msg}
        self.logs.append(entry)
        print(f"[{level}] {msg}")

    @staticmethod
    def compress_png(png: bytes, width: int = 320, quality: int = 55) -> bytes:
        """把 PNG 压成小 JPEG（缩略图用）。"""
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(png))
            if img.mode != "RGB":
                img = img.convert("RGB")
            ratio = width / img.width
            new_size = (width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return png

    @staticmethod
    def make_medium(png: bytes, width: int = 720, quality: int = 82) -> bytes:
        """中等尺寸（大屏用，比原图小 10 倍但够清晰）。"""
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(png))
            if img.mode != "RGB":
                img = img.convert("RGB")
            if img.width > width:
                ratio = width / img.width
                img = img.resize((width, int(img.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue()
        except Exception:  # noqa: BLE001
            return png

    def start_screenshot_loop(self, interval: float = 15.0) -> None:
        if self._running:
            return
        self._running = True
        self._shot_thread = threading.Thread(
            target=self._shot_loop, args=(interval,), daemon=True
        )
        self._shot_thread.start()

        # 健康检查（低频）
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()

    def _health_loop(self, interval: float = 60.0) -> None:
        """后台检查设备健康（电量/温度/存储）。"""
        import re
        import subprocess
        from ..core.config import get_config

        adb = get_config().devices.adb_path
        mgr = get_manager()

        while self._running:
            try:
                for dev in mgr.online():
                    if not self._running:
                        break
                    try:
                        p = subprocess.run(
                            [adb, "-s", dev.serial, "shell", "dumpsys battery"],
                            capture_output=True, text=True, timeout=10,
                        )
                        if p.returncode == 0:
                            lv = re.search(r"level:\s*(\d+)", p.stdout)
                            tp = re.search(r"temperature:\s*(\d+)", p.stdout)
                            if lv:
                                dev.health.battery = int(lv.group(1))
                            if tp:
                                dev.health.battery_temp = int(tp.group(1)) / 10.0
                            dev.health.checked_at = time.time()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

            time.sleep(interval)

    def stop(self) -> None:
        self._running = False

    def _shot_loop(self, interval: float) -> None:
        """后台持续截图（并发，避免单台慢拖累全局）。"""
        import subprocess
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..core.config import get_config

        cfg = get_config()
        adb = cfg.devices.adb_path
        mgr = get_manager()

        def grab(serial: str) -> None:
            try:
                p = subprocess.run(
                    [adb, "-s", serial, "exec-out", "screencap", "-p"],
                    capture_output=True, timeout=20,
                )
                if p.returncode == 0 and p.stdout:
                    png = p.stdout
                    self.raw_png[serial] = png
                    self.screenshots[serial] = self.compress_png(png)
                    self.shot_ts[serial] = time.time()
            except Exception:  # noqa: BLE001
                pass

        while self._running:
            try:
                online = [d.serial for d in mgr.online()]
                # 正在大屏操控的设备，跳过后台轮询（避免双重截图）
                if self.active_serial:
                    online = [s for s in online if s != self.active_serial]
                if online:
                    # 最多 6 台并发，避免 USB 总线过载
                    with ThreadPoolExecutor(max_workers=20) as ex:
                        futures = [ex.submit(grab, s) for s in online]
                        for f in as_completed(futures):
                            if not self._running:
                                break
                            try:
                                f.result(timeout=25)
                            except Exception:  # noqa: BLE001
                                pass
            except Exception as e:  # noqa: BLE001
                self.log(f"截图循环异常: {e}", "error")

            time.sleep(interval)


STATE = FleetState()


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """返回首页。

    ★ 关键：给 ui.js / app.js / style.css 加 ?v=<mtime> 版本号。
      WebView2 对这些静态资源有强缓存，不换 URL 就会一直用旧文件，
      导致改了代码但界面"没有任何变化"。
    """
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        return "<h1>前端未就绪</h1>"

    text = html_path.read_text(encoding="utf-8")

    # 用各文件的修改时间做版本号
    for name in ("ui.js", "app.js", "h264.js", "style.css"):
        f = STATIC_DIR / name
        if not f.exists():
            continue
        ver = str(int(f.stat().st_mtime))
        # 匹配 /static/xxx.js 或 /static/xxx.css（不带 ?v= 的）
        text = text.replace(f'/static/{name}"', f'/static/{name}?v={ver}"')
        text = text.replace(f'/static/{name}\'', f'/static/{name}?v={ver}\'')

    return HTMLResponse(
        content=text,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ---------------------------------------------------------------------------
# API：设备
# ---------------------------------------------------------------------------

@app.get("/api/devices")
async def api_devices() -> JSONResponse:
    mgr = get_manager()
    # ADB 查询可能逐台等待数秒；移出事件循环，避免阻塞投屏推帧。
    devs = await asyncio.to_thread(mgr.refresh)

    labels, numbers = _ensure_device_numbers(devs)
    items = []
    for d in devs:
        identity = d.identity or d.serial
        label = labels.get(identity, {})
        d.index = numbers[identity]
        items.append({
            "serial": d.serial,
            "identity": identity,
            "connection": d.connection,
            "accessible": d.accessible,
            "mode": d.mode,
            "is_accessible": d.is_accessible,
            "accessible_port": d.accessible_port,
            "name": label.get("name", ""),
            "index": d.index,
            "model": d.model or "Samsung",
            "state": d.state.value,
            "battery": d.health.battery,
            "screen_on": d.health.screen_on,
            "has_shot": d.serial in STATE.screenshots,
            "shot_age": round(time.time() - STATE.shot_ts.get(d.serial, 0), 1),
            "account": d.account,
            "current_task": d._current_task,
            "width": d.width,
            "height": d.height,
        })

    return JSONResponse({
        "total": len(items),
        "online": len([i for i in items if i["state"] == "online"]),
        "devices": items,
    })


@app.get("/api/devices/{serial}/screenshot")
async def api_screenshot(serial: str, fresh: bool = False, full: bool = False,
                         size: str = "thumb") -> Response:
    """
    返回设备截图。

    参数：
      fresh  - 立即重新抓一张
      full   - 返回原始 PNG（最高清，最慢）
      size   - thumb(320px) / medium(720px) / full
    """
    if fresh or serial not in STATE.screenshots:
        import subprocess
        from ..core.config import get_config

        adb = get_config().devices.adb_path
        try:
            p = subprocess.run(
                [adb, "-s", serial, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=20,
            )
            if p.returncode == 0 and p.stdout:
                STATE.raw_png[serial] = p.stdout
                STATE.screenshots[serial] = STATE.compress_png(p.stdout)
                STATE.shot_ts[serial] = time.time()
            else:
                raise HTTPException(500, "截图失败")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"截图异常: {e}") from e

    # 全尺寸
    if full or size == "full":
        data = STATE.raw_png.get(serial) or STATE.screenshots.get(serial, b"")
        return Response(content=data, media_type="image/png")

    # 中尺寸（大屏用）
    if size == "medium":
        raw = STATE.raw_png.get(serial)
        if raw:
            cache_key = (serial, "medium", STATE.shot_ts.get(serial, 0))
            cached = STATE._medium_cache.get(cache_key)
            if cached is None:
                cached = STATE.make_medium(raw)
                STATE._medium_cache.clear()          # 只留最新
                STATE._medium_cache[cache_key] = cached
            return Response(content=cached, media_type="image/jpeg")
        return Response(content=STATE.screenshots.get(serial, b""), media_type="image/jpeg")

    # 缩略图
    return Response(content=STATE.screenshots[serial], media_type="image/jpeg")


@app.get("/api/devices/{serial}/info")
async def api_device_info(serial: str) -> JSONResponse:
    """设备详细信息。"""
    mgr = get_manager()
    dev = mgr.get(serial)
    if dev is None:
        mgr.refresh()
        dev = mgr.get(serial)
    if dev is None:
        raise HTTPException(404, "设备不存在")

    from ..core.actions import DeviceController

    info: dict[str, Any] = {
        "serial": dev.serial,
        "index": dev.index,
        "state": dev.state.value,
        "battery": dev.health.battery,
        "battery_temp": dev.health.battery_temp,
        "storage_free_mb": dev.health.storage_free_mb,
    }

    # 无障碍模式（已关 ADB）不需要问 adb 要尺寸：adbd 不在时每次查询要等 10 秒，
    # 前端开大屏就被拖成黑屏。直接用当前流的真实分辨率兜底。
    if dev.state.value != "online":
        w = _streams.get(serial)
        if w is not None and w.sess is not None:
            rw = getattr(w.sess, "real_width", 0) or getattr(w.sess, "width", 0)
            rh = getattr(w.sess, "real_height", 0) or getattr(w.sess, "height", 0)
            if rw and rh:
                info["screen"] = {"w": rw, "h": rh}
                info["source"] = "stream"
                info["transport"] = "accessible" if getattr(w, "accessible_mode", False) else "adb"
                return JSONResponse(info)

    try:
        ctrl = DeviceController(serial)
        app_info = ctrl.current_app()
        info["foreground"] = app_info.get("package", "")
        info["activity"] = app_info.get("activity", "")
        info["screen"] = {"w": ctrl.screen.width, "h": ctrl.screen.height}
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)

    return JSONResponse(info)


# ---------------------------------------------------------------------------
# API：操作
# ---------------------------------------------------------------------------

class TapRequest(BaseModel):
    serial: str
    x: int
    y: int


class TextRequest(BaseModel):
    serial: str
    text: str


class KeyRequest(BaseModel):
    serial: str
    key: str = ''
    code: int | None = None
    shift: bool = False


class SwipeRequest(BaseModel):
    serial: str
    direction: str


class SwipePointsRequest(BaseModel):
    serial: str
    x1: int
    y1: int
    x2: int
    y2: int
    duration: float = 0.3


class ScrollRequest(BaseModel):
    serial: str
    x: int
    y: int
    h: float = 0.0
    v: float = 0.0


class LaunchRequest(BaseModel):
    serial: str
    activity: str = ""
    package: str


class ActiveRequest(BaseModel):
    serial: str
    max_size: int = 720
    max_fps: int = 2
    bit_rate: int = 80000


@app.post("/api/active")
async def api_active(req: ActiveRequest) -> JSONResponse:
    """设置当前正在操控的设备（后台轮询会跳过它）。"""
    STATE.active_serial = req.serial
    if req.serial:
        STATE.log(f"活跃设备切换到 {req.serial}")
    return JSONResponse({"ok": True, "active": STATE.active_serial})


@app.post("/api/tap")
async def api_tap(req: TapRequest) -> JSONResponse:
    """点击：优先走 scrcpy control（<50ms），失败回退 adb（~500ms）。"""
    # ① scrcpy control（快）
    w = _streams.get(req.serial)
    if w and w.sess and w.sess.running:
        if w.sess.tap(req.x, req.y):
            record_input(req.serial,'tap',{'x':req.x,'y':req.y})
            # 不在这里 print：20 台并发操作时 stdout 会串行化，反而拖慢整体
            return JSONResponse({"ok": True, "via": "scrcpy"})
        # sendall 失败可能已发送部分或全部数据，不能回退重复操作。
        return JSONResponse({"ok": False, "via": "scrcpy", "error": "控制发送失败，设备是否收到操作未知"})

    # ② 无可用 scrcpy 会话时才回退 adb
    from ..core.actions import DeviceController
    try:
        ctrl = DeviceController(req.serial)
        ok = ctrl.tap(req.x, req.y)
        if ok:record_input(req.serial,'tap',{'x':req.x,'y':req.y})
        STATE.log(f"{req.serial} 点击 ({req.x},{req.y}) -> {ok} [adb]")
        return JSONResponse({"ok": ok, "via": "adb"})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


@app.post("/api/text")
async def api_text(req: TextRequest) -> JSONResponse:
    """电脑打字直达手机（默认熊猫键盘=XWKeyboard，可中文）。
    优先 XW 广播（base64 防引号问题），不可用时回退 uiautomator2。"""
    import base64 as _b64
    # 先尝试 XW 通道（默认输入法移植自汇城熊猫键盘）
    try:
        await _ensure_xw(req.serial)
        raw = (req.text or '').encode('utf-8', errors='ignore')
        b64 = _b64.b64encode(raw).decode('ascii') if raw else ''
        if b64:
            rc, out, err = await _shell(req.serial, f"am broadcast -a XW_INPUT_B64 --es msg '{b64}'", timeout=20)
            if 'Broadcast completed' in (out or '') + (err or ''):
                record_input(req.serial, 'text', {'text': req.text, 'via': 'xw'})
                STATE.log(f"{req.serial} 输入文字 -> True (xw)")
                return JSONResponse({"ok": True, "via": "xw"})
    except Exception:
        pass
    from ..core.actions import DeviceController
    try:
        ctrl = DeviceController(req.serial)
        ok = ctrl.input_text(req.text)
        if ok:record_input(req.serial,'text',{'text':req.text})
        STATE.log(f"{req.serial} 输入文字 -> {ok}")
        return JSONResponse({"ok": ok})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


@app.post("/api/key")
async def api_key(req: KeyRequest) -> JSONResponse:
    """按键：优先走 scrcpy control socket（2ms），无流时回退 uiautomator2/adb。
    code 为 Android 原生键码（直接键盘模式用，此时 key 可空），shift 变成 scrcpy metaState；
    中文走不了键码中文请走 /api/text（熊猫键盘广播）。"""
    if req.code is not None:
        try:
            code = int(req.code)
        except (TypeError, ValueError):
            raise HTTPException(400, '键码无效') from None
        if not 0 <= code <= 300:
            raise HTTPException(400, '键码越界')
        meta = 1 if req.shift else 0
        w = _streams.get(req.serial)
        if w and w.sess and w.sess.running:
            try:
                ok = w.sess.keycode_meta(code, meta)
            except AttributeError:
                ok = w.sess.press_key(code) if meta == 0 else False
            if ok:
                record_input(req.serial, 'key', {'code': code, 'shift': bool(meta)})
            return JSONResponse({"ok": ok, "via": "scrcpy"})
        rc, out, err = await _shell(req.serial, f"input keyevent {code}", timeout=10)
        ok = rc == 0
        if ok:
            record_input(req.serial, 'key', {'code': code})
        return JSONResponse({"ok": ok, "via": "adb"})
    if not req.key:
        raise HTTPException(400, '缺少按键名或键码')
    w = _streams.get(req.serial)
    if w and w.sess and w.sess.running:
        ok = w.sess.press_key(req.key)
        if ok:record_input(req.serial,'key',{'key':req.key})
        return JSONResponse({"ok": ok, "via": "scrcpy"})

    from ..core.actions import DeviceController
    try:
        loop = asyncio.get_running_loop()
        ctrl = DeviceController(req.serial)
        ok = await loop.run_in_executor(None, ctrl.press_key, req.key)
        if ok:record_input(req.serial,'key',{'key':req.key})
        STATE.log(f"{req.serial} 按键 {req.key} -> {ok} (uia2)")
        return JSONResponse({"ok": ok, "via": "uia2"})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


@app.post("/api/stream/keyframe")
async def api_stream_keyframe(req: ActiveRequest) -> JSONResponse:
    """请求立即产生 IDR（对齐汇城的 sendIframe）。

    链路与汇城完全一致：
      前端 sendIframe → 本接口 → scrcpy TYPE_RESET_OPTIONS(99)
      → Server.resetScreenEncoder → SurfaceEncoder.reset
      → screenCapture.requestReset → 编码器重建 → 立即输出新 IDR
    """
    w = _streams.get(req.serial)
    if not w or not w.sess or not w.sess.running:
        return JSONResponse({"ok": False, "reason": "no-stream"})

    target = getattr(w, '_sent_options', (w.sess.max_size, w.sess.max_fps, w.sess.bit_rate))
    ok = w.sess.request_options(*target)
    print(f"[keyframe] {req.serial} sendIframe -> {ok}", flush=True)
    return JSONResponse({"ok": ok, "via": "scrcpy-reset-options"})


# 常用 App 包名 → (中文名, 图标关键字)
KNOWN_APPS: dict[str, tuple[str, str]] = {
    "com.ss.android.ugc.aweme": ("抖音", "douyin"),
    "com.ss.android.ugc.aweme.lite": ("抖音极速版", "douyin"),
    "com.ss.android.ugc.live": ("抖音火山版", "douyin"),
    "com.zhiliaoapp.musically": ("TikTok", "tiktok"),
    "com.ss.android.ugc.trill": ("TikTok", "tiktok"),
    "com.zhiliaoapp.musically.go": ("TikTok Lite", "tiktok"),
    "com.xingin.xhs": ("小红书", "xhs"),
    "tv.danmaku.bili": ("哔哩哔哩", "bili"),
    "com.smile.gifmaker": ("快手", "kuaishou"),
    "com.kuaishou.nebula": ("快手极速版", "kuaishou"),
    "com.tencent.mm": ("微信", "wechat"),
    "com.tencent.mobileqq": ("QQ", "qq"),
    "com.taobao.taobao": ("淘宝", "taobao"),
    "com.jingdong.app.mall": ("京东", "jd"),
    "com.eg.android.AlipayGphone": ("支付宝", "alipay"),
    "com.xunmeng.pinduoduo": ("拼多多", "pdd"),
    "com.alibaba.android.rimet": ("钉钉", "dingtalk"),
    "com.android.settings": ("设置", "settings"),
    "com.android.camera": ("相机", "camera"),
    "com.android.gallery3d": ("相册", "gallery"),
    "com.google.android.youtube": ("YouTube", "youtube"),
    "com.instagram.android": ("Instagram", "instagram"),
    "com.zhihu.android": ("知乎", "zhihu"),
    "com.weibo": ("微博", "weibo"),
    "com.netease.cloudmusic": ("网易云音乐", "music"),
    "com.tencent.qqlive": ("腾讯视频", "video"),
    "com.qiyi.video": ("爱奇艺", "video"),
    "com.youku.phone": ("优酷", "video"),
}


# 应用列表缓存：{serial: (timestamp, apps)}
# 20 台设备并发时 adb 会排队，每次右键都重查会超时，所以缓存 5 分钟
_APPS_CACHE: dict[str, tuple[float, list]] = {}
_APPS_CACHE_TTL = 300.0


class AppsResponse(BaseModel):
    ok: bool
    serial: str
    apps: list


def _select_apk_png_icon(badging: str) -> str | None:
    """仅接受 aapt 报告的普通 PNG；XML 自适应图标保持未知。"""
    import re
    candidates = []
    for density, path in re.findall(r"^application-icon-(\d{1,5}):'([^'\r\n]+)'$", badging, re.MULTILINE):
        if int(density) <= 65535 and len(path) <= 512 and re.fullmatch(r'[A-Za-z0-9_./-]+\.png', path) and all(part not in ('', '.', '..') for part in path.split('/')):
            candidates.append((int(density), path))
    return max(candidates, default=(0, None))[1]


def _read_apk_png(adb: str, serial: str, apk_path: str, icon_path: str) -> bytes | None:
    """有限读取 APK 内 PNG，失败和超限均视为不可用。"""
    import shlex
    import subprocess as sp
    import struct
    from PIL import Image
    if not apk_path.startswith('/') or any(ord(c) < 32 or ord(c) == 127 for c in apk_path):
        return None
    if _select_apk_png_icon("application-icon-160:'" + icon_path + "'\n") != icon_path:
        return None
    cmd = 'unzip -p ' + shlex.quote(apk_path) + ' ' + shlex.quote(icon_path) + ' | head -c 65537'
    try:
        r = sp.run([adb, '-s', serial, 'exec-out', cmd], capture_output=True, timeout=15)
        data = r.stdout
        if r.returncode != 0 or not 33 <= len(data) <= 65536 or data[:8] != b'\x89PNG\r\n\x1a\n' or data[8:16] != b'\x00\x00\x00\x0dIHDR':
            return None
        w, h = struct.unpack('>II', data[16:24])
        if not (0 < w <= 1024 and 0 < h <= 1024):
            return None
        # 解码并重新编码，避免把未验证的 APK 字节直接交给浏览器。
        import io
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1:
                return None
            image.load()
            output = io.BytesIO()
            image.convert('RGBA').save(output, format='PNG')
            clean = output.getvalue()
            return clean if len(clean) <= 262144 else None
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, sp.TimeoutExpired):
        return None


def _read_device_app_metadata(adb: str, serial: str, package: str) -> tuple[str | None, str, str | None] | None:
    """读取匹配包名的 APK 标签与 PNG 候选；调用前须校验工具身份。"""
    import re
    import shlex
    import subprocess as sp
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', package):
        return None
    try:
        path_result = sp.run([adb, '-s', serial, 'shell', 'pm', 'path', package], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10)
        if path_result.returncode != 0:
            return None
        paths = [line[8:] for line in path_result.stdout.splitlines() if line.startswith('package:')]
        base_paths = [p for p in paths if p.endswith('/base.apk')]
        path = base_paths[0] if len(base_paths) == 1 else (paths[0] if not base_paths and len(paths) == 1 else None)
        if not path or not path.startswith('/') or any(ord(c) < 32 or ord(c) == 127 for c in path):
            return None
        result = sp.run([adb, '-s', serial, 'shell', '/data/local/tmp/aapt dump badging ' + shlex.quote(path)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
        if result.returncode != 0:
            return None
        identity = re.search(r"^package: name='([^'\r\n]+)'", result.stdout, re.MULTILINE)
        if not identity or identity.group(1) != package:
            return None
        match = re.search(r"^application-label:'([^'\r\n]{1,200})'$", result.stdout, re.MULTILINE)
        return (match.group(1) if match else None, path, _select_apk_png_icon(result.stdout))
    except (OSError, sp.TimeoutExpired):
        return None


def _read_device_app_label(adb: str, serial: str, package: str) -> str | None:
    metadata = _read_device_app_metadata(adb, serial, package)
    return metadata[0] if metadata else None


@app.get("/api/devices/{serial}/apps")
async def api_device_apps(serial: str, all: bool = False, refresh: bool = False) -> JSONResponse:
    """列出设备上已安装的应用（对齐汇城右键菜单的应用列表）。

    默认只列第三方应用（-3），all=true 时包含系统应用。
    """
    import subprocess as sp

    w = _streams.get(serial)
    cfg_adb = None
    try:
        from ..core.config import get_config
        cfg_adb = get_config().devices.adb_path
    except Exception:
        cfg_adb = os.environ.get("ADB_PATH", "adb")

    import time as _t

    def _pm(flag: str) -> list:
        cmd = [cfg_adb, "-s", serial, "shell", f"pm list packages {flag}".strip()]
        try:
            r = sp.run(cmd, capture_output=True, text=True, timeout=25,
                       encoding="utf-8", errors="ignore")
            if r.returncode != 0:
                raise RuntimeError(f"pm list packages failed: {r.stderr.strip()[:300]}")
            return [ln[len("package:"):].strip() for ln in (r.stdout or "").splitlines()
                    if ln.startswith("package:")]
        except Exception as exc:
            raise RuntimeError(f"failed to list installed apps: {exc}") from exc

    # ---- 缓存命中直接返回 ----
    cached = _APPS_CACHE.get(serial)
    if cached and not all and not refresh and (_t.time() - cached[0] < _APPS_CACHE_TTL):
        apps = cached[1]
        return JSONResponse({"ok": True, "serial": serial, "apps": apps,
                             "count": len(apps), "cached": True})

    try:
        loop = asyncio.get_running_loop()
        if all:
            lines = await loop.run_in_executor(None, lambda: _pm(""))
        else:
            # 以Android包管理器的第三方分类为准，不按包名前缀猜测。
            lines = await loop.run_in_executor(None, lambda: _pm("-3"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "serial": serial, "apps": [], "error": str(e)})

    # 只运行事先存在且摘要与汇城安装包一致的工具；不推送或覆盖设备文件。
    def _trusted_aapt():
        try:
            r = sp.run([cfg_adb, '-s', serial, 'shell', 'sha256sum', '/data/local/tmp/aapt'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5)
            return r.returncode == 0 and r.stdout.split()[:1] == ['ff33e0f530ae315d6e321c4d2eb977d585a0ca976f9969afdb729b698f452594']
        except (OSError, sp.TimeoutExpired):
            return False

    labels, icons = {}, {}
    icon_bytes = 0
    if not all and len(lines) <= 50 and await asyncio.to_thread(_trusted_aapt):
        # 串行读取并限制总时间；已开始的单次 ADB 调用仍受其自身超时约束。
        deadline = _t.monotonic() + 20
        for pkg in sorted(lines):
            if _t.monotonic() >= deadline:
                break
            metadata = await asyncio.to_thread(_read_device_app_metadata, cfg_adb, serial, pkg)
            if not metadata:
                continue
            label, apk_path, icon_path = metadata
            if label:
                labels[pkg] = label
            if icon_path and icon_bytes < 1024 * 1024 and _t.monotonic() < deadline:
                image = await asyncio.to_thread(_read_apk_png, cfg_adb, serial, apk_path, icon_path)
                if image and icon_bytes + len(image) <= 1024 * 1024:
                    import base64
                    icons[pkg] = base64.b64encode(image).decode('ascii')
                    icon_bytes += len(image)
    apps = []
    for pkg in sorted(lines):
        name, icon = KNOWN_APPS.get(pkg, (pkg, "generic"))
        name = labels.get(pkg, name)
        apps.append({
            "package": pkg,
            "name": name,
            "name_source": "device_aapt" if pkg in labels else ("preset" if pkg in KNOWN_APPS else "package_fallback"),
            "icon": icon,
            "icon_png_base64": icons.get(pkg),
            "known": pkg in KNOWN_APPS,
        })

    if not all:
        _APPS_CACHE[serial] = (_t.time(), apps)
    return JSONResponse({"ok": True, "serial": serial, "apps": apps, "count": len(apps)})


class LaunchRequest(BaseModel):
    package: str


def _launch_cmd(adb: str, serial: str, pkg: str, activity: str = "") -> list:
    """构造启动命令。

    有 activity 时用 am start（能精确到子页面，如 WiFi 设置），
    否则用 monkey 启动主入口（最通用，兼容各种 ROM）。
    """
    if activity:
        comp = activity if activity.startswith(pkg) else f"{pkg}/{activity}"
        return [adb, "-s", serial, "shell", "am", "start", "-n", comp]
    return [adb, "-s", serial, "shell", "monkey", "-p", pkg,
            "-c", "android.intent.category.LAUNCHER", "1"]


@app.post("/api/devices/{serial}/launch")
async def api_device_launch(serial: str, req: LaunchRequest) -> JSONResponse:
    """校验包名并启动应用；只在明确收到成功标记时报告成功。"""
    import re
    import subprocess as sp

    pkg = req.package
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", pkg):
        return JSONResponse({"ok": False, "error": "invalid package name"})

    try:
        from ..core.config import get_config
        adb = get_config().devices.adb_path
        loop = asyncio.get_running_loop()

        def run(cmd):
            return sp.run(cmd, capture_output=True, text=True, timeout=25,
                          encoding="utf-8", errors="ignore")

        r = await loop.run_in_executor(None, lambda: run(_launch_cmd(adb, serial, pkg)))
        stdout, stderr = r.stdout or "", r.stderr or ""
        combined = stdout + "\n" + stderr
        failure = re.search(r"(?im)(?:\b(?:error|exception|aborted|failed|failure)\b|"
                            r"no activities found|no activity found|unable to resolve)", combined)
        ok = (r.returncode == 0 and not failure and
              re.search(r"(?m)^\s*Events injected:\s*1\s*$", stdout) is not None)
        print(f"[launch {serial}] {pkg} -> {ok}", flush=True)
        return JSONResponse({"ok": ok, "output": combined.strip()[-200:],
                             **({} if ok else {"error": "启动请求未成功，请检查设备或应用启动入口"})})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


class TouchRequest(BaseModel):
    serial: str
    x: int
    y: int


def _live_session(serial: str):
    """实时触摸只走已建立的 scrcpy 会话：无会话就拒绝，不回退、不重排。"""
    w = _streams.get(serial)
    if w and w.sess and w.sess.running:
        return w.sess
    return None


@app.post("/api/touch/down")
async def api_touch_down(req: TouchRequest) -> JSONResponse:
    """实时触摸-按下：收到即发，不 sleep、不缩短、不排队。"""
    sess = _live_session(req.serial)
    if sess is None:
        return JSONResponse({"ok": False, "reason": "no-stream"})
    ok = sess.touch_down(req.x, req.y)
    if ok:
        record_input(req.serial, 'touch', {'phase': 'down', 'x': req.x, 'y': req.y})
        return JSONResponse({"ok": True, "via": "scrcpy"})
    return JSONResponse({"ok": False, "via": "scrcpy", "error": "控制发送失败，设备是否收到操作未知"})


@app.post("/api/touch/move")
async def api_touch_move(req: TouchRequest) -> JSONResponse:
    """实时触摸-移动：收到即发单次 MOVE。节流由前端负责（约 20ms）。"""
    sess = _live_session(req.serial)
    if sess is None:
        return JSONResponse({"ok": False, "reason": "no-stream"})
    ok = sess.touch_move(req.x, req.y)
    if ok:
        return JSONResponse({"ok": True, "via": "scrcpy"})
    return JSONResponse({"ok": False, "via": "scrcpy", "error": "控制发送失败，设备是否收到操作未知"})


@app.post("/api/touch/up")
async def api_touch_up(req: TouchRequest) -> JSONResponse:
    """实时触摸-松手：收到即发 UP。"""
    sess = _live_session(req.serial)
    if sess is None:
        return JSONResponse({"ok": False, "reason": "no-stream"})
    ok = sess.touch_up(req.x, req.y)
    if ok:
        record_input(req.serial, 'touch', {'phase': 'up', 'x': req.x, 'y': req.y})
        return JSONResponse({"ok": True, "via": "scrcpy"})
    return JSONResponse({"ok": False, "via": "scrcpy", "error": "控制发送失败，设备是否收到操作未知"})


@app.post("/api/scroll")
async def api_scroll(req: ScrollRequest) -> JSONResponse:
    """鼠标滚轮滚动（scrcpy INJECT_SCROLL_EVENT）。"""
    w = _streams.get(req.serial)
    if w and w.sess and w.sess.running:
        if w.sess.scroll(req.x, req.y, req.h, req.v):
            return JSONResponse({"ok": True, "via": "scrcpy"})
    return JSONResponse({"ok": False, "via": "none"})


@app.post("/api/swipe")
async def api_swipe(req: SwipeRequest) -> JSONResponse:
    from ..core.actions import DeviceController
    try:
        ctrl = DeviceController(req.serial)
        fn = {
            "up": ctrl.swipe_up,
            "down": ctrl.swipe_down,
        }.get(req.direction)
        ok = fn() if fn else False
        STATE.log(f"{req.serial} 滑动 {req.direction} -> {ok}")
        return JSONResponse({"ok": ok})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


@app.post("/api/swipe-points")
async def api_swipe_points(req: SwipePointsRequest) -> JSONResponse:
    """按坐标滑动：优先 scrcpy control。统一走手感曲线并钳制到屏幕内。"""
    from ..core.actions import Humanizer
    import time as _time
    _t0 = _time.monotonic()
    dev = get_manager().get(req.serial)
    sw, sh = (dev.width, dev.height) if dev else (0, 0)
    sx1, sy1, sx2, sy2 = Humanizer.swipe_shorten(req.x1, req.y1, req.x2, req.y2, sw, sh)
    if (sx1, sy1, sx2, sy2) != (req.x1, req.y1, req.x2, req.y2):
        STATE.log(f"{req.serial} 滑动已按手感曲线缩短 ({req.x1},{req.y1})→({req.x2},{req.y2}) 为 ({sx1},{sy1})→({sx2},{sy2})")
    # ★ 群控诊断日志：每台收到的原始坐标、缩短后坐标、通道、结果、耗时。
    #   只记坐标与结果，不记任何密钥。服务重启后从日志按 serial 过滤即可对比。
    def _diag(result: str, via: str) -> None:
        STATE.log(f"[fanout-diag] {req.serial} req=({req.x1},{req.y1})→({req.x2},{req.y2}) "
                  f"send=({sx1},{sy1})→({sx2},{sy2}) dur={req.duration} via={via} "
                  f"result={result} cost={_time.monotonic()-_t0:.2f}s")
    w = _streams.get(req.serial)
    if w and w.sess and w.sess.running:
        # swipe 现在按真实时间铺开事件，会阻塞到 duration 结束，必须放到线程里。
        if await asyncio.to_thread(w.sess.swipe, sx1, sy1, sx2, sy2, req.duration):
            record_input(req.serial,'swipe',{'x1':sx1,'y1':sy1,'x2':sx2,'y2':sy2,'duration':req.duration})
            _diag("ok", "scrcpy")
            return JSONResponse({"ok": True, "via": "scrcpy"})
        # 已尝试发送后结果可能未知，禁止再通过 ADB 重复滑动。
        _diag("fail", "scrcpy")
        return JSONResponse({"ok": False, "via": "scrcpy", "error": "控制发送失败，设备是否收到操作未知"})
    _diag("no-stream", "none")

    from ..core.actions import DeviceController
    try:
        ctrl = DeviceController(req.serial)
        ok = await asyncio.to_thread(ctrl.swipe, sx1, sy1, sx2, sy2, req.duration)
        if ok:record_input(req.serial,'swipe',{'x1':sx1,'y1':sy1,'x2':sx2,'y2':sy2,'duration':req.duration})
        _diag("ok" if ok else "fail", "adb")
        return JSONResponse({"ok": ok})
    except Exception as e:  # noqa: BLE001
        _diag("exception", "adb")
        raise HTTPException(500, str(e)) from e


@app.post("/api/launch")
async def api_launch(req: LaunchRequest) -> JSONResponse:
    from ..core.actions import DeviceController
    try:
        ctrl = DeviceController(req.serial)
        ok = ctrl.launch_app(req.package)
        STATE.log(f"{req.serial} 启动 {req.package} -> {ok}")
        return JSONResponse({"ok": ok})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e


# ---------------------------------------------------------------------------
# API：Agent
# ---------------------------------------------------------------------------

class AgentRequest(BaseModel):
    serial: str
    goal: str
    max_steps: int = 20


@app.post("/api/agent")
async def api_agent(req: AgentRequest) -> JSONResponse:
    """运行 AI Agent（异步执行，立即返回）。"""
    from ..llm.agent import Agent

    task_id = f"agent_{int(time.time())}"

    def _run():
        STATE.tasks[task_id] = {"status": "running", "goal": req.goal, "steps": []}
        STATE.log(f"[{req.serial}] AI 开始: {req.goal}")

        try:
            agent = Agent(req.serial)
            state = agent.run(req.goal, max_steps=req.max_steps, verbose=False)

            STATE.tasks[task_id] = {
                "status": "done" if state.done else "failed",
                "goal": req.goal,
                "result": state.result or state.error,
                "steps": state.history,
            }
            STATE.log(f"[{req.serial}] AI 完成: {state.result or state.error}")
        except Exception as e:  # noqa: BLE001
            STATE.tasks[task_id] = {"status": "error", "goal": req.goal, "error": str(e)}
            STATE.log(f"[{req.serial}] AI 异常: {e}", "error")

    threading.Thread(target=_run, daemon=True).start()

    return JSONResponse({"task_id": task_id, "status": "started"})


@app.get("/api/tasks")
async def api_tasks() -> JSONResponse:
    return JSONResponse({"tasks": STATE.tasks})


# ---------------------------------------------------------------------------
# API：日志
# ---------------------------------------------------------------------------

@app.get("/api/logs")
async def api_logs(limit: int = 100) -> JSONResponse:
    items = list(STATE.logs)[-limit:]
    return JSONResponse({"logs": items})


# ---------------------------------------------------------------------------
# WebSocket：状态推送
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    STATE._subscribers.append(ws)

    try:
        while True:
            # 每 2 秒推一次设备状态
            await asyncio.sleep(2)
            mgr = get_manager()
            devs = mgr.all()

            payload = {
                "type": "status",
                "ts": time.time(),
                "devices": [
                    {
                        "serial": d.serial,
                        "index": d.index,
                        "state": d.state.value,
                        "battery": d.health.battery,
                        "shot_ts": STATE.shot_ts.get(d.serial, 0),
                    }
                    for d in devs
                ],
                "logs": list(STATE.logs)[-10:],
            }
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        if ws in STATE._subscribers:
            STATE._subscribers.remove(ws)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    print("=" * 60)
    print("  FishFleet 群控界面")
    print("=" * 60)
    print(f"  地址: http://{host}:{port}")
    print()

    # 启动截图循环
    STATE.start_screenshot_loop(interval=15.0)
    STATE.log("FishFleet 启动")

    uvicorn.run(app, host=host, port=port, log_level="warning")



# ---------------------------------------------------------------------------
# scrcpy 实时流（H.264 → ffmpeg → JPEG → WebSocket）
# ---------------------------------------------------------------------------

_streams: dict[str, "StreamWorker"] = {}
# 进程内单事件循环使用；不回收锁，避免等待者与新锁分裂。
_stream_locks: dict[str, asyncio.Lock] = {}
# 单进程单事件循环内，所有活动起流及恢复共用四个槽位。
_stream_start_slots = asyncio.Semaphore(4)


def _stream_lock(serial: str) -> asyncio.Lock:
    return _stream_locks.setdefault(serial, asyncio.Lock())


async def _stream_start_call(func, *args, current=None):
    """调用者须先持有 serial 锁；取消时槽位保持到线程实际退出。"""
    async with _stream_start_slots:
        if current is not None and not current():
            return None
        return await _stream_call(func, *args)


async def _stream_call(func, *args):
    """请求取消不会终止线程；线程退出前继续持有外层锁。"""
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        # 取回异常，避免未消费任务异常；取消仍优先传播。
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return result


def _nal_type(data: bytes) -> int:
    """取 Annex-B 数据里第一个 NAL 的类型，跳过 3/4 字节起始码。

    ★ 之前写死 data[4]，只在 4 字节起始码时正确；
      3 字节起始码时判断永远失败 → 缓冲区永不清空 → IDR 被挤出 → 黑屏等新 IDR
    """
    n = len(data)
    if n < 4:
        return -1
    if n >= 4 and data[0] == 0 and data[1] == 0 and data[2] == 0 and data[3] == 1:
        return data[4] & 0x1F if n > 4 else -1
    if data[0] == 0 and data[1] == 0 and data[2] == 1:
        return data[3] & 0x1F if n > 3 else -1
    return -1


def _is_keyframe(data: bytes) -> bool:
    """IDR = NAL type 5。"""
    return _nal_type(data) == 5


class _H264Subscription:
    """仅在所属事件循环访问队列；事件为 (epoch, seq, kind, data, key)。"""

    def __init__(self, loop, epoch, tail, config, replay, capacity=512):
        import asyncio
        self.loop = loop
        self.queue = asyncio.Queue(maxsize=capacity)
        self.epoch = epoch
        self.tail = tail
        self.config = config
        self.waiting_key = True
        self.closed = False
        if config:
            self.queue.put_nowait((epoch, b"C" + config))
        # 不截断一个 GOP：放不下则等待新 IDR。
        if replay and replay[0][4] and len(replay) <= capacity - self.queue.qsize():
            for event in replay:
                self.queue.put_nowait((epoch, b"V" + event[3]))
            self.waiting_key = False

    def _clear(self):
        while not self.queue.empty():
            self.queue.get_nowait()

    def enqueue(self, event):
        if self.closed:
            return
        epoch, seq, kind, data, key = event
        if seq <= self.tail or epoch < self.epoch:
            return
        self.tail = seq
        if kind == "C":
            self.epoch, self.config = epoch, data
            self.waiting_key = True
            self._clear()
            self.queue.put_nowait((epoch, b"C" + data))
            return
        if epoch != self.epoch or not self.config:
            return
        if self.queue.full():
            # 丢失任何 AU 后，后续 delta 不再可安全解码。
            self._clear()
            self.waiting_key = True
            self.queue.put_nowait((epoch, b"C" + self.config))
        if self.waiting_key and not key:
            return
        self.waiting_key = False
        self.queue.put_nowait((epoch, b"V" + data))


class StreamWorker:
    """单设备的实时流工作线程。"""

    def __init__(self, serial: str):
        self.serial = serial
        self.sess = None
        self.dec = None
        self.thread: threading.Thread | None = None
        self.running = False
        # 无障碍会话由用户手动开启/重连控制，不参与自动参数更新与自动重启。
        self.accessible_mode = False
        self.last_jpeg: bytes | None = None
        self.last_h264: bytes | None = None
        self.last_keyframe: bytes | None = None   # 最近的 IDR
        # ★ 帧缓冲：切换设备时必须从 IDR 开始「连续」推，不能跳帧
        #   否则中间丢失的 delta 帧会导致解码器参考数据缺失 → 花屏
        import collections as _col
        # 只保留完整 GOP；容量淘汰其 IDR 时放弃该次回放。
        self.frame_buffer: _col.deque = _col.deque(maxlen=400)
        # JPEG 与 H264 必须使用不同订阅集合。
        self.subscribers: list = []
        self._h264_subscribers = []
        self._h264_lock = threading.Lock()
        self._h264_seq = 0
        self._h264_epoch = 0
        self.config_pkt: bytes | None = None   # SPS/PPS
        self.frame_count = 0
        self.h264_bytes = 0  # 编码包数据字节数，不等于配置码率或网络开销。
        self._stream_generation = 0
        self.jpeg_count = 0
        self.error = ""
        self._first_jpeg_at = None
        self._started_monotonic = None
        self._first_jpeg_delay = None
        self._lock = threading.RLock()
        # 此锁只保护短暂的发布和代际失效，绝不持有它等待解码器或线程。
        self._publish_lock = threading.Lock()
        self._generation = None

    def subscribe_h264(self, loop):
        # 快照和订阅边界必须与生产者更新使用同一个锁。
        with self._h264_lock:
            replay = list(self.frame_buffer)
            sub = _H264Subscription(loop, self._h264_epoch, self._h264_seq,
                                    self.config_pkt, replay)
            self._h264_subscribers.append(sub)
            return sub

    def unsubscribe_h264(self, sub):
        with self._h264_lock:
            sub.closed = True
            if sub in self._h264_subscribers:
                self._h264_subscribers.remove(sub)

    def _publish_h264(self, frame):
        with self._h264_lock:
            if frame.is_config:
                if frame.data == self.config_pkt:
                    return
                self.config_pkt = frame.data
                self._h264_epoch += 1
                self.frame_buffer.clear()
                self.last_h264 = self.last_keyframe = None
                kind, key = "C", False
            else:
                kind, key = "V", bool(frame.is_key)
                self.last_h264 = frame.data
                if key:
                    self.last_keyframe = frame.data
                    self.frame_buffer.clear()
            self._h264_seq += 1
            event = (self._h264_epoch, self._h264_seq, kind, frame.data, key)
            if kind == "V":
                if key or self.frame_buffer:
                    self.frame_buffer.append(event)
                    # deque 自动淘汰 IDR 后整个回放已不完整，不能保留后段。
                    if self.frame_buffer and not self.frame_buffer[0][4]:
                        self.frame_buffer.clear()
            for sub in tuple(self._h264_subscribers):
                try:
                    sub.loop.call_soon_threadsafe(sub.enqueue, event)
                except RuntimeError:
                    sub.closed = True

    def _reset_h264(self):
        with self._h264_lock:
            self._h264_epoch += 1
            self.config_pkt = None
            self.last_h264 = self.last_keyframe = None
            self.frame_buffer.clear()
            for sub in self._h264_subscribers:
                sub.closed = True
            self._h264_subscribers.clear()

    def start(self) -> bool:
        from ..core.scrcpy import ScrcpySession, H264Decoder
        with self._lock:
            if self.running:
                return True
            # stop 的有界 join 可能超时；旧线程仍使用 self.sess/self.dec，
            # 在其退出前不能把这些字段交给新一代会话。
            if self.thread and self.thread.is_alive():
                self.error = "旧流线程尚未退出，不能重启"
                return False
            self.thread = None
            # 自然断流后可能仍持有旧会话和解码器，不能直接覆盖资源。
            if self.sess is not None or self.dec is not None:
                try:
                    self.stop()
                except Exception as exc:
                    self.error = f"旧流清理失败: {type(exc).__name__}"
                    return False

            self._reset_h264()

            # 编码尺寸、帧率和码率由 FishFleet 当前请求配置。
            # 汇城 UI 码率档位到 bps 的映射尚未确认，不能以请求值冒充实测速率。
            ms = getattr(self, "_max_size", 360)
            fps = getattr(self, "_max_fps", 2)
            br = getattr(self, "_bit_rate", 80000)
            # 不复用默认 scrcpy socket，降低与其他投屏会话冲突的概率。
            # scid 是随机隔离标识，不是进程或转发所有权证明。
            import secrets
            scid = secrets.randbelow(0x80000000)
            # 无障碍模式（mode=3）走 App 回连通道：电脑监听、手机主动连入，
            # 建好后不依赖 adb forward，因此可以关掉开发者模式继续投屏。
            # 以运行表为准（实时），不用可能滞后的设备缓存。
            use_accessible = False
            try:
                from ..core import device_ext as _dx_rt
                _rt = _dx_rt.accessible_runtime(self.serial) or {}
                use_accessible = bool(_rt.get("is_accessible"))
            except Exception:
                use_accessible = False
            if use_accessible:
                from ..core.accessible import AccessibleScrcpySession
                # 无障碍默认给清晰档：调用方没填参数（API 默认 720/2/80000
                # 或 worker 默认 360/2/80000）时用 1080/15/6M；显式指定的原样透传。
                # （2fps/80kbps 就是之前"糊成 PPT"的来源。）
                if (ms, fps, br) in ((720, 2, 80000), (360, 2, 80000)):
                    ms, fps, br = 1080, 15, 6000000
                    print(f"[stream {self.serial}] 无障碍用默认清晰档 1080/15/6M", flush=True)
                self.accessible_mode = True
                self.sess = AccessibleScrcpySession(
                    self.serial, max_size=ms, max_fps=fps, bit_rate=br,
                )
            else:
                self.accessible_mode = False
                self.sess = ScrcpySession(self.serial, max_size=ms, max_fps=fps, bit_rate=br, scid=scid)
            self._sent_options = (ms, fps, br)
            try:
                started = self.sess.start()
            except BaseException as start_exc:
                self.error = f'启动异常: {type(start_exc).__name__}'
                try:
                    self.sess.stop()
                except Exception as cleanup_exc:
                    # 保留引用，不能让清理异常遮蔽原始启动异常。
                    self.error += f'；会话清理未确认: {type(cleanup_exc).__name__}'
                else:
                    self.sess = None
                raise
            if not started:
                detail = ""
                try:
                    detail = (self.sess.last_error or "") if self.sess is not None else ""
                except Exception:
                    detail = ""
                # 无障碍保留真实原因（15 秒超时 / App 未回连），便于按汇城口径展示。
                self.error = detail or "scrcpy 启动失败"
                try:
                    self.sess.stop()
                except Exception as exc:
                    self.error += f'；会话清理失败: {type(exc).__name__}'
                else:
                    self.sess = None
                return False

            # ★ 设置设备真实分辨率（scrcpy 触摸坐标换算需要）
            try:
                from ..core.device import get_manager
                dev = get_manager().get(self.serial)
                if dev and dev.width and dev.height:
                    self.sess.real_width = dev.width
                    self.sess.real_height = dev.height
                    print(f"[stream {self.serial}] 真实分辨率 {dev.width}x{dev.height}, "
                          f"画面 {self.sess.width}x{self.sess.height}", flush=True)
            except Exception as e:
                print(f"[stream] 读设备分辨率失败: {e}", flush=True)

            generation = object()
            # FishFleet 使用 PyAV 进程内解码；汇城的实际图像管线尚未证实。
            decoder_ready = False
            try:
                from ..core.av_decode import AvJpegDecoder, av_available
                if av_available():
                    self.dec = AvJpegDecoder(
                        self.sess.width, self.sess.height,
                        # 较低量化值的画质试验，是否生效须通过输出 JPEG 检验。
                        quality=3, fps=0,
                        on_jpeg=lambda jpeg: self._push_jpeg(jpeg, generation),
                    )
                    decoder_ready = bool(self.dec.start())
            except Exception as _e:
                print(f"[stream {self.serial}] PyAV 初始化失败: {_e}", flush=True)

            if not decoder_ready:
                if self.dec is not None:
                    try:
                        self.dec.stop()
                    except Exception as exc:
                        self.error = f'JPEG 解码器清理失败: {type(exc).__name__}'
                        return False
                    self.dec = None
                # 主画面依赖 JPEG；不能把没有解码器的会话报告为正常流。
                self.error = 'JPEG 解码器初始化失败'
                try:
                    self.sess.stop()
                except Exception as exc:
                    # 保留引用；下次启动必须先再次尝试清理旧资源。
                    self.error += f'；会话清理失败: {type(exc).__name__}'
                else:
                    self.sess = None
                return False

            # 首帧时间基准、代际和错误状态在启动线程前一起发布。
            with self._publish_lock:
                self._generation = generation
                self._stream_generation += 1
                self._first_jpeg_at = None
                self._first_jpeg_delay = None
                self._started_monotonic = time.monotonic()
                self.running = True
                self.error = ''
            self.thread = threading.Thread(target=self._run_feed_loop, args=(generation,), daemon=True)
            self.jpeg_thread = None
            try:
                self.thread.start()
            except Exception:
                self.thread = None
                self.stop()
                raise
            return True

    def _run_feed_loop(self, generation) -> None:
        try:
            self._feed_loop()
        except Exception as exc:
            with self._publish_lock:
                if generation is self._generation:
                    self.error = f'读帧线程异常: {type(exc).__name__}'
                    self.running = False
                    self._generation = None
                    self.last_jpeg = None
                    self._reset_h264()
        finally:
            with self._publish_lock:
                if generation is self._generation:
                    self.running = False
                    self._generation = None

    def _feed_loop(self) -> None:
        """读取 H.264 并交给当前解码器。

        静止画面可能长时间没有新数据；超时本身不能证明连接断开。
        """
        session, decoder, generation = self.sess, self.dec, self._generation
        assert session is not None
        while self.running:
            f = session.read_frame(timeout=1.5)
            # stop 可能在 read_frame 阻塞时清理资源；迟到帧不能发布。
            if not self.running:
                break
            if f is None:
                # 静止超时允许继续等待，但 socket 已明确关闭时立即退出。
                if not session.running:
                    if getattr(self, 'accessible_mode', False):
                        self.error = '无障碍连接断开或是超过15秒未连接'
                    else:
                        self.error = '流中断'
                    break
                continue
            if decoder is not None:
                # 解码可能回调 JPEG；不能持有发布锁调用解码器。
                decoder.feed_access_unit(f.data)
            with self._publish_lock:
                if generation is not self._generation or not self.running:
                    break
                self._publish_h264(f)
                self.frame_count += 1
                self.h264_bytes += len(f.data)
        with self._publish_lock:
            if generation is self._generation:
                self.running = False
                self._generation = None
                self.last_jpeg = None
                self._reset_h264()

    def _jpeg_loop(self) -> None:
        """独立线程读 ffmpeg 输出的 JPEG 并分发给订阅者。"""
        decoder, generation = self.dec, self._generation
        assert decoder is not None
        if generation is None:
            return
        print(f"[stream {self.serial}] jpeg_loop start id(self)={id(self)}")
        while self.running:
            jpeg = decoder.read_jpeg(timeout=0.3)
            if jpeg is None:
                continue
            self._push_jpeg(jpeg, generation)
        print(f"[stream {self.serial}] jpeg_loop exit, total={self.jpeg_count}")

    def reconfigure(self, max_size: int, max_fps: int, bit_rate: int) -> dict:
        """在现有控制连接发动态选项；发送成功不等于设备已确认帧率。"""
        with self._lock:
            session = self.sess
            if not self.running or not session:
                return {"ok": False, "reason": "no-session"}
            before = getattr(self, '_sent_options', (session.max_size, session.max_fps, session.bit_rate))
            target = (max_size, max_fps, bit_rate)
            if before == target:
                return {"ok": True, "status": "unchanged"}
            if not session.request_options(max_size=max_size, max_fps=max_fps, bit_rate=bit_rate):
                return {"ok": False, "reason": "control-send-failed"}
            # 记录已发送目标，实际画面尺寸由解码后的AVFrame更新。
            self._sent_options = target
            self._max_size, self._max_fps, self._bit_rate = target
            self._options_update = {"status": "sent", "target": list(target), "at": time.time(),
                                    "monotonic_at": time.monotonic(),
                                    "session_identity": id(session),
                                    "jpeg_before": self.jpeg_count,
                                    "size_change": before[0] != max_size}
            return {"ok": True, **self._options_update}

    def recover_options_if_needed(self) -> bool:
        with self._lock:
            return self._recover_options_locked()

    def _recover_options_locked(self) -> bool:
        """只在持续有旧尺寸帧的证据下回退；静态无输入不视为失败。"""
        update = getattr(self, '_options_update', None)
        if not update or update.get('status') != 'sent' or not update.get('size_change'):
            return False
        if not self.sess or update.get('session_identity') != id(self.sess):
            return False
        if time.monotonic() - update['monotonic_at'] < 8:
            return False
        if self.jpeg_count - update['jpeg_before'] < 10:
            return False
        if not self.running or max(self.sess.width, self.sess.height) == (update['target'][0] & ~7):
            return False
        # 当前目标必须仍是该请求，不能让迟到恢复覆盖用户的新设置。
        if tuple(update['target']) != getattr(self, '_sent_options', None):
            return False
        update['status'] = 'recovering'
        update['reason'] = 'continued-old-size-after-timeout'
        self.stop()
        ok = self.start()
        update['status'] = 'restart-started' if ok else 'restart-failed'
        update['recovery_at'] = time.time()
        return ok

    def _push_jpeg(self, jpeg: bytes, generation=None) -> None:
        """解码器锁内的回调只获取发布锁，不获取生命周期锁。"""
        with self._publish_lock:
            if generation is not None and (generation is not self._generation or not self.running):
                return
            if self.sess and self.dec:
                self.sess.width, self.sess.height = self.dec.width, self.dec.height
                if self.sess.real_width and self.sess.real_height:
                    long = max(self.sess.real_width, self.sess.real_height)
                    short = min(self.sess.real_width, self.sess.real_height)
                    self.sess.real_width, self.sess.real_height = ((long, short) if self.dec.width > self.dec.height else (short, long))
                update = getattr(self, '_options_update', None)
                if update and update['status'] == 'sent':
                    expected = update['target'][0] & ~7
                    if max(self.dec.width, self.dec.height) == expected:
                        update['status'] = 'size-observed'
                        update['observed_size'] = [self.dec.width, self.dec.height]
                        update['size_observed_at'] = time.time()
            self.last_jpeg = jpeg
            if self._first_jpeg_at is None:
                self._first_jpeg_at = time.time()
                if self._started_monotonic is not None:
                    self._first_jpeg_delay = max(0.0, time.monotonic() - self._started_monotonic)
            self.jpeg_count += 1
            for q in list(self.subscribers):
                try:
                    q.put_nowait(jpeg)
                except Exception:
                    pass

    def stop(self) -> None:
        # 与 start/reconfigure 共用可重入锁，恢复路径已持有此锁。
        # 先失效代际，释放发布锁后才等待线程和解码器锁。
        with self._lock:
            with self._publish_lock:
                self.running = False
                self._generation = None
                # 旧会话的静止缓存不能被新连接当成当前画面。
                self.last_jpeg = None
                self._first_jpeg_at = None
                self._first_jpeg_delay = None
                self._started_monotonic = None
            thread = self.thread
            if thread and thread is not threading.current_thread():
                thread.join(timeout=2)
            # 解码线程尚未退出时，不能在此等待 decoder 的内部锁；
            # 保留所有权并拒绝重启，待线程退出后由下一次 stop 清理。
            if thread and thread.is_alive():
                self.thread = thread
                self.error = '旧流线程尚未退出，资源尚未清理'
                self._reset_h264()
                return
            if self.dec:
                self.dec.stop()
            if self.sess:
                self.sess.stop()
            self.thread = None
            self.dec = None
            self.sess = None
            self._reset_h264()

    def _health_check(self) -> bool:
        """检查流是否卡死（running=True 但帧不增长），卡了就自动重启。

        ★ 20 台并发时某个 scrcpy 进程可能挂掉但状态还是 True，
          必须主动检测并重启，否则用户看到的是"卡住的画面"。
        """
        now = time.time()
        last_frames = getattr(self, "_last_frames", 0)
        last_t = getattr(self, "_last_frames_t", now)

        cur = getattr(self, "frames", 0) or 0

        # 每 3 秒采样一次
        if now - last_t < 3.0:
            return True

        grew = cur > last_frames
        self._last_frames = cur
        self._last_frames_t = now

        if grew:
            self._stuck = 0
            return True

        # 连续卡住超过 3 次采样（约 9 秒）→ 重启
        self._stuck = getattr(self, "_stuck", 0) + 1
        if self._stuck >= 3:
            print(f"[stream {self.serial}] 检测到卡死（frames={cur}），重启中…", flush=True)
            self._stuck = 0
            try:
                self.stop()
            except Exception:
                pass
            time.sleep(0.2)
            return self.start()
        return True

    def info(self) -> dict:
        with self._publish_lock:
            frames, encoded_bytes, jpegs = self.frame_count, self.h264_bytes, self.jpeg_count
            generation = self._stream_generation
            sampled_at = time.monotonic()
        return {
            "serial": self.serial,
            "running": self.running,
            "frames": frames,
            "h264_bytes_total": encoded_bytes,
            "stream_generation": generation,
            # 同一发布锁内的采样，可用同一 worker 差值估算编码数据吞吐。
            "sample_monotonic_seconds": sampled_at,
            "jpegs": jpegs,
            "first_jpeg_at": self._first_jpeg_at,
            "first_jpeg_delay_seconds": self._first_jpeg_delay,
            "transcode": self.dec.stats() if self.dec else None,
            "jpeg_delivery": dict(getattr(self, '_jpeg_delivery_stats', {})),
            "options_update": getattr(self, "_options_update", None),
            "session_identity": id(self.sess) if self.sess else None,
            "requested_options": dict(zip(("max_size", "max_fps", "bit_rate"), getattr(self, "_sent_options", (0, 0, 0)))) if self.sess else None,
            "error": self.error,
            "width": self.sess.width if self.sess else 0,
            "height": self.sess.height if self.sess else 0,
        }


def get_stream(serial: str) -> StreamWorker:
    w = _streams.get(serial)
    if w is None:
        w = StreamWorker(serial)
        _streams[serial] = w
    return w


@app.post("/api/stream/start")
async def api_stream_start(req: ActiveRequest) -> JSONResponse:
    """启动实时流（汇城式：保留最近 N 个，切换零等待）。

    对齐汇城：它同时保持 20 路的 scrcpy 连接，
    切设备时直接复用已有 socket，所以"秒开"。
    我们保留最近 MAX_STREAMS 路，兼顾性能和内存。
    """
    # 不同时持有多个 serial 锁；并发请求下数量上限仅为尽力淘汰。
    running = [s for s, w in _streams.items() if s != req.serial and w.running]
    while len(running) >= 20:
        old_s = running.pop(0)
        async with _stream_lock(old_s):
            old_w = _streams.get(old_s)
            if old_w and old_w.running:
                try:
                    await _stream_call(old_w.stop)
                except Exception:
                    pass

    async with _stream_lock(req.serial):
        w = get_stream(req.serial)
        try:
            from ..core import device_ext as _dx2
            want_a11y = bool((_dx2.accessible_runtime(req.serial) or {}).get("is_accessible"))
        except Exception:
            want_a11y = False
        if w.running and w.sess is not None and bool(getattr(w, "accessible_mode", False)) != want_a11y:
            # 通道变了（ADB↔无障碍），旧会话不能复用：停掉走下面重建。
            await _stream_call(w.stop)
        if w.running and w.sess is not None:
            old_ms, old_fps, old_br = getattr(w, '_sent_options', (w.sess.max_size, w.sess.max_fps, w.sess.bit_rate))
            new_fps = getattr(req, "max_fps", 5)
            new_br = getattr(req, "bit_rate", 200000)
            changed = (old_ms and old_ms != req.max_size) or (old_fps and old_fps != new_fps) or (old_br and old_br != new_br)
            if not changed:
                return JSONResponse({"ok": True, "reused": True, **w.info()})
            result = await _stream_call(w.reconfigure, req.max_size, new_fps, new_br)
            if result.get("ok"):
                return JSONResponse({"ok": True, "reused": True, "dynamic": result, **w.info()})
            await _stream_call(w.stop)

        # 始终复用原对象，保留 stop 超时后的旧线程引用。
        w._max_size = req.max_size
        w._max_fps = getattr(req, "max_fps", 2)
        w._bit_rate = getattr(req, "bit_rate", 80000)
        ok = await _stream_start_call(w.start)
        return JSONResponse({"ok": ok, **w.info()})


@app.post("/api/stream/stop")
async def api_stream_stop(req: ActiveRequest) -> JSONResponse:
    """停止实时流，保留 worker 及可能尚存的线程引用。"""
    async with _stream_lock(req.serial):
        w = _streams.get(req.serial)
        if w:
            try:
                await _stream_call(w.stop)
            except Exception as exc:
                return JSONResponse({"ok": False, "error": f"停止失败: {type(exc).__name__}"}, status_code=500)
            if w.thread and w.thread.is_alive():
                return JSONResponse({"ok": False, "error": "流线程尚未退出"}, status_code=503)
    return JSONResponse({"ok": True})


@app.get("/api/stream/info")
async def api_stream_info() -> JSONResponse:
    return JSONResponse({s: w.info() for s, w in _streams.items()})


# ==================== 右键菜单配套接口 ====================

def _adb_path() -> str:
    try:
        from ..core.config import get_config
        return get_config().devices.adb_path
    except Exception:
        return os.environ.get("ADB_PATH", "adb")


async def _shell(serial: str, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    import subprocess as sp
    adb = _adb_path()
    r = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: sp.run([adb, "-s", serial, "shell", cmd],
                       capture_output=True, text=True, timeout=timeout),
    )
    return r.returncode, r.stdout, r.stderr


XW_IME_ID = "com.android.xwkeyboard/.XwIME"
# 2026-11-06：APK 位置统一走 paths.apk_path()（打包后是 exe 旁的 bin/apk/）。
# 原来第二个候选写死了 C:\Users\Administrator\.updateFiles\...，别人机器上不存在。
XW_APK_CANDIDATES = [
    str(__import__("fishfleet.paths", fromlist=["apk_path"]).apk_path("XWKeyboard.apk") or ""),
]


async def _ensure_xw(serial: str) -> bool:
    """确保熊猫键盘（XWKeyboard）已安装并设为默认输入法。失败返回 False，不抛错。"""
    import subprocess as sp
    try:
        adb = _adb_path()
        loop = asyncio.get_running_loop()

        def _run(args, timeout=15):
            try:
                r = sp.run([adb] + args, capture_output=True, text=True, timeout=timeout)
                return (r.stdout or '') + (r.stderr or '')
            except Exception:
                return ''
        out = await loop.run_in_executor(None, lambda: _run(["-s", serial, "shell", "pm list packages com.android.xwkeyboard"], 15))
        if "com.android.xwkeyboard" not in out:
            apk = next((p for p in XW_APK_CANDIDATES if os.path.exists(p)), None)
            if not apk:
                return False
            await loop.run_in_executor(None, lambda: _run(["-s", serial, "install", "-r", apk], 120))
        await loop.run_in_executor(None, lambda: _run(["-s", serial, "shell", f"ime enable {XW_IME_ID}"], 15))
        await loop.run_in_executor(None, lambda: _run(["-s", serial, "shell", f"ime set {XW_IME_ID}"], 15))
        rc, cur, _ = await _shell(serial, "settings get secure default_input_method", timeout=15)
        return "xwkeyboard" in (cur or "")
    except Exception:
        return False


@app.post("/api/devices/ensure-xw")
async def api_ensure_xw(req: dict) -> JSONResponse:
    """批量给手机装熊猫键盘并设默认。body: {serials:[...]}"""
    serials = (req or {}).get("serials") or []
    out = {}
    for s in serials:
        try:
            out[s] = await _ensure_xw(s)
        except Exception as e:  # noqa: BLE001
            out[s] = f"error: {e}"
    return JSONResponse({"ok": True, "result": out})


MOBILECLI_CANDIDATES = [
    __import__("fishfleet.paths", fromlist=["mobilecli_path"]).mobilecli_path(),
]
MOBILECLI_ADB_DIR = str(__import__("fishfleet.paths", fromlist=["adb_dir"]).adb_dir() or "")


def _mobilecli_env():
    """mobilecli 找 adb 靠 PATH（或 ANDROID_HOME）；守护进程必须继承带 adb 的环境，
    否则它能启动但看不到任何手机，全部 MCP 调用报 tool_error（2026-11-04 实测）。"""
    env = dict(os.environ)
    env["PATH"] = MOBILECLI_ADB_DIR + ";" + env.get("PATH", "")
    return env


@app.get("/api/devices/mcp-health")
async def api_mcp_health(repair: bool = True):
    """查 MCP 守护进程 + 可见手机数；挂了就按正确环境拉起。
    所有发布通道报“未知错误”(tool_error) 时先调这个看一眼。"""
    import subprocess as sp
    cli = next((p for p in MOBILECLI_CANDIDATES if os.path.exists(p)), None)
    if not cli:
        return JSONResponse({"ok": False, "error": "mobilecli missing"})
    loop = asyncio.get_running_loop()

    def _run(args, timeout):
        try:
            r = sp.run([cli] + args, capture_output=True, text=True,
                       timeout=timeout, env=_mobilecli_env())
            return r.returncode, (r.stdout or "")[:2000]
        except Exception as e:  # noqa: BLE001
            return -1, "error: %s" % e

    _, status = await loop.run_in_executor(None, _run, ["daemon", "status"], 15)
    running = '"running":true' in status.replace(" ", "")
    repaired = False
    if not running and repair:
        try:
            sp.Popen([cli, "daemon", "start"], env=_mobilecli_env(),
                     stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                     creationflags=getattr(sp, "DETACHED_PROCESS", 0)
                     | getattr(sp, "CREATE_NEW_PROCESS_GROUP", 0))
            repaired = True
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "running": False, "error": "daemon start failed: %s" % e})
        await asyncio.sleep(8)
        _, status = await loop.run_in_executor(None, _run, ["daemon", "status"], 15)
        running = '"running":true' in status.replace(" ", "")
    _, devs = await loop.run_in_executor(None, _run, ["devices"], 60)
    import re as _re
    n_android = len(_re.findall(r'"platform"\s*:\s*"android"', devs))
    return JSONResponse({"ok": running and n_android > 0, "running": running,
                         "repaired": repaired, "android_devices": n_android,
                         "status_raw": status[:300]})


class ReorderRequest(BaseModel):
    order: list = []          # [{serial, index}, ...]



# ===========================================================================
# OTG / USB 转 TCP（对照汇城 otg_scanning / otg_all_scanning / usb_to_tcp）
# ===========================================================================
# 汇城原话： "connect ====> OTG IP: " + "usb_to_tcp"
# 原理：手机 USB 连电脑 → 手机开「USB 网络共享」→ 电脑多一块网卡（常见 192.168.42.x）
#       → 扫描这段网段找手机 IP → adb connect IP:5555 → 变成无线调试
# 这样拔掉数据线也能控制（前提是同一网络）。

OTG_ADB = os.environ.get("ADB_PATH") or __import__("fishfleet.paths", fromlist=["adb_path"]).adb_path()


def _local_ipv4_list() -> list:
    """列出本机所有 IPv4。

    中文系统的 ipconfig 输出是「IPv4 地址 . . . : 192.168.x.x」，
    「IPv4」后面的分隔符不一定含冒号，所以正则要放宽；
    另外用 socket 的 UDP connect 技巧兜底拿主网卡 IP。
    """
    ips = []

    # 方法1：ipconfig（编码可能不是 UTF-8，逐个试）
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            r = subprocess.run(["ipconfig"], capture_output=True, text=True,
                               timeout=15, encoding=enc, errors="ignore")
            out = r.stdout or ""
            # 匹配「IPv4」后面任意字符到 IP
            for m in re.finditer(r"IPv4[^0-9]{0,20}([0-9]{1,3}(?:\.[0-9]{1,3}){3})", out):
                ip = m.group(1)
                if not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
            if ips:
                break
        except Exception:
            continue

    # 方法2：socket 兜底（拿默认出口网卡 IP）
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        finally:
            s.close()
    except Exception:
        pass

    return ips


def _otg_candidates() -> list:
    """生成 OTG 候选网段。

    常见 USB 网络共享网段：
      192.168.42.0/24  (Android 默认 rndis)
      192.168.137.0/24 (Windows 热点)
      192.168.49.0/24  (Android tethering 变体)
    另外把本机实际网段也加进来。
    """
    nets = set()
    for ip in _local_ipv4_list():
        if ip.startswith("127."):
            continue
        nets.add(".".join(ip.split(".")[:3]))
    for fixed in ("192.168.42", "192.168.137", "192.168.49", "10.42.0"):
        nets.add(fixed)
    return sorted(nets)


def _scan_net(net_prefix: str, ports=(5555,), timeout: float = 0.12) -> list:
    """扫一个 /24 网段里开了 5555 的主机。"""
    import socket
    found = []
    for host in range(1, 255):
        ip = f"{net_prefix}.{host}"
        for port in ports:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            try:
                if s.connect_ex((ip, port)) == 0:
                    found.append({"ip": ip, "port": port})
            finally:
                s.close()
    return found


class OtgScanRequest(BaseModel):
    net: str = ""        # 指定网段前缀，如 "192.168.42"；空则自动
    port: int = 5555


@app.get("/api/otg/info")
async def api_otg_info() -> JSONResponse:
    """本机网络环境（OTG 面板顶部显示）。"""
    return JSONResponse({
        "ok": True,
        "local_ips": _local_ipv4_list(),
        "candidates": _otg_candidates(),
        "hint": "手机 USB 连电脑 → 设置里打开「USB 网络共享」→ 点扫描",
    })


@app.post("/api/otg/scan")
async def api_otg_scan(req: OtgScanRequest) -> JSONResponse:
    """扫描 OTG 网段（对照汇城 otg_scanning）。"""
    nets = [req.net] if req.net else _otg_candidates()
    hits = []
    for n in nets:
        hits.extend(_scan_net(n, ports=(req.port,)))
    return JSONResponse({"ok": True, "nets": nets, "found": hits, "count": len(hits)})


class OtgConnectRequest(BaseModel):
    ip: str
    port: int = 5555


@app.post("/api/otg/connect")
async def api_otg_connect(req: OtgConnectRequest) -> JSONResponse:
    """usb_to_tcp：把 OTG 找到的 IP 接进来（等价 adb connect）。"""
    import ipaddress
    try:
        ip = ipaddress.IPv4Address(req.ip)
        if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local or ip.is_reserved or not 1 <= req.port <= 65535:
            raise ValueError('不允许的地址或端口')
    except ValueError:
        return JSONResponse({'ok': False, 'error': 'IP 地址或端口无效'}, status_code=400)
    addr = f"{ip}:{req.port}"
    try:
        code, stdout, stderr = await asyncio.to_thread(get_manager().adb._run, ['connect', addr], 25)
        out = (stdout + stderr).strip()
        ok = code == 0 and bool(re.search(r'^(?:already connected to|connected to)\s+' + re.escape(addr) + r'\s*$', out, re.I))
        # 立刻刷新设备列表
        if ok:
            try:
                get_manager().refresh()
            except Exception:
                pass
        return JSONResponse({"ok": ok, "addr": addr, "message": out})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/reorder")
async def api_devices_reorder(req: ReorderRequest) -> JSONResponse:
    """保存设备显示顺序（拖拽后调用），并持久化到磁盘。"""
    order = req.order or []
    if not order:
        return JSONResponse({"ok": False, "error": "设备顺序不能为空"}, status_code=400)
    serials, indices = set(), set()
    for item in order:
        s, i = item.get('serial'), item.get('index')
        if not isinstance(s, str) or not s or len(s) > 256 or type(i) is not int or not 1 <= i <= 99999 or s in serials or i in indices:
            return JSONResponse({"ok": False, "error": "设备序列号或编号无效、重复"}, status_code=400)
        serials.add(s); indices.add(i)
    try:
        # 现有设置存储是前端 loadOrder 的读取来源。
        from ..core.settings import get_settings
        await asyncio.to_thread(get_settings().update, 'device_order', {'list': order})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"设备顺序保存失败: {type(exc).__name__}"}, status_code=500)
    return JSONResponse({"ok": True, "applied": len(order), "scope": "settings"})


@app.post("/api/devices/{serial}/reboot")
async def api_reboot(serial: str) -> JSONResponse:
    import subprocess as sp
    adb = _adb_path()
    try:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: sp.run([adb, "-s", serial, "reboot"],
                           capture_output=True, text=True, timeout=20),
        )
        return JSONResponse({"ok": True})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/shell")
async def api_shell(serial: str, req: dict) -> JSONResponse:
    cmd = (req or {}).get("command", "")
    if not cmd:
        return JSONResponse({"ok": False, "error": "empty"})
    try:
        rc, out, err = await _shell(serial, cmd, timeout=60)
        return JSONResponse({"ok": rc == 0, "rc": rc, "stdout": out[-4000:], "stderr": err[-2000:]})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/devices/{serial}/clipboard")
async def api_clipboard_get(serial: str) -> JSONResponse:
    try:
        def read_clipboard():
            import uiautomator2 as u
            return u.connect(serial).clipboard
        text=await asyncio.to_thread(read_clipboard)
        if not isinstance(text,str):return JSONResponse({'ok':False,'error':'当前设备未返回剪贴板文本，可能受安卓后台读取限制'})
        return JSONResponse({'ok':True,'text':text,'source':'uiautomator'})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/keep-screen")
async def api_keep_screen(serial: str, req: dict) -> JSONResponse:
    """屏幕常亮（对齐汇城 screenLight：写 screen_off_timeout 并读回确认）。"""
    enabled = bool((req or {}).get("enabled"))
    value = "2147483647" if enabled else "60000"
    try:
        await _shell(serial, f"settings put system screen_off_timeout {value}", timeout=15)
        _rc, cur, _err = await _shell(serial, "settings get system screen_off_timeout", timeout=15)
        ok = (cur or "").strip() == value
        return JSONResponse({"ok": ok, "value": (cur or "").strip(),
                             **({} if ok else {"error": "设备未接受屏幕常亮设置"})})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/rotate")
async def api_rotate(serial: str, req: dict) -> JSONResponse:
    """旋转屏幕（0=0° 1=90° 2=180° 3=270°）。"""
    rot = int((req or {}).get("rotation", 0))
    try:
        await _shell(serial, f"settings put system user_rotation {rot}", timeout=15)
        await _shell(serial, "settings put system accelerometer_rotation 0", timeout=15)
        return JSONResponse({"ok": True, "rotation": rot})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.get("/api/devices/{serial}/imes")
async def api_imes(serial: str) -> JSONResponse:
    """列出输入法，标记当前默认。"""
    try:
        rc, out, err = await _shell(serial, "ime list -a -s", timeout=20)
        rc2, cur, _ = await _shell(serial, "settings get secure default_input_method", timeout=15)
        current = (cur or "").strip()
        imes = []
        for ln in (out or "").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("com.android") and "/" not in ln:
                continue
            if "/" in ln:
                package=ln.split('/',1)[0]
                known={'com.android.xwkeyboard':'熊猫键盘（汇城）','com.samsung.android.honeyboard':'三星键盘','com.sec.android.inputmethod':'三星键盘','com.google.android.inputmethod.latin':'Gboard','com.google.android.inputmethod.pinyin':'Google拼音输入法','com.baidu.input':'百度输入法','com.sohu.inputmethod.sogou':'搜狗输入法','com.iflytek.inputmethod':'讯飞输入法','com.github.uiautomator':'自动化输入法','com.android.adbkeyboard':'ADB输入法','com.android.inputmethod.latin':'Android键盘'}
                label=known.get(package)
                if not label:
                    try:
                        metadata=await asyncio.to_thread(_read_device_app_metadata,_adb_path(),serial,package)
                        if metadata:label=metadata[0]
                    except Exception:pass
                imes.append({"id": ln, "name":label or ('未识别输入法（'+package+'）'),"current": ln == current})
        return JSONResponse({"ok": True, "current": current, "imes": imes})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/ime")
async def api_ime_set(serial: str, req: dict) -> JSONResponse:
    ime = (req or {}).get("ime", "")
    if not ime:
        return JSONResponse({"ok": False, "error": "empty"})
    try:
        await _shell(serial, f"ime enable {ime}", timeout=15)
        await _shell(serial, f"ime set {ime}", timeout=15)
        return JSONResponse({"ok": True, "ime": ime})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


_ONEKEY_CACHE = {"data": None, "at": 0}
def _onekey_actions():
    import json as _json
    import time as _t
    if _ONEKEY_CACHE["data"] and _t.time() - _ONEKEY_CACHE["at"] < 60:
        return _ONEKEY_CACHE["data"]
    base = os.path.join(os.path.dirname(__file__), "..", "ui_library", "phone20", "actions.json")
    base = os.path.abspath(base)
    try:
        data = _json.loads(open(base, encoding="utf-8").read())
        acts = {a["key"]: a for a in data.get("actions", [])}
    except Exception:
        acts = {}
    _ONEKEY_CACHE["data"] = acts
    _ONEKEY_CACHE["at"] = _t.time()
    return acts


@app.get("/api/devices/onekey/actions")
async def api_onekey_actions() -> JSONResponse:
    acts = _onekey_actions()
    out = [{"key": k, "name": v.get("name", k), "group": v.get("group", ""),
            "risk": v.get("risk", "low"), "note": v.get("note", "")} for k, v in acts.items()]
    return JSONResponse({"ok": True, "actions": out})


@app.post("/api/devices/onekey/run")
async def api_onekey_run(req: dict) -> JSONResponse:
    """手机一键操作：上方选手机，下方按分组执行预制动作。danger 需 confirm=true。"""
    serials = (req or {}).get("serials") or []
    key = ((req or {}).get("key") or "").strip()
    confirm = bool((req or {}).get("confirm", False))
    param = str((req or {}).get("param") or "")
    if not serials or not key:
        return JSONResponse({"ok": False, "error": "缺少手机或动作"})
    acts = _onekey_actions()
    a = acts.get(key)
    if not a:
        return JSONResponse({"ok": False, "error": "未知动作"})
    if a.get("risk") in ("danger", "high") and not confirm:
        return JSONResponse({"ok": False, "error": "高危动作需二次确认", "need_confirm": True})
    cmd = (a.get("cmd") or "").strip()
    if "<" in cmd and ">" in cmd and not param:
        return JSONResponse({"ok": False, "error": "该动作需要参数"})
    if param:
        # 仅替换第一个 <...> 占位，避免注入整条拼接
        import re as _re
        cmd = _re.sub(r"<[^>]+>", param, cmd, count=1)
    results = {}
    for s in serials:
        try:
            rc, out, err = await _shell(s, cmd, timeout=30)
            probe = (a.get("probe") or "").split("#")[0].strip()
            verify = ""
            if probe and probe not in ("-", ""):
                try:
                    _, verify, _ = await _shell(s, probe, timeout=15)
                    verify = (verify or "").strip()[:200]
                except Exception:
                    verify = ""
            results[s] = {"ok": rc == 0, "output": ((out or "") + (err or ""))[:500], "verify": verify}
            STATE.log(f"{s} 一键 {a.get('name', key)} -> {results[s]['ok']}")
        except Exception as e:  # noqa: BLE001
            results[s] = {"ok": False, "error": str(e)[:300]}
    return JSONResponse({"ok": True, "action": key, "results": results})


@app.post("/api/devices/selfcheck")
async def api_devices_selfcheck(req: dict) -> JSONResponse:
    """一键自检：核对各机真实坐标，按机型修复 profile。

    body: {"serials": ["…"], "navigate": bool, "hard_nav": bool, "apply_fix": bool}
    """
    import asyncio as _aio
    from . import ui_selfcheck as _sc
    req = req or {}
    serials = req.get("serials") or []
    navigate = bool(req.get("navigate", True))
    # hard_nav 默认关：force-stop 会把小红书这类应用带回登录页，动登录态。
    # 只有用户明确勾"强制重启App"时才用。
    hard_nav = bool(req.get("hard_nav", False))
    all_apps = bool(req.get("all_apps", False))
    apply_fix = bool(req.get("apply_fix", True))
    if not serials:
        return JSONResponse({"ok": False, "error": "请先勾选手机"})
    # 有任务在跑的手机不动，避免打断执行
    busy = set()
    try:
        from . import execution as _ex
        with _ex.LOCK:
            for t in _ex.TASKS.values():
                if t.get("status") in ("queued", "running", "executing", "observing",
                                       "thinking", "checking", "awaiting_approval",
                                       "stopping"):
                    busy.add(t.get("serial"))
    except Exception:
        pass

    def _run(s):
        if s in busy:
            return {"ok": False, "serial": s, "error": "该手机正在执行任务，跳过自检"}
        try:
            if all_apps:
                return _sc.selfcheck_all_apps(s, apply_fix=apply_fix, hard_nav=hard_nav)
            return _sc.selfcheck(s, apply_fix=apply_fix, navigate=navigate, hard_nav=hard_nav)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "serial": s, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

    results = await _aio.gather(*[_aio.to_thread(_run, s) for s in serials])
    return JSONResponse({"ok": True, "results": list(results)})


@app.post("/api/devices/{serial}/selfcheck")
async def api_device_selfcheck(serial: str, req: dict) -> JSONResponse:
    """单机自检（同上，按 serial 指定）。"""
    import asyncio as _aio
    from . import ui_selfcheck as _sc
    req = req or {}
    try:
        rep = await _aio.to_thread(
            _sc.selfcheck, serial,
            apply_fix=bool(req.get("apply_fix", True)),
            navigate=bool(req.get("navigate", True)),
            hard_nav=bool(req.get("hard_nav", False)))
        return JSONResponse({"ok": True, "result": rep})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})


@app.post("/api/devices/{serial}/onboard")
async def api_device_onboard(serial: str, req: dict | None = None) -> JSONResponse:
    """新机接入模拟测试：几何建档 → 全应用自检 → 各平台发布/采集模拟 →
    视觉确认偏移 → 自动修正 → 报告。LLM 只在这里用日常任务不用。

    有任务在跑的手机拒绝避免打断执行；编排在后台线程跑立即返回 oid，
    用 GET /api/devices/onboard/{oid} 看进度用 POST /api/devices/onboard/{oid}/stop 停。
    """
    from . import onboard as _ob
    try:
        _plats = (req or {}).get("platforms") or []
        if not isinstance(_plats, list):
            _plats = [str(_plats)]
        oid = _ob.start_onboard(serial, platforms=_plats)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
    return JSONResponse({"ok": True, "onboard_id": oid})


@app.get("/api/devices/onboard/{oid}")
async def api_onboard_status(oid: str) -> JSONResponse:
    from . import onboard as _ob
    try:
        return JSONResponse({"ok": True, **_ob.onboard_status(oid)})
    except KeyError:
        return JSONResponse({"ok": False, "error": "任务不存在"})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})


@app.post("/api/devices/onboard/{oid}/stop")
async def api_onboard_stop(oid: str) -> JSONResponse:
    from . import onboard as _ob
    try:
        return JSONResponse({**_ob.onboard_stop(oid)})
    except KeyError:
        return JSONResponse({"ok": False, "error": "任务不存在"})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})


@app.post("/api/devices/onboard/{oid}/promote")
async def api_onboard_promote(oid: str, req: dict | None = None) -> JSONResponse:
    """自愈修正的推广决定：scope=all 覆盖所有手机，scope=this 只留本机。"""
    from . import onboard as _ob
    scope = str((req or {}).get("scope") or "this").strip().lower()
    if scope not in ("all", "this"):
        return JSONResponse({"ok": False, "error": "scope 只能是 all 或 this"})
    try:
        return JSONResponse({**_ob.promote(oid, scope)})
    except KeyError:
        return JSONResponse({"ok": False, "error": "任务不存在"})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})


@app.post("/api/devices/{serial}/auto-swipe")
async def api_auto_swipe(serial: str, req: dict) -> JSONResponse:
    """自动滑屏：start/stop。"""
    action = (req or {}).get("action", "start")
    interval = float((req or {}).get("interval", 3.0))
    duration = float((req or {}).get('duration', .3))
    total = float((req or {}).get('total', 60))
    direction=(req or {}).get('direction','up')
    if action not in ('start','stop') or direction not in ('up','down','left','right') or not (.1<=interval<=3600 and .1<=duration<=5 and 1<=total<=86400):
        raise HTTPException(400,'滑屏方向或时间参数无效')
    w = _streams.get(serial)
    if not w or not w.sess:
        return JSONResponse({"ok": False, "reason": "no-stream"})
    sess = w.sess
    if action == "start":
        if getattr(w, "_auto_swipe", None):
            return JSONResponse({"ok": True, "already": True})

        async def _loop():
            deadline=time.monotonic()+total
            try:
                while getattr(w, '_auto_swipe', False) and time.monotonic()<deadline:
                    if not sess.running:break
                    width,height=sess.real_width,sess.real_height
                    points={'up':(.5,.78,.5,.25),'down':(.5,.25,.5,.78),'left':(.78,.5,.25,.5),'right':(.25,.5,.78,.5)}[direction]
                    x1,y1,x2,y2=[int(v*(width if i%2==0 else height)) for i,v in enumerate(points)]
                    ok=await asyncio.to_thread(sess.swipe,x1,y1,x2,y2,duration)
                    if not ok:
                        STATE.log(f'{serial} 自动滑屏控制发送失败，已停止','error');break
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                STATE.log(f'{serial} 自动滑屏异常：{type(exc).__name__}，已停止','error')
            finally:w._auto_swipe=False

        w._auto_swipe = True
        w._auto_swipe_task=asyncio.create_task(_loop())
        return JSONResponse({"ok": True, "running": True})
    w._auto_swipe = False
    pending=getattr(w,'_auto_swipe_task',None)
    if pending and not pending.done():
        # Do not cancel a to_thread swipe midway: await its bounded duration first.
        await asyncio.wait({pending},timeout=duration+1)
    return JSONResponse({'ok':True,'running':bool(pending and not pending.done()),'stopping':bool(pending and not pending.done())})


@app.post("/api/devices/{serial}/apk")
async def api_install_apk(serial: str, req: dict) -> JSONResponse:
    """安装本机 APK 到设备。"""
    import subprocess as sp
    path = (req or {}).get("path", "")
    if not path or not os.path.exists(path):
        return JSONResponse({"ok": False, "error": "file not found"})
    adb = _adb_path()
    try:
        r = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: sp.run([adb, "-s", serial, "install", "-r", path],
                           capture_output=True, text=True, timeout=300),
        )
        return JSONResponse({"ok": "Success" in r.stdout, "output": r.stdout[-500:]})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/push")
async def api_push_file(serial: str, req: dict) -> JSONResponse:
    """推文件到设备。"""
    import subprocess as sp
    local = (req or {}).get("local", "")
    remote = (req or {}).get("remote", "/sdcard/Download/")
    if not local or not os.path.exists(local):
        return JSONResponse({"ok": False, "error": "file not found"})
    adb = _adb_path()
    try:
        r = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: sp.run([adb, "-s", serial, "push", local, remote],
                           capture_output=True, text=True, timeout=600),
        )
        return JSONResponse({"ok": r.returncode == 0, "output": r.stdout[-500:]})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/pull")
async def api_pull_file(serial: str, req: dict) -> JSONResponse:
    """从设备拉文件。"""
    import subprocess as sp
    remote = (req or {}).get("remote", "")
    local = (req or {}).get("local", "./data/pull/")
    if not remote:
        return JSONResponse({"ok": False, "error": "remote required"})
    os.makedirs(local, exist_ok=True)
    adb = _adb_path()
    try:
        r = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: sp.run([adb, "-s", serial, "pull", remote, local],
                           capture_output=True, text=True, timeout=600),
        )
        return JSONResponse({"ok": r.returncode == 0, "output": r.stdout[-500:], "local": local})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


DEVICE_LABELS_PATH = Path(__file__).resolve().parents[2] / "data" / "device_labels.json"

def _device_labels():
    if not DEVICE_LABELS_PATH.exists():
        return {}
    return json.loads(DEVICE_LABELS_PATH.read_text(encoding="utf-8"))

_labels_lock = threading.RLock()

def _write_device_labels(labels):
    DEVICE_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DEVICE_LABELS_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(DEVICE_LABELS_PATH)

def _ensure_device_numbers(devs):
    from ..core.device_numbers import resolve_numbers
    from ..core.settings import get_settings
    with _labels_lock:
        labels = _device_labels()
        legacy = {x['serial']: x for x in get_settings().get('device_order', 'list', []) if isinstance(x, dict) and 'serial' in x}
        # Reserve offline saved devices too, so reconnecting cannot duplicate a number.
        serials = list(dict.fromkeys(list(labels) + list(legacy) + [getattr(d, 'identity', '') or d.serial for d in devs]))
        numbers = resolve_numbers(serials, labels, legacy)
        changed = any(labels.get(s, {}).get('index') != n for s,n in numbers.items())
        if changed:
            for s,n in numbers.items(): labels.setdefault(s, {})['index'] = n
            _write_device_labels(labels)
        return labels, numbers

def _save_device_label(serial, key, value):
    with _labels_lock:
        labels = _device_labels()
        labels.setdefault(serial, {})[key] = value
        _write_device_labels(labels)

@app.post("/api/devices/{serial}/index")
async def api_set_index(serial: str, req: dict) -> JSONResponse:
    idx = (req or {}).get("index")
    if idx is None:
        return JSONResponse({"ok": False, "error": "index required"})
    dev = get_manager().get(serial)
    if dev is None:
        return JSONResponse({"ok": False, "error": "device not found"})
    try:
        value = int(idx)
        if not 1 <= value <= 99999:
            return JSONResponse({"ok": False, "error": "编号范围为1–99999"})
        with _labels_lock:
            labels, numbers = _ensure_device_numbers([dev])
            identity = dev.identity or serial
            owner = next((s for s,n in numbers.items() if n == value and s != identity), None)
            if owner:
                return JSONResponse({'ok': False, 'error': f'编号 {value} 已被设备 {owner} 使用，请选择空闲编号'}, status_code=409)
            _save_device_label(identity, "index", value)
            dev.index = value
        return JSONResponse({"ok": True, "index": dev.index})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/name")
async def api_set_name(serial: str, req: dict) -> JSONResponse:
    name = (req or {}).get("name", "")
    dev = get_manager().get(serial)
    if dev is None:
        return JSONResponse({"ok": False, "error": "device not found"})
    try:
        name = str(name).strip()
        if not name or len(name) > 20:
            return JSONResponse({"ok": False, "error": "名称须为1–20个字符"})
        _save_device_label(dev.identity or serial, "name", name)
        return JSONResponse({"ok": True, "name": name})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/apk-upload")
async def api_apk_upload(serial: str, file: UploadFile = File(...)) -> JSONResponse:
    import subprocess as sp
    import tempfile
    data = await file.read()
    tmp = os.path.join(tempfile.gettempdir(), file.filename or "upload.apk")
    with open(tmp, "wb") as f:
        f.write(data)
    adb = _adb_path()
    try:
        r = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: sp.run([adb, "-s", serial, "install", "-r", tmp],
                           capture_output=True, text=True, timeout=600),
        )
        return JSONResponse({"ok": "Success" in r.stdout, "output": r.stdout[-500:]})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})
    finally:
        try: os.remove(tmp)
        except OSError: pass


@app.post("/api/devices/{serial}/push-upload")
async def api_push_upload(serial: str, file: UploadFile = File(...),
                          remote: str = Form("/sdcard/Download/")) -> JSONResponse:
    import subprocess as sp
    import tempfile
    import uuid
    from urllib.parse import quote
    destinations={'/sdcard/Download/','/sdcard/DCIM/FishFleet/','/sdcard/Movies/FishFleet/','/sdcard/Documents/'}
    if remote not in destinations:raise HTTPException(400,'请选择受支持的导入位置')
    # Unique staging and remote names prevent overwriting another upload or existing phone file.
    name=Path((file.filename or 'upload.bin').replace('\\','/')).name
    if not name or any(c in name for c in '\r\n\x00'):raise HTTPException(400,'文件名无效')
    token=uuid.uuid4().hex;target=remote+token[:8]+'_'+name
    fd,tmp=tempfile.mkstemp(prefix='fishfleet_upload_');size=0
    try:
        with os.fdopen(fd,'wb') as out:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>1024**3:raise HTTPException(413,'单文件最大1GiB')
                out.write(chunk)
        adb=_adb_path()
        created=await asyncio.to_thread(sp.run,[adb,'-s',serial,'shell','mkdir','-p',remote],capture_output=True,text=True,timeout=20)
        if created.returncode:return JSONResponse({'ok':False,'error':'无法创建目标目录'})
        r=await asyncio.to_thread(sp.run,[adb,'-s',serial,'push',tmp,target],capture_output=True,text=True,timeout=900)
        scanned=None
        if r.returncode==0 and remote in ('/sdcard/DCIM/FishFleet/','/sdcard/Movies/FishFleet/'):
            scan=await asyncio.to_thread(sp.run,[adb,'-s',serial,'shell','am','broadcast','-a','android.intent.action.MEDIA_SCANNER_SCAN_FILE','-d','file://'+quote(target,safe='/')],capture_output=True,text=True,timeout=20)
            scanned=scan.returncode==0
        return JSONResponse({'ok':r.returncode==0,'remote':target,'media_scan_requested':scanned,'output':r.stdout[-500:]})
    finally:
        await file.close()
        try:os.remove(tmp)
        except OSError:pass


@app.post("/api/devices/{serial}/record/start")
async def api_record_start(serial: str) -> JSONResponse:
    """开始录制动作：记录后续的 tap/swipe/text 事件。"""
    from .menu_recording import start
    return JSONResponse(start(serial))


@app.get('/api/devices/{serial}/record/status')
async def api_record_status(serial:str):
    from .menu_recording import status
    return status(serial)


@app.post('/api/devices/{serial}/record/stop')
async def api_record_stop(serial:str):
    from .menu_recording import stop
    return stop(serial)


@app.post("/api/devices/{serial}/record/play")
async def api_record_play(serial: str, req: dict) -> JSONResponse:
    """回放录制的动作。"""
    from .execution import start,StartRequest
    script_id=(req or {}).get('script_id','')
    if not script_id:raise HTTPException(400,'请选择录制的动作')
    result=await asyncio.to_thread(start,StartRequest(serial=serial,goal='执行所选录制动作',script_id=script_id))
    return JSONResponse({'ok':True,'task_id':result['id'],'status':result['status']})


@app.post("/api/devices/{serial}/task/run")
async def api_task_run(serial: str, req: dict) -> JSONResponse:
    """在指定设备上执行任务。"""
    task = (req or {}).get("task", "")
    try:
        from ..core.actions import Actions
        st = get_state()
        dev = st.get_device(serial)
        if not dev:
            return JSONResponse({"ok": False, "error": "device not found"})
        acts = Actions(dev)
        if task:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, acts.run_script, task)
        return JSONResponse({"ok": True, "task": task})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


@app.post("/api/devices/{serial}/task/stop")
async def api_task_stop(serial: str) -> JSONResponse:
    try:
        st = get_state()
        st.stop_device_task(serial)
        return JSONResponse({"ok": True})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)})


# ==================== AI / LLM 配置 ====================

LLM_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "llm.json")


def _load_llm() -> dict:
    try:
        with open(LLM_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_llm(cfg: dict) -> None:
    os.makedirs(os.path.dirname(LLM_CONFIG_PATH), exist_ok=True)
    with open(LLM_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


@app.get("/api/llm/config")
async def api_llm_get() -> JSONResponse:
    """读取两个 LLM 的配置（密钥脱敏）。"""
    cfg = _load_llm()

    def mask(v: str) -> str:
        if not v:
            return ""
        if len(v) <= 12:
            return "*" * len(v)
        return v[:6] + "*" * 8 + v[-4:]

    out = {}
    for k in ("auto", "video"):
        c = cfg.get(k, {})
        out[k] = {
            "name": c.get("name", k),
            "url": c.get("url", ""),
            "model": c.get("model", ""),
            "key_masked": mask(c.get("key", "")),
            "has_key": bool(c.get("key")),
            "role": c.get("role", ""),
        }
    out["mcp"] = cfg.get("mcp", {})
    out["proxy"] = public_proxy(cfg)
    return JSONResponse({"ok": True, "config": out})


@app.get("/api/llm/proxy/detect")
async def api_llm_proxy_detect() -> JSONResponse:
    """只读检测本机静态代理，不保存、不启用、不发网络请求。"""
    return JSONResponse({"ok": True, **detect_proxy()})


@app.post("/api/llm/config")
async def api_llm_set(req: dict) -> JSONResponse:
    """保存 LLM 配置。代理先验证，key 为空时保留原值。"""
    try:
        proxy = validate_proxy(req["proxy"]) if "proxy" in req else None
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    cfg = _load_llm()
    if proxy is not None:
        cfg["proxy"] = proxy
    for k in ("auto", "video"):
        if k in (req or {}):
            cur = cfg.get(k, {})
            new = req[k]
            cur["name"] = new.get("name", cur.get("name", k))
            cur["url"] = new.get("url", cur.get("url", ""))
            cur["model"] = new.get("model", cur.get("model", ""))
            if new.get("key"):
                cur["key"] = new["key"]
            cfg[k] = cur
    if "mcp" in (req or {}):
        cfg["mcp"] = req["mcp"]
    _save_llm(cfg)
    return JSONResponse({"ok": True})


@app.post("/api/llm/test")
async def api_llm_test(req: dict) -> JSONResponse:
    """测试 LLM 连通性（发一个极小的请求）。

    2026-11-06 根本性改造（主人反馈「配置 API 时显示配置不完整」）：
        原实现**只读已保存的 llm.json**，于是用户填完 Key 直接点「测试」，
        读到的是空文件 → 报「配置不完整」。
        这是设计缺陷：测试理应测**用户当前填的东西**，而不是逼人先保存。

        现在：请求体里可以带 url / model / key（未保存的当前值），
        这些值**优先于**已保存的配置。前端也改成测试时传当前输入。
        缺失时返回**明确到字段**的提示，而不是含糊的「配置不完整」。
    """
    import urllib.request
    kind = (req or {}).get("kind", "auto")
    try:
        validate_kind(kind)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    wholeconfig = _load_llm()
    cfg = dict(wholeconfig.get(kind, {}) or {})

    # 用「当前输入」覆盖已保存值（空值不覆盖，保持"留空=不改"的约定）
    for field in ("url", "model", "key"):
        val = str((req or {}).get(field) or "").strip()
        if val:
            cfg[field] = val

    url = str(cfg.get("url") or "").strip().rstrip("/")
    key = str(cfg.get("key") or "").strip()
    model = str(cfg.get("model") or "").strip()

    # 缺什么就说缺什么，别只说「配置不完整」
    missing = []
    if not url:
        missing.append("接口地址（URL）")
    if not key:
        missing.append("API 密钥（Key）")
    if not model:
        missing.append("模型名（Model）")
    if missing:
        label = "自动化 LLM" if kind == "auto" else "视频 LLM"
        return JSONResponse({
            "ok": False,
            "error": "%s 还缺：%s" % (label, "、".join(missing)),
            "missing": missing,
            "kind": kind,
        })

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8,
        "stream": False,
    }).encode()
    import uuid as _uuid
    req2 = urllib.request.Request(
        url + "/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "x-opencode-session": str(_uuid.uuid4()),
            "User-Agent": "FishFleet/1.0",
        },
        method="POST",
    )
    try:
        with open_api(req2, kind, wholeconfig, timeout=30) as r:
            body = r.read().decode("utf-8", "ignore")
        return JSONResponse({"ok": True, "sample": body[:300], "model": model})
    except Exception as e:  # noqa: BLE001
        # 把真实原因带出来（原来是固定文案，排查全靠猜）
        msg = str(e)[:400] or type(e).__name__
        return JSONResponse({
            "ok": False,
            "error": "连接失败：%s" % msg,
            "kind": kind,
            "url": url,
            "model": model,
        })


# ==================== LLM 画面判断（校验层） ====================

class JudgeRequest(BaseModel):
    expect: str = ""              # 期望看到什么，例如"抖音首页"
    serials: list[str] = []       # 要判断的设备
    images: dict = {}             # serial -> base64 PNG（可选，不给就自己截）
    model_kind: str = "auto"      # auto | video


def _llm_chat(kind: str, messages: list, max_tokens: int = 512) -> str:
    """调 LLM（OpenAI 兼容）。

    ★ 两个必须的细节（实测出来的）：
      1. opencode.ai 前面有 Cloudflare，缺少 x-opencode-session 会返回
         400 MissingSessionID；带了才通。
      2. 必须设 User-Agent，否则 Cloudflare 直接 403 (error code: 1010)。
    """
    import urllib.request
    import uuid

    validate_kind(kind)
    wholeconfig = _load_llm()
    cfg = wholeconfig.get(kind, {})
    url = (cfg.get("url") or "").rstrip("/")
    key = cfg.get("key", "")
    model = cfg.get("model", "")
    if not url or not key:
        raise RuntimeError("LLM 未配置")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "x-opencode-session": str(uuid.uuid4()),
        "User-Agent": "FishFleet/1.0 (+https://localhost)",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url + "/chat/completions", data=payload,
                                 headers=headers, method="POST")
    with open_api(req, kind, wholeconfig, timeout=120) as r:
        body = json.loads(r.read().decode("utf-8", "ignore"))
    return body.get("choices", [{}])[0].get("message", {}).get("content", "")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=10000)

    @validator("content")
    def nonblank_content(cls, value):
        if not value.strip():
            raise ValueError("消息内容不能为空")
        return value


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=50)


@app.post("/api/llm/chat")
async def api_llm_chat(req: ChatRequest):
    """仅提供文本对话和脚本建议；不调用执行或设备工具。"""
    messages = [{"role": "system", "content": (
        "你是 FishFleet 对话助手，只提供对话及脚本建议，不执行 shell 命令或设备动作。"
        "脚本仅供用户自行审核，不能声称已经执行。"
    )}] + [{"role": m.role, "content": m.content} for m in req.messages]
    try:
        reply = await asyncio.get_running_loop().run_in_executor(
            None, _llm_chat, "auto", messages, 1024
        )
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("empty response")
        return {"ok": True, "reply": reply}
    except Exception:
        # 上游异常可能包含配置、URL 或凭据，不能回传给浏览器。
        return JSONResponse(status_code=502, content={
            "ok": False, "error": "对话服务不可用，请检查配置后重试"
        })


def _grab_b64(serial: str) -> str:
    import base64
    import subprocess as sp
    adb = _adb_path()
    r = sp.run([adb, "-s", serial, "exec-out", "screencap", "-p"],
               capture_output=True, timeout=30)
    return base64.b64encode(r.stdout).decode("ascii") if r.stdout else ""


@app.post("/api/llm/judge")
async def api_llm_judge(req: JudgeRequest) -> JSONResponse:
    """用自动化 LLM 判断每台手机是否到达预期界面。

    ★ 为什么不用图片：
      deepseek-v4.1-flash 的 capabilities 只有 text，不支持视觉。
      所以走「UI dump 提取屏幕文字 → 文字 LLM 判断」，更便宜也更准：
      能直接看到 App 名、按钮文案、输入框提示，判断依据比像素更明确。
    """
    import re as _re
    import subprocess as sp

    serials = req.serials or []
    if not serials:
        return JSONResponse({"ok": False, "error": "serials 为空"})

    loop = asyncio.get_running_loop()
    adb = _adb_path()

    def dump_ui(serial: str) -> str:
        """抓 UI 树并抽出所有可见文字。"""
        try:
            r = sp.run([adb, "-s", serial, "shell", "uiautomator dump /dev/tty"],
                       capture_output=True, text=True, timeout=25)
            xml = r.stdout or ""
            if "<hierarchy" not in xml:
                # 兜底：写到文件再读
                sp.run([adb, "-s", serial, "shell", "uiautomator dump /sdcard/ui.xml"],
                       capture_output=True, text=True, timeout=25)
                r2 = sp.run([adb, "-s", serial, "shell", "cat /sdcard/ui.xml"],
                            capture_output=True, text=True, timeout=25)
                xml = r2.stdout or ""
            texts = _re.findall(r'(?:text|content-desc)="([^"]+)"', xml)
            # 去重保序，去掉空白
            seen, out = set(), []
            for t in texts:
                t = t.strip()
                if t and t not in seen and len(t) < 80:
                    seen.add(t)
                    out.append(t)
            return " | ".join(out[:40])
        except Exception:
            return ""

    def current_pkg(serial: str) -> str:
        try:
            r = sp.run([adb, "-s", serial, "shell",
                        "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'"],
                       capture_output=True, text=True, timeout=20)
            out = r.stdout or ""
            m = _re.search(r"([a-zA-Z][a-zA-Z0-9_.]+)/", out)
            return m.group(1) if m else ""
        except Exception:
            return ""

    expect = req.expect or "手机处于正常界面，没有黑屏、崩溃、验证码、要求登录等异常"

    # 并发抓 UI（20 台约 3-5 秒）
    async def one(serial: str) -> dict:
        ui, pkg = await asyncio.gather(
            loop.run_in_executor(None, dump_ui, serial),
            loop.run_in_executor(None, current_pkg, serial),
        )
        return {"serial": serial, "pkg": pkg, "ui": ui}

    snaps = await asyncio.gather(*[one(s) for s in serials])

    # 批量喂给 LLM（一次判多台，省 token）
    payload_lines = []
    for i, s in enumerate(snaps, 1):
        payload_lines.append(
            f"{i}. 序列号={s['serial']} 前台包名={s['pkg'] or '未知'}\n"
            f"   屏幕文字={s['ui'][:400] or '(空)'}"
        )
    user_msg = (
        f"预期：{expect}\n\n"
        f"下面是每台手机当前的前台包名和屏幕文字，请逐台判断：\n\n"
        + "\n".join(payload_lines)
        + "\n\n只回一个 JSON 数组，每项 {\"serial\":\"...\",\"verdict\":\"ok|suspect|bad\",\"reason\":\"不超过20字\"}。"
          "verdict 含义：ok=符合预期；suspect=不确定；bad=明显异常"
          "（黑屏、崩溃、验证码、登录页、弹窗挡住）。"
    )

    results = []
    try:
        txt = await loop.run_in_executor(
            None, _llm_chat, req.model_kind,
            [
                {"role": "system", "content": "你是手机群控的画面审核员，判断简洁准确，只输出 JSON。"},
                {"role": "user", "content": user_msg},
            ],
            1024,
        )
        m = _re.search(r"\[.*\]", txt or "", _re.S)
        if m:
            arr = json.loads(m.group(0))
            by_serial = {x.get("serial"): x for x in arr if isinstance(x, dict)}
            for s in snaps:
                j = by_serial.get(s["serial"], {})
                results.append({
                    "serial": s["serial"],
                    "pkg": s["pkg"],
                    "verdict": j.get("verdict", "suspect"),
                    "reason": j.get("reason", "LLM 未给出"),
                })
        else:
            # LLM 没按格式回，保守标记
            for s in snaps:
                results.append({
                    "serial": s["serial"], "pkg": s["pkg"],
                    "verdict": "suspect", "reason": (txt or "")[:60],
                })
    except Exception as e:  # noqa: BLE001
        for s in snaps:
            results.append({
                "serial": s["serial"], "pkg": s["pkg"],
                "verdict": "unknown", "reason": str(e)[:100],
            })

    bad = [r for r in results if r["verdict"] == "bad"]
    suspect = [r for r in results if r["verdict"] == "suspect"]
    unknown = [r for r in results if r["verdict"] == "unknown"]

    return JSONResponse({
        "ok": True,
        "expect": expect,
        "total": len(results),
        "ok_count": sum(1 for r in results if r["verdict"] == "ok"),
        "bad": bad, "suspect": suspect, "unknown": unknown,
        "all_ok": not bad and not suspect and not unknown,
        "results": results,
    })


@app.websocket("/ws/h264/{serial}")
async def ws_h264(ws: WebSocket, serial: str) -> None:
    """直接推送 H.264 原始帧（前端用 WebCodecs 解码，零转码开销）。"""
    import asyncio as aio

    await ws.accept()
    # ★ 不要在这里停其他流！预热好的流会被杀掉，导致每次切换都重启 3.4 秒
    #   并发控制由 api_stream_start 的 LRU 负责

    async with _stream_lock(serial):
        w = get_stream(serial)
        started = True if w.running else await _stream_start_call(w.start)
    if not started:
        await ws.send_json({"type": "error", "message": w.error})
        await ws.close()
        return

    # 每个连接只有 sender 写 WS；receiver 只向其控制队列投递回复。
    sub = w.subscribe_h264(aio.get_running_loop())
    replies = aio.Queue(maxsize=16)

    async def request_key():
        # 返回值只代表控制消息已发送，不代表设备已经输出 IDR。
        now = time.monotonic()
        with w._h264_lock:
            last = getattr(w, "_iframe_requested_at", float("-inf"))
            if now - last < 2.0:
                return False
            w._iframe_requested_at = now
        if w.sess and w.sess.running:
            target = getattr(w, '_sent_options', (w.sess.max_size, w.sess.max_fps, w.sess.bit_rate))
            return await aio.to_thread(w.sess.request_options, *target)
        return False

    async def sender():
        await ws.send_json({
            "type": "ready", "width": w.sess.width if w.sess else 0,
            "height": w.sess.height if w.sess else 0,
            "codec": "h264", "has_config": sub.config is not None,
        })
        frame_task = aio.create_task(sub.queue.get())
        reply_task = aio.create_task(replies.get())
        try:
            while not sub.closed:
                done, _ = await aio.wait((frame_task, reply_task),
                                         return_when=aio.FIRST_COMPLETED, timeout=1.0)
                if sub.closed:
                    break
                if reply_task in done:
                    await ws.send_json(reply_task.result())
                    reply_task = aio.create_task(replies.get())
                if frame_task in done:
                    epoch, packet = frame_task.result()
                    if epoch == sub.epoch:
                        await ws.send_bytes(packet)
                    frame_task = aio.create_task(sub.queue.get())
        finally:
            for task in (frame_task, reply_task):
                task.cancel()
            await aio.gather(frame_task, reply_task, return_exceptions=True)

    async def receiver():
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "iframe":
                ok = await request_key()
                await replies.put({"type": "iframe_ack", "ok": ok})
            elif msg.get("type") == "ping":
                await replies.put({"type": "pong"})

    tasks = []
    try:
        tasks = [aio.create_task(sender()), aio.create_task(receiver())]
        # 有完整缓存 GOP 时无需重置静止设备；无缓存才主动请求起帧。
        if sub.waiting_key:
            await request_key()
        await aio.wait(tasks, return_when=aio.FIRST_COMPLETED)
    finally:
        w.unsubscribe_h264(sub)
        for task in tasks:
            task.cancel()
        results = await aio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, WebSocketDisconnect):
                print(f"[h264 {serial}] {type(result).__name__}: {result}", flush=True)
        try:
            await ws.close()
        except Exception:
            pass


@app.get("/api/stream/frame/{serial}")
async def api_stream_frame(serial: str) -> Response:
    """返回当前最新一帧 JPEG（前端循环拉取，最可靠）。"""
    w = _streams.get(serial)
    if w is None or w.last_jpeg is None:
        raise HTTPException(status_code=404, detail="流未启动或无帧")
    return Response(
        content=w.last_jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/stream/{serial}.mjpg")
async def stream_mjpeg(serial: str) -> StreamingResponse:
    """MJPEG 流（浏览器 <img> 原生支持，自动刷新）。"""
    import asyncio as aio

    async with _stream_lock(serial):
        w = get_stream(serial)
        started = True if w.running else await _stream_start_call(w.start)
    if not started:
        raise HTTPException(status_code=500, detail=w.error or "流启动失败")

    async def gen():
        while True:
            jpeg = w.last_jpeg
            if jpeg:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
            await aio.sleep(0.066)

    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )




@app.websocket("/ws/all-frames")
async def ws_all_frames(ws: WebSocket) -> None:
    """单连接推送所有设备的 JPEG 帧（突破浏览器 6 并发限制）。

    ★ 为什么必须这样：
      HTTP/1.1 对同一 host 最多 6 个并发连接，
      MJPEG 是长连接，所以 `<img src=".mjpg">` 最多只能同时显示 6 台。
      这里用一条 WebSocket 把所有设备的帧都推过去，20 台只占 1 个连接。

    协议（二进制）：
      [1 字节 seq_len][seq_len 字节 serial][4 字节 framelen BE][framelen 字节 JPEG]
    """
    import asyncio as aio
    import struct

    await ws.accept()

    # 该连接订阅的设备集合（由客户端发 JSON 指定）
    subs: set[str] = set()
    sending = False
    priority_serial = None

    # 固定数量的消费任务限制并发及任务数；新订阅替换尚未开始的工作。
    pending_options = aio.Queue()
    generation = 0
    serial_versions: dict[str, int] = {}
    # 每次重新订阅应能收到当前缓存帧，即使设备画面未发生变化。
    last: dict[str, int] = {}

    async def reader():
        nonlocal sending, generation, priority_serial
        while True:
            msg = await ws.receive_json()
            if msg.get('type') == 'sub':
                defaults = (int(msg.get('max_size') or 360),
                            int(msg.get('max_fps') or 15),
                            int(msg.get('bit_rate') or 100000))
                generation += 1
                new_subs = set(msg.get('serials', []))
                priority_serial = msg.get('priority_serial') if msg.get('priority_serial') in new_subs else None
                for serial in new_subs - subs:
                    last.pop(serial, None)
                subs.clear()
                subs.update(new_subs)
                sending = True
                while not pending_options.empty():
                    pending_options.get_nowait()
                for serial in dict.fromkeys(msg.get('serials', [])):
                    override = msg.get('device_params', {}).get(serial, {})
                    options = (int(override.get('max_size') or defaults[0]),
                               int(override.get('max_fps') or defaults[1]),
                               int(override.get('bit_rate') or defaults[2]))
                    pending_options.put_nowait((serial, generation, serial_versions.get(serial, 0),
                                                options, bool(msg.get('force'))))
            elif msg.get('type') == 'unsub':
                for serial in msg.get('serials', []):
                    subs.discard(serial)
                    last.pop(serial, None)
                    serial_versions[serial] = serial_versions.get(serial, 0) + 1

    async def apply_one():
        while True:
            serial, version, serial_version, options, force = await pending_options.get()

            def current():
                return (version == generation and serial in subs and
                        serial_versions.get(serial, 0) == serial_version)

            if not current():
                continue
            async with _stream_lock(serial):
                if not current():
                    continue
                w = get_stream(serial)
                # 无障碍会话一旦建立，就不允许被自动参数更新/重建打断：
                # 重建需要重新广播（依赖 adb），会把手动建立的通道打死。
                if getattr(w, 'accessible_mode', False) and w.running:
                    continue
                ms, fps, br = options
                changed = getattr(w, '_sent_options', (0, 0, 0)) != options
                if w.running and changed and not force:
                    result = await _stream_call(w.reconfigure, ms, fps, br)
                    if not current():
                        continue
                    if not result.get('ok'):
                        print(f'[ws-all] 动态更新失败，回退重建 {serial}: {result}', flush=True)
                        await _stream_call(w.stop)
                if not current():
                    continue
                if w.running and force:
                    await _stream_call(w.stop)
                if not current():
                    continue
                if not w.running:
                    w._max_size, w._max_fps, w._bit_rate = options
                    started = await _stream_start_call(w.start, current=current)
                    if started is None:
                        continue
                    if not started:
                        print(f'[ws-all] {serial} 启动失败: {w.error}', flush=True)

    async def apply_options():
        while True:
            try:
                await apply_one()
            except aio.CancelledError:
                raise
            except Exception as exc:
                print(f'[ws-all] 设备启动或更新失败: {type(exc).__name__}: {exc}', flush=True)

    async def recover_options():
        retry_after = {}
        while True:
            await aio.sleep(1)
            # 不在sender内等待重建；恢复期间其他设备继续推帧。
            for serial in tuple(subs):
                version = generation
                serial_version = serial_versions.get(serial, 0)
                def current():
                    return (version == generation and serial in subs and
                            serial_versions.get(serial, 0) == serial_version)
                async with _stream_lock(serial):
                    if not current():
                        continue
                    worker = _streams.get(serial)
                    # 对齐汇城 client_center：无障碍通道按各自 20 秒节奏重试。
                    if worker is not None and getattr(worker, 'accessible_mode', False):
                        try:
                            from ..core import device_ext as _dx
                            still = bool(_dx.accessible_runtime(serial).get("is_accessible"))
                        except Exception:
                            still = True
                        if not still or not current():
                            continue
                        now = aio.get_running_loop().time()
                        if now >= retry_after.get(serial, 0):
                            retry_after[serial] = now + 20
                            try:
                                started = await _stream_start_call(worker.start, current=current)
                                if started:
                                    last.pop(serial, None)
                                    retry_after.pop(serial, None)
                                    continue
                                print(f'[accessible] client_center start serial: {serial}, '
                                      f'err: {getattr(worker, "error", "") or "无障碍连接断开或是超过15秒未连接"}', flush=True)
                            except aio.CancelledError:
                                raise
                            except Exception as exc:
                                print(f'[accessible] client_center start serial: {serial}, '
                                      f'err: {type(exc).__name__}: {exc}', flush=True)
                        continue
                    if worker and not worker.running:
                        now = aio.get_running_loop().time()
                        if now >= retry_after.get(serial, 0):
                            retry_after[serial] = now + 5
                            # 与首次启动共享并发限制及逐设备锁，不干扰其他手机。
                            try:
                                started = await _stream_start_call(worker.start, current=current)
                                if started:
                                    last.pop(serial, None)
                                    retry_after.pop(serial, None)
                            except aio.CancelledError:
                                raise
                            except Exception as exc:
                                print(f'[ws-all] {serial} 断流重连失败: {type(exc).__name__}: {exc}', flush=True)
                        continue
                    update = getattr(worker, '_options_update', None) if worker else None
                    if update and update.get('status') == 'sent' and update.get('size_change'):
                        try:
                            await _stream_start_call(worker.recover_options_if_needed, current=current)
                        except aio.CancelledError:
                            raise
                        except Exception as exc:
                            print(f'[ws-all] {serial} 恢复失败: {type(exc).__name__}: {exc}', flush=True)

    async def sender():
        # 记录每台已发送的帧 id，只在有新帧或重新订阅时推。
        try:
            while True:
                if not sending or not subs:
                    await aio.sleep(0.05)
                    continue
                cycle_started = aio.get_running_loop().time()
                for s2 in sorted(subs, key=lambda s: (s != priority_serial, s)):
                    if s2 not in subs:
                        continue
                    w = _streams.get(s2)
                    if w is None:
                        continue
                    # 帧缓存可能由工作线程同时清空；只读取一次，避免 len(None)
                    # 令整条多设备 WebSocket 发送任务退出。
                    # JPEG 和计数由发布线程同时更新，作为一个快照读取。
                    with w._publish_lock:
                        jpeg, fid = w.last_jpeg, w.jpeg_count
                    if jpeg is None:
                        continue
                    if last.get(s2) == fid:
                        continue
                    last[s2] = fid
                    head = struct.pack(">I", len(jpeg))
                    sb = s2.encode("utf-8")
                    try:
                        send_started = aio.get_running_loop().time()
                        await ws.send_bytes(bytes([len(sb)]) + sb + head + jpeg)
                        duration = aio.get_running_loop().time() - send_started
                        stats = getattr(w, '_jpeg_delivery_stats', None)
                        if stats is None:
                            stats = w._jpeg_delivery_stats = {'sent': 0, 'bytes': 0, 'seconds': 0.0, 'max_seconds': 0.0}
                        stats['sent'] += 1
                        stats['bytes'] += len(jpeg)
                        stats['seconds'] += duration
                        stats['max_seconds'] = max(stats['max_seconds'], duration)
                    except Exception:
                        return
                # 100Hz轻量检查最新缓存，已按fid去重；不重复发送静态帧。
                # 以轮开始计时，不再把全部发送耗时叠加到固定40ms睡眠。
                elapsed = aio.get_running_loop().time() - cycle_started
                await aio.sleep(max(0.001, 0.01 - elapsed))
        except Exception:
            pass

    tasks = [aio.create_task(reader()), aio.create_task(sender()),
             aio.create_task(recover_options())]
    tasks.extend(aio.create_task(apply_options()) for _ in range(4))
    try:
        await aio.wait(tasks, return_when=aio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await aio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close()
        except Exception:
            pass




@app.websocket("/ws/fmp4/{serial}")
async def ws_fmp4(ws: WebSocket, serial: str) -> None:
    """用 MSE 播放单台高清视频流（零拷贝 fMP4）。

    ★ 为什么能做到「高清 + 流畅 + 20 路」：
      scrcpy 输出的 H.264 直接交给 ffmpeg 换容器（-c copy），
      不重新编码，CPU 几乎为 0；浏览器用 MSE 交给 GPU 解码。
      老的 JPEG 方案要「解码 + 重新编码」，20 路必爆。

    参数（高清）：
      max_size=720  max_fps=25  bit_rate=2500000
      → 20 台总共约 50Mbps，USB 2.0 能承受；单台看 1080p 也够清。
    """
    import asyncio as aio
    from ..core.fmp4 import FMP4Packager

    await ws.accept()

    # 此旧接口会与主读线程争抢同一 H.264 socket，暂时禁用，避免破坏主流。
    await ws.send_json({"type": "error", "message": "fMP4 旧接口暂不可用，请使用主投屏"})
    await ws.close()
    return

    # 1. 起流（高清参数）
    w = get_stream(serial)
    high = {
        "_max_size": 720,
        "_max_fps": 25,
        "_bit_rate": 2500000,
    }
    need_restart = False
    if w.running:
        cur_ms = getattr(w, "_max_size", 0)
        cur_fps = getattr(w, "_max_fps", 0)
        if cur_ms != high["_max_size"] or cur_fps != high["_max_fps"]:
            need_restart = True
    for k, v in high.items():
        setattr(w, k, v)
    if need_restart:
        try:
            w.stop()
        except Exception:
            pass
    if not w.start():
        await ws.send_json({"type": "error", "message": w.error or "流启动失败"})
        await ws.close()
        return

    await ws.send_json({
        "type": "ready",
        "serial": serial,
        "width": w.sess.width if w.sess else 720,
        "height": w.sess.height if w.sess else 1280,
    })

    # 2. 起封装器
    q: aio.Queue = aio.Queue(maxsize=64)
    loop = aio.get_running_loop()

    def on_seg(data: bytes) -> None:
        # 从 ffmpeg 线程回调 → 投递到 asyncio 队列
        def _put():
            if not q.full():
                q.put_nowait(data)
        try:
            loop.call_soon_threadsafe(_put)
        except Exception:
            pass

    packager = FMP4Packager(on_segment=on_seg, frag_ms=150)
    if not packager.start():
        await ws.send_json({"type": "error", "message": "fMP4 封装器启动失败"})
        await ws.close()
        return

    # 3. 把 H264 喂给封装器（独立线程，复用 StreamWorker 的读循环）
    feed_stop = threading.Event()

    def feed_loop() -> None:
        assert w.sess is not None
        while not feed_stop.is_set() and w.running:
            f = w.sess.read_frame(timeout=0.5)
            if f is None:
                continue
            packager.feed(f.data)

    ft = threading.Thread(target=feed_loop, daemon=True)
    ft.start()

    # 4. 推送分片
    sent = 0
    try:
        while True:
            try:
                seg = await aio.wait_for(q.get(), timeout=30)
            except aio.TimeoutError:
                await ws.send_json({"type": "stall"})
                continue
            await ws.send_bytes(seg)
            sent += 1
            if sent <= 3 or sent % 100 == 0:
                print(f"[fmp4 {serial}] seg #{sent} {len(seg)}B", flush=True)
    except Exception:
        pass
    finally:
        feed_stop.set()
        packager.stop()
        try:
            await ws.close()
        except Exception:
            pass
        print(f"[fmp4 {serial}] 结束，共 {sent} 片", flush=True)


@app.websocket("/ws/stream/{serial}")
async def ws_stream(ws: WebSocket, serial: str) -> None:
    """WebSocket 推送 JPEG 帧（直接取最新帧，不用队列）。"""
    import asyncio as aio

    await ws.accept()
    async with _stream_lock(serial):
        w = get_stream(serial)
        started = True if w.running else await _stream_start_call(w.start)
    if not started:
        await ws.send_json({"type": "error", "message": w.error})
        await ws.close()
        return

    await ws.send_json({
        "type": "ready",
        "width": w.sess.width if w.sess and w.sess else 0,
        "height": w.sess.height if w.sess and w.sess else 0,
    })

    print(f"[ws {serial}] ENTER id(w)={id(w)} jpeg_count={w.jpeg_count} frame_count={w.frame_count}", flush=True)
    sent = 0
    loops = 0
    try:
        while True:
            loops += 1
            jpeg = w.last_jpeg
            if jpeg:
                await ws.send_bytes(jpeg)
                sent += 1
            await aio.sleep(0.066)  # ~15fps
            if loops % 30 == 0:
                print(f"[ws {serial}] loops={loops} sent={sent} jpeg_count={w.jpeg_count} jpeg_id={id(jpeg)} last_id={id(w.last_jpeg)}", flush=True)
    except Exception as e:
        print(f"[ws {serial}] error: {type(e).__name__}: {e}", flush=True)
    finally:
        print(f"[ws {serial}] exit loops={loops} sent={sent}", flush=True)
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
