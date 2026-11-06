"""抖音跨机定位执行器：把中转站里的一条线索，在另一台手机上找到。

定位链（实测验证过）：
  搜抖音号 → “用户”标签 → 点第一个用户 → 主页核对抖音号
  → 主页内搜索视频标题 → 点进目标视频 → 开评论区 → 滚动找“昵称+正文”

**本模块只定位，不发送。** 找到就把评论所在位置报回来，发不发由调用方决定。
找不到要如实区分三种失败：作者没找到 / 视频没找到 / 评论没找到。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .douyin_lead import (
    _box, _ref_ok, _text_of, page_kind, parse_comment_container, pick_unique,
    read_comments, SELECTABLE,
)
from .douyin_skill import SkillError, elements, foreground_package
from .mobile_gateway import call

PLATFORM = 'douyin'
PACKAGE = 'com.ss.android.ugc.aweme'
SKILL_ID = 'builtin:douyin-locate'
SKILL = {
    'id': SKILL_ID,
    'name': '抖音跨机定位（只找人不发送）',
    'description': '从中转站取线索，用作者抖音号+视频标题定位到指定评论；只定位、只记录，不发送任何内容。',
}
RESULTS_DIR = Path(__file__).resolve().parents[2] / 'data' / 'locate_results'

MAX_COMMENT_ROUNDS = 12      # 评论区最多滚 12 屏找一条
MAX_PROFILE_TRIES = 5        # 用户列表最多点 5 个


def _norm(value) -> str:
    return re.sub(r'[\s@·，,。.!！?？]+', '', str(value or '')).lower()


def same_person(a, b) -> bool:
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    return x == y or ((x in y or y in x) and abs(len(x) - len(y)) <= 2)


def same_text(a, b, head=24) -> bool:
    """评论正文比对：只比前若干字，规避尾部表情/换行差异。"""
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    head = min(head, len(x), len(y))
    return x[:head] == y[:head]


def locate(task, lead, log=None) -> dict:
    """执行一次定位。返回 {'result':..., 'detail':..., 'comment':...}。"""
    serial = task['serial']
    stop = task.get('_stop')
    say = log or (lambda message: None)

    def check():
        if stop is not None and stop.is_set():
            raise SkillError('任务已停止')

    def pause(seconds):
        check()
        if stop is not None:
            stop.wait(seconds)
        else:
            time.sleep(seconds)

    def tree():
        check()
        for _ in range(2):
            try:
                return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))
            except Exception:
                pause(0.6)
        raise SkillError('读取界面失败，已停止')

    def tap(el):
        if not _ref_ok(el):
            raise SkillError('元素引用无效，已停止')
        call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)
        pause(1.2)

    def press(button):
        call('mobile_press_button', {'button': button}, serial)
        pause(0.9)

    def settle(times=6, gap=0.7):
        last = None
        for _ in range(times):
            pause(gap)
            current = tree()
            if last is not None and len(current) == len(last):
                return current
            last = current
        return last or tree()

    def type_text(text):
        call('mobile_type_keys', {'text': text}, serial)
        pause(0.6)

    # ---- 打开抖音
    try:
        execution_pkg = foreground_package(call('mobile_get_foreground_app', {}, serial))
    except Exception:
        execution_pkg = ''
    if execution_pkg != PACKAGE:
        call('mobile_launch_app', {'packageName': PACKAGE}, serial)
        pause(3.0)
        settle()

    # ---- 进搜索页
    def goto_search():
        for _ in range(5):
            page = tree()
            if page_kind(page) == 'search':
                return page
            cands = [e for e in page
                     if '搜索' in _text_of(e, 'label', 'text') and _ref_ok(e) and _box(e)]
            if cands:
                cands.sort(key=lambda e: _box(e)['y'])
                tap(cands[0])
                pause(1.4)
                continue
            press('BACK')
            pause(0.9)
        raise SkillError('未能进入搜索页')

    def search(keyword):
        goto_search()
        page = tree()
        box = pick_unique(page, 'search', 'input')
        if box is None:
            raise SkillError('搜索框不存在')
        tap(box)
        page = tree()
        clear = pick_unique(page, 'search', 'clear')
        if clear is not None:
            tap(clear)
        type_text(keyword)
        page = tree()
        submit = pick_unique(page, 'search', 'submit')
        if submit is None:
            raise SkillError('搜索按钮不存在')
        tap(submit)
        return settle()

    author_id = (lead.get('author_douyin_id') or '').strip()
    nickname = (lead.get('video_author') or '').strip()
    video_title = (lead.get('video_title') or '').strip()
    comment_author = (lead.get('comment_author') or '').strip()
    comment_text = (lead.get('comment_text') or '').strip()

    # ---- 1. 找到作者主页并核对身份
    query = author_id or nickname
    if not query:
        return {'result': 'author_missing', 'detail': '线索里没有作者抖音号或昵称'}
    say('定位作者：' + query)
    page = search(query)
    tabs = [e for e in page if _text_of(e, 'text') == '用户' and _ref_ok(e)]
    if not tabs:
        call('mobile_swipe_on_screen',
             {'direction': 'left', 'distance': 400, 'x': 800, 'y': 300}, serial)
        pause(0.8)
        page = tree()
        tabs = [e for e in page if _text_of(e, 'text') == '用户' and _ref_ok(e)]
    if len(tabs) != 1:
        return {'result': 'author_missing', 'detail': '“用户”标签找不到'}
    tap(tabs[0])
    page = settle()

    buttons = sorted([e for e in page
                      if _text_of(e, 'label') == '关注按钮' and _box(e)],
                     key=lambda e: _box(e)['y'])
    if not buttons:
        return {'result': 'author_missing', 'detail': '搜不到该用户'}

    profile = None
    for button in buttons[:MAX_PROFILE_TRIES]:
        box = _box(button)
        call('mobile_click_on_screen_at_coordinates',
             {'x': 260, 'y': box['y'] + box['height'] // 2}, serial)
        pause(2.0)
        page = settle()
        id_el = pick_unique(page, 'profile', 'douyin_id')
        if id_el is not None:
            raw = _text_of(id_el, 'text', 'label')
            got_id = re.sub(r'^抖音号\s*[:：]\s*', '', raw).strip()
            nick_el = pick_unique(page, 'profile', 'nickname')
            got_nick = (_text_of(nick_el, 'text', 'label') if nick_el else '').split('，')[0].strip()
            matched = (got_id and author_id and got_id == author_id) or \
                      (same_person(got_nick, nickname) and (not author_id or not got_id or got_id == author_id))
            if matched:
                profile = {'nickname': got_nick, 'douyin_id': got_id}
                say('作者确认：%s（%s）' % (got_nick, got_id))
                break
            say('跳过不匹配：%s（%s）' % (got_nick or '未知', got_id or '无抖音号'))
        press('BACK')
        pause(1.0)
    if profile is None:
        return {'result': 'author_missing', 'detail': '用户列表里没找到抖音号一致的主页'}

    # ---- 2. 主页内搜索视频标题
    page = tree()
    entry = pick_unique(page, 'profile', 'profile_search')
    if entry is None:
        return {'result': 'video_missing', 'detail': '主页没有搜索按钮'}
    tap(entry)
    pause(1.2)
    page = settle()
    box = pick_unique(page, 'profile_search', 'input')
    if box is None:
        return {'result': 'video_missing', 'detail': '主页搜索框没打开'}
    tap(box)
    page = tree()
    clear = pick_unique(page, 'profile_search', 'clear')
    if clear is not None:
        tap(clear)
    # 标题可能过长，取前 20 字更稳（实测摘要匹配足够）
    keyword = video_title[:20].strip() or video_title
    type_text(keyword)
    page = tree()
    submit = pick_unique(page, 'profile_search', 'submit')
    if submit is not None:
        tap(submit)
    page = settle()
    say('主页内搜索：' + keyword)

    # ---- 3. 在结果里挑标题最接近的视频
    cards = []
    for el in page:
        if not _ref_ok(el) or not _box(el):
            continue
        ident = (_text_of(el, 'id') or '').split('/')[-1]
        if ident == 'cover':
            cards.append(el)
    if not cards:
        count = pick_unique(page, 'profile_search', 'count')
        detail = _text_of(count, 'text') if count is not None else '没有视频结果'
        return {'result': 'video_missing', 'detail': '主页搜索无结果：' + detail}

    # 结果卡片的标题元素与封面同屏，取纵向最接近的
    titles = [e for e in page
              if (_text_of(e, 'id') or '').endswith('/desc') and _text_of(e, 'text')]
    target, best = None, 0.0
    for card in cards:
        cy = _box(card)['y']
        near = min(titles, key=lambda t: abs(_box(t)['y'] - cy)) if titles else None
        if near is None:
            continue
        score = 1.0 if same_text(_text_of(near, 'text'), video_title, head=12) else 0.0
        if score > best:
            target, best = card, score
    if target is None:
        target = cards[0]      # 主页搜索本身就是该作者内容，取第一个兜底
        say('标题没完全对上，取主页搜索结果第一个进入')

    tap(target)
    pause(1.8)
    page = settle()
    if page_kind(page) == 'comments':
        pass
    else:
        # 先进播放页，再点评论入口
        entry = pick_unique(page, 'video_player', 'comment_entry')
        if entry is None:
            press('BACK')
            return {'result': 'video_missing', 'detail': '进入后没有评论入口'}
        tap(entry)
        page = settle()

    if page_kind(page) != 'comments':
        press('BACK')
        return {'result': 'video_missing', 'detail': '没能打开评论区'}

    # ---- 4. 评论区滚动找“昵称+正文”
    seen = []
    empty_rounds = 0
    for round_index in range(MAX_COMMENT_ROUNDS):
        check()
        found = None
        for el in page:
            info = parse_comment_container(el)
            if not info:
                continue
            if same_person(info.get('author'), comment_author) and \
                    same_text(info.get('content'), comment_text):
                found = info
                break
        if found:
            press('BACK')
            return {'result': 'found', 'detail': '第 %d 屏找到' % (round_index + 1),
                    'comment': found, 'rounds': round_index + 1}
        batch = read_comments(page)
        key = '|'.join(_norm(c.get('author')) + _norm(c.get('content'))[:12] for c in batch)
        if key and key in seen:
            empty_rounds += 1
        else:
            seen.append(key)
            empty_rounds = 0
        if empty_rounds >= 2:
            break
        call('mobile_swipe_on_screen',
             {'direction': 'up', 'distance': 700, 'x': 540, 'y': 1500}, serial)
        pause(1.0)
        page = settle(times=4, gap=0.6)

    press('BACK')
    return {'result': 'comment_missing',
            'detail': '滚了 %d 屏没找到该评论（可能已删除或被顶到很后）' % (round_index + 1),
            'rounds': round_index + 1}


# ---------------------------------------------------------------- 任务入口
def save_result(task_id, payload):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9_-]', '', task_id) or 'task'
    target = RESULTS_DIR / (safe + '.json')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    return target


def run(task):
    """认领中转站线索并逐条定位；只读，不发送，也不改线索状态。"""
    from . import execution, lead_store

    serial = task['serial']
    cfg = task.get('lead') or {}
    limit = max(1, min(200, int(cfg.get('locate_limit') or 10)))
    platform = str(cfg.get('platform') or PLATFORM)

    try:
        rows = lead_store.listing(platform, status='all', limit=2000)
    except Exception as exc:
        execution.log(task, '读取中转站失败：%s' % exc)
        task['status'] = 'failed'
        return
    usable = [row for row in rows if (row.get('author_douyin_id') or '').strip()]
    skipped = len(rows) - len(usable)
    todo = usable[:limit]
    execution.log(task, '中转站共 %d 条，其中 %d 条带作者账号可定位，本次验证 %d 条'
                  % (len(rows), len(usable), len(todo)))
    if skipped:
        execution.log(task, '有 %d 条缺作者账号，跨机无法定位（采集时要开回查）' % skipped)

    results, counts = [], {}
    for index, row in enumerate(todo, 1):
        if task['_stop'].is_set():
            break
        execution.log(task, '[%d/%d] 作者 %s（%s）'
                      % (index, len(todo), row.get('video_author') or '未知',
                         row.get('author_douyin_id')))
        try:
            result = locate(task, row, log=lambda message: execution.log(task, '    ' + message))
        except SkillError as exc:
            result = {'result': 'error', 'detail': str(exc)}
        except Exception as exc:
            result = {'result': 'error', 'detail': '%s: %s' % (type(exc).__name__, exc)}
        result['lead_id'] = row.get('id')
        result['video_author'] = row.get('video_author')
        result['author_douyin_id'] = row.get('author_douyin_id')
        result['comment_author'] = row.get('comment_author')
        results.append(result)
        key = result.get('result') or 'unknown'
        counts[key] = counts.get(key, 0) + 1
        execution.log(task, '    => %s（%s）' % (key, result.get('detail') or ''))

    stats = dict(counts)
    stats['total'] = len(todo)
    stats['no_author_id'] = skipped
    task['lead_stats'] = stats
    path = save_result(task['id'], {'task_id': task['id'], 'serial': serial,
                                    'counts': counts, 'results': results})
    execution.log(task, '定位结束：' + '、'.join('%s %d' % (k, v) for k, v in counts.items())
                  if counts else '定位结束：没有可验证的线索')
    execution.log(task, '明细已保存到 ' + str(path))
    task['status'] = 'completed'
