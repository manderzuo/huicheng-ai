#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""小红书「误点恢复」：把界面从各种意外状态拉回可用状态。

参考抖音的做法：
  1. 标签兜底 —— 见到「我知道了/关闭/取消/暂不」就直接点掉；
  2. Activity + 元素签名识别当前处在什么页面；
  3. 逐级回退 —— 只按必要的 BACK，不盲目连按（连按会退出到桌面）。

小红书特有的异常状态（实测遇到过的）：
  · 评论区被展开成全屏（白底、上排「评论 / 赞和收藏」、底部输入框）
  · 误进用户主页（NewOtherUserActivity）
  · 误进广告/外链（ExternalWebViewActivityV2）、AI 搜索页（SearchAgentPageActivity）
  · 搜索筛选面板被点开
  · 评论输入框被激活（键盘弹起）
"""
from __future__ import annotations

import time

from .mobile_gateway import MobileCallError, call
from .xiaohongshu_lead import (
    _box, _label, _ref_ok, _text, _type, elements, foreground_activity,
)

PACKAGE = 'com.xingin.xhs'

# 「点掉就走」的按钮文案
DISMISS_LABELS = ('我知道了', '知道了', '关闭', '取消', '暂不', '以后再说',
                  '不再提示', '下次再说', '暂不需要')

# 评论区展开后的特征文案
SHEET_MARKS = ('赞和收藏',)
INPUT_HINTS = ('喜欢TA就', '说点什么', '留个言', '友善评论')

STATE_TEXT = {
    'home': '首页',
    'search': '搜索结果',
    'note': '笔记详情',
    'sheet': '评论区（展开）',
    'profile': '用户主页',
    'chat': '私信会话',
    'web': '外链/广告页',
    'other': '未知页面',
}


def snapshot(serial):
    """返回 (活动名, 控件树)。读失败时给空值，不抛异常。"""
    activity = ''
    try:
        activity = foreground_activity(call('mobile_get_foreground_app', {}, serial))
    except (MobileCallError, RuntimeError):
        pass
    try:
        dump = elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))
    except (MobileCallError, RuntimeError):
        dump = []
    return activity, dump


def detect(activity, dump):
    """判断当前状态。Activity 优先，元素签名兜底。"""
    act = activity or ''
    texts = [_text(e) for e in dump if _text(e)]

    if 'IndexActivityV2' in act:
        return 'home'
    if 'ChatActivity' in act:
        return 'chat'
    if 'NewOtherUserActivity' in act:
        return 'profile'
    if 'GlobalSearchActivity' in act:
        return 'search'
    if any(k in act for k in ('ExternalWebView', 'SearchAgentPage', 'XhsReactTranslucent')):
        return 'web'
    if any(k in act for k in ('NoteDetailActivity', 'DetailFeedActivity')):
        # 只有出现「赞和收藏」页签才算评论区展开。
        # 普通笔记页底部也有「说点什么…」占位符，用输入框判断会误判（实测踩过）。
        if any(m in texts for m in SHEET_MARKS):
            return 'sheet'
        return 'note'
    return 'other'


def _close_button(dump):
    """找带「关闭/我知道了」这类文案的可点元素。"""
    for el in dump:
        text = _text(el) or _label(el)
        if text in DISMISS_LABELS and _ref_ok(el):
            return el, text
    return None, ''


def recover(serial, target='home', max_steps=8, log=None,
            pause=0.9, allow_restart=True):
    """把界面拉回到 target（home / search / note）。

    返回 {'ok', 'steps', 'from', 'to', 'restarted'}
    """
    def note(msg):
        if log:
            try:
                log(msg)
            except Exception:                            # noqa: BLE001
                pass

    def reached(state):
        if target == 'home':
            return state == 'home'
        if target == 'search':
            return state == 'search'
        if target == 'note':
            return state == 'note'
        return state == target

    steps = []
    restarted = False
    activity, dump = snapshot(serial)
    start_state = detect(activity, dump)

    for _ in range(max_steps):
        state = detect(activity, dump)
        if reached(state):
            return {'ok': True, 'steps': steps, 'from': start_state,
                    'to': state, 'restarted': restarted}

        el, label = _close_button(dump)
        if el is not None and state in ('sheet', 'other', 'web', 'search'):
            try:
                call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)
                steps.append('点掉「%s」' % label)
            except MobileCallError:
                pass
        else:
            try:
                call('mobile_press_button', {'button': 'BACK'}, serial)
                steps.append('BACK（当前：%s）' % STATE_TEXT.get(state, state))
            except MobileCallError:
                break

        time.sleep(pause)
        activity, dump = snapshot(serial)

    state = detect(activity, dump)
    ok = reached(state)

    # 兜底：逐级回退失败就重启应用回首页（不改任何对外状态）
    if not ok and allow_restart:
        steps.append('逐级回退失败，重启应用')
        try:
            from .xiaohongshu_lead import ensure_app_home
            if ensure_app_home(serial):
                restarted = True
                activity, dump = snapshot(serial)
                state = detect(activity, dump)
                ok = reached(state) or (target != 'home' and state == 'home')
        except Exception:                                # noqa: BLE001
            pass

    if log:
        note('恢复结果：%s → %s（%s）'
             % (STATE_TEXT.get(start_state, start_state),
                STATE_TEXT.get(state, state), '成功' if ok else '未达成'))

    return {'ok': ok, 'steps': steps, 'from': start_state,
            'to': state, 'restarted': restarted}


def close_comment_sheet(serial, log=None):
    """只处理「评论区被展开」这一种情况，尽量温和。"""
    activity, dump = snapshot(serial)
    if detect(activity, dump) != 'sheet':
        return False
    try:
        call('mobile_press_button', {'button': 'BACK'}, serial)
    except MobileCallError:
        return False
    time.sleep(0.9)
    activity, dump = snapshot(serial)
    if detect(activity, dump) == 'sheet':
        # 还开着：点右上角关闭（该按钮无文本，用坐标兜底）
        try:
            call('mobile_click_on_screen_at_coordinates', {'x': 960, 'y': 490}, serial)
        except MobileCallError:
            return False
        time.sleep(0.9)
        activity, dump = snapshot(serial)
    ok = detect(activity, dump) != 'sheet'
    if log:
        try:
            log('评论区已收起' if ok else '评论区仍未收起')
        except Exception:                            # noqa: BLE001
            pass
    return ok
