"""采评论并**逐条取回唯一 profile ID**（稳定性优先）。

流程（每条评论）：
    评论区里找到作者名 → 点进他/她的主页 → 展开「更多個人檔案設定」
    → 读出 profile.php?id=数字 → 返回评论区 → 写回中转站

为什么慢也要这么走：昵称会重名、会改，只有 profile.php?id 是唯一且可跨手机复现的。
多花时间换准确性，是主人明确认可的方向。

用法：
  python fishfleet/web/facebook_collect_ids.py <目标条数> [每屏上限]
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
from fishfleet.web.facebook_live import (  # noqa: E402
    DeviceSession, setup_utf8_stdout)
from fishfleet.web.facebook_reply import ensure_home  # noqa: E402

PLATFORM = 'facebook'


def _text(e: dict) -> str:
    return (e.get('label') or e.get('text') or '').strip()


def open_reels_comment(s: DeviceSession) -> bool:
    tab = T.find('home', 'tab_reels', s.dump())
    if tab is None:
        return False
    s.tap_element(tab)
    time.sleep(4.0)
    for _ in range(6):
        els = s.dump()
        cnt = [e for e in els if e['type'] == 'Button'
               and re.match(r'^\d+\s*(則留言|条留言|comments)$', _text(e))]
        if cnt:
            s.tap_element(cnt[0])
            time.sleep(3.5)
            if parse_comment_items(s.dump()):
                return True
        s.swipe('up', distance=1400, x=540, y=1400)
        time.sleep(1.8)
    return False


def open_author_profile(s: DeviceSession, author: str) -> bool:
    """在评论区里点该作者的名字，进他/她的主页。"""
    els = s.dump()
    for e in els:
        if e['type'] != 'Button' or _text(e) != author:
            continue
        # 必须紧挨着同名头像，才是评论作者名（不是正文里碰巧同名的词）。
        # 放宽：头像 label 只要**以作者名开头**即可（有的带"大頭貼照"、有的带
        # "未讀限時動態"，还有的直接就是名字），y 差放宽到 200。
        near_avatar = any(
            x['type'] == 'ImageView'
            and _text(x).startswith(author)
            and abs(x['y'] - e['y']) <= 200
            for x in els)
        if near_avatar:
            s.tap_element(e)
            # 主页是重页面，4 秒往往只到过渡态；轮询等它真的出来（最多 12 秒）。
            # **不要只看 page_of**：实测有些主页判 None，但 Activity 已经是
            # ImmersivePortraitActivity，页面上也有"發送訊息/大頭貼照"。
            for _ in range(6):
                time.sleep(2.0)
                fg = s.foreground()
                activity = (fg.activity if fg else '') or ''
                e2 = s.dump()
                labels = {_text(x) for x in e2}
                if (T.page_of(e2, activity) == 'person_profile'
                        or 'ImmersivePortraitActivity' in activity
                        or '發送訊息' in labels or '大頭貼照' in labels):
                    return True
            print(f'      [诊断] 点了 {author} 但停在 {activity}')
            return False
    return False


def main() -> int:
    setup_utf8_stdout()
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    per = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    stats = {'collected': 0, 'tried': 0, 'got_id': 0, 'profile_fail': 0, 'id_fail': 0}
    rounds = 0
    records = []

    with DeviceSession('SERIAL0001') as s:
        if not ensure_home(s):
            print('回不到首页')
            return 1

        while stats['collected'] < target:
            rounds += 1
            if rounds > 60:
                print('轮数上限，结束（够不够都停，避免无限转）')
                break
            if not open_reels_comment(s):
                print('找不到可开的评论区，换视频')
                s.back()
                time.sleep(1.0)
                s.swipe('up', distance=1400, x=540, y=1400)
                time.sleep(2.0)
                continue

            # 逐屏处理
            for _ in range(6):
                if stats['collected'] >= target:
                    break
                items = collect_from_current_post(s, 'marketplace', per, scrolls=1)
                if not items:
                    break
                for it in items:
                    if stats['collected'] >= target:
                        break
                    author = it['comment_author']
                    stats['tried'] += 1
                    print(f"  [{stats['tried']}] {author}: {it['comment_text'][:26]}")
                    ok = open_author_profile(s, author)
                    if not ok:
                        print('      ✗ 进不了主页')
                        stats['profile_fail'] += 1
                        continue
                    info = fetch_profile_id(s, author)
                    if info['profile_id']:
                        stats['got_id'] += 1
                        print(f"      ✓ ID={info['profile_id']}")
                        rec = dict(it)
                        rec.update({
                            'commenter_profile_id': info['profile_id'],
                            'commenter_profile_url': info['profile_url'],
                            'keyword': 'marketplace',
                            'collected_page': 'reels',
                            'commenter_identity_source': 'profile_url',
                        })
                        records.append(rec)
                        stats['collected'] += 1
                    else:
                        print('      ✗ 没取到 ID')
                        stats['id_fail'] += 1
                    # 回评论区
                    s.back()
                    time.sleep(1.5)
                if stats['collected'] >= target:
                    break
                s.swipe('up', distance=800, x=540, y=1300)
                time.sleep(1.5)

            # 这条视频处理完，关掉评论区换下一条
            s.back()
            time.sleep(1.0)
            s.swipe('up', distance=1400, x=540, y=1400)
            time.sleep(2.0)

    if records:
        res = L.add_leads(PLATFORM, records, serial='SERIAL0001', reply_text='')
        print(f"\n写入中转站: {res}")

    print()
    print('=' * 52)
    print(f"目标条数      : {target}")
    print(f"尝试条数      : {stats['tried']}")
    print(f"**取到唯一 ID**: {stats['got_id']}")
    print(f"进不了主页    : {stats['profile_fail']}")
    print(f"取不到 ID     : {stats['id_fail']}")
    if stats['tried']:
        print(f"ID 取到率     : {stats['got_id'] / stats['tried'] * 100:.1f}%")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
