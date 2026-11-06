"""临时批量测试：采 20 条 → 立刻领任务回复 → 再采 20 条，直到 100 条。

目的（主人 2026-11-03 定的）：
  快速验证「采→存→领→翻译→回复」能不能准确跑通，而不是追求单次量。

每轮：
  1. 采集 20 条（每 5 条增量入库）
  2. 从待处理里领一批（不超过本轮增量），走私信：翻译 → 填草稿 → 识别发送键
  3. **全程不点发送**

用法：
  python fishfleet/web/facebook_temp_batch.py --total 100 --batch 20
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web import lead_store as L  # noqa: E402
from fishfleet.web.facebook_comments import fetch_profile_id, parse_comment_items  # noqa: E402
from fishfleet.web.facebook_dm_send import (  # noqa: E402
    DRAFT, clear_draft, dismiss_notice, fill_and_detect, is_locked_profile,
    localize, open_dm_thread, open_profile_by_url)
from fishfleet.web.facebook_live import DeviceSession, setup_utf8_stdout  # noqa: E402
from fishfleet.web.facebook_reply import ensure_home  # noqa: E402

PLATFORM = 'facebook'
# 多词轮换：一个词采到尽头就换下一个，避免单词语料被采空
KEYWORDS = ['iPhone', '汽车', '美食', '手机', 'iPhone 15', 'samsung']
_kw = {'i': 0, 'cur': KEYWORDS[0]}


def next_keyword() -> str:
    kw = KEYWORDS[_kw['i'] % len(KEYWORDS)]
    _kw['i'] += 1
    _kw['cur'] = kw
    return kw



def _text(e: dict) -> str:
    return (e.get('label') or e.get('text') or '').strip()


def collect_batch(s: DeviceSession, want: int) -> int:
    """采一批，返回实际入库条数。复用 collect_keyword 的思路，但只在内存里攒 5 条就写。"""
    import re
    REEL = re.compile(r'.+的 Reel，.*點按兩下可觀看')
    CNT = re.compile(r'^(\d+\s*(則留言|条留言|comments)|留言|評論|评论)$')

    sb = T.find('home', 'search', s.dump())
    if sb is None:
        return 0
    s.tap_element(sb)
    time.sleep(2.5)
    kw = _kw["cur"]
    print(f"  搜索关键词: {kw}")
    if not s.type_anywhere(kw, submit=True):
        return 0
    time.sleep(5.0)

    got = 0
    seen = set()
    buf = []
    empty = 0
    while got < want and empty < 15:
        els = s.dump()
        cards = [e for e in els if REEL.match(_text(e))]
        fresh = [c for c in cards if _text(c) not in seen]
        if not fresh:
            empty += 1
            s.swipe('up', distance=1200, x=540, y=1400)
            time.sleep(2.0)
            continue
        empty = 0
        card = fresh[0]
        seen.add(_text(card))
        s.tap_element(card)
        time.sleep(5.0)
        e2 = s.dump()
        cnt = [e for e in e2 if e['type'] == 'Button' and CNT.match(_text(e))]
        if not cnt:
            for _ in range(3):
                s.swipe('up', distance=800, x=540, y=1300)
                time.sleep(1.5)
                e2 = s.dump()
                cnt = [e for e in e2 if e['type'] == 'Button' and CNT.match(_text(e))]
                if cnt:
                    break
        if cnt:
            s.tap_element(cnt[0])
            time.sleep(3.5)
            snap = s.dump()
            items = parse_comment_items(snap)
            for it in items:
                if got >= want:
                    break
                author = it['comment_author']
                if not _open_profile(s, author, snap):
                    continue
                info = fetch_profile_id(s, author)
                if info['profile_id']:
                    rec = dict(it)
                    rec.update({
                        'commenter_profile_id': info['profile_id'],
                        'commenter_profile_url': info['profile_url'],
                        'keyword': _kw['cur'],
                        'collected_page': 'temp_batch',
                        'commenter_identity_source': 'profile_url',
                    })
                    buf.append(rec)
                    got += 1
                    print(f'    采 {got}: {author} | {info["profile_id"]}')
                    if len(buf) >= 5:
                        r = L.add_leads(PLATFORM, buf, serial='SERIAL0001')
                        print(f'    [入库] +{r.get("added", 0)}')
                        buf = []
                s.back()
                time.sleep(1.2)
        for _ in range(3):
            s.back()
            time.sleep(1.8)
            if any(REEL.match(_text(x)) for x in s.dump()):
                break
    if buf:
        r = L.add_leads(PLATFORM, buf, serial='SERIAL0001')
        print(f'    [入库·尾] +{r.get("added", 0)}')
    return got


def _open_profile(s: DeviceSession, author: str, snap: list) -> bool:
    for attempt in range(3):
        els = snap if (attempt == 0 and snap) else s.dump()
        same = [e for e in els if e['type'] == 'Button' and _text(e) == author]
        av = [e for e in els if e['type'] == 'ImageView' and _text(e).startswith(author)]
        for e in same:
            if any(abs(x['y'] - e['y']) <= 200 for x in av):
                s.tap_element(e)
                for _ in range(6):
                    time.sleep(2.0)
                    fg = s.foreground()
                    act = (fg.activity if fg else '') or ''
                    e2 = s.dump()
                    if ('ImmersivePortraitActivity' in act
                            or '更多個人檔案設定' in {_text(x) for x in e2}):
                        return True
                return False
        if attempt < 2:
            time.sleep(1.5)
    return False


def reply_batch(s: DeviceSession, limit: int) -> dict:
    """从待处理里领 limit 条，逐个走私信（翻译+填草稿+识别发送键），不发送。"""
    stats = {'tried': 0, 'ok': 0, 'fail': 0, 'locked': 0, 'langs': {}}
    for _ in range(limit):
        row = L.claim(PLATFORM, 'SERIAL0001')
        if not row:
            break
        name = row['comment_author']
        url = row.get('commenter_profile_url') or ''
        stats['tried'] += 1
        print(f'\n  → {name}: {(row.get("comment_text") or "")[:30]}')
        if not url or not open_profile_by_url(s, url):
            print('     ✗ 打不开主页')
            L.finish(PLATFORM, row['id'], 'SERIAL0001', L.FAILED)
            stats['fail'] += 1
            continue
        # 对方锁了档案（只有好友能发消息）→ 标 skipped，不算失败、不重试
        if is_locked_profile(s.dump()):
            print('     ⊘ 对方锁了个人档案，不可联系 → skipped')
            stats['locked'] = stats.get('locked', 0) + 1
            L.finish(PLATFORM, row['id'], 'SERIAL0001', L.SKIPPED,
                     error='profile_locked')
            s.back(); time.sleep(1.0)
            continue
        if not open_dm_thread(s):
            # 2026-11-03 16:00：区分两种失败——
            #   · 主页根本没有「發送訊息」按钮 → 对方不接收陌生人私信，标 skipped（不重试）
            #   · 有按钮但点不进去 → 真失败，标 failed（可重试）
            has_btn = any(_text(e) in ('發送訊息', 'Message') for e in s.dump())
            if not has_btn:
                print('     ⊘ 主页无可达私信入口 → skipped')
                stats['locked'] = stats.get('locked', 0) + 1
                L.finish(PLATFORM, row['id'], 'SERIAL0001', L.SKIPPED,
                         error='no_message_entry')
            else:
                print('     ✗ 进不了私信（有按钮但打不开）')
                stats['fail'] += 1
                L.finish(PLATFORM, row['id'], 'SERIAL0001', L.FAILED)
            s.back(); time.sleep(1.0)
            continue
        loc = localize(row.get('comment_text') or '', DRAFT)
        draft = loc.get('text') or DRAFT
        lang = loc.get('target_lang') or '-'
        stats['langs'][lang] = stats['langs'].get(lang, 0) + 1
        print(f'     译 lang={lang} fallback={loc.get("fallback")} 草稿={draft[:44]!r}')
        res = fill_and_detect(s, draft)
        good = res['input_found'] and res['filled'] and res['send_found']
        print(f'     填={res["filled"]} 发送键={res["send_found"]} '
              f'{res["send_label"]!r}（未点击）')
        if good:
            stats['ok'] += 1
            L.finish(PLATFORM, row['id'], 'SERIAL0001', L.DRAFTED, reply_text=draft)
        else:
            stats['fail'] += 1
            L.finish(PLATFORM, row['id'], 'SERIAL0001', L.FAILED)
        clear_draft(s)
        time.sleep(0.8)
        s.back(); time.sleep(1.0)
        s.back(); time.sleep(1.2)
    return stats


def main() -> int:
    setup_utf8_stdout()
    total = 100
    batch = 20
    args = sys.argv[1:]
    if '--total' in args:
        total = int(args[args.index('--total') + 1])
    if '--batch' in args:
        batch = int(args[args.index('--batch') + 1])

    agg = {'collected': 0, 'tried': 0, 'ok': 0, 'fail': 0, 'langs': {}}
    rounds = 0
    with DeviceSession('SERIAL0001') as s:
        if not ensure_home(s):
            print('回不到首页')
            return 1
        while agg['collected'] < total and rounds < 8:
            rounds += 1
            print(f'\n{"="*56}\n第 {rounds} 轮：采集 {batch} 条\n{"="*56}')
            next_keyword()
            n = collect_batch(s, batch)
            agg['collected'] += n
            print(f'  本轮采到 {n}，累计 {agg["collected"]}/{total}')
            if n == 0:
                print('  本轮没采到，结束')
                break
            print(f'\n第 {rounds} 轮：回复 {n} 条')
            st = reply_batch(s, n)
            agg['tried'] += st['tried']
            agg['ok'] += st['ok']
            agg['fail'] += st['fail']
            for k, v in st['langs'].items():
                agg['langs'][k] = agg['langs'].get(k, 0) + v

    print()
    print('=' * 56)
    print(f'采集总数     : {agg["collected"]}')
    print(f'回复尝试     : {agg["tried"]}')
    print(f'回复成功     : {agg["ok"]}')
    print(f'回复失败     : {agg["fail"]}')
    if agg['tried']:
        print(f'回复成功率   : {agg["ok"] / agg["tried"] * 100:.1f}%')
    print(f'翻译语种分布 : {agg["langs"]}')
    print('**全程未点击发送**')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
