"""FishFleet 群控 CLI —— 供 AI 调用的统一入口。

设计原则
--------
1. **一个命令操作 N 台**：并行下发，不串行等待
2. **结构化输出**：默认 JSON，AI 直接解析；`--human` 切成人读格式
3. **自带校验**：`--verify` 会截屏算 pHash 分组，标出没跟上的设备
4. **不重造轮子**：所有动作都走本地服务（127.0.0.1:8765）已有的 scrcpy 通道

典型用法
--------
    # 20 台一起打开抖音
    fishfleetctl launch --all --package com.ss.android.ugc.aweme --settle 1500 --verify

    # 指定几台点击
    fishfleetctl tap --devices SERIAL0001,SERIAL0001 --x 540 --y 960

    # 输入中文（走 XWKeyboard 广播）
    fishfleetctl text --all --text "今天的视频很有意思"

    # 只在异常时让 LLM 看一眼
    fishfleetctl tap --all --x 540 --y 960 --verify --llm auto

退出码
------
0 = 全部成功且同步；1 = 有设备失败；2 = 失败且不同步
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

API = os.environ.get("FISHFLEET_API", "http://127.0.0.1:8765")


# ---------------------------------------------------------------------------
# HTTP 小工具（不依赖 requests，避免额外安装）
# ---------------------------------------------------------------------------

def _req(path: str, body: Optional[dict] = None, timeout: float = 30.0) -> dict:
    url = API.rstrip("/") + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read()[:200]!r}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _post(path: str, body: dict, timeout: float = 30.0) -> dict:
    return _req(path, body, timeout)


# ---------------------------------------------------------------------------
# 设备选择
# ---------------------------------------------------------------------------

@dataclass
class Selection:
    all: bool = False
    devices: str = ""
    tag: str = ""
    filt: str = ""
    limit: int = 0
    exclude: str = ""

    def resolve(self) -> list[dict]:
        d = _req("/api/devices")
        devs = d.get("devices", []) or []

        if self.devices:
            want = [x.strip() for x in self.devices.split(",") if x.strip()]
            devs = [x for x in devs if x.get("serial") in want]
        elif self.all:
            devs = [x for x in devs if x.get("state") == "online"]
        elif self.tag:
            # 标签过滤（后端若支持 tags 字段则用之，否则按 account 模糊匹配）
            t = self.tag
            devs = [x for x in devs if t in str(x.get("account", "")) or t in str(x.get("tags", ""))]
        elif self.filt:
            devs = [x for x in devs if _match_filter(x, self.filt)]
        else:
            # 默认：所有在线设备
            devs = [x for x in devs if x.get("state") == "online"]

        if self.exclude:
            ex = {x.strip() for x in self.exclude.split(",") if x.strip()}
            devs = [x for x in devs if x.get("serial") not in ex]

        if self.limit > 0:
            devs = devs[: self.limit]

        return devs


def _match_filter(dev: dict, expr: str) -> bool:
    """支持 battery>50 / state==online 这类简单表达式。"""
    try:
        for part in expr.split(","):
            part = part.strip()
            for op in (">=", "<=", "==", "!=", ">", "<"):
                if op in part:
                    key, val = part.split(op, 1)
                    key, val = key.strip(), val.strip()
                    cur = dev.get(key)
                    try:
                        cur_f, val_f = float(cur), float(val)
                    except (TypeError, ValueError):
                        cur_f, val_f = str(cur), val
                    if op == ">=" and not cur_f >= val_f: return False
                    if op == "<=" and not cur_f <= val_f: return False
                    if op == ">" and not cur_f > val_f: return False
                    if op == "<" and not cur_f < val_f: return False
                    if op == "==" and cur_f != val_f: return False
                    if op == "!=" and cur_f == val_f: return False
                    break
        return True
    except Exception:  # noqa: BLE001
        return True


# ---------------------------------------------------------------------------
# 并行下发
# ---------------------------------------------------------------------------

async def fanout(items: list[dict], fn, concurrency: int = 30) -> list[dict]:
    """对每台设备并发执行 fn(item)，结果按输入顺序返回。"""
    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[Any] = [None] * len(items)

    async def one(i: int, item: dict) -> None:
        async with sem:
            t0 = time.time()
            try:
                r = await asyncio.get_running_loop().run_in_executor(None, fn, item)
                if not isinstance(r, dict):
                    r = {"ok": bool(r)}
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)}
            r["serial"] = item.get("serial")
            r["ms"] = int((time.time() - t0) * 1000)
            results[i] = r

    await asyncio.gather(*[one(i, it) for i, it in enumerate(items)])
    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# 画面校验：pHash 分组
# ---------------------------------------------------------------------------

def _phash(raw: bytes, size: int = 8) -> Optional[str]:
    """感知哈希（DCT 简化版）：只依赖 Pillow。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(raw)).convert("L").resize((32, 32), Image.LANCZOS)
        import numpy as np
        a = np.asarray(img, dtype=np.float32)
        # 2D DCT（可分两次 1D）
        d = _dct2(a)[:size, :size]
        med = np.median(d[1:, 1:])          # 忽略直流分量
        bits = (d > med).flatten()
        return hex(int("".join("1" if b else "0" for b in bits), 2))[2:].zfill(size * size // 4)
    except Exception:  # noqa: BLE001
        return None


def _dct2(a):
    import numpy as np
    n = a.shape[0]
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[:, None] + 1) * k[None, :] / (2 * n))
    m[:, 0] = m[:, 0] / np.sqrt(2)
    m = m * np.sqrt(2.0 / n)
    return m.T @ a @ m


def _hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def verify_screens(serials: list[str], settle: float = 0.8,
                   sample: int = 0, save_dir: str = "") -> dict:
    """截屏 → pHash → 分组，找出与多数不一致的设备。"""
    time.sleep(max(0.0, settle))

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    def grab(serial: str) -> dict:
        url = f"{API}/api/devices/{serial}/screenshot"
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                raw = r.read()
            h = _phash(raw)
            if save_dir:
                with open(os.path.join(save_dir, f"{serial}.png"), "wb") as f:
                    f.write(raw)
            return {"serial": serial, "ok": bool(h), "hash": h, "bytes": len(raw)}
        except Exception as e:  # noqa: BLE001
            return {"serial": serial, "ok": False, "error": str(e)}

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        shots = list(ex.map(grab, serials))

    # 按 hash 分组
    groups: dict[str, list[str]] = {}
    failed: list[str] = []
    for s in shots:
        if not s.get("ok"):
            failed.append(s["serial"])
            continue
        groups.setdefault(s["hash"], []).append(s["serial"])

    if not groups:
        return {"in_sync": False, "groups": [], "outliers": failed,
                "note": "全部截图失败"}

    # 找最大组
    ordered = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    major_hash, major_devs = ordered[0]
    outliers: list[str] = list(failed)
    detail = []
    for h, devs in ordered:
        detail.append({
            "hash": h,
            "count": len(devs),
            "devices": devs,
            "distance_to_major": _hamming(h, major_hash),
        })
        if h != major_hash:
            outliers.extend(devs)

    return {
        "in_sync": len(outliers) == 0 and len(ordered) == 1,
        "major": {"hash": major_hash, "count": len(major_devs)},
        "groups": detail,
        "outliers": outliers,
        "sample": shots[:sample] if sample else [],
    }




def llm_judge(serials: list[str], expect: str, kind: str = "auto") -> dict:
    """用自动化 LLM 判断每台画面是否符合预期。

    这是校验层 2：pHash 只能发现「画面不一致」，
    但 20 台设备各自首页内容本来就不同，pHash 必然不同。
    真正要判断的是「是否都到了预期界面」，只有 LLM 能做。
    """
    if not serials:
        return {"ok": False, "error": "没有设备"}
    return _post("/api/llm/judge", {
        "expect": expect,
        "serials": serials,
        "model_kind": kind,
    }, timeout=180)

# ---------------------------------------------------------------------------
# 动作封装
# ---------------------------------------------------------------------------

def act_tap(serial: str, x: int, y: int) -> dict:
    return _post("/api/tap", {"serial": serial, "x": x, "y": y})


def act_swipe(serial: str, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> dict:
    return _post("/api/swipe-points", {
        "serial": serial, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
        "duration": ms / 1000.0,
    })


def act_key(serial: str, key: str) -> dict:
    return _post("/api/key", {"serial": serial, "key": key})


def act_text(serial: str, text: str) -> dict:
    return _post("/api/text", {"serial": serial, "text": text})


def act_scroll(serial: str, x: int, y: int, v: int) -> dict:
    return _post("/api/scroll", {"serial": serial, "x": x, "y": y, "v": v})


def act_launch(serial: str, package: str) -> dict:
    return _post(f"/api/devices/{serial}/launch", {"package": package})


def act_shell(serial: str, command: str) -> dict:
    return _post(f"/api/devices/{serial}/shell", {"command": command})


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def cmd_devices(args) -> dict:
    sel = Selection(args.all, args.devices, args.tag, args.filter, args.limit, args.exclude)
    devs = sel.resolve()
    return {"ok": True, "count": len(devs), "devices": devs}


async def run_action(args, fn) -> dict:
    sel = Selection(args.all, args.devices, args.tag, args.filter, args.limit, args.exclude)
    devs = sel.resolve()
    if not devs:
        return {"ok": False, "error": "没有匹配的设备", "executed": 0}

    t0 = time.time()
    results = await fanout(devs, fn, concurrency=args.concurrency)
    elapsed = int((time.time() - t0) * 1000)

    ok_n = sum(1 for r in results if r.get("ok"))
    out: dict = {
        "ok": ok_n == len(results),
        "command": args.command,
        "executed": len(results),
        "success": ok_n,
        "failed": len(results) - ok_n,
        "elapsed_ms": elapsed,
        "results": results,
    }

    if args.verify:
        ok_serials = [r["serial"] for r in results if r.get("ok")]

        # 层 1：pHash（便宜、快，只能看「是否一致」）
        out["verify"] = verify_screens(
            ok_serials, settle=args.settle, sample=args.sample,
            save_dir=args.images or "",
        )
        v = out["verify"]

        # 层 2：LLM（判断「是否都到了预期界面」）
        # 20 台各自首页内容不同，pHash 必然不一致，所以必须用 LLM 才准
        if args.llm and ok_serials:
            expect = args.expect or f"刚刚执行了 {args.command} 操作，手机处于正常界面，"
            expect += "没有黑屏、崩溃、验证码、要求登录等异常"
            try:
                j = llm_judge(ok_serials, expect, args.llm)
                out["llm"] = j
                if j.get("ok"):
                    if j.get("all_ok"):
                        out["verify"]["in_sync"] = True
                        out["verify"]["llm_note"] = "LLM 判定全部正常"
                    else:
                        bad = [x["serial"] for x in j.get("bad", [])]
                        susp = [x["serial"] for x in j.get("suspect", [])]
                        out["verify"]["outliers"] = list(dict.fromkeys(bad + susp))
                        out["hint"] = (
                            f"LLM 判定 {len(bad)} 台异常、{len(susp)} 台不确定"
                            f"，建议对 {out['verify']['outliers'][:3]} 单独处理"
                        )
            except Exception as e:  # noqa: BLE001
                out["llm"] = {"ok": False, "error": str(e)[:150]}

        if not out["verify"].get("in_sync") and out["verify"].get("outliers"):
            out.setdefault("hint", f"{len(out['verify']['outliers'])} 台画面需复核，可单独重试")

    return out


def cmd_tap(args) -> dict:
    return asyncio.run(run_action(args, lambda d: act_tap(d["serial"], args.x, args.y)))


def cmd_swipe(args) -> dict:
    return asyncio.run(run_action(args, lambda d: act_swipe(
        d["serial"], args.x1, args.y1, args.x2, args.y2, args.ms)))


def cmd_key(args) -> dict:
    return asyncio.run(run_action(args, lambda d: act_key(d["serial"], args.key)))


def cmd_text(args) -> dict:
    return asyncio.run(run_action(args, lambda d: act_text(d["serial"], args.text)))


def cmd_scroll(args) -> dict:
    return asyncio.run(run_action(args, lambda d: act_scroll(
        d["serial"], args.x, args.y, args.v)))


def cmd_launch(args) -> dict:
    return asyncio.run(run_action(args, lambda d: act_launch(d["serial"], args.package)))


def cmd_shell(args) -> dict:
    return asyncio.run(run_action(args, lambda d: act_shell(d["serial"], args.command)))


def cmd_shot(args) -> dict:
    sel = Selection(args.all, args.devices, args.tag, args.filter, args.limit, args.exclude)
    devs = sel.resolve()
    serials = [d["serial"] for d in devs]
    out_dir = args.out or "./shots"
    os.makedirs(out_dir, exist_ok=True)

    def grab(serial: str) -> dict:
        try:
            with urllib.request.urlopen(
                f"{API}/api/devices/{serial}/screenshot", timeout=25
            ) as r:
                raw = r.read()
            p = os.path.join(out_dir, f"{serial}.png")
            with open(p, "wb") as f:
                f.write(raw)
            return {"serial": serial, "ok": True, "path": p, "bytes": len(raw)}
        except Exception as e:  # noqa: BLE001
            return {"serial": serial, "ok": False, "error": str(e)}

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(grab, serials))

    return {
        "ok": all(r["ok"] for r in results),
        "count": len(results),
        "out_dir": out_dir,
        "results": results,
    }


def cmd_verify(args) -> dict:
    sel = Selection(args.all, args.devices, args.tag, args.filter, args.limit, args.exclude)
    devs = sel.resolve()
    serials = [d["serial"] for d in devs]
    v = verify_screens(serials, settle=args.settle, sample=args.sample,
                       save_dir=args.images or "")
    v["ok"] = True
    return v


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


# ===========================================================================
# 复杂操作（对齐汇城 + 补足 AI 需要的复合能力）
# ===========================================================================


def cmd_ext(args) -> dict:
    """调用扩展能力（对齐汇城的 34 个命令）。

    用法：
      fishfleetctl ext --op get_device_info --devices XXX
      fishfleetctl ext --op uninstall_apk --devices XXX --package com.xxx
      fishfleetctl ext --op switch_device_mode --devices XXX --mode mtp_adb
    """
    op = args.op
    payload = {}
    if getattr(args, "serial", ""):
        payload["serial"] = args.serial
    elif getattr(args, "devices", ""):
        payload["serial"] = args.devices.split(",")[0]
    if getattr(args, "package", ""):
        payload["package"] = args.package
    if getattr(args, "mode", ""):
        payload["mode"] = args.mode
    if getattr(args, "enable", None) is not None:
        payload["enable"] = args.enable
    if getattr(args, "script", ""):
        payload["script"] = args.script
    if getattr(args, "net", ""):
        payload["net"] = args.net

    try:
        return _post(f"/api/ext/{op}", payload, timeout=120)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "op": op}

def cmd_apps(args) -> dict:
    """列出手机已安装应用（对齐汇城 get_apk_list）。"""
    devs = Selection(args.all, args.devices, args.tag, args.filter, args.limit, args.exclude).resolve()
    out = {}
    for d in devs:
        try:
            r = _req(f"/api/devices/{d.serial}/apps")
            apps = r.get("apps") or []
            if getattr(args, "filter", ""):
                kw = args.filter.lower()
                apps = [a for a in apps if kw in (a.get("package", "") + a.get("name", "")).lower()]
            out[d.serial] = {
                "count": len(apps),
                "apps": [{"package": a.get("package"), "name": a.get("name"), "known": a.get("known")}
                         for a in apps[:getattr(args, "limit", 80)]],
            }
        except Exception as e:  # noqa: BLE001
            out[d.serial] = {"error": str(e)}
    return {"ok": True, "devices": out}


def cmd_publish(args) -> dict:
    """自动发布视频到指定平台（真流程，含选视频/文案/声明/发布）。"""
    from fishfleet.batch_publish import PRESET_REAL4, run_batch

    serials = []
    if args.all:
        r = _req("/api/devices")
        serials = [d["serial"] for d in (r.get("devices") or []) if d.get("state") == "online"]
    elif args.serial:
        serials = [x.strip() for x in args.serial.split(",") if x.strip()]
    else:
        return {"ok": False, "error": "必须指定 --serial 或 --all"}

    videos = [x.strip() for x in (args.videos or "").split(",") if x.strip()]
    if not videos:
        return {"ok": False, "error": "必须指定 --videos，如 1.mp4,2.mp4"}

    platform = args.platform
    results = []
    for s in serials:
        try:
            r = run_batch(serials=[s], videos=videos, platform=platform, settle=args.settle)
            results.append(r)
        except Exception as e:  # noqa: BLE001
            results.append({"ok": False, "serial": s, "error": str(e)})

    ok_n = sum(1 for r in results for x in (r.get("results") or []) if x.get("ok"))
    total = sum(len(r.get("results") or []) for r in results)
    return {
        "ok": ok_n == total and total > 0,
        "summary": {"published": ok_n, "total": total, "platform": platform},
        "results": results,
    }


def cmd_pipeline(args) -> dict:
    """完整流水线：AI 写文案 → 发布（可选生成视频）。"""
    from fishfleet.publish_ctl import get_copy_for

    videos = [x.strip() for x in (args.videos or "1.mp4").split(",") if x.strip()]
    platform = args.platform
    # 1. 拿文案
    copies = []
    for v in videos:
        try:
            c = get_copy_for(v) or {}
        except Exception:  # noqa: BLE001
            c = {}
        copies.append({"video": v, "title": c.get("title", ""), "desc": c.get("desc", ""),
                       "tags": c.get("tags", [])})

    if args.dry_run:
        return {"ok": True, "dry_run": True, "platform": platform, "copies": copies}

    # 2. 真要发
    args2 = type("A", (), {})()
    args2.all = args.all
    args2.serial = args.serial
    args2.videos = ",".join(videos)
    args2.platform = platform
    args2.settle = args.settle
    res = cmd_publish(args2)
    res["copies"] = copies
    return res


def cmd_otg(args) -> dict:
    """OTG 扫描（对照汇城 otg_scanning / otg_all_scanning）。"""
    act = getattr(args, "action", "info") or "info"
    if act == "info":
        return _req("/api/otg/info")
    if act == "scan":
        return _post("/api/otg/scan", {"net": args.net or "", "port": args.port})
    if act == "connect":
        return _post("/api/otg/connect", {"ip": args.ip, "port": args.port})
    return {"ok": False, "error": "未指定 action"}


def cmd_flow(args) -> dict:
    """执行一个预定义流程（多步组合）。"""
    from fishfleet.core.actions import run_flow

    devs = Selection(args.all, args.devices, args.tag, args.filter, args.limit, args.exclude).resolve()
    out = []
    for d in devs:
        try:
            r = run_flow(d, args.name, params=args.param or {})
            out.append({"serial": d.serial, "ok": True, "result": r})
        except Exception as e:  # noqa: BLE001
            out.append({"serial": d.serial, "ok": False, "error": str(e)})
    return {"ok": all(x["ok"] for x in out), "flow": args.name, "devices": out}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fishfleetctl",
        description="FishFleet 群控 CLI —— 并行操作 N 台手机，自带画面校验",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  fishfleetctl devices --all
  fishfleetctl tap --all --x 540 --y 960 --verify
  fishfleetctl launch --all --package com.ss.android.ugc.aweme --settle 1500 --verify
  fishfleetctl text --devices SERIAL0001 --text "你好"
  fishfleetctl shot --all --out ./shots
        """,
    )
    p.add_argument("command", choices=[
        "devices", "tap", "swipe", "key", "text", "scroll",
        "launch", "shell", "shot", "verify",
        # ---- 复杂操作 ----
        "apps",       # 列出已安装应用（对齐汇城 get_apk_list）
        "publish",    # 自动发布视频
        "pipeline",   # AI 写文案 + 发布
        "otg",        # OTG 扫描/接入
        "flow",       # 执行预定义流程
        "ext",        # 调用扩展能力（34 个汇城命令）
    ])

    # 设备选择
    p.add_argument("--all", action="store_true", help="所有在线设备")
    p.add_argument("--devices", default="", help="指定序列号，逗号分隔")
    p.add_argument("--tag", default="", help="按标签筛选")
    p.add_argument("--filter", default="", help='条件筛选，如 "battery>50"')
    p.add_argument("--limit", type=int, default=0, help="最多 N 台")
    p.add_argument("--exclude", default="", help="排除，逗号分隔")

    # 动作参数
    p.add_argument("--x", type=int, default=540)
    p.add_argument("--y", type=int, default=960)
    p.add_argument("--x1", type=int, default=540)
    p.add_argument("--y1", type=int, default=1500)
    p.add_argument("--x2", type=int, default=540)
    p.add_argument("--y2", type=int, default=500)
    p.add_argument("--ms", type=int, default=300, help="滑动时长（毫秒）")
    p.add_argument("--key", default="back", help="back|home|recent")
    p.add_argument("--text", default="", help="要输入的文字（支持中文）")
    p.add_argument("--v", type=int, default=-1, help="滚动方向")
    p.add_argument("--package", default="", help="应用包名")
    p.add_argument("--command", dest="shell_command", default="", help="ADB shell 命令")

    # ---- 复杂操作参数 ----
    p.add_argument("--serial", default="", help="publish/pipeline 用：单台序列号")
    p.add_argument("--videos", default="", help='要发布/生成的视频，如 "1.mp4,2.mp4"')
    p.add_argument("--platform", default="", choices=["", "xhs", "douyin", "bili"],
                   help="publish/pipeline 用：目标平台")
    p.add_argument("--dry-run", action="store_true", help="pipeline 用：只生成文案不发布")
    p.add_argument("--action", default="info", help="otg 用：info|scan|connect")
    p.add_argument("--net", default="", help="otg 用：网段前缀")
    p.add_argument("--ip", default="", help="otg 用：目标 IP")
    p.add_argument("--port", type=int, default=5555, help="otg 用：端口")
    p.add_argument("--name", default="", help="flow 用：流程名")
    p.add_argument("--param", default="", help="flow 用：JSON 参数")
    p.add_argument("--op", default="", help="ext 用：操作名，如 get_device_info")
    p.add_argument("--mode", default="", help="ext 用：USB 模式")
    p.add_argument("--enable", default=None, help="ext 用：true/false")
    p.add_argument("--script", default="", help="ext 用：动作脚本")

    # 校验
    p.add_argument("--verify", action="store_true", help="操作后校验画面一致性")
    p.add_argument("--settle", type=float, default=0.8, help="校验前等待秒数")
    p.add_argument("--sample", type=int, default=0, help="附带几张截图")
    p.add_argument("--images", default="", help="把校验截图保存到目录")
    p.add_argument("--llm", default="", choices=["", "auto", "video"],
                   help="校验时用哪个 LLM 判断（推荐 auto）")
    p.add_argument("--expect", default="",
                   help="期望画面，如 \"抖音首页\"。不填则用通用判断")

    # 其它
    p.add_argument("--concurrency", type=int, default=30, help="并发下发数")
    p.add_argument("--out", default="./shots", help="截图输出目录")
    p.add_argument("--human", action="store_true", help="人读格式")

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # shell 命令走 --shell-command，避免与 command 冲突
    if args.command == "shell":
        args.command = "shell"
        setattr(args, "command", "shell")

    handlers = {
        "devices": cmd_devices,
        "tap": cmd_tap,
        "swipe": cmd_swipe,
        "key": cmd_key,
        "text": cmd_text,
        "scroll": cmd_scroll,
        "launch": cmd_launch,
        "shell": cmd_shell,
        "shot": cmd_shot,
        "verify": cmd_verify,
        # ---- 复杂操作 ----
        "apps": cmd_apps,
        "publish": cmd_publish,
        "pipeline": cmd_pipeline,
        "otg": cmd_otg,
        "flow": cmd_flow,
        "ext": cmd_ext,
    }

    try:
        result = handlers[args.command](args)
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "interrupted"}, ensure_ascii=False))
        return 130

    if args.human:
        _print_human(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        return 1
    v = result.get("verify")
    if v and not v.get("in_sync"):
        return 2
    return 0


def _print_human(r: dict) -> None:
    if "devices" in r:
        print(f"设备 {r.get('count', 0)} 台")
        for d in r["devices"]:
            print(f"  {d.get('serial')}  #{d.get('index')}  {d.get('model')}  "
                  f"{d.get('state')}  {d.get('battery')}%")
        return

    print(f"命令: {r.get('command')}  执行 {r.get('executed', 0)} 台  "
          f"成功 {r.get('success', 0)}  失败 {r.get('failed', 0)}  "
          f"用时 {r.get('elapsed_ms', 0)}ms")
    for x in r.get("results", []):
        mark = "OK " if x.get("ok") else "ERR"
        print(f"  [{mark}] {x.get('serial')}  {x.get('ms')}ms  {x.get('error', '')}")

    v = r.get("verify")
    if v:
        print(f"\n画面校验: {'同步' if v.get('in_sync') else '不同步'}")
        for g in v.get("groups", []):
            print(f"  组 {g['hash']}  {g['count']} 台")
        if v.get("outliers"):
            print(f"  异常: {', '.join(v['outliers'])}")


if __name__ == "__main__":
    sys.exit(main())
