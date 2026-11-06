"""Facebook 评论采集：搜索 → 进帖子 → 读评论 → 写入中转站（leads_facebook）。

和其他平台的采集器保持同一套约定：只读、不点赞不回复、边采边入池。
区别在**身份识别**：FB 没有可从 UI 稳定读到的用户 ID，联系对象只能靠
「昵称 + 帖子上下文」定位，昵称会重名也会变。这一点在表结构和备注里都如实写了。

评论条目的稳定锚点是**回复按钮的说明文字**：
    `對<作者>留言的「回覆」按鈕。點按兩下可回覆留言。`
它把作者名和这条评论绑在了一起，比"按 y 坐标猜"可靠得多。
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web import lead_store as L  # noqa: E402
from fishfleet.web.facebook_live import (  # noqa: E402
    DeviceSession, is_visible, parse_elements, setup_utf8_stdout)

PLATFORM = 'facebook'

# 回复按钮说明：把作者名和评论绑定的关键。
# 2026-11-05 修：FB 实际有两套语序——
#   旧版/英文界面的中转：`對XXX留言的「回覆」按鈕`
#   当前繁体界面实测：  `回覆XXX的留言`（反语序，旧正则一条都匹配不上，
#                       导致评论区明明有评论、parse_comment_items 却恒返回 0）
_REPLY_BTN = re.compile(
    r'^(?:對(.+?)留言的「?回[覆复]」?按鈕|回[覆复](.+?)的留言)')
# 点赞按钮说明
_LIKE_BTN = re.compile(r'^說(.+?)的留言讚的按鈕')
# 时间：11小時 / 3天 / 剛剛 / 2分鐘
_TIME = re.compile(r'^(\d+\s*(秒|分鐘|分钟|小時|小时|天|週|周|個月|个月|年)|剛剛|刚刚)$')
# 心情数：25個心情 / 4,101個心情（带千分位）
_LIKES = re.compile(r'^[\d,]+\s*個?[心感]情$')
# 纯数字 / 纯符号 / 计数类，不是正文（10:50 实测 "219"、"14次分享" 被当正文）
_NOT_TEXT = re.compile(
    r'^([\d\s,.\u200b]+|[\d,]+\s*(次分享|次讚|則回覆|个心情|個心情|個讚|次观看|次觀看))$')
# 更多回复：查看1則回覆 / 查看另外1則對X的回覆 / 查看其他1則回覆
_MORE_REPLIES = re.compile(r'^查看(\d+|另外|其他)')
# UI 文案，不是用户评论正文（09:15 实测："查看隱藏的留言" 被误当正文收进池子）
_UI_NOISE = re.compile(
    r'^(查看隱藏的留言|查看隱藏留言|查看全部留言|查看更多|查看更多留言|'
    r'查看其他|查看另外|已編輯|已编辑|已翻譯|已翻译|'
    r'查看更多回覆|查看更多回复|隱藏|隐藏)$')
# 帖子上的评论数入口：202則留言 / 33 comments；帖子详情里也可能只有一个「留言」按钮
_POST_COMMENTS = re.compile(r'^(\d+\s*(則留言|条留言|comments)|留言|評論|评论)$')
# 搜索结果里的帖子卡：`<作者>, 10 小時. <正文…>`
_POST_CARD = re.compile(r'^.+?,\s*\d+\s*(小時|小时|分鐘|分钟|天|週|周)\s*\.\s')


def _text(el: dict) -> str:
    return (el.get('label') or el.get('text') or '').strip()


def parse_comment_items(els: list[dict]) -> list[dict]:
    """从层级里解析出评论条目。

    2026-11-02 19:40 重大修正：**评论正文是 `Button`，不是 `ViewGroup`**。
    真实结构（实测）：
        y=669 ImageView 林立偉大頭貼照
        y=669 Button    林立偉              ← 作者名（可点击）
        y=670 ViewGroup 6週                 ← 时间
        y=738 Button    be whi you are      ← **正文（Button）**
        y=797 Button    說林立偉的留言讚的按鈕…
        y=801 Button    對林立偉留言的「回覆」按鈕…   ← 锚点
    之前只按 ViewGroup 找正文，导致一条正文都没采到、采到的全是别的东西，
    表现为"昵称和内容对不上"（主人一眼看出偏移）。
    """
    # 1) 以「回覆」按钮为锚，拿到 (作者, y)
    anchors = []
    for e in els:
        m = _REPLY_BTN.match(_text(e))
        if m:
            # 两种语序分别落在 group(1)/group(2)，取非空的那个
            author = (m.group(1) or m.group(2) or '').strip()
            if author:
                anchors.append((author, e['y']))
    if not anchors:
        return []

    items = []
    for idx, (author, ay) in enumerate(anchors):
        top = anchors[idx - 1][1] if idx else 0
        bot = anchors[idx + 1][1] if idx + 1 < len(anchors) else 10 ** 9
        seg = [e for e in els if top < e['y'] < bot]
        if not seg:
            continue

        # 2) 作者名（Button 或 ImageView，label 就是作者名）
        author_y = None
        for e in seg:
            if e['type'] in ('Button', 'ImageView') and _text(e) == author:
                author_y = e['y']
                break
        if author_y is None:
            continue

        # 3) 时间：作者名同一行附近的 ViewGroup，形如 `6週` / `23小時`
        when = ''
        for e in seg:
            if abs(e['y'] - author_y) <= 60 and _TIME.match(_text(e)):
                when = _text(e)
                break

        # 4) 正文：作者名之后**第一个合格的 Button**（不是功能按钮、不是噪声）
        body = ''
        for e in sorted(seg, key=lambda x: x['y']):
            if e['y'] <= author_y or e['type'] != 'Button':
                continue
            txt = _text(e)
            if not txt or len(txt) < 2:
                continue
            if (_TIME.match(txt) or _LIKES.match(txt) or _MORE_REPLIES.match(txt)
                    or _REPLY_BTN.match(txt) or _LIKE_BTN.match(txt)
                    or _UI_NOISE.match(txt) or _NOT_TEXT.match(txt)):
                continue
            if txt.startswith(('說', '對', '不推', '開啟', '檢舉', '更多')):
                continue
            body = txt
            break
        if not body:
            continue

        # 5) 心情数
        likes = ''
        for e in seg:
            if _LIKES.match(_text(e)):
                likes = _text(e)
                break

        items.append({
            'comment_author': author,
            'comment_text': body,
            'comment_time': when,
            'comment_likes': likes,
            'has_replies': '1' if any(_MORE_REPLIES.match(_text(e))
                                      and top < e['y'] < bot for e in els) else '',
            'y': ay,
        })
    return items


# FB 的 profile 链接有**两种**，都是唯一标识：
#   1) https://www.facebook.com/profile.php?id=61553716394951   （数字 ID）
#   2) https://www.facebook.com/chew.on.1                       （自定义用户名）
# 2026-11-02 17:40 实测两种都存在，必须都支持，否则会漏掉一大半人。
_PROFILE_URL = re.compile(
    r'https://www\.facebook\.com/(?:profile\.php\?id=(\d+)|([A-Za-z0-9._]+)/?)\s*$')


def fetch_profile_id(s: DeviceSession, author: str) -> dict:
    """从当前页（必须是某个人的主页）取出**唯一数字 ID**。

    2026-11-02 17:20 突破：对方主页 →「更多個人檔案設定」里直接显示
        https://www.facebook.com/profile.php?id=61553716394951
    这个数字就是 FB 的 UID，和抖音号等价；实测 open_url 可直达该人主页。
    取完要点一次「返回」收回菜单，保持页面状态干净。
    """
    out = {'profile_id': '', 'profile_url': ''}
    els = s.dump()

    def _grab(e2):
        for x in e2:
            m = _PROFILE_URL.search(_text(x))
            if m:
                return m.group(1) or m.group(2), m.group(0).strip()
        return '', ''

    # 直接就在页面上的情况
    pid, url = _grab(els)
    if pid:
        out['profile_id'], out['profile_url'] = pid, url
        return out
    # 需要先展开「更多個人檔案設定」
    btn = [e for e in els if _text(e) == '更多個人檔案設定']
    if not btn:
        for _ in range(3):
            s.swipe('up', distance=900, x=540, y=1300)
            time.sleep(1.3)
            els = s.dump()
            btn = [e for e in els if _text(e) == '更多個人檔案設定']
            if btn:
                break
    if not btn:
        return out
    s.tap_element(btn[0])
    time.sleep(2.5)
    pid, url = _grab(s.dump())
    if pid:
        out['profile_id'], out['profile_url'] = pid, url
    # 收回菜单
    s.back()
    time.sleep(1.2)
    return out



def collect_from_current_post(s: DeviceSession, keyword: str, limit: int = 10,
                              scrolls: int = 3) -> list[dict]:
    """在**已经打开评论区的**页面上采评论。

    scrolls=1 时**不滚动**，只采当前屏——这是给"逐屏采集+立即回复"用的：
    一旦滚了，回复时看到的就是另一批人（2026-11-02 13:16 诊断证实）。
    """
    seen = []
    for i in range(scrolls):
        els = s.dump()
        for it in parse_comment_items(els):
            if it['comment_author'] not in [x['comment_author'] for x in seen] \
                    or it['comment_text'] not in [x['comment_text'] for x in seen]:
                if it not in seen:
                    seen.append(it)
        if len(seen) >= limit or i == scrolls - 1:
            break
        s.swipe('up', distance=900, x=540, y=1400)
        time.sleep(1.3)
    return seen[:limit]


def collect_from_feed(s: DeviceSession, keyword: str, posts: int = 2,
                      comments_per_post: int = 10) -> dict:
    """从**首页信息流**采评论。

    为什么不用搜索结果：2026-11-02 10:20–10:40 反复实测，
    搜索结果里点开的帖子是精简页，`留言` 按钮点不动（有时只是聚焦/滚动），
    连续两次确认都进不去评论区。而首页 feed 上带 `N則留言` 的帖子能稳定点开。
    所以关键词在这里只作为**记录字段**，不参与筛选。
    """
    total_added = 0
    total_dup = 0
    done = 0
    for _ in range(12):
        els = s.dump()
        # feed 的互动栏只有「留言」两字，不带数字（10:50 实测），不能要求首字符是数字
        entry = [e for e in els if e['type'] == 'Button'
                 and _POST_COMMENTS.match(_text(e))]
        if not entry:
            s.swipe('up', distance=900, x=540, y=1300)
            time.sleep(1.3)
            continue
        m = _POST_COMMENTS.match(_text(entry[0]))
        declared = m.group(1) if m else ''
        if not declared.strip().isdigit():
            declared = ''
        # 记录这条帖子的作者：feed 的时间行是 `16小時•分享對象：所有人`，**不含作者**。
        # 作者最可靠的来源是**帖子头像的 label**（ImageView，label 就是作者名），
        # 取"留言"按钮上方最近的那一个（同一屏可能有多个帖子的头像，取最近的）。
        post_author = ''
        post_text = ''
        btn_y = entry[0]['y']
        best = None
        for e in els:
            t = _text(e)
            if not t or e['y'] >= btn_y:
                continue
            if e['type'] not in ('ImageView', 'ViewGroup', 'Button'):
                continue
            m = (re.match(r'^開啟(.+?)的限時動態', t)
                 or re.match(r'^(.+?)的頭像$', t)
                 or re.match(r'^(.+?) Profile picture$', t))
            name = m.group(1).strip() if m else None
            if name is None and e['type'] == 'ImageView' and len(t) <= 24:
                name = t  # 头像 label 常常直接就是名字
            if not name:
                continue
            dist = btn_y - e['y']
            if best is None or dist < best[0]:
                best = (dist, name)
        if best:
            post_author = best[1]
        # 帖子时间行作为辅助标识
        times = [e for e in els if '•' in _text(e) and '分享對象' in _text(e)
                 and e['y'] < btn_y]
        if times:
            times.sort(key=lambda x: btn_y - x['y'])
            post_text = _text(times[0])
        print(f'  打开评论区（{declared}，帖子作者={post_author or "未知"}）')
        s.tap_element(entry[0])
        time.sleep(2.5)
        items = collect_from_current_post(s, keyword, comments_per_post)
        print(f'    解析到 {len(items)} 条')
        if items:
            rows = [{
                'comment_author': it['comment_author'],
                'comment_text': it['comment_text'],
                'comment_time': it.get('comment_time', ''),
                'comment_likes': it.get('comment_likes', ''),
                'has_replies': it.get('has_replies', ''),
                'post_author': post_author,
                'post_text': post_text,
                'post_comment_count': declared,
                'keyword': keyword,
                'collected_page': 'home_feed',
                'commenter_identity_source': 'feed_reply_button',
            } for it in items]
            res = L.add_leads(PLATFORM, rows, serial=s.device, reply_text='')
            total_added += res.get('added', 0)
            total_dup += res.get('duplicated', 0)
            print(f'    入中转站 +{res.get("added", 0)}（重复 {res.get("duplicated", 0)}）')
            done += 1
        s.back()
        time.sleep(1.0)
        s.back()
        time.sleep(1.0)
        if done >= posts:
            break
    return {'added': total_added, 'duplicated': total_dup, 'posts': done}


def collect_from_reels(s: DeviceSession, keyword: str, videos: int = 10,
                       comments_per_video: int = 12) -> dict:
    """从 Reels tab 采集：每个视频点开评论区采一批，再上滑切下一个。

    2026-11-02 13:20 实测：Reels 里每个视频都带 `N則留言`，能稳定点开评论区，
    而且视频是无限流，不受 feed"滚到底就没帖子"的限制。
    """
    total_added = 0
    total_dup = 0
    done = 0
    for _ in range(videos * 2):
        if done >= videos:
            break
        els = s.dump()
        cnt = [e for e in els if e['type'] == 'Button'
               and re.match(r'^\d+\s*(則留言|条留言|comments)$',
                            (e.get('label') or e.get('text') or '').strip())]
        if not cnt:
            s.swipe('up', distance=1000, x=540, y=1300)
            time.sleep(1.5)
            continue
        declared = re.match(r'^(\d+)', (cnt[0].get('label') or '').strip()).group(1)
        print(f'  打开视频评论区（{declared}）')
        s.tap_element(cnt[0])
        time.sleep(3.0)
        items = collect_from_current_post(s, keyword, comments_per_video)
        print(f'    解析到 {len(items)} 条')
        if items:
            rows = [{
                'comment_author': it['comment_author'],
                'comment_text': it['comment_text'],
                'comment_time': it.get('comment_time', ''),
                'comment_likes': it.get('comment_likes', ''),
                'has_replies': it.get('has_replies', ''),
                'post_comment_count': declared,
                'keyword': keyword,
                'collected_page': 'reels',
                'commenter_identity_source': 'reels_reply_button',
            } for it in items]
            res = L.add_leads(PLATFORM, rows, serial=s.device, reply_text='')
            total_added += res.get('added', 0)
            total_dup += res.get('duplicated', 0)
            print(f'    入中转站 +{res.get("added", 0)}（重复 {res.get("duplicated", 0)}）')
            done += 1
        # 关评论区，切下一个视频
        s.back()
        time.sleep(1.0)
        s.swipe('up', distance=1000, x=540, y=1300)
        time.sleep(2.0)
    return {'added': total_added, 'duplicated': total_dup, 'videos': done}


def collect(keyword: str, posts: int = 2, comments_per_post: int = 10,
            device: str = 'SERIAL0001') -> dict:
    """完整采集：搜索关键词 → 逐个帖子读评论 → 写入中转站。"""
    total_added = 0
    total_dup = 0
    post_count = 0
    with DeviceSession(device) as s:
        # 回首页顶部
        for _ in range(4):
            els = s.dump()
            fg = s.foreground()
            if T.page_of(els, fg.activity if fg else '') == 'home':
                break
            s.back()
            time.sleep(1.0)
        for _ in range(3):
            s.swipe('down', distance=1200, x=540, y=800)
            time.sleep(0.7)

        # 搜索
        els = s.dump()
        sb = T.find('home', 'search', els)
        if sb is None:
            return {'error': '找不到搜索入口', 'added': 0}
        s.tap_element(sb)
        time.sleep(2.0)
        if not s.type_anywhere(keyword, submit=True):
            return {'error': '关键词输入失败', 'added': 0}
        time.sleep(3.0)

        for _ in range(posts):
            els = s.dump()
            # 排除图片格：`…貼文的相片` 点下去会进看图器，不是帖子详情（09:54 踩过）
            entry = [e for e in els
                     if _POST_CARD.match(_text(e))
                     and '相片' not in _text(e) and '照片' not in _text(e)]
            # 搜索结果首页常混着社团/专页卡片，往下滚几屏才出现帖子卡
            for _ in range(4):
                if entry:
                    break
                s.swipe('up', distance=1000, x=540, y=1500)
                time.sleep(1.3)
                els = s.dump()
                entry = [e for e in els
                         if _POST_CARD.match(_text(e))
                         and '相片' not in _text(e) and '照片' not in _text(e)]
            if not entry:
                print('  滚了 4 屏仍没有帖子卡，停止')
                break
            card_label = _text(entry[0])
            print(f'  点开帖子：{card_label[:40]}')
            s.tap_element(entry[0])
            time.sleep(3.0)

            # 帖子详情里再找「N 則留言」入口打开评论区
            els = s.dump()
            cmt = [e for e in els if _POST_COMMENTS.match(_text(e))]
            for _ in range(6):
                if cmt:
                    break
                s.swipe('up', distance=900, x=540, y=1500)
                time.sleep(1.2)
                els = s.dump()
                cmt = [e for e in els if _POST_COMMENTS.match(_text(e))]
            if not cmt:
                print('    这条帖子没找到留言入口，跳过')
                s.back()
                time.sleep(1.2)
                continue
            declared = _POST_COMMENTS.match(_text(cmt[0])).group(1)
            if not declared.strip().isdigit():
                declared = ''  # 只有「留言」两字时拿不到总数
            print(f'    打开评论区（标注 {declared or "未知"} 条）')
            # 点完必须确认真的进了评论区（出现回复按钮说明），否则再点一次。
            # 10:25 实测：点「留言」有时只是滚动/聚焦，页面还停在帖子详情。
            opened = False
            for attempt in range(2):
                s.tap_element(cmt[0])
                time.sleep(2.5)
                probe = s.dump()
                if parse_comment_items(probe):
                    opened = True
                    break
                print(f'      第 {attempt + 1} 次点击后仍没看到评论，重试')
                els = s.dump()
                cmt = [e for e in els if _POST_COMMENTS.match(_text(e))]
                if not cmt:
                    break
            if not opened:
                print('    无法进入评论区，跳过这条')
                s.back()
                time.sleep(1.2)
                continue

            items = collect_from_current_post(s, keyword, comments_per_post)
            print(f'    解析到 {len(items)} 条评论')
            rows = []
            for it in items:
                rows.append({
                    'comment_author': it['comment_author'],
                    'comment_text': it['comment_text'],
                    'comment_time': it.get('comment_time', ''),
                    'comment_likes': it.get('comment_likes', ''),
                    'has_replies': it.get('has_replies', ''),
                    'post_author': card_label.split(',')[0].strip(),
                    'post_text': card_label[:200],
                    'post_kind': '',
                    'post_comment_count': declared,
                    'keyword': keyword,
                    'collected_page': 'post_detail',
                    'commenter_identity_source': 'comment_list_reply_button',
                })
            if rows:
                res = L.add_leads(PLATFORM, rows, serial=device, reply_text='')
                total_added += res.get('added', 0)
                total_dup += res.get('duplicated', 0)
                print(f'    入中转站 +{res.get("added", 0)}（重复 {res.get("duplicated", 0)}）')
            post_count += 1
            # 关评论区 → 回结果页
            s.back()
            time.sleep(1.0)
            s.back()
            time.sleep(1.2)
    return {'added': total_added, 'duplicated': total_dup, 'posts': post_count}


def main() -> int:
    setup_utf8_stdout()
    if len(sys.argv) < 2:
        print('用法：python fishfleet/web/facebook_comments.py collect <关键词> [帖子数] [每帖评论数]')
        print('      python fishfleet/web/facebook_comments.py pool   # 看中转站状态')
        return 2
    cmd = sys.argv[1]
    if cmd == 'pool':
        st = L.stats(PLATFORM)
        print('中转站 facebook：', st['platforms'].get(PLATFORM, {}))
        for row in L.listing(PLATFORM, status=L.PENDING, limit=10):
            print(f"  [{row['id'][:8]}] {row['comment_author']}: {row['comment_text'][:40]}")
        return 0
    if cmd == 'reels':
        kw = sys.argv[2] if len(sys.argv) > 2 else 'reels'
        videos = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        per = int(sys.argv[4]) if len(sys.argv) > 4 else 12
        with DeviceSession('SERIAL0001') as s:
            from fishfleet.web.facebook_reply import ensure_home
            if not ensure_home(s):
                print('回不到首页')
                return 1
            tab = T.find('home', 'tab_reels', s.dump())
            if tab is None:
                print('找不到 Reels tab')
                return 1
            s.tap_element(tab)
            time.sleep(4.0)
            res = collect_from_reels(s, kw, videos, per)
        print('结果：', res)
        return 0 if res.get('added', 0) > 0 else 1
    if cmd == 'feed':
        kw = sys.argv[2] if len(sys.argv) > 2 else 'feed'
        posts = int(sys.argv[3]) if len(sys.argv) > 3 else 2
        per = int(sys.argv[4]) if len(sys.argv) > 4 else 10
        with DeviceSession('SERIAL0001') as s:
            for _ in range(4):
                els = s.dump()
                fg = s.foreground()
                if T.page_of(els, fg.activity if fg else '') == 'home':
                    break
                s.back()
                time.sleep(1.0)
            for _ in range(3):
                s.swipe('down', distance=1200, x=540, y=800)
                time.sleep(0.7)
            res = collect_from_feed(s, kw, posts, per)
        print('结果：', res)
        return 0 if res.get('added', 0) > 0 else 1
    if cmd == 'collect':
        if len(sys.argv) < 3:
            print('需要关键词')
            return 2
        kw = sys.argv[2]
        posts = int(sys.argv[3]) if len(sys.argv) > 3 else 2
        per = int(sys.argv[4]) if len(sys.argv) > 4 else 10
        res = collect(kw, posts, per)
        print('结果：', res)
        return 0 if res.get('added', 0) > 0 else 1
    print('未知命令')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
