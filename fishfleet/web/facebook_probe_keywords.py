"""关键词「供给探测」：只看一个词能产出多少条评论，不取 ID、不写库。

用途：在正式采集前，先用几十秒判断一个词值不值得跑。
判据：打开 N 个 Reel，统计能解析出多少条**真实评论**。

用法：
  python fishfleet/web/facebook_probe_keywords.py "词1" "词2" ...
  python fishfleet/web/facebook_probe_keywords.py --reels 6 "iPhone" "汽车"
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web.facebook_comments import parse_comment_items  # noqa: E402
from fishfleet.web.facebook_live import DeviceSession, setup_utf8_stdout  # noqa: E402
from fishfleet.web.facebook_reply import ensure_home  # noqa: E402

_REEL_CARD = re.compile(r'.+的 Reel，.*點按兩下可觀看')
_COMMENT_CNT = re.compile(r'^(\d+\s*(則留言|条留言|comments)|留言|評論|评论)$')


def _text(e: dict) -> str:
    return (e.get('label') or e.get('text') or '').strip()


def search(s: DeviceSession, kw: str) -> bool:
    sb = T.find('home', 'search', s.dump())
    if sb is None:
        return False
    s.tap_element(sb)
    time.sleep(2.5)
    if not s.type_anywhere(kw, submit=True):
        return False
    time.sleep(5.0)
    return True


def back_to_results(s: DeviceSession) -> bool:
    for _ in range(3):
        s.back()
        time.sleep(2.0)
        if any(_REEL_CARD.match(_text(x)) for x in s.dump()):
            return True
    return False


def probe_one(s: DeviceSession, kw: str, max_reels: int) -> dict:
    res = {'keyword': kw, 'reels': 0, 'comments': 0, 'with_comments': 0, 'samples': []}
    if not search(s, kw):
        res['error'] = '搜索失败'
        return res
    seen = set()
    empty = 0
    while res['reels'] < max_reels and empty < 12:
        els = s.dump()
        cards = [e for e in els if _REEL_CARD.match(_text(e))]
        fresh = [c for c in cards if _text(c) not in seen]
        if not fresh:
            empty += 1
            s.swipe('up', distance=1200, x=540, y=1400)
            time.sleep(2.0)
            continue
        empty = 0
        card = fresh[0]
        seen.add(_text(card))
        res['reels'] += 1
        s.tap_element(card)
        time.sleep(5.0)
        e2 = s.dump()
        cnt = [e for e in e2 if e['type'] == 'Button' and _COMMENT_CNT.match(_text(e))]
        if not cnt:
            for _ in range(2):
                s.swipe('up', distance=800, x=540, y=1300)
                time.sleep(1.4)
                e2 = s.dump()
                cnt = [e for e in e2 if e['type'] == 'Button' and _COMMENT_CNT.match(_text(e))]
                if cnt:
                    break
        if cnt:
            s.tap_element(cnt[0])
            time.sleep(3.0)
            items = parse_comment_items(s.dump())
            if items:
                res['comments'] += len(items)
                res['with_comments'] += 1
                if len(res['samples']) < 3:
                    res['samples'].append(f"{items[0]['comment_author']}: {items[0]['comment_text'][:24]}")
        if not back_to_results(s):
            if not search(s, kw):
                break
    return res


def main() -> int:
    setup_utf8_stdout()
    args = sys.argv[1:]
    max_reels = 6
    if '--reels' in args:
        i = args.index('--reels')
        max_reels = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    if not args:
        print('用法: python facebook_probe_keywords.py "词1" "词2" ...')
        return 2

    print(f'探测 {len(args)} 个关键词，每个最多打开 {max_reels} 个 Reel\n')
    results = []
    with DeviceSession('SERIAL0001') as s:
        if not ensure_home(s):
            print('回不到首页')
            return 1
        for kw in args:
            print(f'--- {kw} ---')
            r = probe_one(s, kw, max_reels)
            results.append(r)
            print(f"  Reel={r['reels']} 有评论的视频={r['with_comments']} 评论总数={r['comments']}")
            for smp in r['samples']:
                print(f'    样例: {smp}')

    print()
    print('=' * 60)
    print(f"{'关键词':<16} {'Reel':>5} {'有评论':>7} {'评论数':>7}")
    for r in results:
        print(f"{r['keyword']:<16} {r['reels']:>5} {r['with_comments']:>7} {r['comments']:>7}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
