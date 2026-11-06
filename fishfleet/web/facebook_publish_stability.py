"""发帖「进入待发布状态」稳定性测试：连续 N 次，每次都不留草稿。

每次完整走一遍：
    首页 → 发帖入口 → ComposerActivity
    → 圖庫 → MediaPickerActivity → 选中影片 → 繼續
    → 回到 ComposerActivity → 下一步
    → ComposerPreviewActivity → **识别到「發佈」键**
    → 捨棄貼文，退出（不留草稿）

判定成功 = 到达预览页且识别到「發佈」键。
**全程不点「發佈」**，不发布任何内容。

用法：
  python fishfleet/web/facebook_publish_stability.py [次数]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web.facebook_live import DeviceSession, setup_utf8_stdout  # noqa: E402
from fishfleet.web.facebook_reply import ensure_home  # noqa: E402

CAPTION = '稳定性测试，请忽略'


def _text(e: dict) -> str:
    return (e.get('label') or e.get('text') or '').strip()


def _act(s: DeviceSession) -> str:
    fg = s.foreground()
    return (fg.activity if fg else '') or ''


def open_editor(s: DeviceSession) -> bool:
    ent = T.find('home', 'composer', s.dump())
    if ent is None:
        return False
    s.tap_element(ent)
    for _ in range(6):
        time.sleep(2.0)
        if 'composer.activity.ComposerActivity' in _act(s):
            return True
    return False


def pick_video(s: DeviceSession) -> bool:
    """圖庫 → 选第一个影片 → 繼續。"""
    els = s.dump()
    gal = [e for e in els if _text(e) in ('圖庫', '相片/影片', 'Photo/Video')]
    if not gal:
        return False
    s.tap_element(gal[0])
    time.sleep(4.0)

    # 可能弹权限，逐层点掉
    for _ in range(4):
        els = s.dump()
        labels = {_text(e) for e in els}
        if '允許存取' in labels:
            btn = [e for e in els if _text(e) == '允許存取']
            s.tap_element(btn[0]); time.sleep(2.5); continue
        if '允許' in labels and '不允許' in labels:
            btn = [e for e in els if _text(e) == '允許']
            s.tap_element(btn[0]); time.sleep(2.5); continue
        break

    # 选第一个影片
    els = s.dump()
    vids = [e for e in els if _text(e).startswith('影片，項目1')]
    if not vids:
        return False
    img = [v for v in vids if v['type'] == 'ImageView'] or vids
    s.tap_element(img[0])
    time.sleep(3.5)

    els = s.dump()
    go = [e for e in els if _text(e) == '繼續']
    if not go:
        return False
    s.tap_element(go[0])
    for _ in range(6):
        time.sleep(2.0)
        if 'composer.activity.ComposerActivity' in _act(s):
            return True
    return False


def type_caption(s: DeviceSession, text: str) -> bool:
    s.type_anywhere(text, submit=False)
    time.sleep(1.2)
    return any(text[:8] in ((e.get('text') or '') + (e.get('label') or ''))
               for e in s.dump())


def go_preview(s: DeviceSession) -> bool:
    els = s.dump()
    nxt = [e for e in els if _text(e) == '下一步']
    if not nxt:
        return False
    s.tap_element(nxt[0])
    for _ in range(6):
        time.sleep(2.0)
        if 'ComposerPreviewActivity' in _act(s):
            return True
    return False


def find_publish(s: DeviceSession):
    for e in s.dump():
        if _text(e) == '發佈':
            return e
    return None


def discard_all(s: DeviceSession) -> bool:
    """退出并捨棄，不留草稿。"""
    for _ in range(3):
        els = s.dump()
        if 'ComposerPreviewActivity' in _act(s):
            s.back(); time.sleep(2.0); continue
        close = [e for e in els if _text(e) == '關閉']
        if close:
            s.tap_element(close[0]); time.sleep(2.5)
            els = s.dump()
        dis = [e for e in els if _text(e) in ('捨棄貼文', '丟棄貼文')]
        if dis:
            s.tap_element(dis[0]); time.sleep(2.5)
            return True
        if 'FbMainTabActivity' in _act(s):
            return True
        s.back(); time.sleep(1.5)
    return False


def main() -> int:
    setup_utf8_stdout()
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    ok_n = 0
    results = []

    with DeviceSession('SERIAL0001') as s:
        for i in range(1, rounds + 1):
            print(f'\n===== 第 {i}/{rounds} 次 =====')
            step = {'editor': False, 'video': False, 'caption': False,
                    'preview': False, 'publish_btn': False, 'discarded': False}
            if not ensure_home(s):
                print('  ✗ 回不到首页')
                results.append(False)
                continue
            step['editor'] = open_editor(s)
            if step['editor']:
                step['video'] = pick_video(s)
            if step['video']:
                step['caption'] = type_caption(s, CAPTION)
            if step['caption']:
                step['preview'] = go_preview(s)
            if step['preview']:
                step['publish_btn'] = find_publish(s) is not None
            step['discarded'] = discard_all(s)
            good = step['editor'] and step['video'] and step['preview'] and step['publish_btn']
            ok_n += 1 if good else 0
            results.append(good)
            print(f'  编辑器={step["editor"]} 选视频={step["video"]} 文案={step["caption"]} '
                  f'预览页={step["preview"]} 發佈键={step["publish_btn"]} 已捨棄={step["discarded"]}')
            print(f'  本次：{"通过" if good else "未通过"}')
            time.sleep(1.5)

    print()
    print('=' * 56)
    print(f'测试次数   : {rounds}')
    print(f'成功次数   : {ok_n}')
    print(f'成功率     : {ok_n / rounds * 100:.1f}%')
    print('**全程未点「發佈」，未发布任何内容**')
    return 0 if ok_n == rounds else 1


if __name__ == '__main__':
    raise SystemExit(main())
