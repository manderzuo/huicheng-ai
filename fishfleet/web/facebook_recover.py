"""Facebook「误点恢复」：把界面从各种意外状态拉回可用状态。

**参照小红书 `xiaohongshu_recover.py` 的做法**（主人 2026-11-03 要求接入）：
  1. 兜底点掉 —— 见到「確定/關閉/取消/稍後/不是現在」之类就直接点掉；
  2. Activity + 元素签名识别当前在哪个页面；
  3. 逐级回退 —— 只按必要的 BACK，**不盲目连按**（连按会退到系统桌面）；
  4. 逐级回退失败 → **重启应用回首页**（不改任何对外状态）。

Facebook 特有的意外状态（实测遇到过）：
  · 搜索结果页/Reels 播放页（ImmersiveActivity，判定常是 menu/None）
  · 评论区浮层开着一半
  · 私信会话里的「商家聊天室的變動」弹窗
  · 误进个人主页 / 社团介绍页 / 看图器
  · 退到系统桌面（LauncherActivity）
"""
from __future__ import annotations

import time

from . import facebook_templates as T
from .facebook_live import DeviceSession

PACKAGE = 'com.facebook.katana'

# 「点掉就走」的按钮文案（提示类弹窗，点了不改变对外状态）
DISMISS_LABELS = (
    '確定', '确定', 'OK', '知道了', '關閉', '关闭', '取消', '稍後', '稍后',
    '不是現在', '不是现在', '以後再說', '以后再说', '不再提示', '下次再說',
    '下次再说', '暫不需要', '暂不需要', '跳過', '跳过', '略過', '略过',
)

STATE_TEXT = {
    'home': '首页',
    'search': '搜索页',
    'dm': '私信会话',
    'profile': '个人主页',
    'menu': '抽屉菜单',
    'desktop': '系统桌面',
    'other': '未知页面',
}


def _text(e: dict) -> str:
    return (e.get('label') or e.get('text') or '').strip()


def snapshot(s: DeviceSession):
    """返回 (activity, dump)。读失败给空值，不抛异常。"""
    activity = ''
    try:
        fg = s.foreground()
        activity = (fg.activity if fg else '') or ''
    except Exception:                                    # noqa: BLE001
        pass
    try:
        dump = s.dump()
    except Exception:                                    # noqa: BLE001
        dump = []
    return activity, dump


def detect(activity: str, dump: list) -> str:
    """判断当前状态。Activity 优先，元素签名兜底。"""
    act = activity or ''
    # 2026-11-03 13:40 实测：FB 有些 Activity 不带 `katana`，
    # 比如 `com.facebook.messaginginblue.inbox.activities.InboxActivity`，
    # 用 'katana' 判断会把它误认成系统桌面。改成包名前缀。
    if not act.startswith('com.facebook'):
        return 'desktop'
    labels = {_text(e) for e in dump if _text(e)}
    if 'MsysThreadViewActivity' in act:
        return 'dm'
    if 'ImmersivePortraitActivity' in act:
        return 'profile'
    # 2026-11-05 修：FB 的 Activity 名**不可靠** —— 登录态下信息流首页也常驻
    # `com.facebook.katana.LoginActivity`（跟抖音 SplashActivity 一个套路），
    # 而首页又有「搜尋」字样，于是被下面那条判成 search，recover 就一直 BACK，
    # 一路把 App 退到桌面。这里先用首页自己的签名判，判到就直接返回。
    if any(m in labels for m in ('在想些什麼？', '在想些什麼', '建立限時動態', '限時動態匣')):
        return 'home'
    if T.page_of(dump, act) == 'home':
        return 'home'
    # 搜索页必须同时有强特征（取消键 / 结果页标题），光有「搜尋」不够 —— 首页也有搜索入口
    if ('搜尋' in labels or 'Search' in labels) and any(
            k in labels for k in ('取消', 'Cancel', '則搜尋結果')):
        return 'search'
    if T.find('home', 'menu', dump) is not None:
        return 'menu'
    return 'other'


def _dismiss_button(dump: list):
    """找带「確定/關閉/取消」这类文案的可点元素。"""
    for e in dump:
        if e['type'] not in ('Button', 'ViewGroup', 'ImageView'):
            continue
        t = _text(e)
        if t in DISMISS_LABELS:
            return e, t
    return None, ''


def recover(s: DeviceSession, target: str = 'home', max_steps: int = 8,
            log=None, pause: float = 1.0, allow_restart: bool = True) -> dict:
    """把界面拉回到 target（home / search / dm / profile）。

    返回 {'ok', 'steps', 'from', 'to', 'restarted'}
    """
    def note(msg):
        if log:
            try:
                log(msg)
            except Exception:                            # noqa: BLE001
                pass

    def reached(state):
        return state == target

    steps = []
    restarted = False
    activity, dump = snapshot(s)
    start_state = detect(activity, dump)

    for _ in range(max_steps):
        state = detect(activity, dump)
        if reached(state):
            return {'ok': True, 'steps': steps, 'from': start_state,
                    'to': state, 'restarted': restarted}
        el, label = _dismiss_button(dump)
        # 只在"明显被弹窗挡住"时才点兜底按钮，避免误点正常控件
        if el is not None and state in ('other', 'menu', 'search'):
            try:
                s.tap_element(el)
                steps.append('点掉「%s」' % label)
            except Exception:                            # noqa: BLE001
                pass
        else:
            try:
                s.back()
                steps.append('BACK（当前：%s）' % STATE_TEXT.get(state, state))
            except Exception:                            # noqa: BLE001
                break
        time.sleep(pause)
        activity, dump = snapshot(s)

    state = detect(activity, dump)
    ok = reached(state)

    # 兜底：逐级回退失败就重启应用回首页（不改任何对外状态）
    if not ok and allow_restart:
        steps.append('逐级回退失败，重启应用')
        try:
            s._text_result('mobile_launch_app', {'packageName': PACKAGE})
            for _ in range(12):
                time.sleep(2.0)
                activity, dump = snapshot(s)
                if detect(activity, dump) == 'home':
                    restarted = True
                    break
            state = detect(activity, dump)
            ok = reached(state) or (target != 'home' and state == 'home')
        except Exception:                                # noqa: BLE001
            pass

    note('恢复结果：%s → %s（%s）'
         % (STATE_TEXT.get(start_state, start_state),
            STATE_TEXT.get(state, state), '成功' if ok else '未达成'))

    return {'ok': ok, 'steps': steps, 'from': start_state,
            'to': state, 'restarted': restarted}
