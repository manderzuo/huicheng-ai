#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B站私信技能（builtin:bilibili-dm）。

把「领中转站线索 → 打开发信会话 → 填草稿 → 验证」包装成 FishFleet 技能。

⚠️ 2026-11-02 重要修复：这个模块原来复用 dm_bili.py（基于 uiautomator2），
但 FishFleet 的 Mobile MCP 会先注册 UiAutomation，u2 再连就抛
AccessibilityServiceAlreadyRegisteredError，`dump_hierarchy()` 和
`app_current()` 全废 —— 表现为"回复全部失败：没打开用户主页"。

现在改成和 bilibili_lead.py 一样的混合链路：
  · 读 UI 树 → Mobile MCP（mobile_list_elements_on_screen）
  · 点击/返回/DeepLink → adb input tap（MCP 的 click 会被 B站 某些控件吞掉）
  · 中文输入 → MCP 的 mobile_type_keys
  · 前台判断 → adb dumpsys（不占 UiAutomation）

**绝不自动发送**：只填草稿，停在发送键前，由人工放行。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent          # huicheng-auto/
sys.path.insert(0, str(_ROOT))

from . import execution            # noqa: E402
from . import lead_store           # noqa: E402
from .mobile_gateway import MobileCallError, call  # noqa: E402,F401
from .ui_probe_targets import resolve_point as _resolve  # noqa: E402

BILI_DM_SKILL_ID = 'builtin:bilibili-dm'
SKILL_NAME = 'B站私信'
PLATFORM = 'bilibili'
PACKAGE = 'tv.danmaku.bili'

ID_INPUT = 'text_input'
ID_SEND = 'send_button'
ID_TITLE = 'tv_conversation_title'
#: 主页右上角私信图标 → 注册表 'bilibili.dm.icon'（dp 自适应，基准机 852,127）
KEY_DM_ICON = 'bilibili.dm.icon'
#: 私信图标的候选相对位置（按宽高比例，逐个试，命中即止）
DM_ICON_CANDIDATES = ((0.778, 0.077), (0.697, 0.086), (0.860, 0.077),
                      (0.772, 0.086), (0.700, 0.077))

DEFAULT_TPL = ('你好呀，看到你在「{keyword}」相关视频下留言「{评论}」，'
               '我这边刚好懂这块，有问题可以随时问我～')

ADB_FALLBACK = __import__("fishfleet.paths", fromlist=["adb_path"]).adb_path()


class SkillError(RuntimeError):
    pass


# ---------------------------------------------------------------- 基础
def _adb_path() -> str:
    try:
        from .server import _adb_path as p
        path = p()
        if path:
            return path
    except Exception:
        pass
    return ADB_FALLBACK


def _sh(serial, cmd, timeout=30) -> str:
    p = subprocess.run([_adb_path(), '-s', serial, 'shell', cmd],
                       capture_output=True, timeout=timeout)
    return (p.stdout + p.stderr).decode('utf-8', 'replace').strip()


def _tap(serial, x, y):
    _sh(serial, 'input tap %d %d' % (int(x), int(y)))


def _back(serial):
    _sh(serial, 'input keyevent 4')


def activity(serial) -> str:
    """用 dumpsys 读前台 Activity（不占 UiAutomation）。"""
    out = _sh(serial, 'dumpsys activity activities')
    m = re.search(r'mResumedActivity.*?u0\s+([A-Za-z0-9_.]+)/([A-Za-z0-9_.]+)', out)
    if not m:
        return ''
    act = m.group(2)
    return (m.group(1) + act) if act.startswith('.') else act


def elements(serial):
    """MCP 读 UI 树，解出元素列表。坏树直接抛错，不猜坐标。"""
    result = call('mobile_list_elements_on_screen', {'format': 'json'}, serial)
    for item in (result or {}).get('content', []):
        if item.get('type') != 'text':
            continue
        text = item.get('text', '')
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            m = re.search(r'\[\s*\{.*\}\s*\]', text, re.S)
            if not m:
                continue
            try:
                value = json.loads(m.group(0))
            except ValueError:
                continue
        if isinstance(value, list) and all(isinstance(e, dict) for e in value):
            return value
    raise SkillError('无法读取当前 UI 树')


def box(el):
    c = (el or {}).get('coordinates') or {}
    if not all(isinstance(c.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
        return None
    if c['width'] <= 0 or c['height'] <= 0:
        return None
    return {'x': int(c['x']), 'y': int(c['y']),
            'w': int(c['width']), 'h': int(c['height'])}


def center(el):
    b = box(el)
    return (b['x'] + b['w'] // 2, b['y'] + b['h'] // 2) if b else None


def rid(el) -> str:
    return str(el.get('identifier') or '').replace(PACKAGE + ':id/', '')


def txt(el) -> str:
    return (el.get('text') or '').strip()


def find_id(serial, target_id, want_text=None):
    """按 resource-id（可选文本）找元素，返回 (cx, cy) 或 None。"""
    for e in elements(serial):
        if rid(e) != target_id:
            continue
        if want_text is not None and txt(e) != want_text:
            continue
        c = center(e)
        if c:
            return c
    return None


# ---------------------------------------------------------------- 文案
def clean_quote(text: str, limit: int = 24) -> str:
    """把评论原文整理成能塞进一句话里的引用。"""
    s = (text or '').strip()
    s = re.sub(r'\[[^\]\n]{1,12}\]', '', s)
    s = re.sub(r'//[^/\n]{1,20}//', '', s)
    s = re.sub(r'//\S*', '', s)
    s = re.sub(r'@\S+\s*', '', s)
    s = re.sub(r'https?://\S+', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = s.strip('，。,.、;；:：!！?？~～"\'')
    if len(re.sub(r'[\s\W_]', '', s)) < 3:
        return ''
    if len(s) > limit:
        s = s[:limit] + '…'
    return s


def personalize(tpl: str, lead: dict) -> str:
    if not (tpl or '').strip():
        tpl = DEFAULT_TPL
    who = (lead.get('comment_author') or '').strip()
    say = clean_quote(lead.get('comment_text') or '')
    text = tpl.replace('{昵称}', who).replace('{作者}', who)
    text = text.replace('{keyword}', lead.get('keyword', ''))
    if say:
        return text.replace('{评论}', say)
    text = re.sub(r'留言「\{评论\}」[，,]?', '，', text)
    return text.replace('{评论}', '')


def _norm(s):
    return re.sub(r'[\s，。,.、;；:：!！?？~～"\'「」]', '', s or '')


# ---------------------------------------------------------------- 私信页动作
def _screen_size(serial):
    """取屏幕像素尺寸；读不到就按 1080x1920 兜底。"""
    try:
        out = _sh(serial, 'wm size')
        import re as _re
        m = _re.search(r'(\d+)x(\d+)', out or '')
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 1080, 1920


def open_dm(serial, uid):
    """按 UID 打开私信会话，返回对方昵称。"""
    _sh(serial, 'am start -a android.intent.action.VIEW '
               '-d "bilibili://space/%s" -p tv.danmaku.bili' % uid)
    # 主页是 WebView，等渲染完私信图标才能点
    for _ in range(16):
        time.sleep(0.5)
        if 'LocalAuthorSpace' in activity(serial):
            break
    if 'LocalAuthorSpace' not in activity(serial):
        raise SkillError('没打开用户主页（当前 %s）' % (activity(serial).split('.')[-1] or '未知'))

    # 主页加载失败时私信图标点不动，先处理错误页
    for _ in range(3):
        tree = elements(serial)
        bad = next((e for e in tree
                    if txt(e) in ('加载出错，请稍后再试', '加载失败')), None)
        if bad is None:
            break
        retry = next((e for e in tree if txt(e) == '再试一次'), None)
        if retry is None:
            raise SkillError('主页加载失败（接口抖动）')
        c = center(retry)
        _tap(serial, c[0], c[1])
        time.sleep(6)

    last = ''
    for _ in range(3):
        ok = False
        try:
            _tap(serial, *_resolve(serial, KEY_DM_ICON))
            ok = True
        except Exception:
            ok = False
        if not ok or 'ConversationActivity' not in activity(serial):
            # 2026-11-05 实测：私信图标位置随机型不同
            #   41号 720x1612  -> (502,138)  比例 (0.697, 0.0856)
            #   手机4 1080x1920 -> (840,147)  比例 (0.778, 0.077)
            # H5 主页在部分机型不暴露 DOM，只能按候选点轮询。
            w, h = _screen_size(serial)
            for rx, ry in DM_ICON_CANDIDATES:
                _tap(serial, int(w * rx), int(h * ry))
                for _ in range(4):
                    time.sleep(0.5)
                    if 'ConversationActivity' in activity(serial):
                        break
                if 'ConversationActivity' in activity(serial):
                    break
        for _ in range(9):
            time.sleep(0.5)
            if 'ConversationActivity' in activity(serial):
                break
        last = activity(serial)
        if 'ConversationActivity' not in last and 'LocalAuthorSpace' not in last:
            # 落在广告/弹层（如 VertexActivity）：BACK 掉再试一次
            try:
                _back(serial)
                time.sleep(1.5)
            except Exception:
                pass
        if 'ConversationActivity' in last:
            spot = find_id(serial, ID_TITLE)
            if spot:
                for e in elements(serial):
                    if rid(e) == ID_TITLE and txt(e):
                        return txt(e)
            return ''
        time.sleep(2)
    raise SkillError('没进入私信会话页（activity=%s）' % (last.split('.')[-1] or '未知'))


def fill(serial, text):
    """把消息填进输入框（不发送），返回回读内容。"""
    spot = find_id(serial, ID_INPUT)
    if spot is None:
        raise SkillError('找不到输入框')
    _tap(serial, spot[0], spot[1])
    time.sleep(1.0)

    # 中文必须走 MCP（adb input text 不支持非 ASCII）
    call('mobile_type_keys', {'text': text}, serial)
    time.sleep(1.2)

    for e in elements(serial):
        if rid(e) == ID_INPUT:
            return txt(e)
    return ''


def verify_and_mark(task, serial, lid, uid, text):
    """审核：输入框内容匹配 + 发送按钮可读。"""
    tree = elements(serial)

    box_el = next((e for e in tree if rid(e) == ID_INPUT), None)
    got = txt(box_el) if box_el else ''
    if not got:
        raise SkillError('输入框为空')
    if '发个消息聊聊呗' in got:
        raise SkillError('输入框还是占位提示，文字没写进去')

    want, have = _norm(text), _norm(got)
    if want != have and (len(have) < 8 or have[:8] != want[:8]):
        raise SkillError('输入框内容和目标不符：期望 %r… 实际 %r'
                         % (want[:16], have[:16]))

    send_el = next((e for e in tree if rid(e) == ID_SEND), None)
    if send_el is None:
        raise SkillError('读不到发送按钮')
    c = center(send_el)
    if c is None:
        raise SkillError('发送按钮没有可点坐标')

    return {'draft': got[:80], 'send_button': [c[0], c[1]]}


# ---------------------------------------------------------------- 主流程
def run_one(task, serial, tpl=''):
    # 2026-11-06：支持按批次（日期+关键词）只回某一批采集到的线索
    _batch = (task.get('lead') or {}).get('batch') or None
    lead = lead_store.claim(PLATFORM, serial, batch=_batch)
    if not lead:
        return None

    lid = lead['id']
    uid = (lead.get('commenter_uid') or '').strip()
    who = lead.get('comment_author', '')
    execution.log(task, '领到 %s  %s  uid=%s' % (lid[:8], who, uid))

    if not uid:
        lead_store.record_extract(PLATFORM, lid, serial, lead_store.EXTRACT_ERROR,
                                  {'reason': '缺少 commenter_uid'}, 0)
        return {'id': lid, 'ok': False, 'reason': 'no uid'}

    text = personalize(tpl or lead.get('dm_text') or '', lead)
    # 自动翻译：按评论语种本地化草稿（中文评论不动，外文调LLM翻译，失败回填中文）
    try:
        from . import draft_i18n
        _cmt = (lead.get('comment_text') or lead.get('comment') or lead.get('content') or '')
        _loc = draft_i18n.localize_draft(_cmt, text)
        if _loc and _loc.get('text'):
            text = _loc.get('text')
            execution.log(task, '草稿语种：%s' % (_loc.get('target_lang') or '?'))
    except Exception:
        pass
    t0 = time.time()

    try:
        title = open_dm(serial, uid)
        fill(serial, text)
        proof = verify_and_mark(task, serial, lid, uid, text)

        lead_store.record_extract(
            PLATFORM, lid, serial, lead_store.FOUND,
            {'session_title': title, 'uid': uid,
             'input_matched': True, 'send_button': proof['send_button'],
             'draft': proof['draft']},
            time.time() - t0)
        execution.log(task, '  ✓ 会话「%s」输入框已确认 + 发送按钮可读（未发送）' % title)
        return {'id': lid, 'ok': True, 'title': title}

    except Exception as exc:
        # 失败后回首页，避免残留状态污染下一条
        try:
            for _ in range(3):
                act = activity(serial)
                if 'MainActivityV2' in act or not act:
                    break
                _back(serial)
                time.sleep(1.2)
        except Exception:
            pass

        lead_store.record_extract(PLATFORM, lid, serial, lead_store.EXTRACT_ERROR,
                                  {'error': str(exc)[:200]}, time.time() - t0)
        execution.log(task, '  ✗ 失败: %s' % exc)
        return {'id': lid, 'ok': False, 'reason': str(exc)}


def run(task):
    """技能入口。task 里读 serial / lead.reply_limit / lead.dm_template。"""
    task['status'] = 'executing'   # 否则界面一直显示"排队中"，状态与事实不符
    lead = task.get('lead') or {}
    serial = task.get('serial') or lead.get('serial') or ''
    if not serial:
        raise SkillError('缺少 serial')

    limit = int(lead.get('reply_limit') or lead.get('limit') or 10)
    tpl = (lead.get('dm_template') or '').strip()

    # 两套节流：interval 每条都停，cooldown 攒够 N 条停一次
    interval_seconds = max(0, int(lead.get('interval_seconds') or 0))
    cooldown_every = max(1, int(lead.get('cooldown_every') or 10))
    cooldown_seconds = max(0, int(lead.get('cooldown_seconds') or 60))

    execution.log(task,
                  'B站私信：设备 %s，上限 %d 条，每条间隔 %d 秒，每 %d 条冷却 %d 秒，只填草稿不发送'
                  % (serial, limit, interval_seconds, cooldown_every, cooldown_seconds))
    # 2026-11-06：自动确保目标 App 在前台（主人要求，不能要求人工先切好）。
    # 不在前台就用 adb 拉起（约 0.1 秒判断 + 启动等待），绝不 force-stop。
    try:
        from .app_foreground import ensure_app_front as _ensure_front
        if not _ensure_front(serial, 'tv.danmaku.bili',
                             log=lambda m: execution.log(task, m)):
            execution.log(task, '自动启动 tv.danmaku.bili 后仍未到前台，继续尝试')
    except Exception as _exc:                      # noqa: BLE001
        execution.log(task, '自动启动检查异常（继续）：%s' % type(_exc).__name__)


    # 设备在线检查用 adb，不碰 u2。重启服务后 adb 刚重连时偶发空返回，
    # 重试 3 次再判死，并把 adb 原话带上（2026-11-03：原来只说"设备不响应"）。
    probe = ''
    for _ in range(3):
        try:
            probe = _sh(serial, 'echo ok')
        except Exception as exc:                      # noqa: BLE001
            probe = 'adb 异常：%s' % exc
        if 'ok' in probe:
            break
        time.sleep(2)
    if 'ok' not in probe:
        raise SkillError('设备不响应（adb 返回：%s）' % (probe[:160] or '空'))

    ok = fail = skipped = 0
    done = 0
    stop = task.get('_stop')
    # 2026-11-06：改成 while —— 碰到「该 UP 主不接收陌生人私信」（平台限制）
    # 要**换下一条线索继续**，而不是把整轮判成失败。
    # 原来固定 for _ in range(limit)，limit=1 时第一条撞上限制就整轮 0 成功。
    attempts = 0
    max_attempts = max(limit * 3, 3)
    while done < limit and attempts < max_attempts:
        if stop is not None and stop.is_set():
            execution.log(task, '已收到停止指令，收尾退出')
            break
        attempts += 1
        r = run_one(task, serial, tpl)
        if r is None:
            execution.log(task, '中转站没有更多待处理线索')
            break
        reason = str(r.get('reason') or '')
        limited = ('没进入私信会话页' in reason
                   or 'LocalAuthorSpace' in reason
                   or '不接收' in reason or '私信入口' in reason)
        if r['ok']:
            ok += 1
            done += 1
        elif limited:
            skipped += 1
            execution.log(task, '  跳过（平台限制，换下一条）')
            continue
        else:
            fail += 1
            done += 1

        if done >= limit:
            break
        if interval_seconds:
            execution.log(task, '  间隔 %d 秒…' % interval_seconds)
            time.sleep(interval_seconds)
        if cooldown_seconds and done % cooldown_every == 0:
            execution.log(task, '  已处理 %d 条，冷却 %d 秒…' % (done, cooldown_seconds))
            time.sleep(cooldown_seconds)

    st = lead_store.stats(PLATFORM)['platforms'][PLATFORM]
    execution.log(task, '本轮完成：成功 %d，跳过 %d（平台限制），失败 %d；'
                         '中转站 drafted=%d pending=%d'
                  % (ok, skipped, fail, st['drafted'], st['pending']))
    # 兜底：run() 结束必须离开 queued/executing，否则前端永远显示"排队中"，
    # 用户以为任务还没开始（2026-11-02 实测回复任务卡死在这里）。
    if task.get('status') in (None, 'queued', 'executing'):
        task['status'] = 'completed'
    return {'ok': ok, 'fail': fail, 'skipped': skipped}
