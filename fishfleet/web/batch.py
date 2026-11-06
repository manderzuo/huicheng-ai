"""
FishFleet — 批量操作 API

模仿汇城的"分发"能力：
  · 文字分发：一次输入，多台同时执行
  · 文件分发：一次推送，多台同时接收
  · 按键分发：一次点击，多台同时响应
  · ADB 分发：一次命令，多台同时执行

所有批量操作都支持：
  · 延迟偏移（错开执行，避免风控）
  · 并发控制（避免 USB 总线过载）
  · 结果汇总（每台的成败）
"""

from __future__ import annotations

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.config import get_config

router = APIRouter(prefix="/api/batch", tags=["batch"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class BatchRequest(BaseModel):
    serials: list[str]
    delay_ms: int = 0          # 每台之间的延迟（毫秒）
    concurrency: int = 8       # 并发数


class BatchTapRequest(BatchRequest):
    x: int
    y: int


class BatchTextRequest(BatchRequest):
    text: str


class BatchKeyRequest(BatchRequest):
    key: str


class BatchSwipeRequest(BatchRequest):
    direction: str


class BatchLaunchRequest(BatchRequest):
    package: str


class BatchAdbRequest(BatchRequest):
    command: str


class BatchInstallRequest(BatchRequest):
    apk_path: str


class BatchPushRequest(BatchRequest):
    local_path: str
    remote_path: str = "/sdcard/Download/"


class BatchShellRequest(BatchRequest):
    shell_cmd: str


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    total: int = 0
    ok: int = 0
    failed: int = 0
    results: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "ok": self.ok,
            "failed": self.failed,
            "results": self.results,
        }


def run_batch(
    serials: list[str],
    fn: Callable[[str], Any],
    delay_ms: int = 0,
    concurrency: int = 8,
) -> BatchResult:
    """并发执行批量操作。"""
    result = BatchResult(total=len(serials))

    def worker(serial: str, idx: int) -> dict:
        # 延迟偏移
        if delay_ms > 0:
            time.sleep((delay_ms * idx) / 1000.0)
        t0 = time.time()
        try:
            ok = fn(serial)
            return {
                "serial": serial,
                "ok": bool(ok),
                "error": "" if ok else "执行返回 False",
                "elapsed": round(time.time() - t0, 2),
            }
        except Exception as e:  # noqa: BLE001
            return {
                "serial": serial,
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "elapsed": round(time.time() - t0, 2),
            }

    conc = max(1, min(concurrency, 8))
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futures = [ex.submit(worker, s, i) for i, s in enumerate(serials)]
        for f in as_completed(futures):
            r = f.result()
            result.results.append(r)
            if r["ok"]:
                result.ok += 1
            else:
                result.failed += 1

    # 按 serial 顺序排列
    order = {s: i for i, s in enumerate(serials)}
    result.results.sort(key=lambda r: order.get(r["serial"], 999))
    return result


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@router.post("/tap")
async def batch_tap(req: BatchTapRequest) -> dict:
    """批量点击（同一坐标）。"""
    from ..core.actions import DeviceController

    def do(serial: str) -> bool:
        ctrl = DeviceController(serial)
        return ctrl.tap(req.x, req.y)

    return run_batch(req.serials, do, req.delay_ms, req.concurrency).to_dict()


@router.post("/text")
async def batch_text(req: BatchTextRequest) -> dict:
    """文字分发：批量输入同一段文字。"""
    from ..core.actions import DeviceController

    def do(serial: str) -> bool:
        ctrl = DeviceController(serial)
        return ctrl.input_text(req.text)

    return run_batch(req.serials, do, req.delay_ms, req.concurrency).to_dict()


@router.post("/key")
async def batch_key(req: BatchKeyRequest) -> dict:
    """批量按键（back/home/enter...）。"""
    from ..core.actions import DeviceController

    def do(serial: str) -> bool:
        ctrl = DeviceController(serial)
        return ctrl.press_key(req.key)

    return run_batch(req.serials, do, req.delay_ms, req.concurrency).to_dict()


@router.post("/swipe")
async def batch_swipe(req: BatchSwipeRequest) -> dict:
    """批量滑动（自动滑屏用）。"""
    from ..core.actions import DeviceController

    def do(serial: str) -> bool:
        ctrl = DeviceController(serial)
        fn = {"up": ctrl.swipe_up, "down": ctrl.swipe_down}.get(req.direction)
        return fn() if fn else False

    return run_batch(req.serials, do, req.delay_ms, req.concurrency).to_dict()


@router.post("/launch")
async def batch_launch(req: BatchLaunchRequest) -> dict:
    """批量启动应用。"""
    from ..core.actions import DeviceController

    def do(serial: str) -> bool:
        ctrl = DeviceController(serial)
        return ctrl.launch_app(req.package)

    return run_batch(req.serials, do, req.delay_ms, req.concurrency).to_dict()


@router.post("/adb")
async def batch_adb(req: BatchAdbRequest) -> dict:
    """批量 ADB 命令（adb shell <command>）。"""
    adb = get_config().devices.adb_path

    def do(serial: str) -> bool:
        p = subprocess.run(
            [adb, "-s", serial, "shell", req.command],
            capture_output=True, text=True, timeout=60,
        )
        return p.returncode == 0

    return run_batch(req.serials, do, req.delay_ms, req.concurrency).to_dict()


@router.post("/shell")
async def batch_shell(req: BatchShellRequest) -> dict:
    """批量执行 shell 命令并收集输出。"""
    adb = get_config().devices.adb_path

    def do(serial: str) -> bool:
        p = subprocess.run(
            [adb, "-s", serial, "shell", req.shell_cmd],
            capture_output=True, text=True, timeout=60,
        )
        return p.returncode == 0

    return run_batch(req.serials, do, req.delay_ms, req.concurrency).to_dict()


@router.post("/install")
async def batch_install(req: BatchInstallRequest) -> dict:
    """批量安装 APK。"""
    if not os.path.exists(req.apk_path):
        raise HTTPException(400, f"APK 不存在: {req.apk_path}")

    adb = get_config().devices.adb_path

    def do(serial: str) -> bool:
        p = subprocess.run(
            [adb, "-s", serial, "install", "-r", req.apk_path],
            capture_output=True, text=True, timeout=180,
        )
        return p.returncode == 0 and "Success" in p.stdout

    return run_batch(req.serials, do, req.delay_ms, max(1, min(req.concurrency, 3))).to_dict()


@router.post("/push")
async def batch_push(req: BatchPushRequest) -> dict:
    """批量推送文件。"""
    if not os.path.exists(req.local_path):
        raise HTTPException(400, f"文件不存在: {req.local_path}")

    adb = get_config().devices.adb_path

    def do(serial: str) -> bool:
        p = subprocess.run(
            [adb, "-s", serial, "push", req.local_path, req.remote_path],
            capture_output=True, text=True, timeout=120,
        )
        return p.returncode == 0

    return run_batch(req.serials, do, req.delay_ms, max(1, min(req.concurrency, 4))).to_dict()


@router.post("/screenshot-all")
async def batch_screenshot(req: BatchRequest) -> dict:
    """批量截图并返回保存路径。"""
    adb = get_config().devices.adb_path
    out_dir = "data/shots/batch"
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time())

    def do(serial: str) -> bool:
        out = os.path.join(out_dir, f"{serial}_{ts}.png")
        with open(out, "wb") as f:
            p = subprocess.run(
                [adb, "-s", serial, "exec-out", "screencap", "-p"],
                stdout=f, timeout=30,
            )
        return p.returncode == 0 and os.path.getsize(out) > 1000

    return run_batch(req.serials, do, req.delay_ms, req.concurrency).to_dict()
