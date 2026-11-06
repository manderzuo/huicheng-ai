"""Facebook 评论回复：从中转站领任务 → 定位到那条评论 → 填好回复草稿 → **停在发送前**。

安全约定（和项目里其它平台一致）：
- 默认**不发**。定位到评论、把草稿填进输入框，状态记 `drafted`，等人工放行。
- 真正要发必须显式加 `--send`，而且每条都会先打印将要发送的内容。
- 评论内容会做一次**基础安全检查**：命中明显的敏感/刷屏特征就跳过并记 skipped，
  不乱回。这些规则只是兜底，不代表内容审核。

定位方式：评论条目的「回覆」按钮说明里带作者名
    `對<作者>留言的「回覆」按鈕。點按兩下可回覆留言。`
所以先用昵称找到候选，再用正文前缀二次核对，避免同名误回。
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web import lead_store as L  # noqa: E402
from fishfleet.web.facebook_comments import parse_comment_items  # noqa: E402
from fishfleet.web.facebook_live import (  # noqa: E402
    DeviceSession, is_visible, setup_utf8_stdout)

PLATFORM = 'facebook'

_REPLY_BTN = re.compile(r'^對(.+?)留言的「?回[覆复]」?按鈕')

# 基础内容红线：命中就不自动回复（只做兜底，不是内容审核）
_BLOCK_PATTERNS = [
    (r'政治|總統|选举|選舉|民主|威權|台獨|統一|中共|國民黨|民進黨', '政治敏感'),
    (r'詐騙|诈骗|洗錢|洗钱|賭博|赌博|毒品', '违法风险'),
    (r'加微信|加LINE|私聊|私訊|扫码|掃碼|代理|兼職|兼职|日賺|日赚', '营销/引流'),
]


def safety_check(text: str) -> str:
    for pat, why in _BLOCK_PATTERNS:
        if re.search(pat, text):
            return why
    return ''


def find_comment(s: DeviceSession, author: str, body: str,
                 scrolls: int = 6) -> dict | None:
    """在当前评论区里找到目标评论的「回覆」按钮。"""
    for _ in range(scrolls):
        els = s.dump()
        items = parse_comment_items(els)
        for it in items:
            if it['comment_author'] != author:
                continue
            # 正文前缀核对，避免同名不同人
            if body and it['comment_text'][:20] not in (body[:20], ):
                continue
            btn = None
            for e in els:
                m = _REPLY_BTN.match((e.get('label') or e.get('text') or '').strip())
                if m and m.group(1).strip() == author and e['y'] >= it['y'] - 40:
                    btn = e
                    break
            if btn is not None:
                return {'item': it, 'button': btn}
        s.swipe('up', distance=800, x=540, y=1400)
        time.sleep(1.2)
    return None


def scroll_to_top(s: DeviceSession, tries: int = 6) -> bool:
    """一直往下滚到看得见顶栏（菜单/搜索）为止。

    2026-11-02 12:20 实测：首页判定成 home 了，但顶栏仍隐藏，
    后续找搜索入口拿到 None。固定滚 2 次不够，要按"顶栏出现"来判断。
    """
    for _ in range(tries):
        els = s.dump()
        if T.find('home', 'search', els) is not None or \
                T.find('home', 'menu', els) is not None:
            return True
        s.swipe('down', distance=900, x=540, y=500)
        time.sleep(0.9)
    els = s.dump()
    return T.find('home', 'search', els) is not None


def ensure_home(s: DeviceSession) -> bool:
    """确保停在首页。

    2026-11-03 13:45：改成**优先走统一的回退机制** `facebook_recover.recover`，
    它有小红的整套兜底（点掉弹窗 → 逐级 BACK → 重启应用），比原来手写的循环更稳。
    下面保留原逻辑作为 recover 失败时的二次兜底。
    """
    # 2026-11-06 修（8号机 logcat 实证）：
    #   13:44:29 任务创建 → 13:44:46 `Force stopping com.facebook.katana ... from pid 16009`
    #   → 13:45:26 报 activity=LauncherActivity、没能挂上最新视频。
    # 真凶是 `facebook_recover.recover` 的「重启应用」分支：FB 本来好好在前台，
    # 任务一启动就把它 force-stop 再拉起，用户看到的就是「进了相册突然闪退」。
    # 所以：**App 在前台就绝不走 recover**，直接按底栏/页面判定走；
    # 不在前台才主动 launch，也绕开 recover 的重启。
    _fg0 = s.foreground()
    _pkg0 = (_fg0.raw.split('/')[0] if _fg0 and _fg0.raw else '')
    if 'katana' not in _pkg0:
        print('  不在 Facebook（%s），主动拉起（不走 recover，避免它 force-stop 重启）'
              % (_pkg0 or '未知'))
        try:
            s._text_result('mobile_launch_app', {'packageName': 'com.facebook.katana'})
        except Exception:                                 # noqa: BLE001
            pass
        for _ in range(15):
            time.sleep(2.0)
            try:
                _e = s.dump()
                _f = s.foreground()
            except Exception:                             # noqa: BLE001
                continue
            if not _e or not _f or 'katana' not in (_f.raw or ''):
                continue
            if (T.page_of(_e, (_f.activity or '')) == 'home'
                    or T.find('home', 'search', _e) is not None):
                print('    已进入 Facebook')
                scroll_to_top(s)
                return True
        print('    拉起后仍没到首页，交给下面的本地兜底')
    try:
        from . import facebook_recover as _rc
        # 只有 App 已在前台时才让 recover 去"修页面"；
        # 它一旦触发重启应用会 force-stop，对正在发布的场景是破坏性的。
        if 'katana' in _pkg0 and _rc.recover(s, 'home', max_steps=6)['ok']:
            # 2026-11-06 修（8号机实测 run#13:45 复现）：
            # recover 会**误判**——App 根本没启动、当前还停在桌面（LauncherActivity）时，
            # 它照样返回 ok=True。于是 ensure_home 直接 return True，
            # 后面的 open_editor / attach_latest_video 就在**桌面上瞎跑**：
            # 点到了桌面图标（用户看到"进了相册又闪退"），最后报 activity=LauncherActivity。
            # 这里必须自己复核"当前包名是不是 Facebook"；不满足就走下面的兜底
            # （兜底第一件事就是 mobile_launch_app，会真的把 App 拉起来）。
            _fg = s.foreground()
            _pkg = (_fg.raw.split('/')[0] if _fg and _fg.raw else '')
            if 'katana' in _pkg:
                scroll_to_top(s)
                return True
            print('  统一回退回报成功但不在 Facebook（当前 %s），改走本地兜底' % (_pkg or '未知'))
        else:
            print('  统一回退未达成，走本地兜底')
    except Exception as exc:                              # noqa: BLE001
        print('  统一回退异常:', type(exc).__name__)
    for _ in range(8):
        els = s.dump()
        fg = s.foreground()
        activity = (fg.activity if fg else '') or ''
        pkg = (fg.raw.split('/')[0] if fg and fg.raw else '')
        if 'katana' not in pkg:
            print('  不在 Facebook，重新拉起')
            s._text_result('mobile_launch_app', {'packageName': 'com.facebook.katana'})
            # 11:55 实测：固定等 4 秒不够，冷启动时层级还是空的，
            # 后面 find('home','search') 会拿到 None 直接崩。这里等到"能看到首页"为止。
            for _ in range(12):
                time.sleep(2.0)
                e2 = s.dump()
                f2 = s.foreground()
                if not e2 or not f2 or 'katana' not in (f2.raw or ''):
                    continue
                if T.page_of(e2, (f2.activity or '')) == 'home' or \
                        T.find('home', 'search', e2) is not None:
                    print('    已进入 Facebook')
                    break
            continue
        # 先看能不能直接点底栏首頁 tab —— 不依赖页面判定，
        # 因为语言变化后有些页 identity 还没适配（如 Reels 页判 None）。
        tab = T.find('home', 'tab_home', els)
        if tab is not None:
            s.tap_element(tab)
            time.sleep(2.0)
            els2 = s.dump()
            fg2 = s.foreground()
            if T.page_of(els2, (fg2.activity if fg2 else '') or '') == 'home':
                # 首页顶栏会随滚动隐藏；滚到"看得见顶栏"为止，否则后续找不到搜索入口
                scroll_to_top(s)
                return True
            continue
        if T.page_of(els, activity) == 'home':
            return True
        s.back()
        time.sleep(1.0)
    els = s.dump()
    fg = s.foreground()
    return T.page_of(els, fg.activity if fg else '') == 'home'


def open_comment_area(s: DeviceSession) -> bool:
    """从首页 feed 打开一个评论区。"""
    if not ensure_home(s):
        print('  回不到首页')
        return False
    time.sleep(2.0)
    # 先滚到顶，避免落在中间的推荐区
    for _ in range(2):
        s.swipe('down', distance=1200, x=540, y=800)
        time.sleep(0.8)
    for _ in range(12):
        els = s.dump()
        # 2026-11-02 10:50 实测：**feed 的互动栏只显示「留言」两字，不带数字**；
        # `202則留言` 那种带数量的只出现在帖子详情页。只认带数字的会永远 0 命中。
        cnt = [e for e in els
               if e['type'] == 'Button'
               and re.match(r'^(\d+\s*(則留言|条留言|comments)|留言|評論|评论)$',
                            (e.get('label') or e.get('text') or '').strip())]
        if cnt:
            s.tap_element(cnt[0])
            # 评论区是浮层，加载有延迟；12:40 实测等 2.5 秒偶尔还没出来
            time.sleep(3.5)
            if parse_comment_items(s.dump()):
                return True
            print('  点了留言但没进评论区，退回后继续找')
            s.back()
            time.sleep(1.2)
            continue
        s.swipe('up', distance=900, x=540, y=1300)
        time.sleep(1.5)
    return False


def find_post_and_open_comments(s: DeviceSession, post_author: str,
                                post_text: str = '') -> bool:
    """在 feed 里按**帖子作者**找回原帖，并打开它的评论区。

    2026-11-02 11:15 发现的设计缺陷：评论入库时没记"在哪个帖子"，
    领任务后随便打开一个评论区当然找不到人。现在补成：
    先按 post_author（+ post_text 前缀）定位帖子 → 点进详情 → 找 `N則留言` 打开。
    """
    if not ensure_home(s):
        return False
    for _ in range(2):
        s.swipe('down', distance=1200, x=540, y=800)
        time.sleep(0.7)
    for _ in range(12):
        els = s.dump()
        card = None
        for e in els:
            txt = (e.get('label') or e.get('text') or '')
            if not txt or e['type'] != 'Button':
                continue
            if post_author and post_author in txt and ('小時' in txt or '小时' in txt
                                                       or '分鐘' in txt or '分钟' in txt
                                                       or '天' in txt):
                if post_text and post_text[:12] in txt:
                    card = e
                    break
                if card is None:
                    card = e
        if card is not None:
            print(f'  找到原帖（{post_author}）')
            s.tap_element(card)
            time.sleep(3.0)
            els2 = s.dump()
            for _ in range(6):
                cnt = [e for e in els2
                       if e['type'] == 'Button'
                       and re.match(r'^\d+\s*(則留言|条留言|comments)$',
                                    (e.get('label') or e.get('text') or '').strip())]
                if cnt:
                    s.tap_element(cnt[0])
                    time.sleep(2.5)
                    if parse_comment_items(s.dump()):
                        return True
                    break
                s.swipe('up', distance=800, x=540, y=1300)
                time.sleep(1.1)
                els2 = s.dump()
            print('  原帖打开了但没进评论区，回退重找')
            s.back()
            time.sleep(1.2)
        s.swipe('up', distance=900, x=540, y=1300)
        time.sleep(1.3)
    return False


def run_one(s: DeviceSession, row: dict, draft: str, do_send: bool) -> str:
    """处理一条任务，返回最终状态字符串。"""
    author = row['comment_author']
    body = row['comment_text']
    why = safety_check(body)
    if why:
        print(f'  跳过（{why}）：{body[:40]}')
        return L.SKIPPED

    # 先按帖子作者找回原帖并打开评论区，再在里面定位评论
    if not find_post_and_open_comments(s, row.get('post_author', ''),
                                       row.get('post_text', '')):
        print('  找不到原帖的评论区')
        return L.FAILED
    hit = find_comment(s, author, body)
    if hit is None:
        print(f'  没找到该评论：{author} / {body[:30]}')
        return L.FAILED

    print(f'  定位到评论：{author}（{hit["item"]["comment_text"][:40]}）')
    s.tap_element(hit['button'])
    time.sleep(2.0)

    # 输入草稿
    ok = s.type_anywhere(draft, submit=False)
    if not ok:
        print('  草稿输入失败')
        return L.FAILED
    time.sleep(1.0)

    if not do_send:
        print(f'  草稿已填好，**未发送**（加 --send 才会真发）：{draft[:40]}')
        return L.DRAFTED

    # 显式授权后才真发：点「發佈/送出」
    els = s.dump()
    send_btn = None
    for e in els:
        txt = (e.get('label') or e.get('text') or '').strip()
        if txt in ('發佈', '发布', '送出', 'Post', 'Send', '回覆'):
            send_btn = e
            break
    if send_btn is None:
        print('  找不到发送键，草稿保留、不发')
        return L.DRAFTED
    s.tap_element(send_btn)
    time.sleep(2.5)
    print(f'  已点击发送：{draft[:40]}')
    return L.REPLIED


def main() -> int:
    setup_utf8_stdout()
    do_send = '--send' in sys.argv
    if '--stats' in sys.argv:
        print(L.stats(PLATFORM)['platforms'].get(PLATFORM, {}))
        return 0
    if '--dry' in sys.argv:
        # 只演练：不碰设备，把待回复的任务列出来
        rows = L.listing(PLATFORM, status=L.PENDING, limit=20)
        print(f'待回复 {len(rows)} 条：')
        for r in rows:
            print(f"  [{r['id'][:8]}] {r['comment_author']}: {r['comment_text'][:40]}")
        return 0
    if '--send' in sys.argv:
        print('⚠ 已启用真实发送模式（--send）')
    rows = L.listing(PLATFORM, status=L.PENDING, limit=5)
    if not rows:
        print('中转站没有待回复任务')
        return 0
    draft_tpl = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else ''
    done = 0
    with DeviceSession('SERIAL0001') as s:
        if not ensure_home(s):
            print('回不到首页，中止')
            return 1
        for _ in range(3):
            s.swipe('down', distance=1200, x=540, y=800)
            time.sleep(0.7)
        if not open_comment_area(s):
            print('打不开评论区，中止')
            return 1
        for _ in range(min(len(rows), 3)):
            # 直接用 claim 返回的任务，**不要**拿它和预先列出的行比对 id——
            # claim 的取单顺序和 listing 不同，比对会让每条都被 release 掉（11:00 踩过）。
            claimed = L.claim(PLATFORM, 'SERIAL0001')
            if not claimed:
                print('没有可领任务了')
                break
            draft = draft_tpl or f"{claimed['comment_author']}，谢谢你的留言～"
            print(f"处理 [{claimed['id'][:8]}] {claimed['comment_author']}")
            status = run_one(s, claimed, draft, do_send)
            L.finish(PLATFORM, claimed['id'], 'SERIAL0001', status,
                     reply_text=draft if status in (L.DRAFTED, L.REPLIED) else '')
            done += 1
    print('完成，状态统计：', L.stats(PLATFORM)['platforms'].get(PLATFORM, {}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
