"""
FishFleet — 扩展 API

包含：
  · 设备标签（分组）
  · 任务队列
  · 媒体推送
  · 文件传输
  · 快捷短语
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from ..core.config import get_config
from ..core.device import get_manager
from ..core.media import MediaPusher, push_to_devices
from ..core.recorder import get_script_manager
from ..core.settings import get_settings
from ..core.tags import get_tag_manager
from ..scheduler.queue import get_queue

router = APIRouter(prefix="/api", tags=["ext"])


# ===========================================================================
# 标签
# ===========================================================================

@router.get("/tags")
async def api_tags() -> dict:
    """标签列表 + 各分组统计。"""
    mgr = get_manager()
    serials = [d.serial for d in mgr.all()]
    tm = get_tag_manager()
    return tm.summary(serials)


class CreateTagRequest(BaseModel):
    name: str
    color: str = "#2f81f7"


@router.post("/tags")
async def api_create_tag(req: CreateTagRequest) -> dict:
    return get_tag_manager().create_tag(req.name, req.color)


class UpdateTagRequest(BaseModel):
    name: str = ""
    color: str = ""


@router.put("/tags/{tag_id}")
async def api_update_tag(tag_id: str, req: UpdateTagRequest) -> dict:
    ok = get_tag_manager().update_tag(tag_id, req.name, req.color)
    if not ok:
        raise HTTPException(404, "标签不存在")
    return {"ok": True}


@router.delete("/tags/{tag_id}")
async def api_delete_tag(tag_id: str) -> dict:
    ok = get_tag_manager().delete_tag(tag_id)
    if not ok:
        raise HTTPException(404, "标签不存在")
    return {"ok": True}


class SetDeviceTagsRequest(BaseModel):
    tag_ids: list[str]


@router.post("/devices/{serial}/tags")
async def api_set_device_tags(serial: str, req: SetDeviceTagsRequest) -> dict:
    get_tag_manager().set_tags(serial, req.tag_ids)
    return {"ok": True, "tags": get_tag_manager().get_tags(serial)}


@router.get("/devices/{serial}/tags")
async def api_get_device_tags(serial: str) -> dict:
    return {"serial": serial, "tags": get_tag_manager().get_tags(serial)}


# ===========================================================================
# 任务队列
# ===========================================================================

@router.get("/queue")
async def api_queue_list(serial: str = "", state: str = "", limit: int = 50) -> dict:
    """任务列表。"""
    q = get_queue()
    return {
        "stats": q.stats(),
        "tasks": q.list(serial=serial, state=state, limit=limit),
    }


class CreateTaskRequest(BaseModel):
    type: str
    serial: str
    params: dict = {}
    auto_start: bool = True


@router.post("/queue")
async def api_queue_create(req: CreateTaskRequest) -> dict:
    q = get_queue()
    task = q.create(req.type, req.serial, req.params, req.auto_start)
    return task.to_dict()


class CreateBatchTaskRequest(BaseModel):
    type: str
    serials: list[str]
    params: dict = {}


@router.post("/queue/batch")
async def api_queue_batch(req: CreateBatchTaskRequest) -> dict:
    q = get_queue()
    tasks = q.create_batch(req.type, req.serials, req.params)
    return {"count": len(tasks), "tasks": [t.to_dict() for t in tasks]}


@router.post("/queue/{task_id}/cancel")
async def api_queue_cancel(task_id: str) -> dict:
    ok = get_queue().cancel(task_id)
    return {"ok": ok}


@router.post("/queue/cancel-all")
async def api_queue_cancel_all(serial: str = "") -> dict:
    n = get_queue().cancel_all(serial)
    return {"cancelled": n}


@router.post("/queue/clear")
async def api_queue_clear() -> dict:
    n = get_queue().clear_finished()
    return {"cleared": n}


# ===========================================================================
# 媒体推送
# ===========================================================================

class PushMediaRequest(BaseModel):
    local_path: str
    serial: str


@router.post("/media/push")
async def api_push_media(req: PushMediaRequest) -> dict:
    """把本地文件推到单台设备，返回 content_uri。"""
    if not os.path.exists(req.local_path):
        raise HTTPException(400, f"文件不存在: {req.local_path}")
    pusher = MediaPusher(req.serial)
    r = pusher.ensure_video(req.local_path)
    return r.to_dict()


class PushMediaBatchRequest(BaseModel):
    local_path: str
    serials: list[str]
    concurrency: int = 3


@router.post("/media/push-batch")
async def api_push_media_batch(req: PushMediaBatchRequest) -> dict:
    """批量推送。"""
    if not os.path.exists(req.local_path):
        raise HTTPException(400, f"文件不存在: {req.local_path}")
    return push_to_devices(req.local_path, req.serials, req.concurrency)


# ===========================================================================
# 文件传输
# ===========================================================================

class PushFileRequest(BaseModel):
    serial: str
    local_path: str
    remote_dir: str = "/sdcard/Download/"


@router.post("/files/push")
async def api_file_push(req: PushFileRequest) -> dict:
    if not os.path.exists(req.local_path):
        raise HTTPException(400, f"文件不存在: {req.local_path}")

    adb = get_config().devices.adb_path
    remote = req.remote_dir.rstrip("/") + "/" + Path(req.local_path).name
    p = subprocess.run(
        [adb, "-s", req.serial, "push", req.local_path, remote],
        capture_output=True, text=True, timeout=180,
    )
    return {
        "ok": p.returncode == 0,
        "remote_path": remote,
        "output": (p.stdout or p.stderr)[:300],
    }


class PullFileRequest(BaseModel):
    serial: str
    remote_path: str
    local_dir: str = "data/pulled"


@router.post("/files/pull")
async def api_file_pull(req: PullFileRequest) -> dict:
    os.makedirs(req.local_dir, exist_ok=True)
    adb = get_config().devices.adb_path
    p = subprocess.run(
        [adb, "-s", req.serial, "pull", req.remote_path, req.local_dir],
        capture_output=True, text=True, timeout=180,
    )
    local = os.path.join(req.local_dir, Path(req.remote_path).name)
    return {
        "ok": p.returncode == 0,
        "local_path": local if os.path.exists(local) else "",
        "output": (p.stdout or p.stderr)[:300],
    }


class ListFilesRequest(BaseModel):
    serial: str
    remote_dir: str = "/sdcard/Download/"


@router.post("/files/list")
async def api_file_list(req: ListFilesRequest) -> dict:
    adb = get_config().devices.adb_path
    p = subprocess.run(
        [adb, "-s", req.serial, "shell", f"ls -la {req.remote_dir}"],
        capture_output=True, text=True, timeout=30,
    )
    return {"ok": p.returncode == 0, "files": (p.stdout or "").splitlines()[:100]}


# ===========================================================================
# 剪贴板
# ===========================================================================

@router.get("/clipboard/{serial}")
async def api_get_clipboard(serial: str) -> dict:
    """取设备剪贴板。"""
    adb = get_config().devices.adb_path
    # Android 10+ 限制，尝试多种方式
    p = subprocess.run(
        [adb, "-s", serial, "shell", "cmd clipboard get"],
        capture_output=True, text=True, timeout=15,
    )
    if p.returncode == 0 and p.stdout.strip():
        return {"ok": True, "text": p.stdout.strip()}

    # 兜底：service call
    p2 = subprocess.run(
        [adb, "-s", serial, "shell", "service call clipboard 1"],
        capture_output=True, text=True, timeout=15,
    )
    return {
        "ok": False,
        "text": "",
        "error": "Android 10+ 限制剪贴板访问，可用无障碍服务",
        "raw": (p2.stdout or "")[:200],
    }


# ===========================================================================
# 快捷短语
# ===========================================================================

PHRASE_FILE = Path("data/phrases.json")


def _load_phrases() -> list[dict]:
    import json
    if not PHRASE_FILE.exists():
        defaults = [
            {"id": "p1", "text": "你好呀～"},
            {"id": "p2", "text": "感谢支持！"},
            {"id": "p3", "text": "关注我，看更多内容"},
        ]
        PHRASE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PHRASE_FILE.write_text(
            json.dumps({"phrases": defaults}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return defaults
    try:
        return json.loads(PHRASE_FILE.read_text(encoding="utf-8")).get("phrases", [])
    except Exception:  # noqa: BLE001
        return []


def _save_phrases(phrases: list[dict]) -> None:
    import json
    PHRASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PHRASE_FILE.write_text(
        json.dumps({"phrases": phrases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@router.get("/phrases")
async def api_phrases() -> dict:
    return {"phrases": _load_phrases()}


class PhraseRequest(BaseModel):
    text: str


@router.post("/phrases")
async def api_add_phrase(req: PhraseRequest) -> dict:
    phrases = _load_phrases()
    item = {"id": f"p{int(time.time()*1000)%100000}", "text": req.text}
    phrases.append(item)
    _save_phrases(phrases)
    return item


@router.delete("/phrases/{phrase_id}")
async def api_del_phrase(phrase_id: str) -> dict:
    phrases = [p for p in _load_phrases() if p["id"] != phrase_id]
    _save_phrases(phrases)
    return {"ok": True}


# ===========================================================================
# 动作录制 / 回放
# ===========================================================================

@router.get("/actions")
async def api_actions_list() -> dict:
    """脚本列表。"""
    from .douyin_skill import SKILL
    from .xiaohongshu_publish import SKILL as XHS_SKILL
    from .xiaohongshu_lead import SKILL as XHS_LEAD_SKILL
    from .bilibili_publish import SKILL as BILI_SKILL
    from .bilibili_lead import SKILL as BILI_LEAD_SKILL
    return {"scripts": [dict(SKILL), dict(XHS_SKILL), dict(XHS_LEAD_SKILL), dict(BILI_SKILL),
                        dict(BILI_LEAD_SKILL),
                        *get_script_manager().list()]}


class CreateScriptRequest(BaseModel):
    name: str
    description: str = ""


@router.post("/actions")
async def api_actions_create(req: CreateScriptRequest) -> dict:
    s = get_script_manager().create(req.name, req.description)
    return s.to_dict()


@router.get("/actions/{script_id}")
async def api_actions_get(script_id: str) -> dict:
    s = get_script_manager().get(script_id)
    if s is None:
        raise HTTPException(404, "脚本不存在")
    return s.to_dict()


@router.delete("/actions/{script_id}")
async def api_actions_delete(script_id: str) -> dict:
    get_script_manager().delete(script_id)
    return {"ok": True}


class AddActionRequest(BaseModel):
    type: str
    params: dict = {}
    delay: float = 0.5
    comment: str = ""


@router.post("/actions/{script_id}/actions")
async def api_actions_add(script_id: str, req: AddActionRequest) -> dict:
    from ..core.recorder import Action

    ok = get_script_manager().add_action(
        script_id,
        Action(type=req.type, params=req.params, delay=req.delay, comment=req.comment),
    )
    if not ok:
        raise HTTPException(404, "脚本不存在")
    return {"ok": True}


@router.delete("/actions/{script_id}/actions/{index}")
async def api_actions_remove(script_id: str, index: int) -> dict:
    ok = get_script_manager().remove_action(script_id, index)
    return {"ok": ok}


class PlayScriptRequest(BaseModel):
    serial: str
    speed: float = 1.0
    repeat: int = 1


@router.post("/actions/{script_id}/play")
async def api_actions_play(script_id: str, req: PlayScriptRequest) -> dict:
    """回放脚本到指定设备。"""
    from ..core.recorder import ActionPlayer

    s = get_script_manager().get(script_id)
    if s is None:
        raise HTTPException(404, "脚本不存在")

    player = ActionPlayer(req.serial)
    result = player.play(s, speed=req.speed, repeat=req.repeat)
    return result


class PlayScriptBatchRequest(BaseModel):
    serials: list[str]
    speed: float = 1.0
    repeat: int = 1


@router.post("/actions/{script_id}/play-batch")
async def api_actions_play_batch(script_id: str, req: PlayScriptBatchRequest) -> dict:
    """批量回放到多台设备。"""
    import threading

    s = get_script_manager().get(script_id)
    if s is None:
        raise HTTPException(404, "脚本不存在")

    results = {}

    def run_one(serial: str):
        try:
            from ..core.recorder import ActionPlayer
            player = ActionPlayer(serial)
            results[serial] = player.play(s, speed=req.speed, repeat=req.repeat)
        except Exception as e:  # noqa: BLE001
            results[serial] = {"ok": False, "error": str(e)}

    threads = []
    for i, serial in enumerate(req.serials):
        t = threading.Thread(target=run_one, args=(serial,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.3)      # 错开启动

    for t in threads:
        t.join(timeout=300)

    ok = len([r for r in results.values() if r.get("ok")])
    return {
        "total": len(req.serials),
        "ok": ok,
        "results": results,
    }


# ===========================================================================
# 系统设置
# ===========================================================================

@router.get("/settings")
async def api_settings_get() -> dict:
    return get_settings().to_dict()


class UpdateSettingsRequest(BaseModel):
    section: str
    values: dict


@router.post("/settings")
async def api_settings_update(req: UpdateSettingsRequest) -> dict:
    get_settings().update(req.section, req.values)
    return {"ok": True, "settings": get_settings().to_dict()}


@router.post("/settings/reset")
async def api_settings_reset(section: str = "") -> dict:
    get_settings().reset(section)
    return {"ok": True, "settings": get_settings().to_dict()}


# ===========================================================================
# 自动滑屏（连续）
# ===========================================================================

class AutoSwipeRequest(BaseModel):
    serials: list[str]
    duration: int = 30          # 秒
    interval: float = 3.0       # 每次间隔
    direction: str = "up"
    delay_offset: int = 300     # 设备间错开（毫秒）


@router.post("/autoswipe")
async def api_autoswipe(req: AutoSwipeRequest) -> dict:
    """
    自动滑屏（后台执行）。

    模仿真人刷视频：每台设备独立滑，速度略有差异。
    """
    import threading
    import random

    q = get_queue()
    tasks = []

    for i, serial in enumerate(req.serials):
        # 每台设备的滑屏次数不同（略随机，更像真人）
        base_count = int(req.duration / req.interval)
        count = max(1, base_count + random.randint(-2, 2))

        t = q.create(
            "swipe",
            serial,
            {
                "direction": req.direction,
                "count": count,
                "interval": req.interval + random.uniform(-0.5, 0.5),
                "offset_ms": i * req.delay_offset,
            },
            auto_start=False,
        )
        tasks.append(t.id)

    # 批量启动
    for tid in tasks:
        q.start(tid)

    return {
        "count": len(tasks),
        "task_ids": tasks,
        "duration": req.duration,
    }


# ===========================================================================
# 大屏模式
# ===========================================================================

@router.get("/devices/{serial}/bigscreen")
async def api_bigscreen(serial: str) -> dict:
    """获取大屏信息（用于单台放大）。"""
    from ..core.actions import DeviceController

    try:
        ctrl = DeviceController(serial)
        app = ctrl.current_app()
        settings = get_settings()
        return {
            "serial": serial,
            "width": ctrl.screen.width,
            "height": ctrl.screen.height,
            "foreground": app.get("package", ""),
            "big_width": settings.get("display", "big_width", 480),
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)) from e

@router.get("/system/info")
async def api_system_info() -> dict:
    """系统概况。"""
    mgr = get_manager()
    devs = mgr.all()
    q = get_queue()

    return {
        "devices": {
            "total": len(devs),
            "online": len([d for d in devs if d.state.value == "online"]),
        },
        "tasks": q.stats(),
        "tags": len(get_tag_manager().list_tags()),
        "server_time": time.time(),
    }


@router.get("/devices/{serial}/properties")
async def api_device_props(serial: str) -> dict:
    """设备参数（对应汇城的"手机参数"）。"""
    adb = get_config().devices.adb_path
    props = {}

    keys = [
        "ro.product.model", "ro.product.brand", "ro.build.version.release",
        "ro.build.version.sdk", "ro.serialno", "ro.product.cpu.abi",
        "ro.build.fingerprint", None,
    ]

    for k in keys:
        if k is None:
            continue
        p = subprocess.run(
            [adb, "-s", serial, "shell", f"getprop {k}"],
            capture_output=True, text=True, timeout=10,
        )
        props[k] = (p.stdout or "").strip()

    # 额外
    for name, cmd in [
        ("memory", "cat /proc/meminfo | head -3"),
        ("storage", "df /data | tail -1"),
        ("battery", "dumpsys battery | grep -E 'level|temperature'"),
        ("screen", "wm size; wm density"),
    ]:
        p = subprocess.run(
            [adb, "-s", serial, "shell", cmd],
            capture_output=True, text=True, timeout=15,
        )
        props[name] = (p.stdout or "").strip()

    return {"serial": serial, "props": props}
