"""
FishFleet — 媒体文件管理

核心职责：把电脑上的视频/图片推到手机，并让相册能识别到。

关键点（踩过的坑）：
  1. 只 adb push 不够，相册不会立刻识别
  2. 必须触发 MediaStore 扫描
  3. 扫描有延迟，要轮询确认
  4. 不同 Android 版本扫描方式不同
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import get_config

# 手机上的目标目录（Download 最通用，相册也能扫到）
REMOTE_DIR = "/sdcard/Download/"


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------

@dataclass
class PushResult:
    ok: bool
    local_path: str
    remote_path: str = ""
    content_uri: str = ""
    media_id: str = ""
    elapsed: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "local_path": self.local_path,
            "remote_path": self.remote_path,
            "content_uri": self.content_uri,
            "media_id": self.media_id,
            "elapsed": round(self.elapsed, 1),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# 媒体推送器
# ---------------------------------------------------------------------------

class MediaPusher:
    def __init__(self, serial: str, adb_path: str | None = None):
        self.serial = serial
        self.adb = adb_path or get_config().devices.adb_path

    # ---- ADB 基础 ----

    def _run(self, args: list[str], timeout: int = 120) -> tuple[int, str, str]:
        try:
            p = subprocess.run(
                [self.adb, "-s", self.serial] + args,
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return p.returncode, p.stdout or "", p.stderr or ""
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except Exception as e:  # noqa: BLE001
            return -2, "", str(e)

    def shell(self, cmd: str, timeout: int = 60) -> tuple[int, str]:
        code, out, err = self._run(["shell", cmd], timeout=timeout)
        return code, out if code == 0 else err

    # ---- 文件是否存在 ----

    def remote_exists(self, remote_path: str) -> bool:
        code, out = self.shell(f"[ -f '{remote_path}' ] && echo YES || echo NO")
        return "YES" in out

    def remote_size(self, remote_path: str) -> int:
        code, out = self.shell(f"stat -c %s '{remote_path}' 2>/dev/null || echo 0")
        try:
            return int(out.strip())
        except ValueError:
            return 0

    # ---- 推送 ----

    def push(self, local_path: str, remote_dir: str = REMOTE_DIR,
             overwrite: bool = False) -> PushResult:
        """
        推送文件到手机。

        流程：
          1. 检查本地文件
          2. 检查远端是否已存在（大小一致就跳过）
          3. adb push
          4. 触发媒体扫描
          5. 等相册识别
          6. 查 content uri
        """
        t0 = time.time()
        result = PushResult(ok=False, local_path=local_path)

        local = Path(local_path)
        if not local.exists():
            result.error = f"本地文件不存在: {local_path}"
            return result

        local_size = local.stat().st_size
        remote_path = remote_dir.rstrip("/") + "/" + local.name
        result.remote_path = remote_path

        # 1. 确保目录存在
        self.shell(f"mkdir -p '{remote_dir}'")

        # 2. 已存在且大小一致 → 跳过推送
        need_push = True
        if not overwrite and self.remote_exists(remote_path):
            rs = self.remote_size(remote_path)
            if rs == local_size:
                need_push = False

        if need_push:
            code, out, err = self._run(
                ["push", str(local), remote_path],
                timeout=max(120, int(local_size / 1024 / 1024 * 3)),   # 按大小给时间
            )
            if code != 0:
                result.error = f"push 失败: {err[:200]}"
                result.elapsed = time.time() - t0
                return result

        # 3. 验证大小
        rs = self.remote_size(remote_path)
        if rs < local_size:
            result.error = f"传送不完整: 本地 {local_size} / 远端 {rs}"
            result.elapsed = time.time() - t0
            return result

        # 4. 触发媒体扫描
        self.trigger_scan(remote_path)

        # 5. 查 content uri（最多等 15 秒）
        uri, media_id = self.wait_for_media(remote_path, timeout=15.0)
        result.content_uri = uri
        result.media_id = media_id

        result.ok = True
        result.elapsed = time.time() - t0
        return result

    # ---- 媒体扫描 ----

    def trigger_scan(self, remote_path: str) -> None:
        """让系统扫描这个文件（多种方式都试）。"""
        # 方式 1：am broadcast（Android 10 及以下最常见）
        self.shell(
            f'am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE '
            f'-d file://{remote_path}'
        )

        # 方式 2：content call（Android 11+ MediaProvider）
        self.shell(
            f'content call --uri content://media --method scan_volume '
            f'--arg external_primary 2>/dev/null'
        )

        # 方式 3：直接插 MediaStore（最可靠，但需要权限）
        self._insert_mediastore(remote_path)

    def _insert_mediastore(self, remote_path: str) -> None:
        """直接往 MediaStore 插记录（兜底）。"""
        # 判断类型
        ext = Path(remote_path).suffix.lower()
        if ext in (".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm"):
            uri_base = "content://media/external/video/media"
            mime = "video/" + ext.lstrip(".")
            if ext == ".mp4":
                mime = "video/mp4"
        elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            uri_base = "content://media/external/images/media"
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/" + ext.lstrip(".")
        else:
            return

        self.shell(
            f'content insert --uri {uri_base} '
            f'--bind _data:s:"{remote_path}" '
            f'--bind mime_type:s:"{mime}"'
        )

    def wait_for_media(self, remote_path: str, timeout: float = 15.0) -> tuple[str, str]:
        """
        等 MediaStore 识别到文件，返回 (content_uri, media_id)。

        轮询 content query，直到查到或超时。
        """
        fname = Path(remote_path).name
        deadline = time.time() + timeout

        while time.time() < deadline:
            # 查视频
            code, out = self.shell(
                f"content query --uri content://media/external/video/media "
                f"--projection _id:_data "
                f"--where \"_data LIKE '%{fname}%'\" 2>/dev/null"
            )
            parsed = self._parse_query(out)
            if parsed:
                return parsed

            # 查图片
            code, out = self.shell(
                f"content query --uri content://media/external/images/media "
                f"--projection _id:_data "
                f"--where \"_data LIKE '%{fname}%'\" 2>/dev/null"
            )
            parsed = self._parse_query(out)
            if parsed:
                return parsed

            time.sleep(1.0)

        return "", ""

    @staticmethod
    def _parse_query(out: str) -> tuple[str, str]:
        """
        解析 content query 输出。

        格式：Row: 0 _id=123, _data=/sdcard/...
        """
        for line in (out or "").splitlines():
            line = line.strip()
            if not line.startswith("Row:"):
                continue

            media_id = ""
            data_path = ""

            for part in line.split(","):
                part = part.strip()
                if "_id=" in part:
                    media_id = part.split("_id=", 1)[1].strip()
                elif "_data=" in part:
                    data_path = part.split("_data=", 1)[1].strip()

            if not media_id:
                continue

            # 判断是视频还是图片
            if "/video/" in (out or "") or any(
                data_path.lower().endswith(e) for e in (".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm")
            ):
                uri = f"content://media/external/video/media/{media_id}"
            else:
                uri = f"content://media/external/images/media/{media_id}"

            return uri, media_id

        return "", ""

    # ---- 便捷：确保视频在手机里 ----

    def ensure_video(self, local_path: str) -> PushResult:
        """
        确保视频在手机相册里可用。

        这是发布流程的第一步，必须做。
        """
        return self.push(local_path)


# ---------------------------------------------------------------------------
# 批量推送
# ---------------------------------------------------------------------------

def push_to_devices(local_path: str, serials: list[str], concurrency: int = 3) -> dict:
    """把同一个文件推到多台设备。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = []

    def do(serial: str) -> dict:
        try:
            pusher = MediaPusher(serial)
            r = pusher.ensure_video(local_path)
            return {"serial": serial, **r.to_dict()}
        except Exception as e:  # noqa: BLE001
            return {"serial": serial, "ok": False, "error": str(e)}

    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, 5))) as ex:
        futures = [ex.submit(do, s) for s in serials]
        for f in as_completed(futures):
            results.append(f.result())

    ok = len([r for r in results if r.get("ok")])
    return {
        "total": len(serials),
        "ok": ok,
        "failed": len(serials) - ok,
        "results": results,
    }
