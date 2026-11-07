"""桌面窗口关闭 → 整个程序干净退出。

2026-11-07 主人反馈：「每次点软件关闭时，后台关不干净」。
根因（desktop.py）：窗口是 Edge `--app` 起的**独立进程**，关掉它只是关了浏览器，
主进程还在 `while True: time.sleep(1)` 里守着，于是：
    · uvicorn 后端线程还活着（8765 一直占着）
    · mobile-mcp 的 Node 通道（预建 8 个）全留着
    · mobilecli daemon / ffmpeg 投屏 / 一堆 adb 客户端进程继续挂后台
下次双击又会 `_free_port()` 找新端口起一份，越积越多，还容易 409 / 端口冲突。

本模块只做三件事，保持极简：
    1. `beacon()`   —— 前端收到「窗口要关了」，打一个关闭请求；
    2. `ping()`     —— 前端还活着就续命（刷新页面不会误杀）；
    3. `should_quit()` —— 主进程每秒问一次，超过宽限期就真退。

宽限期默认 8 秒：足够让后端把任务状态写盘，也能被一次刷新取消。
所有函数都不抛异常，绝不因为关不掉而拖住主流程。
"""
from __future__ import annotations

import os
import threading
import time

#: 收到关闭请求后再等多少秒才真正退出（可被 FISHFLEET_QUIT_GRACE 覆盖）
def _grace() -> float:
    try:
        v = float(os.environ.get('FISHFLEET_QUIT_GRACE', '') or 0)
    except ValueError:
        v = 0.0
    return v if v > 0 else 8.0


_lock = threading.Lock()
_state = {
    'quit_at': 0.0,      # 计划退出的时间点；0 表示没有待处理的关闭
    'reason': '',        # 触发原因，写日志用
    'closed': False,     # 已经决定退出（消费一次即终态）
    'last_beat': 0.0,    # 最后一次前端心跳
    'watchdog': 0.0,     # 看门狗超时秒数；0 = 不启用
}


def beacon(reason: str = 'window_close', grace: float | None = None) -> dict:
    """登记一个关闭请求。重复调用会顺延，不会叠加。"""
    g = _grace() if grace is None else max(0.0, float(grace))
    with _lock:
        if _state['closed']:
            return {'ok': True, 'state': 'closing'}
        _state['quit_at'] = time.monotonic() + g
        _state['reason'] = str(reason or 'window_close')[:80]
        return {'ok': True, 'state': 'pending', 'grace': g}


def ping() -> dict:
    """前端仍在线：取消尚未触发的关闭请求（页面刷新/误触的兜底）。"""
    with _lock:
        _state['last_beat'] = time.monotonic()
        if _state['closed']:
            return {'ok': True, 'state': 'closing'}
        had = bool(_state['quit_at'])
        _state['quit_at'] = 0.0
        _state['reason'] = ''
    return {'ok': True, 'state': 'alive', 'cancelled': had}


def arm_watchdog(timeout: float = 45.0) -> None:
    """启用「前端心跳」看门狗（只在开窗口模式下用）。

    为什么需要它：窗口关掉时前端发 beacon 不可靠（Edge 复用实例、
    崩溃、被强杀都可能丢）。心跳是更硬的信号——页面活着就会一直 ping，
    关掉之后自然就断了。超时即认定窗口没了。

    默认 45 秒：前端每 15 秒 ping 一次，容忍 3 次丢失，误杀概率极低。
    headless/服务模式**不要启用**（没有前端，会自己把自己关掉）。
    """
    with _lock:
        _state['watchdog'] = max(10.0, float(timeout))
        # 注意：**不要**在这里写 last_beat。只有前端真的 ping 过一次，
        # 计时才启动；否则页面根本没加载成功（白屏/端口错），
        # 把它当成「窗口关了」直接退出，用户会看到程序一闪就没。
        # stale() 对 last_beat==0 永远返回 False，正是这个保护。


def beat() -> None:
    """窗口模式下由主循环打着玩：只更新时间戳。"""
    with _lock:
        _state['last_beat'] = time.monotonic()


def stale() -> bool:
    """心跳超时了吗（只有启用了看门狗才可能为真）。"""
    with _lock:
        if _state['closed'] or not _state['watchdog'] or not _state['last_beat']:
            return False
        idle = time.monotonic() - _state['last_beat']
        if idle <= _state['watchdog']:
            return False
        _state['closed'] = True
        _state['reason'] = 'heartbeat_lost'
        return True


def watchdog_left() -> float:
    """距离心跳超时还有多少秒（诊断用）。"""
    with _lock:
        if not _state['watchdog'] or not _state['last_beat']:
            return -1.0
        return round(max(0.0, _state['watchdog'] - (time.monotonic() - _state['last_beat'])), 1)


def should_quit() -> bool:
    """到点了吗？调用方拿到 True 就该收尾退出。"""
    with _lock:
        if _state['closed']:
            return True
        if not _state['quit_at']:
            return False
        if time.monotonic() < _state['quit_at']:
            return False
        _state['closed'] = True
        return True


def reason() -> str:
    with _lock:
        return _state['reason']


def reset() -> None:
    """测试/热重载用：把状态清空。"""
    with _lock:
        _state['quit_at'] = 0.0
        _state['reason'] = ''
        _state['closed'] = False
        _state['last_beat'] = 0.0
        _state['watchdog'] = 0.0


def pending() -> dict:
    """诊断用快照。"""
    with _lock:
        left = max(0.0, _state['quit_at'] - time.monotonic()) if _state['quit_at'] else 0.0
    return {'pending': bool(left), 'seconds_left': round(left, 1),
            'closed': _state['closed'], 'reason': _state['reason'],
            'watchdog': _state['watchdog'],
            'beat_left': watchdog_left()}
