"""Facebook UI 采集与运行时定位的底层封装。

设计原则（沿用 douyin_templates.py 的实测教训）：
  · **不持久化 ref，也不持久化坐标**。ref 每次 dump 都会重排；
    坐标随屏幕、版本、账号状态变化。这里只做「当次 dump 内」的解析。
  · 页面判定必须多锚点，不能靠单个 label。
  · 本模块只读 UI：dump、前台应用、点击、返回、滑动。
    任何会产生对外内容的动作（发帖、评论、私信、点赞、关注、分享、支付、授权）
    都不在这里自动执行，调用方必须先取得主人确认。

编码注意：Mobile MCP 子进程输出是 UTF-8；PowerShell 控制台默认 gbk 时中文会显示成
`���`，那只是显示层问题，数据本身正确。脚本里用 setup_utf8_stdout() 修正。
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web.mobile_mcp_adapter import MobileMCPAdapter  # noqa: E402

PKG = 'com.facebook.katana'

# 行格式：@e60 ViewGroup text="…" label="…" value="…" id="…" at=x,y size=WxH [flags]
_LINE = re.compile(
    r'^@(?P<ref>\S+)\s+(?P<type>\S+)'
    r'(?P<attrs>(?:\s+(?:text|label|name|value|id)="[^"]*")*)'
    r'\s+at=(?P<x>-?\d+),(?P<y>-?\d+)\s+size=(?P<w>\d+)x(?P<h>\d+)'
    r'(?P<flags>.*)$'
)
_ATTR = re.compile(r'(text|label|name|value|id)="([^"]*)"')
_FLAG_WORDS = ('focused', 'selected', 'checked', 'disabled')


def setup_utf8_stdout() -> None:
    """控制台是 gbk 时，让中文正常打印。不改变数据。"""
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass


def parse_elements(dump_text: str) -> list[dict]:
    """把 mobile_list_elements_on_screen 的文本解析成元素列表。

    无法解析的行直接丢弃——它们不构成可用锚点，绝不猜测。
    """
    out: list[dict] = []
    for line in dump_text.splitlines():
        line = line.strip()
        if not line.startswith('@'):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        attrs = dict(_ATTR.findall(m.group('attrs')))
        flags = m.group('flags') or ''
        out.append({
            'ref': '@' + m.group('ref'),
            'type': m.group('type'),
            'text': attrs.get('text', ''),
            'label': attrs.get('label', ''),
            'name': attrs.get('name', ''),
            'value': attrs.get('value', ''),
            'identifier': attrs.get('id', ''),
            'x': int(m.group('x')),
            'y': int(m.group('y')),
            'width': int(m.group('w')),
            'height': int(m.group('h')),
            **{f: (f in flags) for f in _FLAG_WORDS},
        })
    return out


def blob(el: dict) -> str:
    """一个元素的全部可见语义，用于正则匹配。"""
    return ' '.join(x for x in (el.get('label', ''), el.get('text', ''),
                                el.get('name', ''), el.get('value', '')) if x)


# ---------------------------------------------------------------------------
# 设备会话
# ---------------------------------------------------------------------------
@dataclass
class Foreground:
    package: str
    activity: str

    @property
    def raw(self) -> str:
        return f'{self.package}/{self.activity}'


def safe_point(el: dict, risky_rects: list[tuple[int, int, int, int]]
               ) -> tuple[int, int] | None:
    """在元素内部挑一个不落在任何高风险控件上的点。

    来历：2026-11-01 21:02 点搜索结果条目中心时，坐标正好压在条目内的
    「加入」按钮上，把账号真实加进了一个公开社团。条目中心并不总是安全区。
    从"中心 → 左侧 1/4 → 1/8 → 1/12"里挑第一个不冲突的点。
    """
    def clash(x: int, y: int) -> bool:
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in risky_rects)

    mid_y = el['y'] + el['height'] // 2
    for frac in (2, 4, 8, 12):
        x = el['x'] + el['width'] // frac
        if not clash(x, mid_y):
            return x, mid_y
    return None


def rect_of(el: dict) -> tuple[int, int, int, int]:
    return (el['x'], el['y'], el['x'] + el['width'], el['y'] + el['height'])


def tap_with_gate(s: 'DeviceSession', el: dict, els: list[dict],
                  risk_lookup, allow_send: bool = False) -> bool:
    """统一的带闸门点击：先查风险，再用安全点，最后才点。

    `risk_lookup(el) -> list[str]` 由调用方提供（flow 用模板查、recipe 用 JSON 查），
    这样三处脚本共用同一套点击策略，不会各自漂移。
    """
    risky = risk_lookup(el)
    if risky and not allow_send:
        print('  拒绝：目标控件属高风险 -> ' + ', '.join(risky))
        return False
    if risky:
        print('  ⚠ 按调用方授权继续：' + ', '.join(risky))
    rects = [rect_of(e) for e in els if e is not el and risk_lookup(e)]
    pt = safe_point(el, rects)
    if pt is None:
        # 2026-11-02 04:34 实测：有元素（如带未读角标的通知 tab）内部所有候选点
        # 都被某个高风险矩形覆盖，导致连"点通知 tab"都被拒、整条 walk 挂掉。
        # 目标本身既然不是高风险控件，就退化成点中心并明确警告，
        # 而不是把正常导航也一并禁掉。
        if not risky:
            cx = el['x'] + el['width'] // 2
            cy = el['y'] + el['height'] // 2
            print(f'  ⚠ 找不到无冲突点，退化为元素中心 ({cx},{cy})：目标本身非高风险')
            s.tap(cx, cy)
            return True
        print('  拒绝：元素内部找不到避开高风险控件的点')
        return False
    print(f'  点 {el["ref"]} 安全点={pt}')
    s.tap(*pt)
    return True


def is_visible(el: dict, screen_w: int, screen_h: int, top: int = 140) -> bool:
    """元素中心点是否落在屏幕内（还要避开状态栏）。

    来历：2026-11-02 03:55 发现横向 ViewPager 里，**未显示页的元素坐标是负数**
    （如 `Photo at=(-1080,391)`）。只查 y 不查 x 就会把屏幕外元素当成可见，
    点下去等于点到屏幕外，表现是"点了没反应"而不是报错。
    用中心点判断而不是矩形完全包含，避免把横跨全屏的元素误判为不可见。
    """
    cx = el['x'] + el['width'] // 2
    cy = el['y'] + el['height'] // 2
    return 0 <= cx < screen_w and top <= cy < screen_h


class DeviceSession:
    """串行访问 Mobile MCP；只做可逆的读与导航。"""

    def __init__(self, device: str, timeout: float = 60.0):
        self.device = device
        self.timeout = timeout
        self._mcp = None      # 网关 adapter（由 mobile_gateway.device_session 提供）
        self._cm = None

    def __enter__(self) -> 'DeviceSession':
        # 2026-11-05：原来自己 new 一个 MCP 客户端，任务线程已绑定同一台手机时
        # 会抢跨进程设备锁而永久阻塞（表现：FB 私信任务卡在 executing）。
        # 改用网关会话：同线程嵌套会复用已有绑定，不重复加锁。
        from .mobile_gateway import device_session
        self._cm = device_session(self.device)
        self._mcp = self._cm.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._cm is not None:
            try:
                self._cm.__exit__(*exc)
            finally:
                self._cm = None
                self._mcp = None

    # -- 基础调用 ---------------------------------------------------------
    def _text_result(self, tool: str, args: dict | None = None) -> str:
        assert self._mcp is not None, 'session 未打开'
        payload = {'device': self.device, **(args or {})}
        r = self._mcp.call_tool(tool, payload)
        if not r.ok:
            raise RuntimeError(f'{tool} 失败: {r.error}')
        content = (r.result or {}).get('content') or []
        parts = [c.get('text', '') for c in content if isinstance(c, dict)]
        return '\n'.join(p for p in parts if p)

    def call(self, tool: str, args: dict | None = None) -> str:
        return self._text_result(tool, args)

    # -- 读 ---------------------------------------------------------------
    def dump(self) -> list[dict]:
        return parse_elements(self._text_result('mobile_list_elements_on_screen'))

    def dump_text(self) -> str:
        return self._text_result('mobile_list_elements_on_screen')

    def foreground(self) -> Foreground | None:
        txt = self._text_result('mobile_get_foreground_app')
        # Foreground app: Facebook (com.facebook.katana), activity: com.…
        m = re.search(r'\(([\w.]+)\).*?activity:\s*([\w.$]+)', txt)
        if m:
            return Foreground(m.group(1), m.group(2))
        m2 = re.search(r'\(([\w.]+)\)', txt)
        return Foreground(m2.group(1), '') if m2 else None

    def screen_size(self) -> tuple[int, int]:
        txt = self._text_result('mobile_get_screen_size')
        m = re.search(r'(\d+)x(\d+)', txt)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    def screenshot(self, save_to: str) -> str:
        return self._text_result('mobile_save_screenshot', {'saveTo': save_to})

    # -- 可逆导航 ---------------------------------------------------------
    def tap_ref(self, ref: str) -> None:
        self._text_result('mobile_click_on_screen_at_coordinates', {'ref': ref})

    def tap(self, x: int, y: int) -> None:
        self._text_result('mobile_click_on_screen_at_coordinates', {'x': int(x), 'y': int(y)})

    def tap_element(self, el: dict) -> None:
        """点元素中心；坐标只在此刻有效。"""
        cx = el['x'] + el['width'] // 2
        cy = el['y'] + el['height'] // 2
        self.tap(cx, cy)

    def tap_verified(self, el: dict, expect_re: str) -> None:
        """带语义核对的点击：元素必须匹配 expect_re，否则拒绝。

        来历：2026-11-01 21:02 用**固定坐标**点搜索结果条目，
        坐标正好压在条目内的「加入」按钮上，把账号真实加入了一个公开社团。
        从此规则改为：只按元素定位，点之前必须核对这个元素到底是什么。
        """
        import re as _re
        fields = [f for f in (el.get('label', ''), el.get('text', ''),
                              el.get('name', ''), el.get('value', '')) if f]
        if not any(_re.search(expect_re, f) for f in fields):
            raise RuntimeError(f'拒绝点击：元素语义 {fields!r} 不匹配 {expect_re!r}')
        cx = el['x'] + el['width'] // 2
        cy = el['y'] + el['height'] // 2
        self.tap(cx, cy)

    def long_press(self, x: int, y: int, duration: int = 900) -> None:
        """长按某点。用于弹出表情托盘这类"长按才出"的组件。

        注意：长按本身不改状态，但如果松手落在选项上会选中，所以时长要控制、
        位置要用元素中心，且采集完立刻用返回键关闭。
        """
        self._text_result('mobile_long_press_on_screen_at_coordinates',
                          {'x': int(x), 'y': int(y), 'duration': int(duration)})

    def back(self) -> None:
        self._text_result('mobile_press_button', {'button': 'BACK'})

    def swipe(self, direction: str, distance: int = 800, x: int | None = None,
              y: int | None = None) -> None:
        args: dict = {'direction': direction, 'distance': int(distance)}
        if x is not None and y is not None:
            args.update({'x': int(x), 'y': int(y)})
        self._text_result('mobile_swipe_on_screen', args)

    def type_text(self, text: str, submit: bool = False) -> None:
        """仅用于搜索等可逆输入；会留下搜索历史的场景需调用方判断。"""
        self._text_result('mobile_type_keys', {'text': text, 'submit': submit})

    def keyboard_visible(self) -> bool:
        """软键盘是否抬起（用 systemui 的输入法切换键判断）。"""
        els = self.dump()
        return any('ime_switcher' in (e.get('identifier') or '') for e in els)

    def type_anywhere(self, text: str, submit: bool = False) -> bool:
        """把文字打进"当前该打的地方"，找不到显式输入框也能工作。

        来历：2026-11-02 00:44 实测，社团搜索页的 `Search groups` 输入框
        **完全不在无障碍层级里**——截图里看得见、键盘也弹着，但 dump 只有 Back。
        这类框没法按元素点，只能靠"焦点已经在里面"直接打字。

        策略：优先用 focused 的可编辑元素 → 其次任意 EditText/AutoCompleteTextView
        → 最后若键盘已抬起，直接向当前焦点打字。返回是否成功发出输入。
        """
        els = self.dump()
        focus = [e for e in els if e['focused'] and 'Edit' in e['type']]
        editable = focus or [e for e in els
                             if e['type'] in ('AutoCompleteTextView', 'EditText')]
        if editable:
            self.tap_element(editable[0])
            time.sleep(0.5)
            self.type_text(text, submit=submit)
            return True
        if self.keyboard_visible():
            self.type_text(text, submit=submit)
            return True
        return False
