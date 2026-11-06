"""
FishFleet — 命令行入口

用法：
    python -m fishfleet.cli doctor              # 环境检查
    python -m fishfleet.cli devices             # 列出设备
    python -m fishfleet.cli agent --goal "打开抖音"  # 让 AI 干活
    python -m fishfleet.cli publish --device SERIAL0001 --video 1.mp4 --title "标题"
    python -m fishfleet.cli gen --topic "雨夜的猫"   # 调 MCP 生成视频
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 环境检查
# ---------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    from .core.config import get_config

    print("=== FishFleet 环境检查 ===\n")
    ok = True

    # Python
    print(f"Python: {sys.version.split()[0]}")

    # 依赖
    deps = ["yaml", "httpx", "uiautomator2", "PIL"]
    for d in deps:
        try:
            __import__(d)
            print(f"  ✓ {d}")
        except ImportError:
            print(f"  ✗ {d}  (pip install {d})")
            ok = False

    # 配置
    try:
        cfg = get_config()
        problems = cfg.validate()
        if problems:
            print("\n配置问题:")
            for p in problems:
                print(f"  ✗ {p}")
            ok = False
        else:
            print("\n配置: ✓")
    except Exception as e:  # noqa: BLE001
        print(f"\n配置加载失败: {e}")
        ok = False

    # ADB
    try:
        from .core.device import get_manager
        mgr = get_manager()
        devs = mgr.refresh()
        online = mgr.online()
        print(f"\n设备: {len(devs)} 台，在线 {len(online)} 台")
        for d in devs[:5]:
            print(f"  [{d.index:02d}] {d.serial}  {d.state.value}")
        if len(devs) > 5:
            print(f"  ... 还有 {len(devs)-5} 台")
    except Exception as e:  # noqa: BLE001
        print(f"\n设备检查失败: {e}")
        ok = False

    print("\n" + ("=== 全部通过 ===" if ok else "=== 有问题需要处理 ==="))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 设备
# ---------------------------------------------------------------------------

def cmd_devices(args) -> int:
    from .core.device import get_manager

    mgr = get_manager()
    devs = mgr.refresh()

    print(f"{'#':<4}{'序列号':<18}{'状态':<14}{'型号':<16}{'电量'}")
    print("-" * 64)
    for d in devs:
        battery = f"{d.health.battery}%" if d.health.battery is not None else "-"
        print(f"{d.index:<4}{d.serial:<18}{d.state.value:<14}{d.model:<16}{battery}")

    print(f"\n共 {len(devs)} 台，在线 {len(mgr.online())} 台")
    return 0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def cmd_agent(args) -> int:
    from .llm.agent import Agent

    if not args.device:
        print("需要指定设备: --device <序列号>")
        return 1

    agent = Agent(args.device)
    state = agent.run(args.goal, max_steps=args.max_steps, verbose=True)

    print("\n=== 结果 ===")
    print(f"完成: {state.done}")
    print(f"结果: {state.result or state.error}")
    print(f"步数: {state.step}")

    if args.json:
        print("\n" + json.dumps({
            "done": state.done,
            "result": state.result,
            "error": state.error,
            "history": state.history,
        }, ensure_ascii=False, indent=2))

    return 0 if state.done else 1


# ---------------------------------------------------------------------------
# 发布
# ---------------------------------------------------------------------------

def cmd_publish(args) -> int:
    from .apps.douyin import DouyinAdapter
    from .flows.publish import PublishFlow

    if not args.device or not args.video:
        print("需要: --device <序列号> --video <文件>")
        return 1

    adapter_map = {
        "douyin": DouyinAdapter,
    }
    adapter_cls = adapter_map.get(args.platform.lower())
    if adapter_cls is None:
        print(f"不支持的平台: {args.platform}（目前支持: {list(adapter_map)}）")
        return 1

    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]

    flow = PublishFlow(args.device, adapter_cls)
    result = flow.run(args.video, args.title, args.desc, tags)

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


# ---------------------------------------------------------------------------
# 视频生成（MCP）
# ---------------------------------------------------------------------------

def cmd_gen(args) -> int:
    from .content.mcp_client import get_video_client

    client = get_video_client()

    if not client.enabled:
        print("视频生成未启用。请在 config.yaml 中设置 video_generation.enabled = true")
        print("并配置 video_generation.mcp.server 指向你的 MCP 服务")
        return 1

    print(f"正在生成：{args.topic or args.prompt}")
    result = client.generate(
        topic=args.topic,
        prompt=args.prompt,
        style=args.style,
        duration=args.duration,
    )

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


# ---------------------------------------------------------------------------
# 审核
# ---------------------------------------------------------------------------

def cmd_review(args) -> int:
    from .content.reviewer import ContentReviewer

    if not args.video:
        print("需要: --video <文件>")
        return 1

    reviewer = ContentReviewer()
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    result = reviewer.review(
        args.video,
        title=args.title or "",
        description=args.desc or "",
        tags=tags,
        platform=args.platform,
        use_llm=not args.no_llm,
    )

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fishfleet", description="AI 驱动的多机内容运营系统")
    sub = p.add_subparsers(dest="command", required=True)

    # doctor
    sub.add_parser("doctor", help="环境检查")

    # devices
    sub.add_parser("devices", help="列出设备")

    # agent
    a = sub.add_parser("agent", help="让 AI 执行任务")
    a.add_argument("--device", "-d", help="设备序列号")
    a.add_argument("--goal", "-g", required=True, help="任务目标（自然语言）")
    a.add_argument("--max-steps", type=int, default=40)
    a.add_argument("--json", action="store_true")

    # publish
    pb = sub.add_parser("publish", help="发布内容")
    pb.add_argument("--device", "-d", required=True)
    pb.add_argument("--video", "-v", required=True)
    pb.add_argument("--platform", "-p", default="douyin")
    pb.add_argument("--title", "-t", default="")
    pb.add_argument("--desc", default="")
    pb.add_argument("--tags", default="")

    # gen
    g = sub.add_parser("gen", help="生成视频（MCP）")
    g.add_argument("--topic", default="")
    g.add_argument("--prompt", default="")
    g.add_argument("--style", default="")
    g.add_argument("--duration", type=int, default=0)

    # review
    r = sub.add_parser("review", help="审核内容")
    r.add_argument("--video", "-v", required=True)
    r.add_argument("--title", "-t", default="")
    r.add_argument("--desc", default="")
    r.add_argument("--tags", default="")
    r.add_argument("--platform", "-p", default="抖音")
    r.add_argument("--no-llm", action="store_true", help="只做规则检查")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "doctor": cmd_doctor,
        "devices": cmd_devices,
        "agent": cmd_agent,
        "publish": cmd_publish,
        "gen": cmd_gen,
        "review": cmd_review,
    }

    fn = handlers.get(args.command)
    if fn is None:
        parser.print_help()
        return 1

    try:
        return fn(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"错误: {type(e).__name__}: {e}")
        if "--debug" in (argv or sys.argv):
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
