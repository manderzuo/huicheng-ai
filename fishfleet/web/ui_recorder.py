"""UI 信息录制器：抖音三链的「影子黑匣子」。

设计定位
--------
三链（发布/采集/回复）跑的时候，把每一次 UI dump、每一个动作、每一次报错
原样追加到 JSONL。业务代码完全无感——它不知道自己在被录。

开启方式
--------
    set FISHFLEET_UI_RECORD=1     # 然后重启服务

输出
----
    data/ui_record/<serial>_<进程启动时间>.jsonl

铁律（违反任何一条都会伤害主流程，所以都写在代码最外面）
------------------------------------------------------
1. 开关关闭时 ``record()`` 第一行就 return —— 零 IO、零对象分配。
2. 任何内部异常都被吞掉，绝不改变业务的返回值、异常类型与时序。
3. 只追加写文件；不读业务状态、不阻塞、不重试、不做任何判断。
4. 不缓存 ref/坐标给业务复用——ref 每次 dump 都会变，这里只做存档。

和「逐动作审计钩子」的区别（那条已被主人撤掉）：
那个钩子会在每个动作前插判断、可能改变执行路径；这里只往磁盘追加一行，
连返回值都不碰。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'ui_record'

#: 会记动作的工具（其余工具只在报错时记一条，避免噪音）
ACTION_TOOLS = {
    'mobile_click_on_screen_at_coordinates': 'tap',
    'mobile_double_tap_on_screen': 'double_tap',
    'mobile_long_press_on_screen_at_coordinates': 'long_press',
    'mobile_type_keys': 'type',
    'mobile_swipe_on_screen': 'swipe',
    'mobile_press_button': 'press',
    'mobile_launch_app': 'launch',
    'mobile_terminate_app': 'terminate',
    'mobile_open_url': 'open_url',
}

DUMP_TOOL = 'mobile_list_elements_on_screen'
FG_TOOL = 'mobile_get_foreground_app'
SCREEN_TOOL = 'mobile_get_screen_size'

MAX_NODES = 1500          # 异常膨胀的 dump 直接截断并标记，别把磁盘写爆
MAX_JSONL_BYTES = 200 * 1024 * 1024   # 单文件 200MB 上限，超过则滚动

_LOCK = threading.Lock()
_FILES: dict[str, Path] = {}
_SEQ: dict[str, int] = {}
_LAST_MD5: dict[str, str] = {}
_STARTED = time.strftime('%Y%m%d_%H%M%S')

#: 文件开关：存在即录制。比改环境变量/改任务定义都轻——放文件即开，删文件即关，
#: 且默认（文件不存在）就是关闭。
FLAG = ROOT / 'ENABLED'
_last_check = 0.0
_cached = False


def on() -> bool:
    """录制开关。两种方式任一即可：
    1) 环境变量 FISHFLEET_UI_RECORD=1（进程启动时生效）
    2) 存在 data/ui_record/ENABLED 文件（5 秒内自动生效，无需重启）
    """
    global _last_check, _cached
    if os.environ.get('FISHFLEET_UI_RECORD') == '1':
        return True
    now = time.monotonic()
    if now - _last_check > 5:
        _last_check = now
        try:
            _cached = FLAG.exists()
        except Exception:
            _cached = False
    return _cached


def enable() -> str:
    """打开录制（建开关文件）。"""
    ROOT.mkdir(parents=True, exist_ok=True)
    FLAG.write_text('1', encoding='utf-8')
    return str(FLAG)


def disable() -> bool:
    """关闭录制（删开关文件）。"""
    try:
        if FLAG.exists():
            FLAG.unlink()
            return True
    except Exception:
        pass
    return False


# --------------------------------------------------------------------- 内部
def _safe_serial(serial: str) -> str:
    text = ''.join(ch for ch in str(serial or '') if ch.isalnum() or ch in '-_')
    return text or 'unknown'


def _target(serial: str) -> Path:
    key = _safe_serial(serial)
    path = _FILES.get(key)
    if path is None:
        ROOT.mkdir(parents=True, exist_ok=True)
        path = ROOT / ('%s_%s.jsonl' % (key, _STARTED))
        _FILES[key] = path
        _SEQ.setdefault(key, 0)
    return path


def _parse_text_nodes(text: str):
    """解析 MCP 的**文本格式**元素树。

    Facebook 的 facebook_live 调 mobile_list_elements_on_screen 时没传
    format='json'，返回的是这种一行一个元素：
        @e1 FrameLayout id="com.x:id/xxx" at=0,1794 size=1080x126 [focused]
        @e5 TextView text="hello" label="xx" at=10,20 size=100x40
    不认这种格式，FB 的整棵树都会被当成"无法解析"丢掉（实测 13/13）。
    """
    import re as _re
    out = []
    kv_re = _re.compile(r'(\w+)=(?:"((?:[^"\\]|\\.)*)"|(\S+))')
    for line in (text or '').splitlines():
        line = line.strip()
        if not line.startswith('@e'):
            continue
        head = _re.match(r'^(@e\d+)\s+(\S+)\s*(.*)$', line)
        if not head:
            continue
        ref, typ, rest = head.groups()
        at = _re.search(r'\bat=(-?\d+),(-?\d+)', rest)
        size = _re.search(r'\bsize=(\d+)x(\d+)', rest)
        if not at or not size:
            continue
        kv = {}
        for m in kv_re.finditer(rest):
            key = m.group(1)
            val = m.group(2) if m.group(2) is not None else m.group(3)
            kv[key] = val.replace('\\"', '"') if val else ''
        out.append({
            'ref': ref,
            'type': typ,
            'text': kv.get('text', ''),
            'label': kv.get('label', '') or kv.get('name', ''),
            'identifier': kv.get('id', ''),
            'value': kv.get('value', ''),
            'coordinates': {'x': int(at.group(1)), 'y': int(at.group(2)),
                            'width': int(size.group(1)), 'height': int(size.group(2))},
            'enabled': 'disabled' not in rest,
            'selected': '[selected]' in rest,
            'focused': '[focused]' in rest,
        })
    return out


def _parse_nodes(result):
    """从 MCP 返回里解出元素数组；解不出返回 None（保留原文另存）。"""
    try:
        content = (result or {}).get('content') or []
    except Exception:
        return None
    for item in content:
        if not isinstance(item, dict) or item.get('type') != 'text':
            continue
        text = item.get('text') or ''
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            # 兼容被前后缀包住的 JSON（日志里偶尔混进说明文字）
            start, end = text.find('['), text.rfind(']')
            value = None
            if start >= 0 and end > start:
                try:
                    value = json.loads(text[start:end + 1])
                except ValueError:
                    value = None
            if value is None:
                # 再试文本格式（Facebook 走的是这种）
                parsed = _parse_text_nodes(text)
                if parsed:
                    return parsed
                continue
        if isinstance(value, list) and value and all(isinstance(e, dict) for e in value):
            return value
    return None


def _error_text(error) -> str:
    try:
        return '%s: %s' % (type(error).__name__, error)
    except Exception:
        return 'unknown'


def _write(serial: str, event: dict) -> None:
    """串行写一行。任何失败都吞掉——绝不让录制影响业务。"""
    key = _safe_serial(serial)
    try:
        with _LOCK:
            path = _target(serial)
            if path.exists() and path.stat().st_size > MAX_JSONL_BYTES:
                path = path.with_name(path.stem + '_roll.jsonl')
                _FILES[key] = path
            _SEQ[key] = _SEQ.get(key, 0) + 1
            event['seq'] = _SEQ[key]
            event['ts'] = round(time.time(), 3)
            line = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
            with path.open('a', encoding='utf-8') as handle:
                handle.write(line + '\n')
    except Exception:
        pass


# --------------------------------------------------------------------- 对外
def record(tool, args, serial, result=None, error=None) -> None:
    """挂点入口：由 mobile_gateway.call() 在每次调用后调用。"""
    if not on():
        return
    try:
        tool = str(tool or '')

        # 1) 完整元素树（最重要的证据）
        if tool == DUMP_TOOL:
            nodes = _parse_nodes(result)
            event = {'kind': 'dump', 'tool': tool, 'serial': serial}
            if nodes is None:
                event['kind'] = 'dump_unparsed'
                event['raw'] = (json.dumps(result, ensure_ascii=False)[:2000]
                                if result is not None else '')
                if error is not None:
                    event['error'] = _error_text(error)
                _write(serial, event)
                return
            truncated = len(nodes) > MAX_NODES
            if truncated:
                nodes = nodes[:MAX_NODES]
            digest = hashlib.md5(
                json.dumps(nodes, ensure_ascii=False, sort_keys=True).encode('utf-8')
            ).hexdigest()
            key = _safe_serial(serial)
            event.update(node_count=len(nodes), md5=digest,
                         nodes=nodes, truncated=truncated)
            # 连续的相同树只存一份，后续记引用（滚动页面时大量重复）
            if _LAST_MD5.get(key) == digest:
                event = {'kind': 'dump_dup', 'tool': tool, 'serial': serial,
                         'node_count': len(nodes), 'md5': digest}
            else:
                _LAST_MD5[key] = digest
            _write(serial, event)
            return

        # 2) 前台应用 / 屏幕尺寸（给 dump 补上下文）
        if tool in (FG_TOOL, SCREEN_TOOL):
            event = {'kind': 'context', 'tool': tool, 'serial': serial}
            if isinstance(result, dict):
                event['result'] = (json.dumps(result, ensure_ascii=False)[:600])
            if error is not None:
                event['error'] = _error_text(error)
            _write(serial, event)
            return

        # 3) 动作流水
        action = ACTION_TOOLS.get(tool)
        if action:
            event = {'kind': 'action', 'act': action, 'tool': tool, 'serial': serial}
            for key in ('ref', 'x', 'y', 'direction', 'distance', 'button',
                        'text', 'packageName', 'url'):
                if key in (args or {}):
                    value = args[key]
                    if key == 'text' and isinstance(value, str):
                        value = value[:120]
                    event[key] = value
            event['ok'] = error is None
            if error is not None:
                event['error'] = _error_text(error)
            _write(serial, event)
            return

        # 4) 其他工具的失败现场（成功的不记，省体积）
        if error is not None:
            _write(serial, {'kind': 'tool_error', 'tool': tool, 'serial': serial,
                            'error': _error_text(error)})
    except Exception:
        pass


def where() -> str:
    """给日志/报告用：当前写盘目录。"""
    return str(ROOT)


def stats() -> dict:
    """已写了多少事件（排查用，不影响业务）。"""
    with _LOCK:
        return {'files': {k: str(v) for k, v in _FILES.items()},
                'seq': dict(_SEQ)}
