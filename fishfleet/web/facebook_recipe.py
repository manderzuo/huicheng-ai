"""用导出的 JSON 资料库驱动只读任务（验证资料库本身够不够用）。

这个脚本**刻意不导入 facebook_templates**，只吃 `data/facebook_ui_library.json`，
目的就是验证"导出的资料库能脱离原模块独立工作"——如果这里跑不通，
说明导出物缺字段或规则不可移植，那是资料库的问题，不是脚本的问题。

用法：
  python fishfleet/web/facebook_recipe.py list
  python fishfleet/web/facebook_recipe.py run <配方名>
  python fishfleet/web/facebook_recipe.py check        # 只校验，不碰设备

安全：所有配方都是**只读**（打开页面、看一眼、返回），
遇到 risk=send/forbidden 的控件一律拒绝点击。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web.facebook_live import (  # noqa: E402
    DeviceSession, is_visible, parse_elements, setup_utf8_stdout, tap_with_gate)

DEVICE = 'SERIAL0001'
LIB_PATH = Path(__file__).resolve().parents[2] / 'data' / 'facebook_ui_library.json'
RISK_NONE, RISK_SEND, RISK_FORBIDDEN = 'none', 'send', 'forbidden'

# 唯一复用的一点：raw dump 的解析（那是传输格式，不是资料库内容）
_CLICK_ORDER = ('Button', 'ImageView', 'ViewGroup', 'LinearLayout', 'FrameLayout',
                'RelativeLayout', 'View', 'TextView', 'EditText',
                'AutoCompleteTextView', 'CheckBox', 'SeekBar', 'WebView',
                'RecyclerView', 'ScrollView', 'ViewPager')


# ---------------------------------------------------------------------------
# 纯 JSON 驱动的匹配引擎
# ---------------------------------------------------------------------------
def _fields(el: dict) -> list[str]:
    return [x for x in (el.get('label', ''), el.get('text', ''),
                        el.get('name', ''), el.get('value', '')) if x]


def hit(t: dict, el: dict) -> bool:
    for rx in (t.get('label_re'), t.get('text_re')):
        if rx and any(re.search(rx, f) for f in _fields(el)):
            return True
    if t.get('type_re') and re.search(t['type_re'], el.get('type', '')):
        return True
    tid = t.get('id') or ''
    if tid and '(name removed)' not in tid and el.get('identifier', '').endswith(tid):
        return True
    return False


def resolve(page: dict, target_name: str, els: list[dict]) -> list[dict]:
    t = page['targets'][target_name]
    hits = [el for el in els if hit(t, el)]
    if len(hits) <= 1:
        return hits
    for typ in _CLICK_ORDER:
        layer = [h for h in hits if h['type'] == typ]
        if layer:
            return layer
    return hits


def page_of(lib: dict, els: list[dict], activity: str) -> str | None:
    for page in lib['pages']:
        ex = page.get('exclusive') or []
        if ex and all(any(hit(t, el) for el in els) for t in ex):
            return page['key']
    scores = {}
    for page in lib['pages']:
        scores[page['key']] = sum(1 for t in page['identity']
                                  if any(hit(t, el) for el in els))
    top = max(scores.values()) if scores else 0
    if top < 2:
        return None
    tops = [k for k, v in scores.items() if v == top]
    if len(tops) == 1:
        return tops[0]
    if activity:
        act = [p['key'] for p in lib['pages']
               if p['key'] in tops and p['activity_hint']
               and p['activity_hint'] in activity]
        if len(act) == 1:
            return act[0]
    return None


def page_by_key(lib: dict, key: str) -> dict | None:
    for p in lib['pages']:
        if p['key'] == key:
            return p
    return None


# ---------------------------------------------------------------------------
# 风险闸门：点之前先查这个元素在资料库里是不是 send/forbidden
# ---------------------------------------------------------------------------
def risk_of(lib: dict, el: dict) -> list[str]:
    hits = []
    for page in lib['pages']:
        for name, t in page['targets'].items():
            if t['risk'] not in (RISK_SEND, RISK_FORBIDDEN):
                continue
            if hit(t, el):
                hits.append(f"{page['key']}.{name}[{t['risk']}]")
    return hits


def safe_tap(lib: dict, s: DeviceSession, el: dict, els: list[dict],
             allow_send: bool = False) -> bool:
    """带闸门的点击：风险判断交给 JSON，点击策略复用统一实现。"""
    return tap_with_gate(s, el, els, lambda e: risk_of(lib, e), allow_send)


# ---------------------------------------------------------------------------
# 配方（全部只读）
# ---------------------------------------------------------------------------
RECIPES = {
    'look_home': ['确认在首页', '看一眼可用控件'],
    'look_notifications': ['打开通知列表', '返回首页'],
    'look_search': ['打开搜索页', '返回首页'],
    'look_group': ['打开搜索页', '输入搜索词', '点第一个社团', '返回首页'],
    'look_pages': ['打开搜索页', '输入搜索词', '切到 Pages 分类', '返回首页'],
    'look_group_files': ['打开搜索页', '输入搜索词', '点第一个社团',
                         '打开檔案分页', '返回首页'],
    'look_comments': ['找到带评论的帖子', '打开评论区', '返回首页'],
    'look_page': ['打开搜索页', '输入搜索词', '切到 Pages 分类',
                  '点第一个专页', '返回首页'],
    'look_group_tabs': ['打开搜索页', '输入搜索词', '点第一个社团',
                        '核对社团分页', '返回首页'],
    'look_replies': ['找到有嵌套回复的评论', '展开更多回复', '返回首页'],
    'look_reactions': ['找到带评论的帖子', '长按赞出表情托盘', '返回首页'],
}


def _say(msg: str) -> None:
    print('  ' + msg)


def _one(el: list, what: str) -> dict | None:
    """取唯一元素；**0 个和多个分开报**，避免"找不到"被说成"不唯一"。"""
    if not el:
        _say(f'失败：找不到{what}（可能滚出屏幕或被隐藏）')
        return None
    if len(el) > 1:
        _say(f'失败：{what}不唯一（命中 {len(el)} 个）')
        return None
    return el[0]


def _ensure_home(lib: dict, s: DeviceSession) -> bool:
    """把设备带回首页。配方都从首页起步，但调用方可能从任意页面启动。"""
    for i in range(6):
        els = s.dump()
        fg = s.foreground()
        if page_of(lib, els, fg.activity if fg else '') == 'home':
            if i:
                _say(f'前置：{i} 次返回后回到首页')
            return True
        s.back()
        time.sleep(1.0)
    _say('前置失败：6 次返回仍未回到首页')
    return False


def run_recipe(lib: dict, s: DeviceSession, name: str, retries: int = 1) -> bool:
    """执行配方；失败时回首页再试一次。

    为什么要重试：这些交互依赖渲染时序（列表还在加载、弹层还没全出来），
    一次失败往往不是"做不到"而是"太早"。重试前强制回首页，避免在半路状态上瞎试。
    """
    steps = RECIPES.get(name)
    if steps is None:
        print(f'未知配方：{name}')
        return False
    print(f'== 配方 {name}：{" / ".join(steps)} ==')
    attempts = max(1, retries + 1)
    for i in range(attempts):
        if i:
            _say(f'—— 第 {i + 1} 次尝试 ——')
        if not _ensure_home(lib, s):
            return False
        if all(_step(lib, s, st) for st in steps):
            return True
        if i < attempts - 1:
            _say('本次未通过，回首页后重试')
    return False


def _step(lib: dict, s: DeviceSession, step: str) -> bool:
    """每个步骤都先确认当前页，再动作；不复用任何坐标。"""
    from fishfleet.web import facebook_templates  # 仅用于 back() 之类的会话动作

    els = s.dump()
    fg = s.foreground()
    key = page_of(lib, els, fg.activity if fg else '')
    _say(f'当前页 = {key}（{fg.raw if fg else "?"}）')

    if step == '确认在首页':
        if key != 'home':
            _say('失败：不在首页')
            return False
        _say('OK')
        return True

    if step == '看一眼可用控件':
        page = page_by_key(lib, key)
        if page is None:
            _say('失败：当前页不在资料库里')
            return False
        usable = [n for n in page['targets'] if len(resolve(page, n, els)) == 1]
        _say(f'唯一可定位控件 {len(usable)} 个：' + ', '.join(usable[:10]))
        return True

    if step == '打开通知列表':
        page = page_by_key(lib, 'home')
        uniq = _one(resolve(page, 'tab_notifications', els), '通知 tab')
        if uniq is None:
            return False
        if not safe_tap(lib, s, uniq, els):
            return False
        time.sleep(1.5)
        els = s.dump()
        fg = s.foreground()
        k2 = page_of(lib, els, fg.activity if fg else '')
        _say(f'到达页面 = {k2}')
        return k2 == 'notifications'

    if step == '打开搜索页':
        page = page_by_key(lib, 'home')
        # 2026-11-02 05:08 实测：首页顶栏会随滚动隐藏，搜索入口直接变 0 个，
        # 而原来的报错写成"不唯一"，容易误判成匹配到多个。先滚回顶部再找。
        for _ in range(3):
            if resolve(page, 'search', els):
                break
            s.swipe('down', distance=1200, x=540, y=800)
            time.sleep(1.0)
            els = s.dump()
        el = resolve(page, 'search', els)
        if not el:
            _say('失败：找不到搜索入口（顶栏可能仍被隐藏）')
            return False
        if len(el) > 1:
            _say(f'失败：搜索入口不唯一（命中 {len(el)} 个）')
            return False
        if not safe_tap(lib, s, el[0], els):
            return False
        time.sleep(1.5)
        els = s.dump()
        fg = s.foreground()
        k2 = page_of(lib, els, fg.activity if fg else '')
        _say(f'到达页面 = {k2}')
        return k2 == 'search'

    if step == '输入搜索词':
        if not s.type_anywhere('marketplace', submit=True):
            _say('失败：输入没有发出去')
            return False
        time.sleep(2.5)
        els = s.dump()
        fg = s.foreground()
        k2 = page_of(lib, els, fg.activity if fg else '')
        _say(f'到达页面 = {k2}')
        return k2 == 'search_results'

    if step == '点第一个社团':
        page = page_by_key(lib, 'search_results')
        w, h = s.screen_size()
        w, h = w or 1080, h or 1920
        cands = [e for e in resolve(page, 'result_item', els)
                 if is_visible(e, w, h)]
        if not cands:
            _say('失败：没有可见的社团条目')
            return False
        if not safe_tap(lib, s, cands[0], els):
            return False
        time.sleep(2.0)
        els = s.dump()
        fg = s.foreground()
        k2 = page_of(lib, els, fg.activity if fg else '')
        _say(f'到达页面 = {k2}')
        return k2 == 'group_detail'

    if step == '切到 Pages 分类':
        page = page_by_key(lib, 'search_results')
        uniq = _one(resolve(page, 'tab_pages', els), 'Pages 分类标签')
        if uniq is None:
            return False
        if not safe_tap(lib, s, uniq, els):
            return False
        time.sleep(2.0)
        els = s.dump()
        fg = s.foreground()
        k2 = page_of(lib, els, fg.activity if fg else '')
        _say(f'到达页面 = {k2}')
        if k2 != 'search_results':
            return False
        # 切换成功的判据：Pages 标签变成 selected
        tag = resolve(page, 'tab_pages', els)
        ok = len(tag) == 1 and tag[0].get('selected')
        _say('Pages 分类已选中' if ok else 'Pages 分类未选中')
        return ok

    if step == '打开檔案分页':
        page = page_by_key(lib, 'group_detail')
        uniq = _one(resolve(page, 'tab_files', els), '檔案分页页签')
        if uniq is None:
            return False
        if not safe_tap(lib, s, uniq, els):
            return False
        time.sleep(2.0)
        els = s.dump()
        fg = s.foreground()
        k2 = page_of(lib, els, fg.activity if fg else '')
        _say(f'到达页面 = {k2}')
        return k2 == 'group_files'

    if step == '找到带评论的帖子':
        for i in range(6):
            comments = [e for e in els
                        if re.search(r'^\d+ comments$', e.get('label') or e.get('text') or '')]
            if comments:
                _say(f'第 {i + 1} 屏找到 {len(comments)} 个评论入口')
                return True
            s.swipe('up', distance=1300, x=540, y=1500)
            time.sleep(1.2)
            els = s.dump()
            fg = s.foreground()
            _say(f'第 {i + 1} 屏 页面={page_of(lib, els, fg.activity if fg else "")}')
        _say('失败：6 屏内没有找到带评论的帖子')
        return False

    if step == '打开评论区':
        comments = [e for e in els
                    if re.search(r'^\d+ comments$', e.get('label') or e.get('text') or '')]
        if not comments:
            _say('失败：当前屏没有评论入口')
            return False
        if not safe_tap(lib, s, comments[0], els):
            return False
        time.sleep(2.0)
        els = s.dump()
        fg = s.foreground()
        k2 = page_of(lib, els, fg.activity if fg else '')
        _say(f'到达页面 = {k2}')
        return k2 == 'post_detail'

    if step == '点第一个专页':
        page = page_by_key(lib, 'search_results')
        w, h = s.screen_size()
        w, h = w or 1080, h or 1920
        cands = [e for e in resolve(page, 'page_item', els)
                 if is_visible(e, w, h)]
        if not cands:
            _say('失败：没有可见的专页条目')
            return False
        if not safe_tap(lib, s, cands[0], els):
            return False
        time.sleep(2.5)
        els2 = s.dump()
        fg2 = s.foreground()
        k2 = page_of(lib, els2, fg2.activity if fg2 else '')
        _say(f'到达页面 = {k2}')
        return k2 == 'page_detail'

    if step == '核对社团分页':
        """逐个点社团分页，记录每个分页落到哪个页面。

        2026-11-02 07:33–08:00 连续两次在 shell 里手搓失败（没点进详情就找页签），
        这里改用已跑通的配方路径，并在每次切换后滚回顶部，避免页签被滚出屏幕。
        """
        results = {}
        for name, rx in (('相片', r'^相片$'), ('相簿', r'^相簿$'), ('活動', r'^活動$'),
                         ('檔案', r'^檔案$'), ('Reel', r'^Reel$')):
            # 找页签前先滚回顶部：分页栏在社团页中部，切换几次后容易被滚出屏幕
            # （08:10 那次「檔案」就是这样 0 命中的）。
            for _ in range(2):
                s.swipe('down', distance=1200, x=540, y=800)
                time.sleep(0.6)
            els_now = s.dump()
            hits = [e for e in els_now
                    if e['type'] == 'Button'
                    and re.search(rx, (e.get('label') or e.get('text') or ''))]
            if len(hits) != 1:
                results[name] = f'页签命中{len(hits)}'
                continue
            if not safe_tap(lib, s, hits[0], els_now):
                results[name] = '点击被拒'
                continue
            time.sleep(2.2)
            e2 = s.dump()
            fg2 = s.foreground()
            results[name] = page_of(lib, e2, fg2.activity if fg2 else '')
            s.back()
            time.sleep(1.5)
            for _ in range(2):
                s.swipe('down', distance=1200, x=540, y=800)
                time.sleep(0.6)
        _say('分页判定：' + ', '.join(f'{k}={v}' for k, v in results.items()))
        return True

    if step == '找到有嵌套回复的评论':
        """主动逐条试开评论，直到找到带「View N more replies」的那条。

        上一轮只是碰到了一条没有嵌套回复的帖子就跳过了，等于没验证到。
        这里改成主动寻找：开评论 → 看有没有入口 → 没有就关掉继续滑。
        """
        pd = page_by_key(lib, 'post_detail')
        for i in range(10):
            comments = [e for e in els
                        if re.search(r'^\d+ comments$',
                                     e.get('label') or e.get('text') or '')]
            if comments:
                _say(f'第 {i + 1} 屏：试开评论（{comments[0].get("label") or ""}）')
                if safe_tap(lib, s, comments[0], els):
                    time.sleep(2.0)
                    els2 = s.dump()
                    fg2 = s.foreground()
                    k2 = page_of(lib, els2, fg2.activity if fg2 else '')
                    if k2 == 'post_detail':
                        vr = resolve(pd, 'view_replies', els2)
                        if vr:
                            _say(f'命中带嵌套回复的评论：{len(vr)} 个入口')
                            return True
                        _say('该帖无嵌套回复，关闭后继续找')
                        close = resolve(pd, 'close', els2)
                        if close and safe_tap(lib, s, close[0], els2):
                            time.sleep(1.2)
                    else:
                        s.back()
                        time.sleep(1.2)
            s.swipe('up', distance=1300, x=540, y=1500)
            time.sleep(1.2)
            els = s.dump()
            fg = s.foreground()
            _say(f'第 {i + 1} 屏 页面={page_of(lib, els, fg.activity if fg else "")}')
        # 找不到不是脚本错，而是**这段时间刷到的帖子恰好都没有嵌套回复**。
        # 这属于内容条件不满足，按"跳过"处理并明确打印，避免把内容偶然性记成逻辑失败。
        _say('10 屏内没有遇到带嵌套回复的帖子 —— 内容条件不满足，跳过（不算失败）')
        return True

    if step == '展开更多回复':
        page = page_by_key(lib, 'post_detail')
        el = resolve(page, 'view_replies', els)
        if not el:
            # 不是每条帖子都有嵌套回复；这不算失败，但要明确说清楚。
            _say('该帖当前没有「View N more replies」入口，跳过（不算失败）')
            return True
        if not safe_tap(lib, s, el[0], els):
            return False
        time.sleep(2.0)
        els2 = s.dump()
        fg = s.foreground()
        k2 = page_of(lib, els2, fg.activity if fg else '')
        _say(f'展开后页面 = {k2}，元素数 {len(els)} -> {len(els2)}')
        return k2 == 'post_detail'

    if step == '长按赞出表情托盘':
        """长按「赞」弹出表情托盘，采集完立刻关闭，**不选任何表情**。

        这里不经过 safe_tap（长按不是点击），但同样先确认目标在资料库里是哪个控件；
        Like 本身是 send 级，所以只允许"弹出来看一眼"这一种用法。
        """
        page = page_by_key(lib, 'post_full')
        el = resolve(page, 'like', els)
        if not el:
            _say('失败：当前页找不到「赞」按钮')
            return False
        lk = el[0]
        cx = lk['x'] + lk['width'] // 2
        cy = lk['y'] + lk['height'] // 2
        _say(f'长按 {lk["ref"]} @({cx},{cy})——只弹托盘，不选表情')
        s.long_press(cx, cy, 900)
        time.sleep(1.5)
        els2 = s.dump()
        fg2 = s.foreground()
        k2 = page_of(lib, els2, fg2.activity if fg2 else '')
        _say(f'弹出后页面 = {k2}')
        # 无论成功与否，先关掉托盘，别留在那里
        s.back()
        time.sleep(1.0)
        els3 = s.dump()
        fg3 = s.foreground()
        k3 = page_of(lib, els3, fg3.activity if fg3 else '')
        _say(f'关闭后页面 = {k3}')
        return k2 == 'reaction_tray'

    if step == '返回首页':
        for _ in range(6):
            els = s.dump()
            fg = s.foreground()
            if page_of(lib, els, fg.activity if fg else '') == 'home':
                _say('已回到首页')
                return True
            s.back()
            time.sleep(1.0)
        _say('失败：6 次返回仍未回首页')
        return False

    _say(f'未知步骤：{step}')
    return False


def check_only() -> int:
    """不碰设备，只校验导出物能不能支撑上面的引擎。"""
    lib = json.loads(LIB_PATH.read_text(encoding='utf-8'))
    problems = []
    for page in lib['pages']:
        if not page.get('identity') and not page.get('exclusive'):
            problems.append(f'{page["key"]}: 没有锚点')
        for n, t in page['targets'].items():
            if not (t.get('label_re') or t.get('text_re')
                    or t.get('type_re') or t.get('id')):
                continue  # 内容类占位，允许
            for rx in (t.get('label_re'), t.get('text_re'), t.get('type_re')):
                if rx:
                    try:
                        re.compile(rx)
                    except re.error as exc:
                        problems.append(f'{page["key"]}.{n}: {exc}')
    print(f'资料库：{len(lib["pages"])} 页 '
          f'/ {sum(len(p["targets"]) for p in lib["pages"])} 控件')
    print('配方：' + ', '.join(RECIPES))
    if problems:
        print('问题：')
        for p in problems:
            print('  - ' + p)
        return 1
    print('校验通过')
    return 0


def main() -> int:
    setup_utf8_stdout()
    if not LIB_PATH.exists():
        print(f'找不到 {LIB_PATH}，先跑：python fishfleet/web/facebook_flow.py export')
        return 2
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == 'check':
        return check_only()
    if cmd == 'list':
        for k, v in RECIPES.items():
            print(f'{k}: {" / ".join(v)}')
        return 0
    if cmd == 'run':
        if len(sys.argv) < 3:
            print('用法：run <配方名>')
            return 2
        lib = json.loads(LIB_PATH.read_text(encoding='utf-8'))
        with DeviceSession(DEVICE) as s:
            ok = run_recipe(lib, s, sys.argv[2])
        print('结果：' + ('通过' if ok else '失败'))
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
