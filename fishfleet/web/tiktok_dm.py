"""TikTok 私信链路（收尾到「差最后一步」）：定位 → 主页「消息」→ 会话 → 输入 → 不发送。

安全边界（写死在代码里，不靠调用方自觉）：
  · 本模块**没有任何**点击发送的代码路径；
  · `prepare_dm()` 最多把文本填进输入框，然后返回「发送按钮位置」，
    由人类决定是否真的发出去；
  · 发送按钮在 TikTok 上没有二次确认，点了就外发且不可撤回。

实测依据（手机7 / v35.8.1 / 2026-11-01）：
  · 他人主页：`cth`（text=消息）进入会话
  · 会话页：`eew` 昵称、`g8f` 输入框容器（内部 EditText 无 id）、`g8h` 右侧按钮组
  · 输入有内容后右侧才出现纸飞机
"""
from __future__ import annotations

import time

from .douyin_skill import SkillError, elements
from .mobile_gateway import call
from .tiktok_lead import (
    _box, _ident, _ref_ok, _text_of, by_id, page_kind, profile_handle,
)
from .tiktok_locate import (
    locate_by_handle, normalize_handle, go_home, quick_locate,
)


def _tree(serial):
    return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))


def _tap(el, serial):
    if not _ref_ok(el):
        raise SkillError('控件引用不可用，已停止')
    call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)


def is_dm_page(page) -> bool:
    """会话页判定。

    主判据是输入容器 `g8f`；但 TikTok 的会话页布局会随版本/会话类型变化，
    所以补一条退路：有会话标题 `eew` + 页面上有 EditText + 不是评论区。
    （实测 2026-11-02：有一条 `@mayoniqqer` 点了「消息」后 page_kind=unknown，
     当时没有留下足够信息判断，这里先把判定放宽，避免把真会话判成未知。）
    """
    labels = {_text_of(e, 'label') for e in page}
    if '发布评论' in labels or '在评论中提及某人' in labels:
        return False
    ids = {_ident(e) for e in page}
    if 'g8f' in ids:
        return True
    if 'eew' in ids and any('EditText' in str(e.get('type') or '') for e in page):
        return True
    return False


def find_message_button(page):
    """主页上的「消息」按钮。TikTok 里它是 cth（TextView），关注是另一个 cth。"""
    cands = [e for e in page
             if _ident(e) == 'cth' and _text_of(e, 'text').strip() == '消息' and _ref_ok(e)]
    if len(cands) != 1:
        return None
    return cands[0]


def find_input(page):
    """会话输入框：g8f 容器内的 EditText。"""
    boxes = [e for e in page if _ident(e) == 'g8f']
    if len(boxes) != 1:
        return None
    container = boxes[0]
    edits = [e for e in page if 'EditText' in str(e.get('type') or '')
             and _box(e) and _box(container)
             and _box(e)['y'] >= _box(container)['y'] - 20
             and _box(e)['y'] <= _box(container)['y'] + _box(container)['height'] + 20]
    if len(edits) != 1:
        return None
    return edits[0]


def find_send_button(page):
    """发送按钮：g8h 容器内最右侧的 ImageView（无 id、无 label）。

    返回元素或 None；None 表示当前还没有发送按钮（输入框为空时 TikTok 不显示纸飞机）。
    """
    groups = [e for e in page if _ident(e) == 'g8h']
    if len(groups) != 1:
        return None
    container = groups[0]
    icons = [e for e in page
             if str(e.get('type') or '').endswith('ImageView')
             and _box(e) and _box(container)
             and _inside_box(e, container)]
    if not icons:
        return None
    icons.sort(key=lambda e: _box(e)['x'])
    return icons[-1]


def _inside_box(el, container) -> bool:
    a, b = _box(el), _box(container)
    if not a or not b:
        return False
    return (b['x'] <= a['x'] and b['y'] <= a['y']
            and a['x'] + a['width'] <= b['x'] + b['width'] + 2
            and a['y'] + a['height'] <= b['y'] + b['height'] + 2)


def _direct_locate(serial, handle):
    """用 `https://www.tiktok.com/@handle` 直达主页（2026-11-05 起第一选择）。

    页面上必须能认出 `@handle` 才认账，否则返回 None 让调用方走搜号兜底。
    TikTok 所有页面都挂在同一个 TikTokHostActivity 上，所以只能靠页面内容判定。
    """
    h = normalize_handle(handle)
    if not h:
        return None
    try:
        from . import deeplink as DL
        r = DL.open_profile(serial, 'tiktok', h, wait=9.0)
    except Exception:   # noqa: BLE001 直达异常一律回落
        return None
    if not r.get('ok'):
        return None
    page = _tree(serial)
    low = h.lower()
    texts = []
    for e in page:
        for k in ('text', 'label'):
            v = str(_text_of(e, k) or '').lower()
            if v:
                texts.append(v)
    hit = any(('@' + low) in t or low in t for t in texts)
    try:
        from .deeplink import note as _note
        _note('tk direct %s handle=%s act=%s sample=%s'
              % ('HIT' if hit else 'MISS', low,
                 (r.get('activity') or '').split('/')[-1],
                 '|'.join(texts[:6])[:120]))
    except Exception:
        pass
    if hit:
        return {'matched': True, 'nickname': '', 'stage': 'direct_profile',
                'evidence': {'entry': 'deeplink', 'activity': r.get('activity')}}
    return None


def open_dm(serial, handle, timeout=30.0, quick=False):
    """定位用户 → 进主页 → 点「消息」→ 等到会话页。

    2026-11-05 起先试**直达**（https://www.tiktok.com/@handle），
    认不出 @handle 再走原来的搜号路径（兜底）。
    quick=True 时走批量模式（复用当前搜索页），失败自动回退完整流程。
    """
    located = None
    try:
        located = _direct_locate(serial, handle)
    except Exception:   # noqa: BLE001
        located = None
    if not located and quick:
        try:
            located = quick_locate(serial, handle)
        except SkillError:
            located = None
    if not located:
        located = locate_by_handle(serial, handle)
    if not located.get('matched'):
        return {'handle': normalize_handle(handle), 'nickname': '',
                'matched': False, 'stage': 'locate',
                'evidence': located.get('evidence') or {}}

    # ★优化：主页已就绪时消息按钮就在第一屏，补读 2 次足够（原来轮询 10 秒）
    page = _tree(serial)
    button = find_message_button(page)
    for _ in range(2):
        if button is not None:
            break
        time.sleep(0.8)
        page = _tree(serial)
        button = find_message_button(page)
    if button is None:
        return {'handle': normalize_handle(handle), 'nickname': located.get('nickname'),
                'matched': False, 'stage': 'message_button_missing',
                'evidence': {'page_kind': page_kind(page)}}

    _tap(button, serial)
    time.sleep(2.2)
    # ★优化：会话页通常 2-3 秒内打开，最多读 4 次（原来 30 秒轮询）
    last_page = []
    for _ in range(4):
        page = _tree(serial)
        last_page = page
        if is_dm_page(page):
            return {'handle': normalize_handle(handle),
                    'nickname': located.get('nickname') or '',
                    'matched': True, 'stage': 'dm_opened',
                    'evidence': {'page_kind': page_kind(page)}}
        # 少数用户点「消息」后会先弹「消息请求」之类的确认层，
        # 这里把当前页面的文字带出去，便于判断到底是哪种拦截。
        hint = [_text_of(e, 'text').strip() for e in page
                if _text_of(e, 'text').strip()][:12]
        if hint and any('请求' in h or 'request' in h.lower() for h in hint):
            return {'handle': normalize_handle(handle),
                    'nickname': located.get('nickname') or '',
                    'matched': False, 'stage': 'message_request_prompt',
                    'evidence': {'page_kind': page_kind(page), 'texts': hint}}
        time.sleep(1.0)
    return {'handle': normalize_handle(handle), 'nickname': located.get('nickname'),
            'matched': False, 'stage': 'dm_not_opened',
            'evidence': {'page_kind': page_kind(last_page),
                         'texts': [_text_of(e, 'text').strip() for e in last_page
                                   if _text_of(e, 'text').strip()][:12]}}


SKILL_ID = 'builtin:tiktok-dm'
SKILL = {
    'id': SKILL_ID,
    'name': 'TikTok 截流回复（填草稿，不发送）',
    'description': '从中转站领线索，定位到人、打开私信、填草稿并验证「输入框内容 + 发送键」；从不点击发送。',
}


def run_reply(task):
    """回复技能主流程：领单 → 填草稿 → 验证 → 记证据。

    设计对齐 B站 面板的硬规则：
      · 状态机到 drafted 为止，本函数**没有**任何点击发送的路径；
      · 每条必须验证「输入框内容 == 期望」且「发送键可读」，否则不算成功；
      · 验证证据写入 extract_evidence，事后可查；
      · 节流两层（每条间隔 + 定期冷却）。
    """
    from . import lead_store

    task['status'] = 'executing'   # 否则界面一直显示"排队中"，状态与事实不符
    cfg = task.get('lead') or {}
    serial = task['serial']
    stop = task['_stop']
    limit = int(cfg.get('reply_limit') or cfg.get('locate_limit') or 50)
    interval = max(0.0, float(cfg.get('interval_seconds') or 2.0))
    cool_every = max(0, int(cfg.get('cooldown_every') or 20))
    cool_seconds = max(0.0, float(cfg.get('cooldown_seconds') or 45.0))
    template = str(cfg.get('dm_template') or '').strip()
    fixed_text = str(cfg.get('reply_text') or '').strip()

    stats = {'tried': 0, 'ok': 0, 'skipped': 0, 'released': 0, 'errors': 0}
    task['lead_stats'] = stats
    # 2026-11-06：自动确保目标 App 在前台（主人要求，不能要求人工先切好）。
    # 不在前台就用 adb 拉起（约 0.1 秒判断 + 启动等待），绝不 force-stop。
    try:
        from .app_foreground import ensure_app_front as _ensure_front
        if not _ensure_front(serial, 'com.zhiliaoapp.musically',
                             log=lambda m: execution.log(task, m)):
            execution.log(task, '自动启动 com.zhiliaoapp.musically 后仍未到前台，继续尝试')
    except Exception as _exc:                      # noqa: BLE001
        execution.log(task, '自动启动检查异常（继续）：%s' % type(_exc).__name__)


    def note(msg):
        try:
            from . import execution
            execution.log(task, msg)
        except Exception:                                  # noqa: BLE001
            task.setdefault('events', []).append({'at': time.time(), 'message': msg})
            print(' *', msg, flush=True)

    def check():
        if stop.is_set():
            raise SkillError('任务已停止')

    while stats['tried'] < limit:
        check()
        # 2026-11-06：支持按批次（日期+关键词）只回某一批采集到的线索
        _batch = (task.get('lead') or {}).get('batch') or None
        if _batch:
            note('限定批次：%s' % _batch)
        row = lead_store.claim('tiktok', serial, lease=600,
                               require_nonempty=('commenter_handle',),
                               batch=_batch)
        if row is None:
            note('池里没有可领的线索了')
            break
        stats['tried'] += 1
        lead_id = row['id']
        handle = (row.get('commenter_handle') or '').strip()

        # 待填文本：模板优先（带 {昵称}/{评论}），否则固定文本，最后兜底 dm_text
        text = fixed_text
        if not text and template:
            text = (template.replace('{昵称}', str(row.get('comment_author') or ''))
                            .replace('{评论}', str(row.get('comment_text') or ''))
                            .replace('{keyword}', str(row.get('keyword') or '')))
        if not text:
            text = (row.get('dm_text') or '').strip() or 'hi'

        # ★自动翻译：按评论语种本地化草稿（对齐抖音侧 draft_i18n 的做法）
        #   中文评论 → 不翻译；判定不了 → 回填中文；外文 → 翻成该语种。
        #   翻译失败一律回退中文母版，绝不影响发不发送这件事（本来就不发）。
        i18n_ev = {}
        try:
            from . import draft_i18n
            comment_text = str(row.get('comment_text') or '')
            localized = draft_i18n.localize_draft(comment_text, text)
            i18n_ev = {
                'source_hint': comment_text[:80],
                'target_lang': localized.get('target_lang') or '',
                'draft_zh': text,
                'draft_localized': localized.get('text') or text,
                'translator': 'auto',
                'fallback': bool(localized.get('fallback')),
                'reason': localized.get('reason') or '',
            }
            text = localized.get('text') or text
        except Exception as exc:                            # noqa: BLE001
            i18n_ev = {'target_lang': '', 'translator': 'auto', 'fallback': True,
                       'reason': 'i18n_error:%s' % type(exc).__name__}

        t0 = time.time()
        try:
            res = prepare_dm(serial, handle, text, quick=stats['tried'] > 1)
        except Exception as exc:                            # noqa: BLE001
            res = {'matched': False, 'reason': 'exception',
                   'detail': '%s: %s' % (type(exc).__name__, exc)}
            note('TK异常详情：%s: %s' % (type(exc).__name__, exc))
        seconds = round(time.time() - t0, 1)

        filled = (res.get('text_filled') or '').strip()
        want = text.strip()
        text_ok = bool(filled) and (filled == want
                                    or (filled.startswith(want) and len(filled) - len(want) <= 1))
        send_ok = bool(res.get('send_button'))
        passed = bool(res.get('matched')) and text_ok and send_ok

        evidence = {
            'input_matched': text_ok,
            'input_text': filled,
            'expected_text': want,
            'send_button': res.get('send_button'),
            'session_handle': handle,
            'fail_reason': '' if passed else (res.get('reason') or 'unknown'),
            'verified_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'sent': False,
            'i18n': i18n_ev,
        }

        if passed:
            stats['ok'] += 1
            lead_store.record_extract('tiktok', lead_id, serial, 'found',
                                      evidence=evidence, seconds=seconds, status='drafted')
            note('草稿已就绪：%s（%r，%ss，未发送）' % (handle, filled[:16], seconds))
        else:
            blocked = (res.get('reason') or '') in ('message_button_missing',
                                                    'message_request_prompt',
                                                    'dm_not_opened')
            if blocked:
                stats['skipped'] += 1
                lead_store.record_extract('tiktok', lead_id, serial, 'skipped',
                                          evidence=evidence, seconds=seconds, status='skipped')
                note('跳过（平台侧限制）：%s' % handle)
            else:
                stats['released'] += 1
                lead_store.release('tiktok', lead_id, serial,
                                   note='模拟回复失败：%s' % (res.get('reason') or 'unknown'))
                note('失败已放回待办：%s（%s）' % (handle, res.get('reason')))

        # 节流两层；最后一条不再等待
        if stats['tried'] < limit:
            if interval:
                time.sleep(interval)
            if cool_every and stats['tried'] % cool_every == 0:
                note('已处理 %d 条，冷却 %.0f 秒' % (stats['tried'], cool_seconds))
                time.sleep(cool_seconds)

    note('回复流程结束：尝试 %d、草稿就绪 %d、跳过 %d、放回 %d'
         % (stats['tried'], stats['ok'], stats['skipped'], stats['released']))
    # 2026-11-05 修：这里原来只有 return，**从没把状态置成完成**——
    # 入口把 status 设成 executing 之后就再没改过，任务列表永远显示"执行中"，
    # 界面上看就是"卡在回复页一动不动"，其实流程早跑完了（草稿也确实填好了）。
    task['status'] = 'completed'
    return stats


def prepare_dm(serial, handle, text, timeout=25.0, quick=False):
    """把文本填进私信输入框并确认发送按钮已就绪，**不发送**。

    返回 {matched, text_filled, send_button, reason}。
    send_button 只给位置信息，供人工/上层确认后才点。
    """
    opened = open_dm(serial, handle, quick=quick)
    if not opened.get('matched'):
        return {'matched': False, 'text_filled': '', 'send_button': None,
                'reason': opened.get('stage'), 'detail': opened.get('evidence')}

    page = _tree(serial)
    box = find_input(page)
    if box is None:
        return {'matched': False, 'text_filled': '', 'send_button': None,
                'reason': 'input_missing'}
    _tap(box, serial)
    time.sleep(1.2)                       # 等键盘就绪，避免吞掉首字符

    # ★输入前先清空既有草稿：TikTok 会保留上一次对这个人的草稿，
    #   不清空就会变成「hihi」这种叠加内容（实测 2026-11-02 真的发生过，
    #   而且旧判定用了「包含」比较，把 hihi 误判成了通过）。
    page = _tree(serial)
    existing = ''
    for el in page:
        if 'EditText' in str(el.get('type') or ''):
            existing = _text_of(el, 'text').strip()
            break
    if existing:
        for _ in range(min(len(existing) + 3, 60)):
            call('mobile_press_button', {'button': 'BACKSPACE'}, serial)
        time.sleep(0.4)

    if text:
        call('mobile_type_keys', {'text': text}, serial)
        time.sleep(0.7)

    page = _tree(serial)
    send = find_send_button(page)
    filled = ''
    for el in page:
        if 'EditText' in str(el.get('type') or ''):
            filled = _text_of(el, 'text').strip()
            break
    return {
        'matched': True,
        'text_filled': filled,
        'send_button': (_box(send) if send else None),
        'send_button_ref': (_text_of(send, 'ref') if send else ''),
        'reason': 'ready' if send else 'send_button_missing',
        'note': '未发送：本函数不含点击发送的任何代码路径',
    }
