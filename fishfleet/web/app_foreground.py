"""确保目标 App 在前台：不属于任何平台，回复/提取链路共用。

2026-11-06 主人要求：
> 2号 出错：提取：抖音不在前台；提取器不会启动/重启应用，请先人工切到目标页
> 这是错误的啊，要会自动启动切换啊。

以前各平台都把"App 已经在前台"当成前提，不在就报错让人先手动切。
这里改成：不在前台就**自动拉起来并等它上来**，用 adb 判断（约 0.1 秒，
不占 MCP 通道、不产生额外 dump）。
"""
from __future__ import annotations

import subprocess
import time

ADB_CANDIDATES = (
    __import__('fishfleet.paths', fromlist=['adb_path']).adb_path(),
    'adb',
)


def _adb() -> str:
    import os
    for path in ADB_CANDIDATES:
        if path == 'adb' or os.path.exists(path):
            return path
    return 'adb'


def foreground_activity(serial: str) -> str:
    """返回 `package/activity` 形式的前台组件；读不到返回空串。"""
    try:
        out = subprocess.run(
            [_adb(), '-s', serial, 'shell',
             'dumpsys window | grep mCurrentFocus'],
            capture_output=True, text=True, encoding='utf-8', errors='ignore',
            timeout=8).stdout or ''
    except Exception:                                     # noqa: BLE001
        return ''
    # mCurrentFocus=Window{xxx u0 com.pkg/com.pkg.Activity}
    for token in out.replace('}', ' ').replace('{', ' ').split():
        if '/' in token and '.' in token:
            return token.strip()
    return ''


def foreground_package(serial: str) -> str:
    comp = foreground_activity(serial)
    return comp.split('/')[0] if comp else ''


def ensure_app_front(serial: str, package: str, wait: float = 15.0,
                     log=None) -> bool:
    """确保 package 在前台；不在就启动并等它上来。

    返回 True 表示已在前台（原本就在、或自动拉起成功）。
    返回 False 表示拉起后仍未到前台（调用方自行决定报错还是继续）。
    绝不杀进程：只 `am start`，不 `force-stop`（杀进程会让下次冷启动更慢）。
    """
    def _say(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:                            # noqa: BLE001
                pass

    if foreground_package(serial) == package:
        return True

    _say('目标应用不在前台，自动启动：%s' % package)
    try:
        subprocess.run(
            [_adb(), '-s', serial, 'shell', 'monkey', '-p', package,
             '-c', 'android.intent.category.LAUNCHER', '1'],
            capture_output=True, text=True, encoding='utf-8', errors='ignore',
            timeout=20)
    except Exception:                                    # noqa: BLE001
        pass

    deadline = time.monotonic() + max(1.0, wait)
    while time.monotonic() < deadline:
        time.sleep(0.8)
        if foreground_package(serial) == package:
            _say('已到前台：%s' % package)
            return True
    _say('自动启动后仍未到前台：%s' % package)
    return False
