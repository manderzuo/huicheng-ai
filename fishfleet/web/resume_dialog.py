"""残留编辑弹窗清理：App 重启后常见「是否继续编辑上次内容？」模态框。

主人 2026-11-05 定的处理顺序（不要点「存草稿」「去编辑」，那会写脏数据
或直接进入编辑流程）：

    1. 系统后退键（BACK）
    2. 弹窗右上角的 ❌（label/文本为 关闭 / 取消 / × / ✕）

典型来源：上一轮视频发布被直接中断（没选"不存草稿"），下次进入小红书时
会弹「继续编辑」，下面两个选项是「存草稿」「去编辑」。这个弹窗**盖住整屏
并吃掉所有点击**，不清理就会卡在"进不去搜索页"。

只做两件事：找到关闭入口、点它。找不到就退回 BACK，绝不点选项本身。
"""
from __future__ import annotations

#: 出现这些词就认为可能是「继续编辑」弹窗（选项 + 标题）
MARKERS = ('继续编辑', '存草稿', '保存草稿', '去编辑', '上次编辑')

#: 关闭入口的常见文案
CLOSE_LABELS = ('关闭', '取消', '×', '✕', 'x', 'X', '关闭按钮')


def _blob(nodes) -> str:
    return ' '.join('%s %s' % (n.get('text') or '', n.get('label') or '')
                    for n in (nodes or []))


def looks_like_dialog(nodes) -> bool:
    blob = _blob(nodes)
    return any(m in blob for m in MARKERS)


def _close_candidates(nodes):
    """挑出可能是「×」的元素，**右上角优先**。

    2026-11-06 实测（3号机 SERIAL0001，小红书 9.4.0）：
      · 弹窗是「继续编辑视频笔记吗？」+ [存草稿][去编辑]，× 在**预览图右上角**；
      · 原生 BACK（adb keyevent 4）按下去**弹窗纹丝不动**，只能点 ×；
      · 实测 × 落在 (828, 496)（1080x1920），按下去立刻就关掉了；
      · 原来的排序（高度最小 → y → x）会把弹窗里那些**更小的装饰图标**
        排在 × 前面，连点 @e41/@e42/@e43 都不生效 —— 30 多轮空转后放弃。
    现在改成按「屏幕右上角」优先：先看中心点是否落在右上象限，再看靠右靠上。
    """
    nodes = [n for n in (nodes or []) if n.get('coordinates')]
    if not nodes:
        return []
    right = max((n['coordinates'].get('x', 0) + n['coordinates'].get('width', 0))
                for n in nodes) or 1080
    bottom = max((n['coordinates'].get('y', 0) + n['coordinates'].get('height', 0))
                 for n in nodes) or 1920

    out = []
    for n in nodes:
        text = (n.get('text') or '').strip()
        label = (n.get('label') or '').strip()
        box = n['coordinates']
        w, h = box.get('width', 0), box.get('height', 0)
        if w <= 0 or h <= 0:
            continue
        cx = box.get('x', 0) + w / 2.0
        cy = box.get('y', 0) + h / 2.0
        # 文字/标签直接叫「关闭/取消」的，无条件收
        if text in CLOSE_LABELS or label in CLOSE_LABELS:
            out.append(n)
            continue
        # 纯图标：无文字 + 可点（MCP 里能点的必有 ref）+ 尺寸像按钮
        clickable = n.get('clickable') or bool(n.get('ref'))
        if not text and not label and clickable and 30 <= w <= 140 and 30 <= h <= 140:
            # 只在右上区域里找（× 一定在弹窗右上），避免点到返回键/菜单
            if cx > right * 0.60 and cy < bottom * 0.55:
                out.append(n)

    # 右上角优先：越靠右、越靠上越优先
    out.sort(key=lambda n: (-(n['coordinates'].get('x', 0) + n['coordinates'].get('width', 0)),
                            n['coordinates'].get('y', 0)))
    return out


def dismiss(read_tree, press, tap, pause, log=None, max_rounds=3) -> bool:
    """尝试清掉弹窗。返回 True 表示确实见到并处理过。

    read_tree()      -> 当前元素列表
    press('BACK')    -> 按系统返回键
    tap(node)        -> 点某个元素（调用方负责用 ref 或坐标）
    pause(seconds)   -> 等待

    **清理顺序按主人 2026-11-05 定的来：1. 系统后退键  2. 右上角 ×**
    之前的实现先点 ×、BACK 只当兜底，结果 × 点不中（那是个纯图标，外层可点、
    内层不响应）时，日志连打十几次"点右上角关闭"、弹窗纹丝不动，
    主人一眼看出"兜底没了"。现在恢复 BACK 优先，× 只作为 BACK 无效后的第二手段。
    """
    say = log or (lambda m: None)
    handled = False
    tried = set()
    for i in range(max_rounds):
        nodes = read_tree() or []
        if not looks_like_dialog(nodes):
            break
        handled = True
        # ① 先按系统后退键（主人经验：这个弹窗 BACK 就能关）
        say('残留编辑弹窗：按后退键')
        press('BACK')
        pause(1.3)
        if not looks_like_dialog(read_tree() or []):
            return True
        # ② BACK 没关掉，再点右上角 ×
        cands = [c for c in _close_candidates(nodes)
                 if (c.get('ref') or c.get('identifier') or '') not in tried]
        if not cands:
            continue
        cand = cands[0]
        tried.add(cand.get('ref') or cand.get('identifier') or '')
        say('残留编辑弹窗：点右上角关闭')
        try:
            tap(cand)
            pause(1.2)
            # 点完立刻验证：还在就继续（下一轮换下一个候选）
            if not looks_like_dialog(read_tree() or []):
                return True
        except Exception as exc:      # noqa: BLE001
            say('关闭按钮点击失败（%s）' % exc)
    return handled
