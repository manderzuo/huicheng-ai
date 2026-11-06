"""直接用 adb `uiautomator dump` 读界面，替代 Mobile MCP 的 list_elements。

实测对比（手机7，2026-11-02）：
  · mobile_list_elements_on_screen format=json : 3.49 秒 / 次，返回 32 KB
  · mobile_list_elements_on_screen format=text : 3.77 秒 / 次，返回 15 KB
  · adb shell uiautomator dump                 : 0.31 秒（dump 本身）
  · adb dump + pull 到本地                     : 0.71 秒
→ 快 5-10 倍。

输出结构与 Mobile MCP 的 JSON 保持一致（identifier/text/label/type/coordinates），
区别是 **没有 ref**，所以点击要用坐标而不是 ref。

用法：
    from .adb_ui import dump_ui
    tree = dump_ui(serial)        # 失败会抛异常，由调用方决定是否回退到 MCP
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ADB_PATH = __import__('fishfleet.paths', fromlist=['adb_path']).adb_path()
REMOTE_PATH = '/sdcard/ui_dump.xml'

_BOUNDS = re.compile(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]')


class AdbUiError(RuntimeError):
    pass


def _adb(serial, *args, timeout=15):
    cmd = [ADB_PATH, '-s', serial, *args]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise AdbUiError('adb 命令失败: %s' % proc.returncode)
    return proc.stdout


def _parse_bounds(text):
    m = _BOUNDS.search(text or '')
    if not m:
        return None
    x1, y1, x2, y2 = (int(v) for v in m.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return {'x': x1, 'y': y1, 'width': x2 - x1, 'height': y2 - y1}


def _walk(node, out):
    tag = node.tag
    if tag == 'node':
        attrs = node.attrib
        box = _parse_bounds(attrs.get('bounds'))
        if box:
            out.append({
                'identifier': attrs.get('resource-id') or '',
                'text': attrs.get('text') or '',
                'label': attrs.get('content-desc') or '',
                'type': attrs.get('class') or '',
                'ref': '',                     # adb 不分配 ref，点击用坐标
                'coordinates': box,
                'package': attrs.get('package') or '',
                'clickable': attrs.get('clickable') == 'true',
                'enabled': attrs.get('enabled') == 'true',
            })
    for child in list(node):
        _walk(child, out)


def dump_ui(serial, timeout=15):
    """读一次界面，返回与 Mobile MCP 同结构的元素列表（按文档顺序）。

    抛 AdbUiError 表示这次读失败，调用方应回退到 MCP。
    """
    _adb(serial, 'shell', 'uiautomator', 'dump', REMOTE_PATH, timeout=timeout)

    # 优先用 exec-out 直接取内容（少一次 pull 往返）
    raw = _adb(serial, 'exec-out', 'cat', REMOTE_PATH, timeout=timeout)
    text = raw.decode('utf-8', errors='replace').strip()
    if not text.startswith('<?xml') and '<hierarchy' not in text:
        # 兜底：pull 到本地再读
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / 'ui.xml'
            _adb(serial, 'pull', REMOTE_PATH, str(local), timeout=timeout)
            text = local.read_text(encoding='utf-8', errors='replace')

    start = text.find('<?xml')
    if start < 0:
        start = text.find('<hierarchy')
    if start < 0:
        raise AdbUiError('dump 内容里没有 XML')
    try:
        root = ET.fromstring(text[start:])
    except ET.ParseError as exc:
        raise AdbUiError('XML 解析失败: %s' % exc)

    out = []
    _walk(root, out)
    if not out:
        raise AdbUiError('dump 没有读到任何节点')
    return out


def center(el):
    """元素中心坐标，用于没有 ref 的点击。"""
    box = (el or {}).get('coordinates') or {}
    return (int(box.get('x', 0) + box.get('width', 0) / 2),
            int(box.get('y', 0) + box.get('height', 0) / 2))
