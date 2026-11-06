# -*- coding: utf-8 -*-
"""采集时抓「真 ID」：抖音数字uid / 小红书24位user_id / B站mid / FB profile_id / TK handle。

为什么必须在采集时抓（主人 2026-11-05 拍板）：
    以前回复阶段靠"搜号 → 找主页 → 找对人"，错一次就全错，且极难复现。
    抓真 ID 每条多花 3~5 秒，但之后直达是 100% 确定的对象，值得。

抓取方法统一为「分享 → 复制链接 → 粘贴到搜索框读回 → 解析」：
    网关禁止直接读剪贴板，所以走"粘贴到 EditText 再从元素树读文本"。
"""
from __future__ import annotations

import re
import time

from . import deeplink as D

# 各平台"打开搜索页"的动作：抓 ID 时要有一个能粘贴的输入框。
# 必须是**同一任务内压栈**的 scheme（实测 2026-11-05）：
#   · 抖音 snssdk1128://search  → SearchResultActivity（压在主页上，BACK 可回）
#   · 小红书 xhsdiscover://search/result → GlobalSearchActivity（输入框已聚焦）
SEARCH_OPENERS = {
    'douyin': ('snssdk1128://search?keyword=', 'com.ss.android.ugc.aweme'),
    'xiaohongshu': ('xhsdiscover://search/result?keyword=', 'com.xingin.xhs'),
}


def _first_edittext(nodes):
    """取屏幕上最靠上的可见 EditText（搜索框/输入框）。"""
    best = None
    for n in nodes or []:
        if 'EditText' not in str(n.get('type') or ''):
            continue
        c = (n or {}).get('coordinates') or {}
        try:
            x, y = float(c['x']), float(c['y'])
            w, h = float(c['width']), float(c['height'])
        except (KeyError, TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        if best is None or y < best[1]:
            best = (n, y, int(x + w / 2), int(y + h / 2))
    return best


def _open_search(serial, platform, log=None):
    """打开 App 的搜索页（供粘贴用）。

    各平台实测结论（2026-11-05）：
      · 抖音：`am start snssdk1128://search` 会开**独立任务**，连按 3 次 BACK 都退不出来；
              必须**点主页上的放大镜图标**，搜索页才压在同一任务里，BACK 两次即回主页。
      · 小红书：`xhsdiscover://search/result?keyword=` 压在同一任务，BACK 一次回主页。
    """
    pkg = D.PACKAGES.get(platform, '')
    if not pkg:
        return False
    from . import ui_resolve as UR

    say = log or (lambda m: None)
    if platform == 'douyin':
        # 必须先确保停在主页：分享面板/搜索页残留会让这次点击落到别的控件上
        for _ in range(2):
            act = D.current_activity(serial)
            if 'UserProfile' in act:
                break
            D.adb(serial, 'shell', 'input', 'keyevent', '4')
            time.sleep(1.5)
        hit = UR.resolve(serial, ['搜索', '搜尋'], allow_vision=False)
        if hit.get('ok'):
            say('点搜索图标(%s,%s)' % (hit['x'], hit['y']))
            UR.tap_node(serial, hit.get('node'), {'cx': hit['x'], 'cy': hit['y']})
            time.sleep(5.0)
            say('搜索页 -> %s' % (D.current_activity(serial) or '').split('/')[-1])
            return True
        # 兜底：主页顶部栏的放大镜（1080x1920 实测 880,192）
        geo = None
        try:
            from . import ui_probe_targets as U
            geo = U.screen_profile(serial)
        except Exception:   # noqa: BLE001
            geo = None
        w = (geo or {}).get('width') or 1080
        h = (geo or {}).get('height') or 1920
        UR.tap_point(serial, int(w * 0.815), int(h * 0.10))
        time.sleep(5.0)
        return True

    scheme = SEARCH_OPENERS.get(platform)
    if not scheme:
        return False
    D.adb(serial, 'shell', 'am', 'start', '-a', 'android.intent.action.VIEW',
          '-d', scheme[0], '-p', pkg)
    time.sleep(5.0)
    return True


def paste_read(serial, platform, log=None):
    """分享面板里已复制到剪贴板后：打开搜索页 → 粘贴 → 读输入框文本。"""
    say = log or (lambda m: None)
    from . import ui_resolve as UR
    if not _open_search(serial, platform, log=say):
        return ''
    try:
        nodes = UR.dump(serial)
    except RuntimeError:
        return ''
    spot = _first_edittext(nodes)
    if spot is None:
        # 有些版本搜索页要先点一下搜索入口
        hit = UR.resolve(serial, ['搜索', '搜尋', 'Search'], allow_vision=False)
        if hit.get('ok'):
            UR.tap_node(serial, hit.get('node'), {'cx': hit['x'], 'cy': hit['y']})
            time.sleep(1.6)
            try:
                nodes = UR.dump(serial)
            except RuntimeError:
                return ''
            spot = _first_edittext(nodes)
    if spot is None:
        say('找不到可粘贴的输入框')
        return ''
    node, _y, cx, cy = spot
    # 搜索页打开时输入框常常已经聚焦，此时再点一次反而可能取消聚焦
    if not node.get('focused'):
        D.adb(serial, 'shell', 'input', 'tap', str(cx), str(cy))
        time.sleep(1.0)
    D.adb(serial, 'shell', 'input', 'keyevent', '279')   # KEYCODE_PASTE
    time.sleep(1.4)
    try:
        nodes = UR.dump(serial)
    except RuntimeError:
        return ''
    for n in nodes:
        if 'EditText' not in str(n.get('type') or ''):
            continue
        txt = str(n.get('text') or n.get('value') or '').strip()
        if txt:
            return txt
    return ''


def capture(serial, platform, log=None):
    """在**对方主页**上抓真 ID。返回 {ok, ident, kind, raw, final}。"""
    say = log or (lambda m: None)
    if platform == 'douyin':
        # 抖音：⋮ → 分享名片 → 复制链接（已验证）
        return D.capture_uid(serial, platform, log=say)
    if platform == 'xiaohongshu':
        from . import ui_resolve as UR
        # 小红书主页的「复制链接」在右上角 **更多(⋮)** 里，不是「分享」。
        # 实测 2026-11-05：点 ⋮ 弹出的面板里有 复制链接/举报/拉黑。
        opened = False
        for words in (['更多操作', '更多'], ['分享']):
            hit = UR.resolve(serial, words, allow_vision=False)
            if hit.get('ok'):
                UR.tap_node(serial, hit.get('node'), {'cx': hit['x'], 'cy': hit['y']})
                opened = True
                break
        if not opened:
            # 兜底：右上角三个点的位置（1080x1920 实测 978,124）
            geo = None
            try:
                from . import ui_probe_targets as U
                geo = U.screen_profile(serial)
            except Exception:   # noqa: BLE001
                geo = None
            w = (geo or {}).get('width') or 1080
            h = (geo or {}).get('height') or 1920
            UR.tap_point(serial, int(w * 0.906), int(h * 0.065))
            opened = True
        time.sleep(1.6)
        copy = UR.resolve(serial, ['复制链接', '複製連結'], allow_vision=False)
        if not copy.get('ok'):
            return {'ok': False, 'reason': '面板里没有「复制链接」'}
        UR.tap_node(serial, copy.get('node'), {'cx': copy['x'], 'cy': copy['y']})
        time.sleep(1.2)
        raw = D.read_clipboard(serial)
        say('剪贴板(粘贴读回)：%s' % (raw[:100] if raw else '(空)'))
        if not raw:
            return {'ok': False, 'reason': '粘贴读回为空'}
        parsed = D.parse_xiaohongshu(raw)
        if not parsed.get('user_id'):
            return {'ok': False, 'reason': '没解析出 user_id', 'raw': raw[:160]}
        return {'ok': True, 'ident': parsed['user_id'], 'kind': 'user_id',
                'raw': raw[:160], 'final': parsed.get('final', '')}
    if platform == 'tiktok':
        from . import ui_resolve as UR
        # TK：直接读页面上的 @handle
        try:
            nodes = UR.dump(serial)
        except RuntimeError:
            return {'ok': False, 'reason': '读不到界面'}
        for n in nodes:
            for t in (n.get('text'), n.get('label')):
                m = re.fullmatch(r'@([A-Za-z0-9._]{2,40})', str(t or '').strip())
                if m:
                    return {'ok': True, 'ident': m.group(1), 'kind': 'handle'}
        return {'ok': False, 'reason': '页面上没找到 @用户名'}
    # B站 mid / FB profile_id 采集阶段本来就有，不需另抓
    return {'ok': False, 'reason': '该平台采集阶段已有唯一ID'}


def capture_and_return(serial, platform, log=None):
    """在**对方主页**上抓真 ID，并保证抓完仍停在同一个主页。

    采集流程依赖"从主页 BACK 一次回评论页"，所以抓 ID 期间必须保住返回栈：
    搜索页是**压在主页上面**的（同一任务），读完后按 BACK 就能回主页；
    实在回不去才用 CLEAR_TOP 复用主页实例（实测不会清掉底下的评论页）。
    """
    say = log or (lambda m: None)
    from . import ui_resolve as UR  # noqa: F401
    cap = capture(serial, platform, log=say)
    marks = D.LANDING.get(platform, ())

    def on_profile():
        act = D.current_activity(serial)
        return any(k in act for k in marks)

    if on_profile():
        cap['restored'] = True
        return cap

    # 回主页：抓 ID 时只多压了「搜索页」一层。抖音搜索页对 BACK 的反应很慢
    # （实测按完要 2~3 秒才切页，1.5 秒会误判成"没回去"），所以按一次就轮询等。
    # 限 3 次：按多了会越过主页把底下的视频页也退掉。
    for i in range(3):
        D.adb(serial, 'shell', 'input', 'keyevent', '4')
        for _ in range(6):          # 最多等 3 秒
            time.sleep(0.5)
            if on_profile():
                break
        say('BACK%d -> %s' % (i + 1, (D.current_activity(serial) or '').split('/')[-1]))
        if on_profile():
            break
    cap['restored'] = on_profile()
    # 不用 CLEAR_TOP 回主页：它会重建主页实例、清掉底下的视频页/评论面板。
    say('抓ID后回主页：%s' % ('成功' if cap['restored'] else '未回到，交给调用方回退'))
    return cap


def store(platform, ident, lead_id=None, serial=''):
    """把抓到的真 ID 写进中转站（按平台写对应字段）。"""
    from . import lead_store as L
    field = {'douyin': 'commenter_uid',
             'xiaohongshu': 'commenter_user_id',
             'tiktok': 'commenter_handle'}.get(platform)
    if not field or not ident:
        return 0
    try:
        conn = L._db()
        name = L.ensure(platform)
        with conn:
            if lead_id:
                cur = conn.execute(
                    'UPDATE %s SET %s=? WHERE id=?' % (name, field),
                    (ident, lead_id))
                return cur.rowcount
            cur = conn.execute(
                'UPDATE %s SET %s=? WHERE collected_serial=? AND %s=0' %
                (name, field, field), (ident, serial))
            return cur.rowcount
    except Exception:
        return 0
