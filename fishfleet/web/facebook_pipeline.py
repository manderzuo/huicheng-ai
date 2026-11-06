"""采采回复流水线：**同一个评论区里**采完立即逐条模拟回复，统计成功率。

为什么这样设计：
2026-11-02 13:00 发现 FB 没有可从 UI 稳定读到的用户/帖子 ID，
采集完再"回头找原帖"在 Reels 这种无限流里几乎不可能。
而真实业务也是**进到一个帖子、把这里的评论处理完**。
所以链路做成：打开评论区 → 采评论入中转站 → 立即对刚采的每条跑一遍
「定位 → 填草稿 → 识别发送键（不点）」→ 写回状态。

成功判定（用户定义的四项）：
  1) 评论落进中转站
  2) 能从数据里生成回复语
  3) 定位到那条评论并把它填进输入框（回读确认）
  4) 识别到发送键且**不点击**

用法：
  python fishfleet/web/facebook_pipeline.py <目标条数> [每帖上限]
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
    collect_from_current_post, parse_comment_items)
from fishfleet.web.facebook_live import (  # noqa: E402
    DeviceSession, setup_utf8_stdout)
from fishfleet.web.facebook_reply import ensure_home, open_comment_area  # noqa: E402

PLATFORM = 'facebook'
DRAFT = '谢谢分享，学到了～'
_REPLY_BTN = re.compile(r'^對(.+?)留言的「?回[覆复]」?按鈕')


def build_draft(row: dict) -> str:
    """从入库数据生成回复语。"""
    return f"{row['comment_author']}，{DRAFT}"


def try_draft(s: DeviceSession, row: dict) -> tuple[bool, bool, str]:
    """定位 → 填草稿 → 识别发送键。返回 (located, drafted, send_or_reason)。"""
    author = row['comment_author']
    body = row['comment_text']
    els = s.dump()
    btn = None
    for e in els:
        m = _REPLY_BTN.match((e.get('label') or e.get('text') or '').strip())
        if m and m.group(1).strip() == author:
            btn = e
            break
    if btn is None:
        # 诊断：把当前屏所有回复按钮的作者打出来，看是"没滚到位"还是"名字对不上"
        avail = []
        for e in els:
            m = _REPLY_BTN.match((e.get('label') or e.get('text') or '').strip())
            if m:
                avail.append(m.group(1).strip())
        print(f'      [诊断] 目标={author!r} 当前可见回复按钮={avail[:6]}')
        return False, False, '找不到回覆按钮'
    s.tap_element(btn)
    time.sleep(2.0)

    els2 = s.dump()
    box = None
    for e in els2:
        if e['type'] in ('EditText', 'AutoCompleteTextView'):
            box = e
            break
    if box is None:
        return False, False, '找不到输入框'
    s.tap_element(box)
    time.sleep(0.8)
    draft = build_draft(row)
    if not s.type_anywhere(draft, submit=False):
        return False, False, '输入失败'
    time.sleep(1.0)

    els3 = s.dump()
    found = any(draft in (e.get('text') or '') or draft in (e.get('label') or '')
                for e in els3)
    if not found:
        return True, False, '草稿没出现在输入框'

    snd = None
    for e in els3:
        txt = (e.get('label') or e.get('text') or '').strip()
        if txt in ('傳送', '发送', '發佈', '发布', '送出', 'Post', 'Send'):
            if box is not None and abs(e['y'] - box['y']) <= 150:
                snd = e
                break
    if snd is None:
        return True, True, '没识别到发送键'
    return True, True, f'发送键={snd.get("label") or snd.get("text")}'


def main() -> int:
    setup_utf8_stdout()
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 14
    stats = {'collected': 0, 'located': 0, 'drafted': 0, 'send_found': 0, 'total_checked': 0}

    with DeviceSession('SERIAL0001') as s:
        if not ensure_home(s):
            print('回不到首页')
            return 1
        tab = T.find('home', 'tab_reels', s.dump())
        if tab is None:
            print('找不到 Reels tab')
            return 1
        s.tap_element(tab)
        time.sleep(4.0)
        print('已切到 Reels')

        rounds = 0
        miss = 0
        while stats['collected'] < target and rounds < 400:
            rounds += 1
            els = s.dump()
            cnt = [e for e in els if e['type'] == 'Button'
                   and re.match(r'^\d+\s*(則留言|条留言|comments)$',
                                (e.get('label') or e.get('text') or '').strip())]
            if not cnt:
                # 大部分轮次都花在"找有留言的视频"上；滚太短会一直在同一处打转。
                # 每 12 次自救一次：确认还在 Reels，不在了就重新进。
                miss += 1
                if miss % 12 == 0:
                    fg = s.foreground()
                    if T.page_of(s.dump(), (fg.activity if fg else '') or '') != 'reels':
                        tab2 = T.find('home', 'tab_reels', s.dump())
                        if tab2 is not None:
                            print('    重新进入 Reels')
                            s.tap_element(tab2)
                            time.sleep(3.0)
                s.swipe('up', distance=1400, x=540, y=1400)
                time.sleep(1.8)
                continue
            miss = 0
            s.tap_element(cnt[0])
            time.sleep(3.0)
            # 在同一个视频的评论区里**逐屏**处理：采这一屏 → 立刻回复这一屏 → 滚下一屏。
            # 不能先滚再回来看（那就是另一批人了，13:16 诊断证实）。
            for _ in range(8):
                items = collect_from_current_post(s, 'marketplace', per, scrolls=1)
                if not items:
                    break
                rows = [{
                    'comment_author': it['comment_author'],
                    'comment_text': it['comment_text'],
                    'comment_time': it.get('comment_time', ''),
                    'comment_likes': it.get('comment_likes', ''),
                    'has_replies': it.get('has_replies', ''),
                    'keyword': 'marketplace',
                    'collected_page': 'reels',
                    'commenter_identity_source': 'reels_reply_button',
                } for it in items]
                res = L.add_leads(PLATFORM, rows, serial='SERIAL0001',
                                  reply_text=DRAFT)
                stats['collected'] += res.get('added', 0)
                print(f'[第{rounds}轮] 采集 +{res.get("added", 0)}，'
                      f'累计 {stats["collected"]}/{target}')

                for row in rows:
                    stats['total_checked'] += 1
                    located, drafted, info = try_draft(s, row)
                    if located:
                        stats['located'] += 1
                    if drafted:
                        stats['drafted'] += 1
                    if info.startswith('发送键='):
                        stats['send_found'] += 1
                    print(f'    {row["comment_author"]}: 定位={located} '
                          f'草稿={drafted} {info}')
                    # 收起键盘：按一次返回键。**不要点屏幕空白处**——
                    # 评论区上方就是关闭区，一点就把整个浮层关掉（13:30 踩过）
                    s.back()
                    time.sleep(0.7)
                    if not any(_REPLY_BTN.match((e.get('label') or e.get('text') or '').strip())
                               for e in s.dump()):
                        print('    评论区已关闭，结束本屏')
                        break
                if stats['collected'] >= target:
                    break
                s.swipe('up', distance=800, x=540, y=1300)
                time.sleep(1.5)
            s.back()
            time.sleep(1.0)
            s.swipe('up', distance=1000, x=540, y=1300)
            time.sleep(2.0)

    print()
    print('=' * 50)
    print(f"采集条数        : {stats['collected']}")
    print(f"抽查条数        : {stats['total_checked']}")
    print(f"定位到评论      : {stats['located']}")
    print(f"草稿回读成功    : {stats['drafted']}")
    print(f"识别到发送键    : {stats['send_found']}")
    if stats['total_checked']:
        rate = stats['send_found'] / stats['total_checked'] * 100
        print(f"四项全中占比    : {rate:.1f}%")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
