"""Facebook 持续测试用的场景驱动脚本（只做可逆浏览）。

用法：
  python fishfleet/web/facebook_flow.py search "关键词"
  python fishfleet/web/facebook_flow.py dump
  python fishfleet/web/facebook_flow.py back
  python fishfleet/web/facebook_flow.py home

每次 dump 都会落盘到 huicheng-auto/data/facebook_dumps/*.txt，便于复盘与回归。

安全边界：本脚本**不会**点赞/评论/关注/加好友/发帖/发私信/安装/付款。
只做：读层级、读前台应用、点搜索、输入关键词、点结果、返回。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web.facebook_live import (  # noqa: E402
    DeviceSession, is_visible, parse_elements, setup_utf8_stdout, tap_with_gate)

DEVICE = 'SERIAL0001'
DUMP_DIR = Path(__file__).resolve().parents[2] / 'data' / 'facebook_dumps'


def save_dump(label: str, text: str, meta: dict) -> Path:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in label)[:40]
    path = DUMP_DIR / f'{stamp}_{safe}.txt'
    head = ('# facebook dump\n# time: %s\n# device: %s\n# meta: %s\n\n'
            % (datetime.now().isoformat(timespec='seconds'), DEVICE,
               json.dumps(meta, ensure_ascii=False)))
    path.write_text(head + text, encoding='utf-8')
    return path


def describe(els: list[dict], fg, limit: int = 60) -> None:
    """打印有语义的元素，方便人眼比对。"""
    print(f'前台：{fg.raw if fg else "?"}')
    print(f'元素数：{len(els)}')
    key = T.page_of(els, fg.activity if fg else '')
    print(f'判定页面：{key}')
    print('— 有文本/标签的元素 —')
    shown = 0
    for el in els:
        if not (el['label'] or el['text']):
            continue
        shown += 1
        if shown > limit:
            print(f'  … 其余省略（共 {sum(1 for e in els if e["label"] or e["text"])} 个）')
            break
        print(f'  {el["ref"]:<7} {el["type"]:<22} '
              f'{(el["label"] or el["text"])[:70]!r} '
              f'@({el["x"]},{el["y"]} {el["width"]}x{el["height"]})'
              + (' [disabled]' if el['disabled'] else '')
              + (' [selected]' if el['selected'] else ''))


def cmd_dump(s: DeviceSession, label: str = 'dump') -> None:
    time.sleep(0.6)
    text = s.dump_text()
    fg = s.foreground()
    els = parse_elements(text)
    describe(els, fg)
    p = save_dump(label, text, {'activity': fg.activity if fg else '',
                                'page': T.page_of(els, fg.activity if fg else '')})
    print(f'已保存：{p}')


def cmd_export(s: DeviceSession | None, out: str = '') -> None:
    """把页面/控件资料库导出成 JSON（与设备无关，不需要连手机）。"""
    target = Path(out) if out else DUMP_DIR.parent / 'facebook_ui_library.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    data = T.export_json()
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                      encoding='utf-8')
    sm = data['summary']
    print(f'已导出：{target}')
    print(f'页面 {sm["pages"]} 个 / 控件 {sm["targets"]} 个 / '
          f'send {sm["send_targets"]} / forbidden {sm["forbidden_targets"]}')
    print('注意：导出只含定位规则（正则/类型/风险），不含 ref、坐标或任何账号数据。')


def cmd_find_hashtag(s: DeviceSession, tries: int = 8) -> None:
    """在信息流里找带 `#话题` 的帖子。

    话题标签是 Button，label 就是 `#漲知識` 这种形式。
    找到后打印，由调用方决定是否点进话题页。
    """
    for i in range(1, tries + 1):
        els = s.dump()
        fg = s.foreground()
        key = T.page_of(els, fg.activity if fg else '')
        tags = [(e['type'], e['label'] or e['text'])
                for e in els if (e['label'] or e['text']).startswith('#')]
        print(f'第 {i} 屏：页面={key} 话题标签 {len(tags)} 个'
              + (' -> ' + ', '.join(t for _, t in tags[:4]) if tags else ''))
        if tags:
            print('找到话题标签，停止滑动')
            cmd_dump(s, 'found_hashtag')
            return
        s.swipe('up', distance=1400, x=540, y=1500)
        time.sleep(1.2)
    print(f'{tries} 屏内没有找到话题标签')


def cmd_find_photo_post(s: DeviceSession, tries: int = 6) -> None:
    """在信息流里下滑，找到一条「非视频」帖子就停下并报告。

    为什么要专门写：这个账号的信息流几乎全是视频帖，
    靠人工反复滑很难碰到图文帖，而验证 post_full 对图文帖是否成立需要一个样本。
    判断依据：出现 `N comments` / `Like button` 说明是帖子卡；
    同时没有 `Mute video` / `Toggle video sound` 说明不是视频。
    """
    for i in range(1, tries + 1):
        els = s.dump()
        fg = s.foreground()
        key = T.page_of(els, fg.activity if fg else '')
        blob = [(e['label'] or e['text']) for e in els]
        has_post = any((' comments' in b) or b.endswith('shares') for b in blob)
        has_video = any('Mute video' in b or 'Toggle video sound' in b for b in blob)
        print(f'第 {i} 屏：页面={key} 帖子={has_post} 视频={has_video}')
        if has_post and not has_video:
            print('找到非视频帖子，停止滑动')
            cmd_dump(s, 'found_photo_post')
            return
        s.swipe('up', distance=1400, x=540, y=1500)
        time.sleep(1.2)
    print(f'{tries} 屏内没有找到图文帖')


def cmd_top(s: DeviceSession, times: int = 3) -> None:
    """把当前页滚回顶部（首页顶栏隐藏时，判定会失效，必须先回到顶部）。"""
    for _ in range(max(1, times)):
        s.swipe('down', distance=1600, x=540, y=700)
        time.sleep(0.5)
    cmd_dump(s, 'after_top')


def cmd_type(s: DeviceSession, text: str, submit: bool = False) -> None:
    """输入文字：自动处理"输入框不在层级里"的情况。

    只用于可逆输入（发帖草稿、搜索词）。**绝不自动点发送/发布。**
    submit=True 只用于搜索框回车（会产生搜索记录，属主人已授权行为）。
    """
    els = s.dump()
    fg = s.foreground()
    key = T.page_of(els, fg.activity if fg else '')
    print(f'当前页：{key}')
    ok = s.type_anywhere(text, submit=submit)
    if not ok:
        print('找不到可输入控件且键盘未弹出；停止')
        return
    print(f'已输入（submit={submit}）')
    time.sleep(1.5 if submit else 1.2)
    cmd_dump(s, 'after_type')


def _safe_point(el: dict, els: list[dict]) -> tuple[int, int] | None:
    """在元素内部挑一个不落在任何 send/forbidden 控件上的点。

    来历：2026-11-01 21:02 点搜索结果条目中心时，那个坐标正好压在条目内的
    「加入」按钮上，把账号真实加进了一个公开社团。条目中心并不总是安全区，
    所以这里先算出所有高风险元素的矩形，再从"中心 → 左侧 1/4 → 左侧 1/8"
    里挑第一个不冲突的点。
    """
    rects = []
    for other in els:
        if other is el:
            continue
        if _risk_of(other):
            rects.append((other['x'], other['y'],
                          other['x'] + other['width'], other['y'] + other['height']))

    def hits_risky(x, y):
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in rects)

    mid_y = el['y'] + el['height'] // 2
    for frac in (2, 4, 8, 12):
        x = el['x'] + el['width'] // frac
        if not hits_risky(x, mid_y):
            return x, mid_y
    return None


def cmd_open(s: DeviceSession, page: str, target: str, index: int = 0) -> None:
    """按模板定位并点击一个控件，点前核对语义。

    只允许 risk=none 的控件；repeated 的取可见区域内的第 index 个。
    这是 2026-11-01 误触「加入」后的强制规范：不用坐标，只按元素 + 语义核对。
    """
    els = s.dump()
    fg = s.foreground()
    key = T.page_of(els, fg.activity if fg else '')
    if key != page:
        print(f'页面不符：期望 {page}，实际 {key}；停止')
        return
    t = T.PAGES[page].targets[target]
    if t.risk != T.RISK_NONE:
        print(f'拒绝：{page}.{target} 风险等级为 {t.risk}（send/forbidden 必须主人确认）')
        return
    hits = T.resolve(page, target, els)
    # 注意：screen_size() 返回 (宽, 高)。曾把它解包成 (h, w)，
    # 结果高度被当成 1080，整屏元素全被判"不可见"，白白拒绝点击。
    w, h = s.screen_size()
    w, h = w or 1080, h or 1920
    visible = [e for e in hits if is_visible(e, w, h)]
    print(f'命中 {len(hits)} 个，可见 {len(visible)} 个')
    if not visible:
        print('无可见命中；停止')
        return
    if not t.repeated and len(hits) != 1:
        print('非 repeated 控件但多命中；停止以免误点')
        return
    el = visible[min(index, len(visible) - 1)]
    expect = t.label_re or t.text_re
    # 先校验语义，再走统一闸门（风险检查 + 安全点）
    import re as _re
    fields = [f for f in (el.get('label', ''), el.get('text', '')) if f]
    if expect and not any(_re.search(expect, f) for f in fields):
        print(f'拒绝：元素语义 {fields!r} 不匹配 {expect!r}')
        return
    print(f'目标 {el["ref"]} 语义={(el["label"] or el["text"])[:60]!r}')
    if not tap_with_gate(s, el, els, _risk_of):
        return
    time.sleep(2.0)
    cmd_dump(s, f'after_open_{page}_{target}')


def _risk_of(el: dict) -> list[str]:
    """风险反查：委托给模板里的唯一实现。"""
    return T.risk_of_element(el)


def cmd_tap_label(s: DeviceSession, pattern: str, allow_send: bool = False) -> None:
    """按语义正则找唯一元素并点击；多命中或有风险词就拒绝。

    这是唯一允许的点击方式：先 dump、再按语义定位、点前核对。
    默认拒绝 send/forbidden 级控件；确实需要"只打开面板、不确认"时，
    由调用方显式加 --allow-send，并在事后立即关闭面板。
    """
    els = s.dump()
    fg = s.foreground()
    key = T.page_of(els, fg.activity if fg else '')
    print(f'当前页：{key}（{fg.raw if fg else "?"}）')
    hits = T.find_by_regex(pattern, els)
    # 同一个按钮常同时暴露 Button 与其内部 View，两者语义相同。
    # 优先取真正可点的容器，再要求唯一，避免把"同一控件的两层"当成两个控件。
    for typ in ('Button', 'ImageView', 'ViewGroup', 'LinearLayout', 'View', 'TextView'):
        layer = [h for h in hits if h['type'] == typ]
        if layer:
            hits = layer
            break
    print(f'命中 {len(hits)} 个：' + ', '.join((h['label'] or h['text'])[:30] for h in hits[:6]))
    # 0 个和多个是两种完全不同的失败，别混成一句"不是唯一命中"——
    # 那会把"根本没找到"误导成"找到了太多个"。
    if not hits:
        print('没有命中任何元素；换个更宽松的语义，或先 dump 看看当前页')
        return
    if len(hits) > 1:
        print('命中多个、无法确定点哪个；请给更精确的语义')
        return
    el = hits[0]
    risky = _risk_of(el)
    if risky and not allow_send:
        print('拒绝：该控件在模板里属于高风险 -> ' + ', '.join(risky))
        print('如确需只打开面板（不确认动作），加 --allow-send 并在之后立即关闭')
        return
    if risky:
        print('⚠ 高风险控件，按调用方授权继续：' + ', '.join(risky))
    print(f'点击 {el["ref"]} {el["type"]} {(el["label"] or el["text"])[:60]!r}')
    s.tap_verified(el, pattern)
    time.sleep(2.5)
    cmd_dump(s, 'after_tap_label')


def cmd_open_person(s: DeviceSession, index: int = 0) -> None:
    """在 People 搜索结果里点开一个人（只读：进入其个人主页）。

    People 条目的 label 格式不统一（`名字`、`名字, 现居地点`、`名字, 公司 员工 · 现居地点`），
    无法用一个稳定正则覆盖，因此这里用「整宽条目 Button」的几何+类型特征筛选，
    但点之前仍要求 label 非空且不是动作词（加朋友/加入/前往/是/否）。
    """
    els = s.dump()
    fg = s.foreground()
    key = T.page_of(els, fg.activity if fg else '')
    if key != 'search_results':
        print(f'不在搜索结果页（{key}）；停止')
        return
    banned = ('加朋友', '加入', '前往', '是', '否', '查看全部', 'Filters', 'Clear text')
    cands = [e for e in els
             if e['type'] == 'Button' and e['width'] >= 1000 and e['y'] >= 400
             and (e['label'] or e['text'])
             and not any(b in (e['label'] or e['text']) for b in banned)]
    print(f'候选条目 {len(cands)} 个')
    if not cands:
        print('没有候选；停止')
        return
    el = cands[min(index, len(cands) - 1)]
    name = el['label'] or el['text']
    print(f'点击 {el["ref"]} -> {name[:60]!r}')
    # 点前校验：label 必须非空，且确实是我们筛出来的那个名字
    s.tap_verified(el, '^' + __import__('re').escape(name[:20]))
    time.sleep(2.5)
    cmd_dump(s, 'after_open_person')


def cmd_search(s: DeviceSession, keyword: str) -> None:
    """从首页进入搜索并输入关键词（会写入账号搜索历史）。"""
    # 先回首页，保证入口稳定
    fg = s.foreground()
    els = s.dump()
    key = T.page_of(els, fg.activity if fg else '')
    if key != 'home':
        print(f'当前不在首页（{key}），先按返回键回到稳定位置')
        for _ in range(3):
            s.back()
            time.sleep(0.8)
            els = s.dump()
            fg = s.foreground()
            if T.page_of(els, fg.activity if fg else '') == 'home':
                break
    els = s.dump()
    entry = T.find('home', 'search', els)
    if entry is None:
        cands = T.matches('home', 'search', els)
        print(f'搜索入口不唯一：命中 {len(cands)} 个，停止以免点错')
        return
    print('点搜索入口 ref=%s' % entry['ref'])
    s.tap_element(entry)
    time.sleep(1.2)
    cmd_dump(s, 'search_empty')

    # 输入关键词
    els = s.dump()
    box = T.find('search', 'input', els)
    if box is None:
        print('找不到搜索输入框（可能键盘未就绪），停止输入')
        return
    s.tap_element(box)
    time.sleep(0.5)
    s.type_text(keyword, submit=True)
    time.sleep(2.5)
    cmd_dump(s, f'search_result_{keyword}')
    els = s.dump()
    fg = s.foreground()
    key = T.page_of(els, fg.activity if fg else '')
    print(f'搜索后判定页面：{key}（结果页若为 None，说明资料库缺这一页）')


def main() -> int:
    setup_utf8_stdout()
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == 'export':
        # 导出不碰设备，直接在当前进程完成
        cmd_export(None, sys.argv[2] if len(sys.argv) > 2 else '')
        return 0
    with DeviceSession(DEVICE) as s:
        if cmd == 'search':
            if len(sys.argv) < 3:
                print('用法：search "关键词"')
                return 2
            cmd_search(s, sys.argv[2])
        elif cmd == 'dump':
            cmd_dump(s, sys.argv[2] if len(sys.argv) > 2 else 'dump')
        elif cmd == 'open':
            if len(sys.argv) < 4:
                print('用法：open <page> <target> [index]')
                return 2
            idx = int(sys.argv[4]) if len(sys.argv) > 4 else 0
            cmd_open(s, sys.argv[2], sys.argv[3], idx)
        elif cmd == 'open_person':
            cmd_open_person(s, int(sys.argv[2]) if len(sys.argv) > 2 else 0)
        elif cmd == 'tap':
            if len(sys.argv) < 3:
                print('用法：tap <语义正则> [--allow-send]')
                return 2
            cmd_tap_label(s, sys.argv[2], allow_send='--allow-send' in sys.argv)
        elif cmd == 'type':
            if len(sys.argv) < 3:
                print('用法：type <文字>')
                return 2
            cmd_type(s, sys.argv[2], submit='--submit' in sys.argv)
        elif cmd == 'top':
            cmd_top(s, int(sys.argv[2]) if len(sys.argv) > 2 else 3)
        elif cmd == 'find_photo':
            cmd_find_photo_post(s, int(sys.argv[2]) if len(sys.argv) > 2 else 6)
        elif cmd == 'find_tag':
            cmd_find_hashtag(s, int(sys.argv[2]) if len(sys.argv) > 2 else 8)
        elif cmd == 'back':
            s.back()
            time.sleep(1)
            cmd_dump(s, 'after_back')
        elif cmd == 'home':
            s.back()
            time.sleep(1)
            cmd_dump(s, 'after_back')
        else:
            print(__doc__)
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
