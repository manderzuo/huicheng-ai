"""TikTok 按 @handle 定位指定用户：搜索 → 用户分栏 → 进主页 → 核验 handle。

用途（对齐抖音的 douyin-locate）：
  A 端采集到的线索只带 @handle，B 端手机要在自己的账号环境里把这个账号找回来，
  才能进入主页私信。本模块只做「找到并核验」，不关注、不私信、不点赞。

只读约束：
  · 找到人之后只读主页的 @handle / 昵称 / 粉丝数，不做任何交互；
  · handle 必须与目标完全一致才算命中，同名/相似一律不算。
"""
from __future__ import annotations

import re
import time

from .douyin_skill import SkillError, elements, foreground_package
from .mobile_gateway import MobileCallError, call
from .ui_probe_targets import resolve_point as _resolve
from .tiktok_lead import (
    PACKAGE, _box, _ident, _ref_ok, _text_of, by_id, page_kind, profile_handle,
)

SEARCH_TAB = '用户'

#: 搜索页固定位置的坐标（手机7 / 1080×1920 实测）。
#: 这些位置在搜索结果页和搜索输入页都一样，用坐标点可以直接省掉 3 次读界面（约 10 秒）。
#: 一旦页面不是预期结构，下面的逻辑会 dump 校验并回退，不会盲点。
_BOX_KEYS = {
    'box': 'tiktok.search.box',
    'clear': 'tiktok.search.clear',
    'submit': 'tiktok.search.submit',
}


def _tap_xy(serial, xy):
    call('mobile_click_on_screen_at_coordinates', {'x': int(xy[0]), 'y': int(xy[1])}, serial)


def _tap_key(serial, which):
    """按语义键换算本机坐标再点（dp 自适应不同分辨率/DPI）。"""
    _tap_xy(serial, _resolve(serial, _BOX_KEYS[which]))


def wait_activity(serial, marker, timeout=8.0, poll=0.35):
    """轮询 Activity 直到出现目标标记。比固定 sleep 精确，页面就绪就走。

    参照 B站 的经验：能轮询就别盲等（他们实测省 36% 时间）。
    Activity 探测只要 0.5 秒，适合做这种「等页面切换」的等待。
    """
    end = time.time() + timeout
    while time.time() < end:
        if marker in foreground_activity(serial):
            return True
        time.sleep(poll)
    return False


def _tree(serial, retries=3):
    last = None
    for attempt in range(retries):
        try:
            return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))
        except (SkillError, MobileCallError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(0.8)
    raise last


def foreground_activity(serial):
    """只读前台 Activity 名，不 dump 层级。

    ★这是本次提速的关键：`mobile_get_foreground_app` 约 0.5 秒，
    而 `mobile_list_elements_on_screen` 要 3-6 秒（TikTok 层级 300+ 节点）。
    能用 activity 判断的页面判断，就不要 dump。
    """
    result = call('mobile_get_foreground_app', {}, serial)
    for item in (result or {}).get('content', []):
        if item.get('type') == 'text':
            match = re.search(r'activity:\s*([\w.]+)', item.get('text', ''))
            if match:
                return match.group(1)
    return ''


def _tap(el, serial):
    if not _ref_ok(el):
        raise SkillError('控件引用不可用，已停止')
    call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)


def _press(button, serial):
    call('mobile_press_button', {'button': button}, serial)


def normalize_handle(text) -> str:
    value = str(text or '').strip()
    if not value:
        return ''
    if not value.startswith('@'):
        value = '@' + value
    return value.lower()


def go_home(serial):
    """回到 TikTok 主界面（有底部导航）。退不出来就用 HOME + 重新拉起。

    ★注意：TikTok 的 Activity 名是 `com.ss.android.ugc.aweme.*`，
    包名却是 `com.zhiliaoapp.musically`，两者不同——用包名去匹配 Activity 会永远失败。
    """
    def in_app(act):
        return 'aweme' in act or 'musically' in act

    def maybe_home(act):
        return ('SplashActivity' in act or 'MainActivity' in act) \
            and 'SearchResult' not in act

    act = foreground_activity(serial)
    if in_app(act) and maybe_home(act):
        page = _tree(serial)
        if page_kind(page) == 'video' and any(_ident(e) == 'i5n' for e in page):
            return page

    for _attempt in range(3):
        for _ in range(5):
            act = foreground_activity(serial)
            if not in_app(act):
                break
            if maybe_home(act):
                page = _tree(serial)
                if page_kind(page) == 'video' and any(_ident(e) == 'i5n' for e in page):
                    return page
            _press('BACK', serial)
            time.sleep(0.8)
        _press('HOME', serial)
        time.sleep(1.0)
        call('mobile_launch_app', {'packageName': PACKAGE}, serial)
        time.sleep(2.5)
    raise SkillError('未能回到 TikTok 首页，已停止')


def open_search(serial):
    """从首页进入搜索输入页。

    ★优化：先用 activity 判断是否已在搜索/结果页，避免每次循环都 dump。
    """
    act = foreground_activity(serial)
    if 'SearchResultActivity' in act or '.search' in act:
        # 已经在搜索体系里：dump 一次确认，若是结果页则退回输入页
        page = _tree(serial)
        if page_kind(page) == 'search':
            return page
        if page_kind(page) == 'search_result':
            _press('BACK', serial)
            time.sleep(1.0)

    page = _tree(serial)
    icons = [e for e in page if _ident(e) == 'fj6' and _ref_ok(e) and _box(e)]
    if icons:
        icons.sort(key=lambda e: _box(e)['x'])
        _tap(icons[-1], serial)
        for _ in range(4):
            time.sleep(0.9)
            page = _tree(serial)
            if page_kind(page) == 'search':
                return page
    raise SkillError('未能进入搜索页，已停止')


def search_user(serial, handle):
    """搜索 handle 并切到「用户」分栏，返回结果页的树。

    ★优化：固定 dump 次数（5 次），不做 16 秒轮询。TikTok 的搜索结果
    通常在 2-3 秒内就绪，之前「边轮询边 dump」是最慢的一段。
    """
    page = open_search(serial)
    boxes = [e for e in page if _ident(e) == 'e22' and _ref_ok(e)]
    if len(boxes) != 1:
        raise SkillError('搜索框不存在或不唯一，已停止')
    _tap(boxes[0], serial)
    time.sleep(1.0)
    page = _tree(serial)                       # dump 1：找清除按钮
    clears = [e for e in page if _text_of(e, 'label') == '清除搜索字段']
    if len(clears) == 1:
        _tap(clears[0], serial)
        time.sleep(0.6)
        page = _tree(serial)                   # dump 2：清空后重新定位输入框
    boxes = [e for e in page if _ident(e) == 'e22' and _ref_ok(e)]
    if len(boxes) == 1:
        _tap(boxes[0], serial)
        time.sleep(0.6)
    call('mobile_type_keys', {'text': handle.lstrip('@')}, serial)
    time.sleep(0.9)
    page = _tree(serial)                       # dump 3：找提交按钮
    submits = [e for e in page if _ident(e) == 'plh' and _ref_ok(e)]
    if len(submits) != 1:
        raise SkillError('搜索按钮不存在或不唯一，已停止')
    _tap(submits[0], serial)
    wait_activity(serial, 'SearchResult', timeout=8.0)
    time.sleep(0.4)
    page = _tree(serial)                       # dump 4：找「用户」分栏
    tabs = [e for e in page
            if _text_of(e, 'label') == SEARCH_TAB and _ref_ok(e) and _box(e)
            and _box(e)['y'] < 450 and _box(e)['height'] < 200]
    if len(tabs) == 1:
        _tap(tabs[0], serial)
        time.sleep(2.0)
        return _tree(serial)                   # dump 5：分栏结果
    return page


def find_user_row(page, handle):
    """在用户结果列表里找 handle 完全一致的那一行。

    用户卡结构（实测）：整行 Button（无 id）/ 昵称 psz / handle oyj / 粉丝数 p3y / 关注 l7n。
    只认 oyj 文本与目标完全相等，避免同名不同人。
    """
    want = normalize_handle(handle)
    hits = []
    for el in by_id(page, 'oyj'):
        if normalize_handle(_text_of(el, 'text')) == want:
            hits.append(el)
    if len(hits) != 1:
        return None
    return hits[0]


def quick_locate(serial, handle):
    """批量模式：假定已经在 TikTok 搜索体系里，直接换搜索词找人。

    ★为什么要有这个：实测单条「回首页 16.4s + 进搜索 9.4s」，两条就要多花 50 秒。
    连续处理多条线索时，完全没必要每次都退出搜索重来。
    返回 None 表示当前不在搜索页，调用方应回退到完整的 locate_by_handle。
    """
    if not normalize_handle(handle):
        raise SkillError('handle 为空，已停止')

    # ★上一条结束时通常停在「私信会话页」，需要先退回搜索体系。
    #   这里用 Activity 判断（0.5 秒），比 dump 一次（3.5 秒）快得多。
    act = foreground_activity(serial)
    if 'SearchResult' not in act and '.search' not in act:
        for _ in range(4):
            _press('BACK', serial)
            time.sleep(0.7)
            act = foreground_activity(serial)
            if 'SearchResult' in act or '.search' in act:
                break
        else:
            return None

    page = _tree(serial)
    kind = page_kind(page)
    # ★关键：搜索结果页**本身就有搜索框**（e22），直接在结果页改词即可。
    #   之前这里对 search_result 又按了一次 BACK，会直接退回首页 → 判定失败。
    if kind not in ('search', 'search_result'):
        return None

    boxes = [e for e in page if _ident(e) == 'e22' and _ref_ok(e)]
    if len(boxes) != 1:
        return None

    # 点搜索框 → 清空 → 输入。★清除按钮必须用 dump 找：
    #   用固定坐标点时偶发点不中，搜索词会叠加成「旧词@新词」，结果搜不到人
    #   （实测 20 条里 7 条因此 locate 失败）。
    _tap_key(serial, 'box')
    time.sleep(0.6)
    page = _tree(serial)
    clears = [e for e in page if _text_of(e, 'label') == '清除搜索字段' and _ref_ok(e)]
    if len(clears) == 1:
        _tap(clears[0], serial)
        time.sleep(0.4)
        _tap_key(serial, 'box')
        time.sleep(0.4)

    call('mobile_type_keys', {'text': handle.lstrip('@')}, serial)
    time.sleep(0.7)

    # 输入后校验：搜索框里的词必须就是目标，否则清空重输一次
    page = _tree(serial)
    typed = ''
    for el in page:
        if _ident(el) == 'e22':
            typed = _text_of(el, 'text').strip()
            break
    if typed and normalize_handle(typed).lstrip('@') != normalize_handle(handle).lstrip('@'):
        clears = [e for e in page if _text_of(e, 'label') == '清除搜索字段' and _ref_ok(e)]
        if len(clears) == 1:
            _tap(clears[0], serial)
            time.sleep(0.5)
            _tap_key(serial, 'box')
            time.sleep(0.4)
            call('mobile_type_keys', {'text': handle.lstrip('@')}, serial)
            time.sleep(0.7)

    _tap_key(serial, 'submit')
    # 轮询结果页 Activity，就绪即走（替代固定 2.3 秒盲等）
    wait_activity(serial, 'SearchResult', timeout=8.0)
    time.sleep(0.4)

    page = _tree(serial)
    tabs = [e for e in page
            if _text_of(e, 'label') == SEARCH_TAB and _ref_ok(e) and _box(e)
            and _box(e)['y'] < 450 and _box(e)['height'] < 200]
    if len(tabs) == 1:
        _tap(tabs[0], serial)
        time.sleep(1.4)
        page = _tree(serial)

    row = find_user_row(page, handle)
    if row is None:
        time.sleep(0.9)
        page = _tree(serial)
        row = find_user_row(page, handle)
    if row is None:
        return {'handle': normalize_handle(handle), 'nickname': '', 'matched': False,
                'evidence': {'reason': 'user_row_not_found'}}

    _tap(row, serial)
    time.sleep(1.6)
    for _ in range(4):
        page = _tree(serial)
        got, nickname = profile_handle(page)
        if got:
            return {'handle': got, 'nickname': nickname,
                    'matched': normalize_handle(got) == normalize_handle(handle),
                    'evidence': {'reason': 'quick_locate', 'page_kind': page_kind(page)}}
        time.sleep(0.6)
    return {'handle': normalize_handle(handle), 'nickname': '', 'matched': False,
            'evidence': {'reason': 'profile_handle_missing', 'page_kind': page_kind(page)}}


def locate_by_handle(serial, handle, timeout=8.0):
    """搜索并打开该 handle 的主页，核验后返回身份信息。

    ★优化：找用户行最多补读 2 次（原来轮询 25 秒）；进主页后最多读 5 次。
    """
    if not normalize_handle(handle):
        raise SkillError('handle 为空，已停止')
    go_home(serial)
    page = search_user(serial, handle)

    row = find_user_row(page, handle)
    if row is None:
        for _ in range(2):                     # 结果可能还在渲染，补读两次就够
            time.sleep(1.0)
            page = _tree(serial)
            row = find_user_row(page, handle)
            if row is not None:
                break
    if row is None:
        return {'handle': normalize_handle(handle), 'nickname': '', 'matched': False,
                'evidence': {'reason': 'user_row_not_found'}}

    # 点整行进主页：实测最稳的是点 handle 文本本身（整行 Button 会一起响应）
    _tap(row, serial)
    time.sleep(2.0)

    # 进主页后最多读 5 次（原来 10 次）：主页通常在 2 秒内就绪
    for _ in range(5):
        page = _tree(serial)
        got, nickname = profile_handle(page)
        if got:
            matched = normalize_handle(got) == normalize_handle(handle)
            return {'handle': got, 'nickname': nickname, 'matched': matched,
                    'evidence': {'reason': 'profile_handle_read', 'page_kind': page_kind(page)}}
        time.sleep(0.8)
    return {'handle': normalize_handle(handle), 'nickname': '', 'matched': False,
            'evidence': {'reason': 'profile_handle_missing', 'page_kind': page_kind(page)}}
