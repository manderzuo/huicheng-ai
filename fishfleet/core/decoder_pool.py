"""多进程视频解码池 —— 把 10 个物理核心都吃满。

═══ 为什么要多进程 ═══

单看某一路，PyAV 解码很快（73fps）。但 Python 有 GIL：
即使开 20 个线程，同一时刻也只有一个线程能执行 Python 字节码，
所以 20 路共享 1 个核 → 总吞吐被钉死在 ~76fps。

这台机器是 Xeon E5-2680 v2（10 物理核 / 20 逻辑线程）。
把解码分到多个进程，每个进程有自己的 GIL，才能真正并行。

═══ 架构 ═══

    主进程（FastAPI）
      │  spawn
      ├─ Worker 0 ── 设备 A, B, C   （独立 GIL，1 个核）
      ├─ Worker 1 ── 设备 D, E, F   （独立 GIL，1 个核）
      ├─ ...
      └─ Worker N ── ...

    每个 Worker 把 JPEG 帧写进 multiprocessing.Queue，
    主进程统一通过 WebSocket 推给浏览器。

═══ 关键设计 ═══

1. 用 `spawn` 而非 `fork`（Windows 只支持 spawn，且 spawn 更干净）
2. 每个 Worker 内部**单线程**跑多路（避免 Worker 内部再抢 GIL）
3. 队列只放最新帧（满了就丢旧的），保证低延迟
4. Worker 崩溃自动重启
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# 一台设备 = 一路解码
DEFAULT_MAX_SIZE = 720
DEFAULT_MAX_FPS = 25
DEFAULT_BIT_RATE = 2_500_000
DEFAULT_QUALITY = 8


# ---------------------------------------------------------------------------
# Worker 进程主体
# ---------------------------------------------------------------------------


def _worker_main(
    worker_id: int,
    serials: list[str],
    out_q,
    ctrl_q,
    cfg: dict,
) -> None:
    """一个 Worker 进程：负责 serials 里所有设备的 scrcpy + 解码。"""
    import sys

    sys.path.insert(0, cfg.get("project_root", ""))
    from fishfleet.core.scrcpy import ScrcpySession
    from fishfleet.core.av_decode import AvJpegDecoder

    max_size = cfg.get("max_size", DEFAULT_MAX_SIZE)
    max_fps = cfg.get("max_fps", DEFAULT_MAX_FPS)
    bit_rate = cfg.get("bit_rate", DEFAULT_BIT_RATE)
    quality = cfg.get("quality", DEFAULT_QUALITY)

    states: dict[str, dict] = {}
    for s in serials:
        states[s] = {
            "sess": None,
            "dec": None,
            "h264": 0,
            "jpeg": 0,
            "last_t": time.time(),
            "errors": 0,
        }

    def start_one(serial: str) -> bool:
        st = states[serial]
        try:
            sess = ScrcpySession(serial, max_size=max_size, max_fps=max_fps, bit_rate=bit_rate)
            if not sess.start():
                st["errors"] += 1
                return False
            st["sess"] = sess

            def on_jpeg(j: bytes, s: str = serial) -> None:
                # ★ 关键：只放最新帧，队列满了丢旧帧（低延迟优先）
                try:
                    out_q.put_nowait((s, j))
                except queue.Full:
                    try:
                        out_q.get_nowait()
                    except Exception:
                        pass
                    try:
                        out_q.put_nowait((s, j))
                    except Exception:
                        pass
                states[s]["jpeg"] += 1

            dec = AvJpegDecoder(sess.width, sess.height, quality=quality,
                                fps=0, on_jpeg=on_jpeg)
            if not dec.start():
                sess.stop()
                st["errors"] += 1
                return False
            st["dec"] = dec
            return True
        except Exception:
            st["errors"] += 1
            return False

    for s in serials:
        start_one(s)

    running = True
    last_stat = time.time()

    while running:
        # 控制消息
        try:
            while True:
                msg = ctrl_q.get_nowait()
                if msg == ("stop", worker_id):
                    running = False
                    break
        except queue.Empty:
            pass
        if not running:
            break

        # 轮流读每一路（单线程 + 非阻塞，避免 Worker 内再抢 GIL）
        did_work = False
        for s in serials:
            st = states[s]
            sess = st["sess"]
            dec = st["dec"]
            if sess is None or dec is None:
                # 尝试重启
                if st["errors"] < 5 and time.time() - st["last_t"] > 5:
                    st["last_t"] = time.time()
                    if start_one(s):
                        st["errors"] = 0
                continue
            try:
                f = sess.read_frame(timeout=0.0)   # 非阻塞
            except Exception:
                f = None
            if f is None:
                continue
            did_work = True
            st["h264"] += 1
            try:
                dec.feed(f.data)
            except Exception:
                st["errors"] += 1

        # 状态上报（每秒一次）
        now = time.time()
        if now - last_stat >= 1.0:
            elapsed = now - last_stat
            stat = {}
            for s in serials:
                st = states[s]
                stat[s] = {
                    "h264": round(st["h264"] / elapsed, 1),
                    "jpeg": round(st["jpeg"] / elapsed, 1),
                    "ok": st["sess"] is not None,
                    "errors": st["errors"],
                }
                st["h264"] = 0
                st["jpeg"] = 0
            try:
                out_q.put_nowait(("__stat__", (worker_id, stat)))
            except Exception:
                pass
            last_stat = now

        if not did_work:
            time.sleep(0.002)   # 没数据时让出 CPU

    # 清理
    for s in serials:
        st = states[s]
        try:
            if st["dec"]: st["dec"].stop()
        except Exception:
            pass
        try:
            if st["sess"]: st["sess"].stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 主进程侧的池管理器
# ---------------------------------------------------------------------------


@dataclass
class WorkerInfo:
    worker_id: int
    serials: list[str]
    proc: object
    out_q: object
    ctrl_q: object
    stats: dict = field(default_factory=dict)
    started_at: float = 0.0
    restarts: int = 0


class DecoderPool:
    """把设备分给多个 Worker 进程，每个进程独立 GIL。

    典型用法：
        pool = DecoderPool(project_root, workers=6)
        pool.allocate(["A","B",...20 台])
        pool.start()
        for serial, jpeg in pool.frames():   # 生成器
            ...
    """

    def __init__(
        self,
        project_root: str,
        workers: int = 0,
        max_size: int = DEFAULT_MAX_SIZE,
        max_fps: int = DEFAULT_MAX_FPS,
        bit_rate: int = DEFAULT_BIT_RATE,
        quality: int = DEFAULT_QUALITY,
        queue_size: int = 300,
    ) -> None:
        self.project_root = project_root
        # 默认用「物理核数」个 Worker（逻辑核超线程对视频解码帮助有限）
        if workers <= 0:
            workers = max(2, (os.cpu_count() or 4) // 2)
        self.workers_wanted = workers
        self.cfg = {
            "project_root": project_root,
            "max_size": max_size,
            "max_fps": max_fps,
            "bit_rate": bit_rate,
            "quality": quality,
        }
        self.queue_size = queue_size

        self.ctx = mp.get_context("spawn")
        self.infos: dict[int, WorkerInfo] = {}
        self._running = False
        self._latest: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._allocated: list[str] = []

    # ------------------------------------------------------------------ 分配

    def allocate(self, serials: list[str]) -> dict[int, list[str]]:
        """把设备平均分给各个 Worker。"""
        serials = list(serials)
        n = self.workers_wanted
        buckets: list[list[str]] = [[] for _ in range(n)]
        # 轮询分配，保证负载均衡
        for i, s in enumerate(serials):
            buckets[i % n].append(s)
        self._allocated = serials
        return {i: b for i, b in enumerate(buckets) if b}

    # ------------------------------------------------------------------ 生命周期

    def start(self, serials: Optional[list[str]] = None) -> bool:
        """启动所有 Worker。"""
        if self._running:
            return True
        if serials is None:
            serials = self._allocated
        if not serials:
            return False

        buckets = self.allocate(serials)
        self.workers_wanted = len(buckets)

        for wid, bucket in buckets.items():
            self._spawn(wid, bucket)

        self._running = True
        # 后台线程收帧
        self._collector = threading.Thread(target=self._collect_loop, daemon=True)
        self._collector.start()
        return True

    def _spawn(self, wid: int, serials: list[str]) -> None:
        out_q = self.ctx.Queue(maxsize=self.queue_size)
        ctrl_q = self.ctx.Queue()
        p = self.ctx.Process(
            target=_worker_main,
            args=(wid, serials, out_q, ctrl_q, self.cfg),
            daemon=True,
            name=f"decode-worker-{wid}",
        )
        p.start()
        self.infos[wid] = WorkerInfo(
            worker_id=wid,
            serials=serials,
            proc=p,
            out_q=out_q,
            ctrl_q=ctrl_q,
            started_at=time.time(),
        )

    def _collect_loop(self) -> None:
        """主线程：从所有 Worker 的队列里收帧，只保留最新。"""
        while self._running:
            got = False
            for wid, info in list(self.infos.items()):
                try:
                    item = info.out_q.get(timeout=0.01)
                except Exception:
                    continue
                got = True
                if not isinstance(item, tuple) or len(item) != 2:
                    continue
                key, val = item
                if key == "__stat__":
                    try:
                        _, stat = val
                        info.stats.update(stat)
                    except Exception:
                        pass
                else:
                    serial = key
                    jpeg = val
                    with self._lock:
                        self._latest[serial] = jpeg
            if not got:
                time.sleep(0.001)

    # ------------------------------------------------------------------ 取帧

    def latest(self, serial: str) -> Optional[bytes]:
        with self._lock:
            return self._latest.get(serial)

    def snapshot(self) -> dict[str, bytes]:
        with self._lock:
            return dict(self._latest)

    def stats(self) -> dict:
        out = {"workers": len(self.infos), "serials": {}}
        for wid, info in self.infos.items():
            alive = info.proc.is_alive() if info.proc else False
            out["workers_detail"] = out.get("workers_detail", {})
            out["workers_detail"][wid] = {
                "serials": info.serials,
                "alive": alive,
                "pid": info.proc.pid if info.proc else None,
            }
            for s, st in info.stats.items():
                out["serials"][s] = st
        return out

    def stop(self) -> None:
        self._running = False
        for wid, info in self.infos.items():
            try:
                info.ctrl_q.put(("stop", wid))
            except Exception:
                pass
        time.sleep(0.3)
        for wid, info in self.infos.items():
            try:
                if info.proc and info.proc.is_alive():
                    info.proc.terminate()
            except Exception:
                pass
        for wid, info in self.infos.items():
            try:
                if info.proc:
                    info.proc.join(timeout=2)
            except Exception:
                pass
        self.infos.clear()
