# -*- coding: utf-8 -*-
"""平台直达：把「分享→复制链接」得到的口令/短链解析成唯一ID，再一键打开本人主页。

实测结论（2026-11-05，41/3/1/2/4/7/8 号机）：
  · 抖音   ：只认**数字 uid**（抖音号不行，会开空主页）
             uid 来自短链解析：…&sec_uid=MS4wLjABAAAA…&activity_info={"social_author_id":"<uid>",…}
  · 小红书 ：`xhsdiscover://user/<小红书号或24位user_id>` 都行
  · B站    ：`https://space.bilibili.com/<mid>`
  · FB     ：`fb://profile/<profile_id>`（落 ImmersivePortraitActivity 就是主页）
  · TikTok ：`https://www.tiktok.com/@handle`（host activity 统一，需看页面锚点）
所有链接都必须带 `-p <包名>`，否则系统弹「打开方式」选择器。
"""
from __future__ import annotations

import io

import json
import re
import subprocess
import time
import urllib.parse
import urllib.request

ADB_CANDIDATES = (__import__('fishfleet.paths', fromlist=['adb_path']).adb_path(), 'adb')
UA = ('Mozilla/5.0 (Linux; Android 11; SM-G973U1) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36')

PACKAGES = {
    'douyin': 'com.ss.android.ugc.aweme',
    'xiaohongshu': 'com.xingin.xhs',
    'bilibili': 'tv.danmaku.bili',
    'tiktok': 'com.zhiliaoapp.musically',
    'facebook': 'com.facebook.katana',
}

# ---------------------------------------------------------------- 新老优先级
# 主人 2026-11-05 拍板：新流程（真 ID 直达）做第一选择，老流程整体保留做兜底。
# 关掉开关 = 立刻恢复旧行为，不用改代码。
DEEPLINK_FIRST = True
PLATFORM_SWITCH = {
    'douyin': True,
    'xiaohongshu': True,
    'bilibili': True,
    'tiktok': True,
    'facebook': True,
}

# 中转站里各平台的「真 ID」字段（按优先级排列）
IDENT_FIELDS = {
    'douyin': ('commenter_uid',),
    'xiaohongshu': ('commenter_user_id',),
    'bilibili': ('commenter_uid', 'up_uid'),
    'tiktok': ('commenter_handle',),
    'facebook': ('commenter_profile_id',),
}

# 这些平台的兜底还需要的"号"（老流程用）
LEGACY_FIELDS = {
    'douyin': 'commenter_douyin_id',
    'xiaohongshu': 'author_red_id',
}


def enabled(platform):
    return bool(DEEPLINK_FIRST and PLATFORM_SWITCH.get(platform, False))


def pick_ident(platform, lead):
    """从一条线索里取"真 ID"；没有就返回空串（调用方走老流程）。"""
    for f in IDENT_FIELDS.get(platform, ()):
        v = str((lead or {}).get(f) or '').strip()
        if v:
            return v
    return ''

# 落地页特征：命中即认为真到了主页
LANDING = {
    'douyin': ('UserProfileActivity',),
    'xiaohongshu': ('NewOtherUserActivity', 'MyUserActivity', 'profile'),
    'bilibili': ('LocalAuthorSpaceActivity', 'space'),
    'tiktok': ('TikTokHostActivity',),
    'facebook': ('ImmersivePortraitActivity', 'Profile'),
}


def note(message):
    """把直达决策写进 reports/deeplink_live.log（失败不影响主流程）。"""
    try:
        import os
        import time as _t
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), 'reports', 'deeplink_live.log')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (_t.strftime('%H:%M:%S'), message))
    except Exception:
        pass


def _adb():
    import os
    import shutil
    for c in ADB_CANDIDATES:
        if os.path.isfile(c) or shutil.which(c):
            return c
    return 'adb'


def adb(serial, *args, timeout=60):
    return subprocess.run([_adb(), '-s', serial, *args], capture_output=True,
                          text=True, encoding='utf-8', errors='ignore',
                          timeout=timeout)


# ---------------------------------------------------------------- 解析
def resolve_url(url, timeout=20):
    """跟随重定向拿最终地址（短链 → 真实链接）。"""
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.geturl() or url
    except Exception:
        return url


def find_url(text):
    m = re.search(r'https?://[^\s，,。]+', str(text or ''))
    return m.group(0).rstrip('.') if m else ''


def parse_douyin(text):
    """从口令/短链/长链里取 {short, sec_uid, uid}。"""
    url = find_url(text)
    out = {'short': url, 'sec_uid': '', 'uid': '', 'final': url}
    if not url:
        return out
    final = resolve_url(url)
    out['final'] = final
    m = re.search(r'sec_uid=([A-Za-z0-9_\-]+)', final)
    if m:
        out['sec_uid'] = m.group(1)
    else:
        m = re.search(r'/share/user/([A-Za-z0-9_\-]+)', final)
        if m:
            out['sec_uid'] = m.group(1)
    m = re.search(r'social_author_id%22%3A%22(\d+)', final) or \
        re.search(r'"social_author_id":"(\d+)"', final)
    if m:
        out['uid'] = m.group(1)
    return out


def parse_xiaohongshu(text):
    """从口令/短链/长链里取 {short, user_id}。"""
    url = find_url(text)
    out = {'short': url, 'user_id': '', 'final': url}
    if not url:
        return out
    final = resolve_url(url)
    out['final'] = final
    m = re.search(r'/user/profile/([0-9a-f]{16,32})', final)
    if m:
        out['user_id'] = m.group(1)
    return out


def parse_link(platform, text):
    if platform == 'douyin':
        return parse_douyin(text)
    if platform == 'xiaohongshu':
        return parse_xiaohongshu(text)
    return {'short': find_url(text), 'final': resolve_url(find_url(text))}


# ---------------------------------------------------------------- 直达
def valid_ident(platform, ident):
    """ID 形态校验：不合法就别发 intent，否则 Android 会把它交给别的应用。"""
    ident = str(ident or '').strip()
    if not ident or ident.lower() in ('none', 'null', 'undefined'):
        return False
    if platform in ('douyin', 'bilibili'):
        return bool(re.fullmatch(r'\d{4,25}', ident))
    if platform == 'xiaohongshu':
        return bool(re.fullmatch(r'[0-9a-fA-F]{16,32}', ident)) \
            or bool(re.fullmatch(r'\d{5,20}', ident))
    if platform == 'tiktok':
        return bool(re.fullmatch(r'[A-Za-z0-9._]{2,40}', ident.lstrip('@')))
    if platform == 'facebook':
        return bool(re.fullmatch(r'[A-Za-z0-9._\-]{2,60}', ident))
    return True


def build_link(platform, ident):
    """按平台把 ID 组装成能唤起 App 的链接。"""
    ident = str(ident or '').strip()
    if not ident:
        return ''
    if not valid_ident(platform, ident):
        return ''
    if platform == 'douyin':
        # 数字 uid 才有效；传进来是抖音号时也照样试一次（会开空页，由调用方校验）
        return 'snssdk1128://user/profile/%s' % ident
    if platform == 'xiaohongshu':
        return 'xhsdiscover://user/%s' % ident
    if platform == 'bilibili':
        return 'https://space.bilibili.com/%s' % ident
    if platform == 'tiktok':
        return 'https://www.tiktok.com/@%s' % ident.lstrip('@')
    if platform == 'facebook':
        return ('fb://profile/%s' % ident if ident.isdigit()
                else 'https://www.facebook.com/%s' % ident)
    return ''


def current_activity(serial):
    out = adb(serial, 'shell', 'dumpsys activity activities').stdout or ''
    for line in out.splitlines():
        if 'mResumedActivity' in line:
            m = re.search(r'([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)', line)
            return m.group(1) if m else line.strip()
    return ''


def open_profile(serial, platform, ident, wait=9.0, force_stop=False, clear_top=False):
    """直达某人主页，返回 {ok, link, activity, reason}。

    force_stop=False + clear_top=True：不杀进程，用 CLEAR_TOP 把栈上多余页面清掉、
    复用已有主页实例（实测 2026-11-05：从搜索页回主页且**保住底下的评论页**）。
    """
    pkg = PACKAGES.get(platform, '')
    link = build_link(platform, ident)
    if not pkg or not link:
        return {'ok': False, 'reason': '平台或ID无效', 'link': link}
    if force_stop:
        # 注意：杀前台会把任务栈里的**下一个应用**顶上来（实测会闪一下 FB）。
        # 所以杀完必须立刻把目标 App 拉回前台，再发直达链接。
        adb(serial, 'shell', 'am', 'force-stop', pkg)
        time.sleep(0.6)
        adb(serial, 'shell', 'monkey', '-p', pkg, '-c',
            'android.intent.category.LAUNCHER', '1')
        time.sleep(1.6)
    cmd = ['shell', 'am', 'start', '-a', 'android.intent.action.VIEW',
           '-d', link, '-p', pkg]
    if clear_top:
        cmd += ['-f', '0x20000000']
    adb(serial, *cmd)
    time.sleep(wait)
    act = current_activity(serial)
    # 被别的应用抢走（典型：com.facebook.appmanager 抢 xhsdiscover://）→ 停掉它再试一次
    if pkg and pkg not in act:
        stolen = act.split('/')[0].strip()
        if stolen and stolen != pkg:
            try:
                adb(serial, 'shell', 'am', 'force-stop', stolen)
                time.sleep(1.0)
                adb(serial, *cmd)
                time.sleep(wait)
                act = current_activity(serial)
            except Exception:
                pass
    hit = any(k in act for k in LANDING.get(platform, ()))
    try:
        note('OPEN %s link=%s -> %s' % (platform, link,
                                        (act or '').split('/')[-1] or '未知'))
    except Exception:
        pass
    return {'ok': hit, 'link': link, 'activity': act,
            'reason': '' if hit else '落地页不是主页（%s）' % act}


def platform_of_activity(act):
    for name, pkg in PACKAGES.items():
        if pkg in (act or ''):
            return name
    return ''


def direct_or_fallback(serial, platform, lead, fallback, log=None):
    """新流程优先：有真 ID → 直达并校验；没有或失败 → 回落老流程。

    fallback 是"老流程"的可调用对象，返回什么就原样返回什么。
    返回 (used_deeplink, result, detail)。
    """
    say = log or (lambda m: None)
    if not enabled(platform):
        say('直达开关关闭，走老流程')
        return False, fallback(), {'reason': 'switch_off'}
    ident = pick_ident(platform, lead)
    if not ident:
        say('无真ID，走老流程（%s）' % LEGACY_FIELDS.get(platform, ''))
        return False, fallback(), {'reason': 'no_ident'}
    r = open_profile(serial, platform, ident)
    if r.get('ok'):
        say('直达命中：%s' % (r.get('activity') or '').split('/')[-1])
        return True, None, r
    say('直达失败回落老流程：%s' % r.get('reason', ''))
    return False, fallback(), r


# ---------------------------------------------------------------- 采集唯一ID
def read_clipboard(serial):
    """读剪贴板：**先直读**（read=True），读不到才退回粘贴法。

    2026-11-05 实测：抖音 `am start snssdk1128://search` 会开独立任务，
    按 BACK 永远退不回主页，粘贴法会把采集流程弄断。所以直读是第一选择；
    读到的内容只用于解析我们自己刚复制的「复制链接」口令。
    """
    try:
        from .mobile_gateway import call
        res = call('mobile_clipboard', {'read': True}, serial)
        texts = []
        for item in (res or {}).get('content', []) or []:
            if item.get('type') == 'text':
                texts.append(item.get('text') or '')
        txt = '\n'.join(texts).strip()
        if txt:
            return txt
    except Exception:   # noqa: BLE001 直读不可用就退回粘贴法
        pass
    return _clipboard_via_paste(serial)


def _clipboard_via_paste(serial):
    """老办法：打开搜索页 -> 粘贴 -> 读输入框文本（仅作兜底）。"""
    from . import ui_resolve as UR
    adb(serial, 'shell', 'am', 'start', '-a', 'android.intent.action.VIEW',
        '-d', 'snssdk1128://search?keyword=', '-p', PACKAGES['douyin'])
    time.sleep(6)
    geo = None
    try:
        from . import ui_probe_targets as U
        geo = U.screen_profile(serial)
    except Exception:
        geo = None
    w = (geo or {}).get('width') or 1080
    h = (geo or {}).get('height') or 1920
    adb(serial, 'shell', 'input', 'tap', str(int(w * 0.37)), str(int(h * 0.1)))
    time.sleep(1.2)
    adb(serial, 'shell', 'input', 'keyevent', '279')   # KEYCODE_PASTE
    time.sleep(1.4)
    try:
        nodes = UR.dump(serial)
    except RuntimeError:
        return ''
    for n in nodes:
        rid = str(n.get('identifier') or n.get('id') or '')
        if rid.endswith('et_search_kw'):
            return str(n.get('text') or n.get('value') or '').strip()
    return ''

def capture_uid(serial, platform, log=None):
    """在当前「某人主页」上：分享 → 复制链接 → 读剪贴板 → 解析出唯一ID。

    这是抖音唯一能拿到数字 uid 的路子（抖音号会开空主页）。
    返回 {ok, ident, kind, raw, final}。
    """
    say = log or (lambda m: None)
    from . import ui_resolve as UR

    pkg = PACKAGES.get(platform, '')
    act = current_activity(serial)
    if pkg and pkg not in act:
        return {'ok': False, 'reason': '当前不在 %s（%s）' % (platform, act)}

    # 1) 抖音：右上角「更多(⋮)」→「分享名片」；其它平台直接找分享
    if platform == 'douyin':
        more = UR.resolve(serial, ['更多', '更多个人档案设置'], allow_vision=False)
        if not more.get('ok'):
            found = UR._vision(serial, '页面右上角的「更多」三个点图标', '个人主页')
            if not found:
                return {'ok': False, 'reason': '没找到「更多」入口'}
            UR.tap_point(serial, found['x'], found['y'])
        else:
            UR.tap_node(serial, more.get('node'),
                        {'cx': more['x'], 'cy': more['y']})
        time.sleep(1.4)
        card = UR.resolve(serial, ['分享名片'], allow_vision=False)
        if not card.get('ok'):
            return {'ok': False, 'reason': '菜单里没有「分享名片」'}
        UR.tap_node(serial, card.get('node'), {'cx': card['x'], 'cy': card['y']})
        time.sleep(1.4)
    else:
        hit = UR.resolve(serial, ['分享', 'Share'], allow_vision=False)
        if not hit.get('ok'):
            found = UR._vision(serial, '页面右上角的「分享」箭头图标', '个人主页')
            if not found:
                return {'ok': False, 'reason': '没找到分享入口'}
            UR.tap_point(serial, found['x'], found['y'])
        else:
            UR.tap_node(serial, hit.get('node'), {'cx': hit['x'], 'cy': hit['y']})
        time.sleep(1.5)

    # 2) 点「复制链接」
    copy = UR.resolve(serial, ['复制链接', '複製連結', 'Copy link'], allow_vision=False)
    if not copy.get('ok'):
        return {'ok': False, 'reason': '分享面板里没有「复制链接」'}
    UR.tap_node(serial, copy.get('node'), {'cx': copy['x'], 'cy': copy['y']})
    time.sleep(1.2)

    raw = read_clipboard(serial)
    say('剪贴板：%s' % (raw[:120] if raw else '(空)'))
    if not raw:
        return {'ok': False, 'reason': '剪贴板为空（可能没点中复制链接）'}

    parsed = parse_link(platform, raw)
    ident = parsed.get('uid') or parsed.get('user_id') or parsed.get('sec_uid') or ''
    kind = ('uid' if parsed.get('uid') else
            'user_id' if parsed.get('user_id') else
            'sec_uid' if parsed.get('sec_uid') else '')
    if platform == 'bilibili':
        m = re.search(r'space\.bilibili\.com/(\d+)', parsed.get('final') or '')
        ident, kind = (m.group(1), 'mid') if m else ('', '')
    if not ident:
        return {'ok': False, 'reason': '没能从链接里解析出ID', 'raw': raw[:200],
                'final': parsed.get('final', '')}
    return {'ok': True, 'ident': ident, 'kind': kind, 'raw': raw[:200],
            'final': parsed.get('final', '')}
