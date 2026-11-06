#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""小红书私信技能（builtin:xiaohongshu-dm）。

把「领中转站线索 → 打开私信会话 → 填草稿 → 验证」包装成 FishFleet 技能。
界面里勾好手机、设好上限就能直接跑。

**绝不自动发送**：只填草稿、确认发送键可读，停在发送键前由人工放行。
首私信对方未回复前只有 1 条配额，发错不可撤回——所以这里永远不点发送。
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent          # huicheng-auto/
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / 'fishfleet' / 'web'))

from . import execution            # noqa: E402
from . import lead_store           # noqa: E402
from . import xiaohongshu_lead as xhs   # noqa: E402
from .mobile_gateway import MobileCallError, call  # noqa: E402,F401

XHS_DM_SKILL_ID = 'builtin:xiaohongshu-dm'
SKILL_NAME = '小红书私信'
PLATFORM = 'redbook'

DEFAULT_TPL = ('你好{昵称}，看到你说{评论}，我这边可以帮你看看'
               '（如果打扰到你可以直接忽略～）')

STOP_CHARS = set('的了是在和有我你他她它们就都而及与着或一个上上来去到说会这那个什怎么')


class SkillError(RuntimeError):
    pass


def clean_quote(text: str, limit: int = 24) -> str:
    """把评论原文整理成能塞进一句话里的引用。"""
    s = (text or '').strip()
    s = re.sub(r'\[[^\]\n]{1,12}\]', '', s)
    s = re.sub(r'@\S+\s*', '', s)
    s = re.sub(r'https?://\S+', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = s.strip('，。,.、;；:：!！?？~～"\'')
    if len(re.sub(r'[\s\W_]', '', s)) < 2:
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
    text = text.replace('{标题}', lead.get('note_title') or '')
    if say:
        return text.replace('{评论}', say)
    text = re.sub(r'看到你说\{评论\}[，,]\s*', '', text)
    return text.replace('{评论}', '')


def _norm(s):
    return re.sub(r'[\s，。,.、;；:：!！?？~～"\'「」]', '', s or '')


def _clear_and_exit(serial: str, text_len: int):
    """清空输入框并退回首页，避免残留影响下一条。"""
    try:
        for _ in range(min(text_len + 5, xhs.INPUT_LIMIT + 5)):
            call('mobile_press_button', {'button': 'BACKSPACE'}, serial)
        for _ in range(3):
            call('mobile_press_button', {'button': 'BACK'}, serial)
            time.sleep(0.8)
            if 'IndexActivityV2' in xhs.foreground_activity(
                    call('mobile_get_foreground_app', {}, serial)):
                break
    except (SkillError, MobileCallError):
        pass
    except Exception:                                    # noqa: BLE001
        pass


def run_one(task, serial, tpl=''):
    """处理一条线索。返回 dict 或 None（没有更多线索）。"""
    # 2026-11-06：支持按批次（日期+关键词）只回某一批采集到的线索
    _batch = (task.get('lead') or {}).get('batch') or None
    lead = lead_store.claim(PLATFORM, serial,
                            require_nonempty=('author_red_id',), batch=_batch)
    if not lead:
        return None

    lid = lead['id']
    xhs_id = (lead.get('author_red_id') or '').strip()
    # 采集时抓到的 24 位 user_id：有它就直达主页，不用再搜号（2026-11-05）
    user_id = (lead.get('commenter_user_id') or '').strip()
    who = lead.get('comment_author', '')
    execution.log(task, '领到 %s  %s  小红书号=%s%s'
                  % (lid[:8], who, xhs_id,
                     '  user_id=' + user_id[:12] + '…' if user_id else '  (无user_id，走搜号)'))

    if not xhs_id:
        lead_store.record_extract(PLATFORM, lid, serial, lead_store.EXTRACT_ERROR,
                                  {'reason': '缺少 author_red_id'}, 0)
        return {'id': lid, 'ok': False, 'reason': '缺少小红书号'}

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
    ok = False
    reason = ''

    try:
        info = xhs.open_chat(serial, xhs_id, user_id=user_id)
        if info is None:
            raise SkillError('搜不到这个小号（可能注销或不可搜索）')
        if info.get('opened') is False:
            raise SkillError('会话没打开：%s' % (info.get('reason') or '未知原因'))

        done = xhs.draft_message(serial, xhs_id, text, already_open=True,
                                 user_id=user_id)
        if not done:
            raise SkillError('草稿未写入')
        got = (done.get('input_value') or '').strip()
        want = text.strip()

        # 审核标准：输入框里必须是我们想输的字 + 必须能读到发送键
        if not done.get('send_present'):
            raise SkillError('读不到发送键')
        if got != want:
            raise SkillError('输入框内容不符：期望 %d 字，实际 %d 字' % (len(want), len(got)))

        lead_store.record_extract(
            PLATFORM, lid, serial, lead_store.FOUND,
            {'xhs_id': xhs_id, 'nickname': info.get('nickname', ''),
             'draft': got[:80], 'input_matched': True, 'chars': len(want)},
            time.time() - t0)
        execution.log(task, '  ✓ 草稿已就绪（%d 字）+ 发送键可读，未发送' % len(want))
        ok = True
    except Exception as exc:                             # noqa: BLE001
        reason = str(exc)[:160]
        lead_store.record_extract(PLATFORM, lid, serial, lead_store.EXTRACT_ERROR,
                                  {'error': reason}, time.time() - t0)
        execution.log(task, '  ✗ 失败：%s' % reason)
    finally:
        _clear_and_exit(serial, len(text))

    return {'id': lid, 'ok': ok, 'reason': reason}


def run(task):
    """技能入口。task.lead 里读 reply_limit / interval_seconds / cooldown_*。"""
    lead = task.get('lead') or {}
    serial = task.get('serial') or lead.get('serial') or ''
    if not serial:
        raise SkillError('缺少 serial')

    limit = max(1, int(lead.get('reply_limit') or lead.get('limit') or 10))
    tpl = (lead.get('dm_template') or '').strip()

    # 两套节流：
    #   interval_seconds  —— 每条之间都停一下
    #   cooldown_every / cooldown_seconds —— 攒够 N 条停一次（防账号异常）
    interval = max(0, int(lead.get('interval_seconds') or 0))
    cd_every = max(1, int(lead.get('cooldown_every') or 10))
    cd_seconds = max(0, int(lead.get('cooldown_seconds') or 0))

    # 一开始就置 executing，结束时落终态；否则跑完了还显示"排队中"（2026-11-03）
    task['status'] = 'executing'
    execution.log(task,
                  '小红书私信：设备 %s，上限 %d 条，每条间隔 %d 秒，每 %d 条冷却 %d 秒，只填草稿不发送'
                  % (serial, limit, interval, cd_every, cd_seconds))
    # 2026-11-06：自动确保目标 App 在前台（主人要求，不能要求人工先切好）。
    # 不在前台就用 adb 拉起（约 0.1 秒判断 + 启动等待），绝不 force-stop。
    try:
        from .app_foreground import ensure_app_front as _ensure_front
        if not _ensure_front(serial, 'com.xingin.xhs',
                             log=lambda m: execution.log(task, m)):
            execution.log(task, '自动启动 com.xingin.xhs 后仍未到前台，继续尝试')
    except Exception as _exc:                      # noqa: BLE001
        execution.log(task, '自动启动检查异常（继续）：%s' % type(_exc).__name__)


    ok = fail = done = 0
    stop = task.get('_stop')
    for _ in range(limit):
        if stop is not None and stop.is_set():
            execution.log(task, '已收到停止指令，收尾退出')
            break

        r = run_one(task, serial, tpl)
        if r is None:
            execution.log(task, '中转站没有更多待处理线索')
            break
        if r['ok']:
            ok += 1
        else:
            fail += 1
        done += 1

        if done >= limit:
            break

        if interval:
            execution.log(task, '  间隔 %d 秒…' % interval)
            time.sleep(interval)

        if cd_seconds and done % cd_every == 0:
            execution.log(task, '  已处理 %d 条，冷却 %d 秒…' % (done, cd_seconds))
            time.sleep(cd_seconds)

    st = lead_store.stats(PLATFORM)['platforms'][PLATFORM]
    execution.log(task, '本轮完成：成功 %d，失败 %d；中转站 drafted=%d pending=%d'
                  % (ok, fail, st['drafted'], st['pending']))
    task['status'] = 'stopped' if (stop is not None and stop.is_set()) else 'completed'
    task['end_reason'] = '已处理 %d 条：成功 %d、失败 %d' % (done, ok, fail)
    return {'ok': ok, 'fail': fail}
