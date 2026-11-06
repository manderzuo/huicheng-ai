"""按**关键词**采集：搜索 → 滚到下方 Reels → 逐个打开 → 采评论 → 取唯一 ID。

2026-11-02 18:25 发现：搜索结果「全部」分类里，**社团卡片下面就是 Reels**，
格式是 `<作者>的 Reel，<时间>前，<N個讚>，點按兩下可觀看`。
点开 Reel 就有留言入口，可以正常采。

用法：
  python fishfleet/web/facebook_collect_keyword.py <关键词> <目标条数> [每帖上限]
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web import lead_store as L  # noqa: E402
from fishfleet.web.facebook_comments import (  # noqa: E402
    collect_from_current_post, fetch_profile_id, parse_comment_items)
from fishfleet.web.facebook_live import DeviceSession, setup_utf8_stdout  # noqa: E402
from fishfleet.web.facebook_reply import ensure_home  # noqa: E402

PLATFORM = 'facebook'
# 搜索结果里的 Reels 卡片
_REEL_CARD = re.compile(r'.+的 Reel，.*點按兩下可觀看')
_COMMENT_CNT = re.compile(r'^(\d+\s*(則留言|条留言|comments)|留言|評論|评论)$')
_REPLY_BTN = re.compile(r'^對(.+?)留言的「?回[覆复]」?按鈕')


def _text(e: dict) -> str:
    return (e.get('label') or e.get('text') or '').strip()


def _flush(records: list) -> dict:
    """把攒够的一批写进中转站，并清空传入列表。"""
    if not records:
        return {'added': 0, 'duplicated': 0, 'skipped': 0}
    res = L.add_leads(PLATFORM, records, serial='SERIAL0001', reply_text='')
    print(f'  [入库] +{res.get("added", 0)}（重复 {res.get("duplicated", 0)}）')
    return res


def search_keyword(s: DeviceSession, keyword: str) -> bool:
    """搜索关键词，停在结果页。"""
    sb = T.find('home', 'search', s.dump())
    if sb is None:
        print('找不到搜索入口')
        return False
    s.tap_element(sb)
    time.sleep(2.5)
    if not s.type_anywhere(keyword, submit=True):
        print('输入失败')
        return False
    time.sleep(5.0)
    return True


def goto_reel_tab(s):
    for _ in range(3):
        els = s.dump()
        tabs = [e for e in els if _text(e) == 'Reel' and e.get('y', 9999) < 400]
        if not tabs:
            tabs = [e for e in els if 'Reel' in _text(e) and e.get('y', 9999) < 400]
        if not tabs:
            time.sleep(1.5)
            continue
        s.tap_element(tabs[0])
        time.sleep(3.0)
        return True
    print('  no Reel tab found')
    return False


def open_author_profile(s: DeviceSession, author: str, snap: list | None = None) -> bool:
    # snap：解析评论时那一刻的元素快照。用它找名字，避免评论列表刷新后对不上。
    for attempt in range(3):
        els = snap if (attempt == 0 and snap) else s.dump()
        same_btn = [e for e in els if e['type'] == 'Button' and _text(e) == author]
        same_av = [e for e in els if e['type'] == 'ImageView' and _text(e).startswith(author)]
        for e in same_btn:
            if any(abs(x['y'] - e['y']) <= 200 for x in same_av):
                s.tap_element(e)
                for _ in range(6):
                    time.sleep(2.0)
                    fg = s.foreground()
                    act = (fg.activity if fg else '') or ''
                    e2 = s.dump()
                    labels = {_text(x) for x in e2}
                    if 'ImmersivePortraitActivity' in act:
                        return True
                    if '更多個人檔案設定' in labels:
                        return True
                    # 2026-11-05：原来只认上面两个特征，实测点开后进的是
                    # 普通个人主页（activity 里带 profile），判定不到就被判成
                    # "进不了主页"，整条采集白跑。这里补上主页特征。
                    if 'profile' in act.lower():
                        return True
                    if any(l in labels for l in ('追蹤', '加朋友', '發送訊息', '關於', '更多')):
                        if any(l in labels for l in ('追蹤', '加朋友', '發送訊息')):
                            return True
                    last_act = act
                print(f'      [诊断] 点了 {author} 但停在 {last_act}')
                return False
        if attempt < 2:
            time.sleep(1.5)
    print(f'      [诊断] 找不到 {author!r} 的名字按钮（同名Button={len(same_btn)} 同名头像={len(same_av)}）')
    return False




class _TeeOut:
    """把采集过程中的 print 实时转进任务日志，同时留一份完整文本用于解析。

    之前用 redirect_stdout(StringIO()) 憋到结束才写日志，任务卡住时
    界面上一片空白，根本看不出卡在哪一步（实测 FB 卡在回首页，等了 10 分钟）。
    """

    KEYS = ('✓', '✗', '入库', '搜索', 'Reel', '评论', '异常', '回不到',
            '目标/实际', '回首页', '[诊断]')

    def __init__(self, say=None):
        self.say = say
        self.parts = []

    def write(self, s):
        if not s:
            return 0
        self.parts.append(s)
        if self.say is not None:
            for line in str(s).splitlines():
                text = line.strip()
                if text and any(k in text for k in self.KEYS):
                    try:
                        self.say('    ' + text[:120])
                    except Exception:
                        pass
        return len(s)

    def flush(self):
        pass

    def value(self):
        return ''.join(self.parts)


def collect_for_skill(serial, keyword, target, per=8, task=None):
    """给 skill 层用的入口：采一批并入库，返回实际入库条数。

    2026-11-05 改：原来是 `subprocess.run([sys.executable, __file__, ...])`
    **另起一个 Python 进程**跑采集，后果三个：
      · 壳进程的 UI 录制器抓不到子进程的 MCP 调用 → FB 一条证据都没进库；
      · 子进程卡在「回首页」时，壳进程只能干等（实测 10 分钟无输出）；
      · 子进程的 print 被吞掉，排错全靠猜。
    现在改成**进程内调用 main()**，并把输出实时转进任务日志。
    """
    import contextlib
    import re as _re
    say = None
    if task is not None:
        try:
            from . import execution
            say = lambda m: execution.log(task, m)      # noqa: E731
        except Exception:
            say = None
    sink = _TeeOut(say)
    error = ''
    try:
        with contextlib.redirect_stdout(sink):
            main([keyword, str(target), str(per)], serial=serial)
    except Exception as exc:                            # noqa: BLE001
        error = '%s: %s' % (type(exc).__name__, exc)
        print('采集异常：' + error, file=sink)
    out = sink.value()
    if say is not None and error:
        say('    采集异常：' + error[:160])
    m = _re.search(r'目标/实际\s*:\s*\d+\s*/\s*(\d+)', out)
    return int(m.group(1)) if m else 0


def main(argv=None, serial: str = 'SERIAL0001') -> int:
    setup_utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print('用法: python facebook_collect_keyword.py <关键词> <目标条数> [每帖上限]')
        return 2
    keyword = argv[0]
    target = int(argv[1])
    per = int(argv[2]) if len(argv) > 2 else 8

    stats = {'reels': 0, 'tried': 0, 'got_id': 0, 'fail': 0}
    records = []
    seen_cards: set[str] = set()   # 已经打开过的 Reel 卡（按 label 去重）
    empty_scrolls = 0              # 连续多少次没出现**新**卡片

    with DeviceSession(serial) as s:
        if not ensure_home(s):
            print('回不到首页')
            return 1
        if not search_keyword(s, keyword):
            return 1
        print(f'已搜索「{keyword}」')
        goto_reel_tab(s)

        rounds = 0
        # 2026-11-06 主人定：**不设轮数上限**，停止只有两个：
        #   ① 采集数量够了（stats['got_id'] >= target）；② 连续多次滚动没有新卡片（翻到底）。
        # 原来这里是 `while stats['got_id'] < target and rounds < 40:`，
        # 40 轮是人为封顶，跟"一直往下翻"的规则冲突。
        # rounds 保留但只用于日志/统计，不再参与循环条件。
        while stats['got_id'] < target:
            rounds += 1
            els = s.dump()
            reels = [e for e in els if _REEL_CARD.match(_text(e))]
            # 2026-11-02 19:25 主人指出：原逻辑每轮都取 reels[0]，
            # 关掉 Reel 回来又取同一个，**根本没往下翻页**（日志里 3 轮全是同一张卡）。
            # 改成：只挑"还没打开过"的卡；当前屏没有新卡就往下翻一屏。
            fresh = [e for e in reels if _text(e) not in seen_cards]
            if not fresh:
                empty_scrolls += 1
                if empty_scrolls > 15:
                    print(f'连续 {empty_scrolls} 屏没有新的 Reel，到底了，停止')
                    break
                s.swipe('up', distance=1200, x=540, y=1400)
                time.sleep(2.2)
                continue
            empty_scrolls = 0
            card = fresh[0]
            seen_cards.add(_text(card))
            stats['reels'] += 1
            print(f'\n[Reel {stats["reels"]}] {_text(card)[:52]}')
            s.tap_element(card)
            time.sleep(5.0)

            e2 = s.dump()
            cnt = [e for e in e2 if e['type'] == 'Button' and _COMMENT_CNT.match(_text(e))]
            if not cnt:
                for _ in range(3):
                    s.swipe('up', distance=800, x=540, y=1300)
                    time.sleep(1.5)
                    e2 = s.dump()
                    cnt = [e for e in e2 if e['type'] == 'Button' and _COMMENT_CNT.match(_text(e))]
                    if cnt:
                        break
            if not cnt:
                print('  没找到留言入口，跳过')
                s.back(); time.sleep(1.5)
                s.swipe('up', distance=1200, x=540, y=1400); time.sleep(2.0)
                continue

            s.tap_element(cnt[0])
            time.sleep(3.5)
            # **同一份 dump 里解析并存下当时的元素**：评论列表会在两次 dump 之间刷新，
            # 第二次再去按名字找就找不到了（19:40 实测同名 Button=0）。
            snap = s.dump()
            items = parse_comment_items(snap)
            print(f'  评论 {len(items)} 条')
            for it in items:
                if stats['got_id'] >= target:
                    break
                author = it['comment_author']
                stats['tried'] += 1
                print(f'    {author}: {it["comment_text"][:26]}')
                if not open_author_profile(s, author, snap):
                    print('      ✗ 进不了主页')
                    stats['fail'] += 1
                    continue
                info = fetch_profile_id(s, author)
                if info['profile_id']:
                    stats['got_id'] += 1
                    print(f'      ✓ ID={info["profile_id"]}')
                    rec = dict(it)
                    rec.update({
                        'commenter_profile_id': info['profile_id'],
                        'commenter_profile_url': info['profile_url'],
                        'keyword': keyword,
                        'collected_page': 'search_reels',
                        'commenter_identity_source': 'profile_url',
                    })
                    records.append(rec)
                    # 2026-11-03 13:25 主人要求：**每采到 5 条就入库一次**，
                    # 不要攒到最后——中途中断也不会丢已采的数据。
                    if len(records) >= 5:
                        _flush(records)
                        records = []
                else:
                    print('      ✗ 没取到 ID')
                    stats['fail'] += 1
                s.back()
                time.sleep(1.5)
            # 退出 Reel 回到结果页。评论区还开着时，一次 back 只关评论区，
            # 所以这里**循环 back 直到看见 Reel 卡**（最多 3 次），避免卡在 Reel 页。
            for _ in range(3):
                s.back()
                time.sleep(2.0)
                probe = s.dump()
                if any(_REEL_CARD.match(_text(x)) for x in probe):
                    break
            else:
                print('  退不回结果页，重新搜索')
                if not search_keyword(s, keyword):
                    break

    # 最后不足 5 条的尾巴也写进去
    if records:
        _flush(records)

    print()
    print('=' * 52)
    print(f'关键词      : {keyword}')
    print(f'目标/实际   : {target} / {stats["got_id"]}')
    print(f'打开 Reel   : {stats["reels"]}')
    print(f'尝试评论    : {stats["tried"]}')
    print(f'失败        : {stats["fail"]}')
    if stats['tried']:
        print(f'ID 取到率   : {stats["got_id"] / stats["tried"] * 100:.1f}%')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
