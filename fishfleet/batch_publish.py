"""批量发布 —— 7 个视频 × N 台设备，逐条执行并记录。

用法
----
    python -m fishfleet.batch_publish --platform dy --serial SERIAL0001
    python -m fishfleet.batch_publish --preset real4

设计
----
每台设备、每个视频：
  1. bring_to_front()  把该视频刷成相册第一个（touch + 媒体扫描）
  2. publish()         走完整发布流程
  3. 记录结果（含截图路径）

失败不中断，继续下一个；最后汇总。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fishfleet.publish_ctl import Phone, DouyinPublisher, XhsPublisher, BiliPublisher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

PUSHERS = {"dy": DouyinPublisher, "xhs": XhsPublisher, "bili": BiliPublisher}

# 用户确认的 4 台
PRESET_REAL4 = [
    {"idx": 1, "serial": "SERIAL0001", "platform": "xhs", "name": "小红书"},
    {"idx": 2, "serial": "SERIAL0001", "platform": "dy",  "name": "抖音"},
    {"idx": 3, "serial": "SERIAL0001", "platform": "bili", "name": "哔哩哔哩"},
    {"idx": 4, "serial": "SERIAL0001", "platform": "dy",  "name": "抖音"},
]

VIDEOS = [f"{i}.mp4" for i in range(1, 8)]


def load_copy() -> dict:
    path = os.path.join(DATA, "copy.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def publish_all(devices: list, videos: list, skip_existing: bool = True,
                settle: float = 2.0) -> dict:
    copy = load_copy()
    results = []
    t0 = time.time()

    for dev in devices:
        serial = dev["serial"]
        platform = dev["platform"]
        cls = PUSHERS.get(platform)
        if not cls:
            print(f"[{serial}] 未知平台 {platform}")
            continue

        print(f"\n{'='*60}")
        print(f"设备 #{dev['idx']} {serial} → {dev['name']}（{platform}）")
        print(f"{'='*60}")

        phone = Phone(serial, f"dev{dev['idx']:02d}")

        for vi, video in enumerate(videos, 1):
            meta = copy.get(video, {})
            title = meta.get("topic", video)
            desc = meta.get("desc", "")
            tags = meta.get("tags", [])

            # ★★ 抖音的描述里绝对不能出现 # —— 输入 # 会瞬间跳到话题搜索页，
            #    整个发布流程被打断（这是实测踩了两次的坑）。
            #    小红书和 B站 能接受 #标签，照常带上。
            if tags and platform != "dy":
                desc = desc + "\n" + " ".join("#" + t for t in tags)
            elif tags and platform == "dy":
                print(f"    [注意] 抖音不带 #标签（会打断流程），标签仅记入报告")

            print(f"\n--- [{vi}/{len(videos)}] {video}  「{title}」 ---")

            # 1. 排到相册第一
            front_ok = phone.bring_to_front(video)
            if not front_ok:
                print(f"    ✗ 无法把 {video} 排到相册第一，跳过")
                results.append({
                    "serial": serial, "platform": platform, "video": video,
                    "ok": False, "error": "bring_to_front failed",
                })
                continue

            # 2. 发布
            pub = cls(phone)
            try:
                r = pub.publish(video, title, desc, row=0, col=0)
                d = r.to_dict()
            except Exception as e:  # noqa: BLE001
                d = {"ok": False, "error": str(e), "video": video,
                     "platform": platform, "serial": serial}

            d["idx"] = dev["idx"]
            results.append(d)
            mark = "OK " if d.get("ok") else "ERR"
            print(f"    [{mark}] {d.get('elapsed', 0):.0f}s  {d.get('error', '')}")

            time.sleep(settle)

    el = time.time() - t0
    ok_n = sum(1 for r in results if r.get("ok"))
    summary = {
        "ok": ok_n == len(results),
        "total": len(results),
        "success": ok_n,
        "failed": len(results) - ok_n,
        "elapsed": round(el, 1),
        "results": results,
    }

    # ★ 按时间戳保存，避免多次运行互相覆盖
    ts = time.strftime("%Y%m%d_%H%M%S")
    hist = os.path.join(DATA, "publish_history")
    os.makedirs(hist, exist_ok=True)

    # 1) 完整历史
    out_hist = os.path.join(hist, f"batch_{ts}.json")
    with open(out_hist, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 2) 最新一份（方便脚本读）
    out = os.path.join(DATA, "batch_publish_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 3) 追加到总账（JSONL，永不覆盖）
    ledger = os.path.join(DATA, "publish_ledger.jsonl")
    with open(ledger, "a", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps({"ts": ts, **item}, ensure_ascii=False) + "\n")

    print(f"\n结果已保存:")
    print(f"  历史: {out_hist}")
    print(f"  最新: {out}")
    print(f"  总账: {ledger}")

    return summary


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="FishFleet 批量发布")
    ap.add_argument("--preset", default="", help="real4 = 用户确认的 4 台")
    ap.add_argument("--serial", default="", help="单台序列号")
    ap.add_argument("--platform", default="dy", choices=list(PUSHERS))
    ap.add_argument("--videos", default="", help="指定视频，逗号分隔；空=1-7")
    ap.add_argument("--settle", type=float, default=2.0)
    a = ap.parse_args(argv)

    if a.preset == "real4":
        devices = PRESET_REAL4
    elif a.serial:
        devices = [{"idx": 0, "serial": a.serial, "platform": a.platform,
                    "name": a.platform}]
    else:
        print("需要 --preset real4 或 --serial xxx")
        return 1

    videos = [v.strip() for v in a.videos.split(",") if v.strip()] or VIDEOS

    print(f"设备 {len(devices)} 台，视频 {len(videos)} 个，共 {len(devices)*len(videos)} 条")
    summary = publish_all(devices, videos, settle=a.settle)
    print(f"\n总计 {summary['total']} 条：成功 {summary['success']}，"
          f"失败 {summary['failed']}，用时 {summary['elapsed']:.0f} 秒")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
