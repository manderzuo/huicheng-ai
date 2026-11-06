"""私信「发送端」验证：**只填草稿、识别发送键，绝不点击发送**。

成功判定（主人定的）：
  1) 用 profile URL 能打开该人主页
  2) 能点开「發送訊息」进入私信会话
  3) 指定回复语**真的填进了输入框**（回读确认）
  4) 能识别到发送键
  **不点击发送**。

用法：
  python fishfleet/web/facebook_dm_send.py [条数]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web import lead_store as L  # noqa: E402
from fishfleet.web.facebook_live import DeviceSession, setup_utf8_stdout  # noqa: E402
from fishfleet.web.ui_probe_targets import resolve_point as _resolve  # noqa: E402

PLATFORM = 'facebook'
DRAFT = '您好，看到您的留言很有意思，想和您聊聊～'   # 中文母版（仅填入，不发送）


def localize(comment_text: str, draft_zh: str) -> dict:
    """按评论语种本地化草稿。和抖音侧同一套实现（draft_i18n.localize_draft）。

    2026-11-03 13:20 接入：项目里已有 `draft_i18n`（LLM 翻译 + 失败回填中文），
    抖音已用，FB 之前没接。这里保持一致：中文评论不动，外文才调 LLM。
    """
    try:
        from fishfleet.web import draft_i18n
        from fishfleet.web.llm_light import llm_call as _llm
        return draft_i18n.localize_draft(comment_text, draft_zh, _llm)
    except Exception as exc:
        return {'text': draft_zh, 'target_lang': '', 'fallback': True,
                'reason': 'llm_import_error:%s' % type(exc).__name__}


def _text(e: dict) -> str:
    return (e.get('label') or e.get('text') or '').strip()


def open_profile_by_url(s: DeviceSession, url: str) -> bool:
    """用 profile URL 直达该人主页。

    2026-11-05 实测：网关里 mobile_open_url 会 tool_error，改用 adb 发 fb:// intent：
      · 数字 id  → fb://profile/<id>
      · 用户名   → fb://facewebmodal/f?href=<URL 编码的原链接>
    落地判定沿用原逻辑（ImmersivePortraitActivity / 出现「發送訊息」）。
    """
    import re as _re
    import urllib.parse as _up
    try:
        from . import deeplink as _dl
    except ImportError:              # 作为 __main__ 直接跑时没有包上下文
        from fishfleet.web import deeplink as _dl

    cands = []
    m = _re.search(r'profile\.php\?id=(\d+)', url or '')
    if m:
        cands.append('fb://profile/%s' % m.group(1))
    if url:
        cands.append('fb://facewebmodal/f?href=%s' % _up.quote(url, safe=''))

    # 2026-11-06 修：清场/冷启动后 deeplink **会被丢掉** —— 实测连续 20 条
    # 全是「① 打开主页：False」（FB 还没起来，intent 打进了空的进程）。
    # 发 intent 之前先把 FB 拉起来并等到它真的在前台，否则后面等再久也没用。
    for _warm in range(3):
        try:
            fg = s.foreground()
            pkg = (fg.raw.split('/')[0] if fg and fg.raw else '')
        except Exception:
            pkg = ''
        if pkg == 'com.facebook.katana':
            break
        try:
            s._text_result('mobile_launch_app', {'packageName': 'com.facebook.katana'})
        except Exception:
            pass
        time.sleep(4.0)
    time.sleep(2.0)

    for link in cands:
        for _attempt in range(2):
            if _attempt:
                print('  deeplink 没落地，重试一次')
                time.sleep(3.0)
            _dl.adb(s.device, 'shell', 'am', 'start', '-a',
                    'android.intent.action.VIEW', '-d', link, '-p', 'com.facebook.katana')
            for _ in range(10):
                time.sleep(2.0)
                fg = s.foreground()
                activity = (fg.activity if fg else '') or ''
                els = s.dump()
                labels = {_text(x) for x in els}
                if (T.page_of(els, activity) == 'person_profile'
                        or 'ImmersivePortraitActivity' in activity
                        or '發送訊息' in labels):
                    return True
    return False
def is_locked_profile(els: list[dict]) -> bool:
    """判断对方是否锁了个人档案（只有好友能发消息）。

    2026-11-03 13:24 实测：这类主页有「XXX 鎖住了他的/她的個人檔案」，
    并且**没有「發送訊息」按钮**。属于对方隐私设置，不可绕过。
    """
    for e in els:
        t = _text(e)
        if '鎖住' in t and '個人' in t:
            return True
    return False


def dismiss_notice(s: DeviceSession) -> bool:
    """处理「商家聊天室的變動」等提示弹窗：点「確定」继续。

    2026-11-03 12:55 主人指出：有时进入私信会弹出商家聊天室说明，
    挡住输入框。这个弹窗是提示性质，点「確定」即可继续，不涉及对外动作。
    """
    for _ in range(3):
        els = s.dump()
        labels = {_text(e) for e in els}
        if not ({'商家聊天室的變動', '管理陌生訊息', '一切由你掌控'} & labels):
            return False
        btn = [e for e in els if _text(e) in ('確定', '确定', 'OK')]
        if not btn:
            return False
        print('  遇到「商家聊天室的變動」弹窗，点確定继续')
        s.tap_element(btn[0])
        time.sleep(2.0)
    return True


def open_dm_thread(s: DeviceSession) -> bool:
    """在主页点「發送訊息」，确认进入私信会话。

    2026-11-02 17:45 实测：公众人物/粉丝页（页判定是 `page_detail` 而不是 person_profile）
    也有「發送訊息」，但进入私信的过程更慢，10 秒内偶尔还没跳到 MsysThreadViewActivity。
    这里加长到 20 秒，并多重判断。
    """
    els = s.dump()
    btn = [e for e in els if _text(e) in ('發送訊息', 'Message')]
    if not btn:
        # 有的主页按钮在下面，滚一下再找
        for _ in range(3):
            s.swipe('up', distance=900, x=540, y=1300)
            time.sleep(1.3)
            els = s.dump()
            btn = [e for e in els if _text(e) in ('發送訊息', 'Message')]
            if btn:
                break
    if not btn:
        print('      [诊断] 主页上没有「發送訊息」按钮')
        return False
    s.tap_element(btn[0])
    for _ in range(10):
        time.sleep(2.0)
        dismiss_notice(s)
        fg = s.foreground()
        activity = (fg.activity if fg else '') or ''
        if 'MsysThreadViewActivity' in activity:
            return True
        els2 = s.dump()
        if any(_text(x) == '輸入訊息' for x in els2):
            return True
    print(f'      [诊断] 点了發送訊息但停在 {activity}')
    return False


def fill_and_detect(s: DeviceSession, draft: str) -> dict:
    """点开输入框 → 填草稿 → 回读 → 识别发送键（不点击）。"""
    out = {'input_found': False, 'filled': False, 'send_found': False, 'send_label': ''}
    dismiss_notice(s)
    els = s.dump()
    ed = [e for e in els if e['type'] in ('EditText', 'AutoCompleteTextView')]
    # 2026-11-03 12:50 实测：私信会话刚进来时输入框常常还没渲染出来，
    # 只点一次屏幕下部不够。这里**反复点 + 等**，最多 4 轮。
    for _ in range(4):
        if ed:
            break
        # 输入框未渲染时点屏幕下部唤起（注册表 dp 自适应）
        s.tap(*_resolve(s.device, 'facebook.dm.input_wake'))
        time.sleep(2.5)
        els = s.dump()
        ed = [e for e in els if e['type'] in ('EditText', 'AutoCompleteTextView')]
    if not ed:
        return out
    out['input_found'] = True
    s.tap_element(ed[0])
    time.sleep(1.0)
    if not s.type_anywhere(draft, submit=False):
        return out
    time.sleep(1.5)
    els3 = s.dump()
    out['filled'] = any(draft in ((e.get('text') or '') + (e.get('label') or ''))
                        for e in els3)
    snd = T.find('dm_thread', 'send', els3)
    if snd is None:
        # 兜底：按输入框位置就近找
        box = ed[0]
        for e in els3:
            t = _text(e)
            if t in ('發送👍', '傳送', '發送') and abs(e['y'] - box['y']) <= 200:
                snd = e
                break
    if snd is not None:
        out['send_found'] = True
        out['send_label'] = _text(snd)
    return out


def clear_draft(s: DeviceSession, n: int = 60) -> None:
    """清空草稿（不发送）。

    2026-11-05：调用方改成传「实际字数 + 余量」。原来固定按 60 次，
    而草稿通常只有 20 来字，每轮白按 40 下（实测 19 轮共 1140 次 BACKSPACE），
    既慢又容易把输入法状态搞乱。
    """
    n = max(1, min(int(n or 0), 200))
    for _ in range(n):
        s._text_result('mobile_press_button', {'button': 'BACKSPACE'})




def run_for_skill(serial, limit=20, task=None, batch=None):
    """给 skill 层用：**独立子进程**跑（进程内会卡设备通道，已实测）。

    batch：{'day': '2026-11-06', 'keyword': 'coffee'}，只回这一批线索；
           以 JSON 作为第 4 个命令行参数传给子进程。
    """
    import re as _re
    import subprocess
    cmd = [sys.executable, __file__, str(limit), str(serial)]
    if batch:
        cmd.append(json.dumps(batch, ensure_ascii=False))
    try:
        p2 = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                            errors='ignore', cwd=str(Path(__file__).resolve().parents[2]),
                            timeout=60 * 30)
        out = (p2.stdout or '') + (p2.stderr or '')
    except Exception as exc:          # noqa: BLE001
        out = ''
        if task is not None:
            try:
                from . import execution
                execution.log(task, '    FB 子进程异常：%s: %s'
                              % (type(exc).__name__, exc))
            except Exception:
                pass

    stats = {'tried': 0, 'ok': 0, 'fail': 0, 'locked': 0}
    m = _re.search(r'测试条数:\s*(\d+)', out)
    if m:
        stats['tried'] = int(m.group(1))
    m = _re.search(r'四项全中:\s*(\d+)', out)
    if m:
        stats['ok'] = int(m.group(1))
    stats['locked'] = out.count('⊘')
    stats['fail'] = max(0, stats['tried'] - stats['ok'] - stats['locked'])

    if task is not None:
        try:
            from . import execution
            for line in out.splitlines():
                t = line.strip()
                if t and ('①' in t or '②' in t or '③' in t or '④' in t
                          or '本次' in t or '译：' in t or '⊘' in t or '✗' in t):
                    execution.log(task, '    ' + t[:120])
        except Exception:
            pass
    return stats


LAST_STATS = {'tried': 0, 'ok': 0, 'fail': 0, 'locked': 0}


def main(serial='SERIAL0001', limit=None) -> int:
    setup_utf8_stdout()
    if limit is None:
        limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    if serial in (None, '', 'SERIAL0001') and len(sys.argv) > 2:
        serial = sys.argv[2]
    limit = max(1, int(limit))
    # 2026-11-06：第 4 个参数是批次（JSON），形如
    #   {"day":"2026-11-06","keyword":"coffee"}
    # 用于「选择任务再回复」——只回某一批采集到的线索。
    batch = None
    if len(sys.argv) > 3 and sys.argv[3].strip():
        try:
            batch = json.loads(sys.argv[3])
            print('  限定批次：%s' % batch)
        except Exception as exc:                          # noqa: BLE001
            print('  批次参数解析失败（忽略）：%s' % exc)
            batch = None
    # 2026-11-04 修：原来用 listing(PENDING) 只读不领，导致
    # ① 结果写不回中转站（record_extract 只对 claimed/sending 生效）；
    # ② 同一批用户会被反复重领。
    # 改成 claim 领单：领取即锁定，处理完写回结果。
    rows = []
    for _ in range(limit):
        try:
            r = L.claim(PLATFORM, serial,
                        require_nonempty=('commenter_profile_url',),
                        skip_done_accounts=False, newest_first=True,
                        batch=batch)
        except Exception as exc:
            print(f'领单失败：{type(exc).__name__}: {exc}')
            break
        if not r:
            break
        rows.append(r)
    if not rows:
        print('中转站没有待处理任务')
        return 1

    ok_all = 0
    locked_all = 0
    with DeviceSession(serial) as s:
        for i, row in enumerate(rows, 1):
            name = row['comment_author']
            url = row.get('commenter_profile_url') or ''
            print(f'\n===== [{i}/{len(rows)}] {name} =====')
            print(f'  URL: {url}')
            if not url:
                print('  ✗ 没有 profile URL，跳过')
                continue
            st = {'profile': False, 'thread': False, 'input': False,
                  'filled': False, 'send': False}
            st['profile'] = open_profile_by_url(s, url)
            print(f'  ① 打开主页：{st["profile"]}')
            if not st['profile']:
                try:
                    L.record_extract(PLATFORM, row['id'], 'SERIAL0001', 'error',
                                     {'stage': {'profile': False},
                                      'failure': 'open_profile_failed'}, 0.0)
                except Exception:
                    pass
                continue
            st['thread'] = open_dm_thread(s)
            print(f'  ② 进入私信：{st["thread"]}')
            if not st['thread']:
                try:
                    L.record_extract(PLATFORM, row['id'], 'SERIAL0001', 'error',
                                     {'stage': {'profile': True, 'thread': False},
                                      'failure': 'open_dm_thread_failed'}, 0.0)
                except Exception:
                    pass
                s.back()
                time.sleep(1.0)
                continue
            # 按评论语种本地化草稿（中文评论不动，外文调 LLM 翻译，失败回填中文）
            comment_text = row.get('comment_text') or ''
            loc = localize(comment_text, DRAFT)
            draft_to_use = loc.get('text') or DRAFT
            print(f'  译：lang={loc.get("target_lang") or "-"} '
                  f'fallback={loc.get("fallback")} reason={loc.get("reason")}')
            print(f'      草稿={draft_to_use[:50]!r}')

            res = fill_and_detect(s, draft_to_use)
            st['input'] = res['input_found']
            st['filled'] = res['filled']
            st['send'] = res['send_found']
            print(f'  ③ 输入框：{res["input_found"]}  填入：{res["filled"]}')
            print(f'  ④ 发送键：{res["send_found"]} {res["send_label"]!r}（**未点击**）')
            good = all(st.values())
            ok_all += 1 if good else 0
            print(f'  本次：{"通过" if good else "未通过"}')
            # 2026-11-04 修：原来跑完**不回写中转站**，导致失败用户没登记、
            # 下次还会被当成 pending 重新领取。这里把结果和证据落库。
            try:
                L.record_extract(
                    PLATFORM, row['id'], serial,
                    'found' if good else 'error',
                    {'stage': {k: bool(v) for k, v in st.items()},
                     'send_label': res.get('send_label'),
                     'lang': loc.get('target_lang'),
                     'fallback': loc.get('fallback'),
                     'reason': loc.get('reason')},
                    0.0)
            except Exception as _exc:
                print(f'  [warn] 回写中转站失败：{type(_exc).__name__}')
            # 清空草稿并退出，不留痕迹。
            # 2026-11-05 改：①按实际字数清（原来固定 60 次，白按一半以上）；
            # ②只退一层（离开会话页），第二层不再无条件按 ——
            #   实测 FB 在**首页**再按两次 BACK 会直接退出到桌面，
            #   之后下一轮只能重新拉起 App。回首页统一交给 ensure_home 判断。
            clear_draft(s, len(draft_to_use) + 5)
            time.sleep(0.8)
            # 只在「确实还在会话/消息页」时才按一次 BACK。
            # 2026-11-05 实测：无条件 back 一次，碰上本来就在首页的那一轮，
            # 这一按就**直接退出到桌面**（FB 首页再按两次就回桌面），
            # 下一轮又得重新拉起 App。
            try:
                fg = s.foreground()
                act = (fg.activity if fg else '') or ''
            except Exception:
                act = ''
            if ('MsysThreadViewActivity' in act or 'InboxActivity' in act
                    or 'messaging' in act.lower() or 'chat' in act.lower()):
                s.back()
                time.sleep(1.2)

    print()
    print('=' * 52)
    print(f'测试条数: {len(rows)}')
    print(f'四项全中: {ok_all}')
    if rows:
        print(f'成功率  : {ok_all / len(rows) * 100:.1f}%')
    print('**全程未点击发送**')
    LAST_STATS.update({'tried': len(rows), 'ok': ok_all,
                       'fail': max(0, len(rows) - ok_all - locked_all),
                       'locked': locked_all})
    return 0 if ok_all == len(rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
