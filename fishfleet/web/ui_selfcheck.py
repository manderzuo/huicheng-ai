# -*- coding: utf-8 -*-
"""一键自检：核对真实坐标，按机型生成/修复 profile。

思路（2026-11-04 主人要求）：
    1. 读这台机器的真实几何（分辨率/DPI/状态栏/导航栏）；
    2. 和基准机型对比，算出缩放与偏移；
    3. 对注册表里的每个坐标：按比例算出"应该在哪"，再 dump 真机界面，
       看那个位置附近有没有**符合预期的元素**（文字/可点击）；
    4. 命中就记录真实元素中心 → 反算出正确比例 → 写进本机 profile；
    5. 输出报告：哪些对上、哪些偏了、偏多少、有没有修好。

踩坑记录（都实测过）：
    · `adb shell cat` 读 UI XML：UTF-8 中文变乱码，节点还少一半；
    · `uiautomator dump` + `adb pull`：文件根本生成不出来——
      mobile-mcp 的 uiautomator2 服务常驻占用，adb 版抢不到；
    · 所以最终走项目自己的 mobile-mcp 通道（已在跑、编码正常、带 device 参数）。
"""
from __future__ import annotations

import os
import re
import subprocess

from . import ui_probe_targets as U


def _adb(serial: str, *args: str, timeout: float = 25.0) -> str:
    adb = os.environ.get('ADB_PATH') or __import__('fishfleet.paths', fromlist=['adb_path']).adb_path()
    try:
        p = subprocess.run([adb, '-s', serial] + list(args),
                           capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='ignore')
        return (p.stdout or '') + (p.stderr or '')
    except Exception:
        return ''


def _foreground(serial: str) -> str:
    out = _adb(serial, 'shell', 'dumpsys window | grep -E "mCurrentFocus|mFocusedApp"')
    m = re.search(r'([A-Za-z0-9_.]+)/[A-Za-z0-9_$.]+', out)
    return m.group(1) if m else ''


def dump_nodes(serial: str) -> list[dict]:
    """dump 一次界面层级，返回 [{text,desc,cls,clickable,x,y,w,h,cx,cy}]。"""
    text = ''
    try:
        from . import mobile_gateway
        with mobile_gateway.device_session(serial) as ad:
            res = ad.call_tool('mobile_list_elements_on_screen',
                               {'device': serial, 'format': 'text'})
        payload = getattr(res, 'result', None) or {}
        for item in (payload.get('content') or []):
            if isinstance(item, dict) and item.get('type') == 'text':
                text += (item.get('text') or '') + '\n'
    except Exception:
        return []
    nodes = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('@e'):
            continue
        m_at = re.search(r'at=(-?\d+),(-?\d+)\s+size=(\d+)x(\d+)', line)
        if not m_at:
            continue
        x, y, w, h = (int(v) for v in m_at.groups())
        if w <= 0 or h <= 0:
            continue
        m_text = re.search(r'text="([^"]*)"', line)
        m_label = re.search(r'label="([^"]*)"', line)
        m_cls = re.match(r'@e\d+\s+([A-Za-z0-9_$.]+)', line)
        nodes.append({
            'text': (m_text.group(1) if m_text else '').strip(),
            'desc': (m_label.group(1) if m_label else '').strip(),
            'cls': (m_cls.group(1).split('.')[-1] if m_cls else ''),
            'clickable': True,
            'x': x, 'y': y, 'w': w, 'h': h,
            'cx': x + w // 2, 'cy': y + h // 2,
        })
    return nodes


def _match(nodes, expect, x, y, tol):
    """在期望点附近找符合特征的节点，返回最近的。"""
    want = [t.lower() for t in (expect.get('text_in') or [])]
    best, bestd = None, None
    for n in nodes:
        blob = (n['text'] + ' ' + n['desc']).strip().lower()
        if want and not any(w in blob for w in want):
            continue
        dx, dy = n['cx'] - x, n['cy'] - y
        d = (dx * dx + dy * dy) ** 0.5
        if d > tol:
            continue
        if bestd is None or d < bestd:
            best, bestd = n, d
    if best is None:
        return None
    return {'node': best, 'dist': round(bestd or 0, 1),
            'dx': best['cx'] - x, 'dy': best['cy'] - y}


def _page_ready(nodes, markers) -> bool:
    """页面就绪判断：命中任意一个页面标记文字，才认为停在目标页面。"""
    if not markers:
        return True
    blob = ' '.join((n['text'] + ' ' + n['desc']) for n in nodes)
    return any(m in blob for m in markers)


def launch_home(serial: str, pkg: str, markers=None, hard: bool = False) -> bool:
    """把应用带到目标页面。

    实测：`monkey` 只把 app 带到前台（停在原页面）；`am start SplashActivity`
    会被 top-most 实例吃掉。所以用两层：
      1) 温和：最多按 4 次返回键，每次复检页面标记；
      2) 彻底（hard=True）：force-stop 再拉起，一定回首页，但会中断应用当前状态。
    """
    import time as _t
    if not pkg:
        return False
    if hard:
        _adb(serial, 'shell', 'am force-stop ' + pkg, timeout=20)
        _t.sleep(1.2)
        _adb(serial, 'shell',
             'monkey -p %s -c android.intent.category.LAUNCHER 1' % pkg, timeout=25)
        if not markers:
            # 没给标记就按老行为等固定时间
            _t.sleep(3.5)
            return True
        # 2026-11-05：慢机（41号）3.5 秒常常还没画完首页，必须轮询等标记
        for _ in range(8):
            _t.sleep(1.5)
            if _page_ready(dump_nodes(serial), markers):
                return True
        # 还不到就试一次关弹窗（隐私政策/青少年模式/更新提示等）
        try:
            from . import ui_resolve as UR
            UR.dismiss(serial, force=True, max_rounds=2)
        except Exception:
            pass
        for _ in range(4):
            _t.sleep(1.5)
            if _page_ready(dump_nodes(serial), markers):
                return True
        return False
    for _ in range(4):
        nodes = dump_nodes(serial)
        if _page_ready(nodes, markers):
            return True
        _adb(serial, 'shell', 'input keyevent 4', timeout=15)
        _t.sleep(1.3)
    return _page_ready(dump_nodes(serial), markers)


def selfcheck(serial: str, only_app: str = '', apply_fix: bool = True,
              navigate: bool = False, hard_nav: bool = False) -> dict:
    """跑一次自检。返回结构化报告，并在 apply_fix 时写本机 profile。

    navigate=True：目标应用在前台但不在目标页面时，先尝试回到主页再复检；
    hard_nav=True：改用 force-stop 重开（一定能回首页，但会中断应用当前状态）。
    """
    geo = U.screen_profile(serial)
    key = U.device_key(geo)
    fg = _foreground(serial)
    nodes = dump_nodes(serial)

    if navigate and fg:
        markers, has_t = [], False
        for t in U.TARGETS.values():
            if t['app'] == fg and (not only_app or t['app'] == only_app):
                markers = t.get('page_markers') or []
                has_t = True
                break
        if has_t and markers and not _page_ready(nodes, markers):
            launch_home(serial, fg, markers, hard=hard_nav)
            fg = _foreground(serial)
            nodes = dump_nodes(serial)

    tol = max(60, int(geo['width'] * 0.09))
    prof0 = U.load_profile(key) or {}
    eff = dict(prof0.get('dp_offsets') or {})

    items, fixes = [], {}
    for tkey, t in U.TARGETS.items():
        if t.get('checkable') is False:
            continue  # 子页面坐标只做解析，不参检（一键自检只覆盖各 App 首页）
        if only_app and t['app'] != only_app:
            continue
        x, y = U.resolve_point(serial, tkey)
        same_app = fg == t['app']
        ready = same_app and _page_ready(nodes, t.get('page_markers'))
        hit = _match(nodes, t['expect'], x, y, tol) if ready else None
        if hit:
            anchor = t['anchor']
            dx, dy = U.point_to_anchor(geo, anchor, hit['node']['cx'], hit['node']['cy'])
            base = eff.get(tkey) or t.get('dp') or (0.0, 0.0)
            # dp 容差 2dp（相当于 5px 左右），超过就认为需要校正
            fixed = abs(dx - float(base[0])) > 2.0 or abs(dy - float(base[1])) > 2.0
            if apply_fix and fixed:
                fixes[tkey] = [dx, dy]
            items.append({
                'key': tkey, 'app': t['app'], 'page': t['page'],
                'anchor': anchor,
                'result': 'recalibrated' if fixed else 'ok',
                'expect_point': [x, y], 'actual_point': [hit['node']['cx'], hit['node']['cy']],
                'offset': [hit['dx'], hit['dy']], 'dist': hit['dist'],
                'dp_default': list(t.get('dp') or []), 'dp_actual': [dx, dy],
                'matched_text': (hit['node']['text'] or hit['node']['desc'])[:30],
                'ratio_default': list(t['ratio']), 'ratio_actual': [],
            })
        else:
            if not same_app:
                verdict, reason = 'skipped', '当前前台是 %s，未切换到该应用' % (fg or '未知')
            elif not ready:
                verdict, reason = 'need_nav', '在 %s 但不在「%s」页（缺页面标记）' % (t['app'], t['page'])
            else:
                verdict, reason = 'miss', '在目标页面但该坐标附近没有预期元素（真实偏差）'
            items.append({
                'key': tkey, 'app': t['app'], 'page': t['page'],
                'anchor': t['anchor'],
                'result': verdict, 'reason': reason,
                'expect_point': [x, y],
                'dp_default': list(t.get('dp') or []),
                'ratio_default': list(t['ratio']),
            })

    dp_offsets = dict(prof0.get('dp_offsets') or {})
    if fixes:
        dp_offsets.update(fixes)
    doc = {
        'device_key': key, 'serial': serial,
        'screen': geo, 'baseline': U.BASELINE,
        'dp_offsets': dp_offsets, 'last_foreground': fg,
    }
    path = U.save_profile(key, doc) if (fixes or not prof0) else ''

    ok = sum(1 for i in items if i['result'] == 'ok')
    fixed_n = sum(1 for i in items if i['result'] == 'recalibrated')
    miss = sum(1 for i in items if i['result'] == 'miss')
    skip = sum(1 for i in items if i['result'] == 'skipped')
    need_nav = sum(1 for i in items if i['result'] == 'need_nav')
    sanity = U.sanity_check(geo)
    return {
        'ok': True, 'serial': serial, 'device_key': key, 'foreground': fg,
        'screen': geo, 'baseline': U.BASELINE,
        'size_changed': (geo['width'] != U.BASELINE['width'] or geo['height'] != U.BASELINE['height']),
        'dpi_changed': geo['dpi'] != U.BASELINE['dpi'],
        'dp_changed': abs(geo['dp_w'] - U.BASELINE['dp_w']) > 12,
        'sanity': sanity,
        'summary': {'ok': ok, 'recalibrated': fixed_n, 'miss': miss,
                    'skipped': skip, 'need_nav': need_nav, 'total': len(items)},
        'items': items,
        'profile_path': path,
        'profile': doc,
    }


def selfcheck_all_apps(serial: str, apply_fix: bool = True, hard_nav: bool = True) -> dict:
    """一键自检（全覆盖）：依次把每个 App 切到主界面，各自核对坐标。

    会切换应用（hard_nav 时 force-stop 重开），所以只在设备空闲时用。
    """
    import time as _t
    apps = []
    for t in U.TARGETS.values():
        if t['app'] not in apps:
            apps.append(t['app'])
    merged, profiles, notes = [], [], []
    for app in apps:
        try:
            # 把该 App 自己的页面标记交给 launch_home，才能"等到真的到首页"
            app_markers = []
            for t in U.TARGETS.values():
                if t['app'] == app:
                    for m in (t.get('page_markers') or []):
                        if m not in app_markers:
                            app_markers.append(m)
            launch_home(serial, app, app_markers or None, hard=hard_nav)
            _t.sleep(2.0)
            rep = selfcheck(serial, only_app=app, apply_fix=apply_fix, navigate=False)
            merged.extend(rep['items'])
            if rep.get('profile_path'):
                profiles.append(rep['profile_path'])
        except Exception as e:  # noqa: BLE001
            notes.append('%s 自检失败：%s' % (app, str(e)[:120]))
    ok = sum(1 for i in merged if i['result'] == 'ok')
    fixed_n = sum(1 for i in merged if i['result'] == 'recalibrated')
    miss = sum(1 for i in merged if i['result'] == 'miss')
    nav = sum(1 for i in merged if i['result'] == 'need_nav')
    skip = sum(1 for i in merged if i['result'] == 'skipped')
    geo = U.screen_profile(serial)
    return {
        'ok': True, 'serial': serial, 'device_key': U.device_key(geo),
        'screen': geo, 'baseline': U.BASELINE,
        'size_changed': (geo['width'] != U.BASELINE['width'] or geo['height'] != U.BASELINE['height']),
        'dpi_changed': geo['dpi'] != U.BASELINE['dpi'],
        'dp_changed': abs(geo['dp_w'] - U.BASELINE['dp_w']) > 12,
        'sanity': U.sanity_check(geo),
        'summary': {'ok': ok, 'recalibrated': fixed_n, 'miss': miss,
                    'need_nav': nav, 'skipped': skip, 'total': len(merged)},
        'items': merged,
        'profile_path': (profiles[0] if profiles else ''),
        'notes': notes, 'all_apps': True,
    }
