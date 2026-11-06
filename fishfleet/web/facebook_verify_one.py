"""单条闭环验证：在**已经打开的评论区**里，完整走一遍

    解析评论 → 挑一条 → 点它的「回覆」→ 输入草稿 → 识别发送键 → **不点发送** → 关闭

成功判定（用户定义）：
  1) 评论数据能解析出来
  2) 能找到那条评论的「回覆」按钮并点开
  3) 草稿真的出现在输入框里（回读校验）
  4) 能识别到发送键（只识别、不点击）

用法：
  python fishfleet/web/facebook_verify_one.py            # 只在当前评论区验证
  python fishfleet/web/facebook_verify_one.py <作者>      # 指定作者
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web.facebook_comments import parse_comment_items  # noqa: E402
from fishfleet.web.facebook_live import DeviceSession, setup_utf8_stdout  # noqa: E402

_REPLY_BTN = re.compile(r'^對(.+?)留言的「?回[覆复]」?按鈕')
_DRAFT = '谢谢分享，学到了～'


def find_draft_box(els: list[dict]) -> dict | None:
    """找回复输入框。FB 的评论输入框通常没有固定 label，用几类特征一起找。"""
    for e in els:
        txt = (e.get('label') or e.get('text') or '').strip()
        if e['type'] in ('EditText', 'AutoCompleteTextView'):
            return e
        if e['type'] in ('Button', 'ViewGroup') and re.search(
                r'(回覆|回复|留言|写下|寫下|comment)', txt, re.I):
            if e['y'] > 1200:  # 输入区一般在屏幕下半部
                return e
    return None


def find_send_button(els: list[dict], box: dict | None = None) -> dict | None:
    """识别发送键（只识别，不点）。

    2026-11-02 12:10 实测：发送键**不在屏幕底部**（那里是系统导航栏），
    而是在输入框同一行的右侧。所以先按"输入框附近"找，找不到再全屏兜底。
    """
    cand_txt = ('發佈', '发布', '送出', '发送', '傳送', 'Post', 'Send', '回覆', '回复')
    near = []
    for e in els:
        txt = (e.get('label') or e.get('text') or '').strip()
        if not txt:
            continue
        if box is not None:
            if abs(e['y'] - box['y']) <= 120 and e['x'] >= box['x'] + box['width'] - 20:
                near.append(e)
        if e['type'] in ('Button', 'ImageView', 'ViewGroup') and txt in cand_txt:
            if box is not None and abs(e['y'] - box['y']) <= 120:
                return e
            if box is None and e['y'] > 1200:
                return e
    # 输入框右侧的按钮多半就是发送键（哪怕文案不是上面那几个）
    for e in near:
        txt = (e.get('label') or e.get('text') or '').strip()
        if e['type'] in ('Button', 'ImageView') and txt and len(txt) <= 8:
            return e
    return None


def run_once(s: DeviceSession, want: str = '', index: int = 0) -> dict:
    """跑一次完整闭环，返回四项结果。index 用于在同一评论区里换不同评论。"""
    ok = {'parsed': False, 'located': False, 'drafted': False, 'send_found': False}
    els = s.dump()
    items = parse_comment_items(els)
    print(f'① 解析评论：{len(items)} 条')
    if not items:
        print('   当前页不是评论区')
        return ok
    ok['parsed'] = True
    for it in items[:3]:
        print(f"   - {it['comment_author']}: {it['comment_text'][:34]}")

    target = None
    if want:
        for it in items:
            if it['comment_author'] == want:
                target = it
                break
    if target is None:
        target = items[index % len(items)]
    author = target['comment_author']
    print(f'② 目标评论：{author} / {target["comment_text"][:30]}')

    btn = None
    for e in els:
        m = _REPLY_BTN.match((e.get('label') or e.get('text') or '').strip())
        if m and m.group(1).strip() == author:
            btn = e
            break
    if btn is None:
        print('   找不到该评论的「回覆」按钮')
        return ok
    print(f'   点开回覆按钮：{btn["ref"]}')
    s.tap_element(btn)
    time.sleep(2.0)
    ok['located'] = True

    els2 = s.dump()
    box = find_draft_box(els2)
    if box is None:
        print('   找不到输入框，尝试直接输入')
        typed = s.type_anywhere(_DRAFT, submit=False)
    else:
        print(f'   输入框：{box["ref"]} ({box["type"]})')
        s.tap_element(box)
        time.sleep(0.8)
        typed = s.type_anywhere(_DRAFT, submit=False)
    if not typed:
        print('   草稿输入失败')
        return ok
    time.sleep(1.0)

    els3 = s.dump()
    found = any(_DRAFT in (e.get('text') or '') or _DRAFT in (e.get('label') or '')
                for e in els3)
    print(f'③ 草稿回读：{"出现" if found else "没找到"}')
    ok['drafted'] = found

    snd = find_send_button(els3, box)
    if snd is not None:
        print(f'④ 识别到发送键：{snd["ref"]} 「{(snd.get("label") or snd.get("text"))[:20]}」'
              f'（**未点击**）')
        ok['send_found'] = True
    else:
        print('④ 没识别到发送键')
    return ok


def main() -> int:
    setup_utf8_stdout()
    want = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else ''
    repeat = 1
    if '--repeat' in sys.argv:
        i = sys.argv.index('--repeat')
        repeat = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 10

    from fishfleet.web.facebook_reply import ensure_home, open_comment_area

    results = []
    with DeviceSession('SERIAL0001') as s:
        # **只打开一次评论区**：反复开关是这个流程里最不稳的环节
        # （实测 5/10~8/10 波动都出在这里）。批量时也是在一个帖子里连回复多条。
        if repeat > 1:
            if not open_comment_area(s):
                print('打不开评论区，中止')
                return 1
        for n in range(1, repeat + 1):
            print(f'\n===== 第 {n}/{repeat} 次 =====')
            ok = run_once(s, want, index=n - 1)
            good = all(ok.values())
            results.append(good)
            print(f'  本次：{"通过" if good else "未通过"} {ok}')
            # 只关掉输入框，不退出评论区
            s.back()
            time.sleep(1.0)

    passed = sum(1 for x in results if x)
    print(f'\n连续验证结果：{passed}/{len(results)} 次四项全中')
    if passed == len(results):
        print('判定：全部通过，可以跑量')
    else:
        print('判定：存在失败项，先别跑量')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
