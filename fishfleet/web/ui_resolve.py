# -*- coding: utf-8 -*-
"""界面自愈解析：元素树 → 视觉 OCR/LLM → 人工。

只在「新机接入模拟测试 / 自检」链路里启用，日常任务不走（主人定的规矩）。

四层证据链，每层命中都带 via 标签，报告里能看清是谁找到的：
    tree_exact  元素树里 text/desc/id 精确命中且唯一
    tree_fuzzy  元素树里包含命中（去空格、大小写、繁简近似）
    tree_spot   元素树按「区域 + 可点击 + 尺寸」启发（右上角按钮这类）
    vision      截图 + 多模态 LLM 读图定位（对自绘/WebView 界面的 OCR 等价物）

视觉层三道门禁（沿用 vision_locate）：置信度 <0.55、越界、与预期区域不符，
任一不过就返回 ok=False 交人工，绝不硬点。
"""
from __future__ import annotations

import json
import re
import subprocess
import time

MIN_CONF = 0.55
VISION_COOLDOWN = 6.0      # 同一目标两次视觉尝试的最小间隔，防刷模型

_LAST_VISION = {}

# 权限类弹窗：目标是「继续流程」，所以优先点允许
PERM_TEXTS = ('允许', '同意', '始终允许', '仅在使用中允许', '在使用中允许',
              'Allow', 'While using the app')
# 干扰类弹窗：退出优先（系统返回 → ✕/关闭 → 视觉找关闭）
CLOSE_TEXTS = ('关闭', '取消', '×', '✕', 'x', 'Close', '跳过', '以后再说',
               '稍后', '暂不', 'Not now', '知道了', '我知道了')
# 出现这些词基本可判定「屏幕上有弹窗/遮罩」
POPUP_HINTS = PERM_TEXTS + CLOSE_TEXTS + (
    '立即更新', '发现新版本', '去更新', '权限', '申请', '允许访问', '去设置',
    '开启通知', '开启定位', '广告', '福利', '推荐给', '青少年', '用户协议', '隐私')


def _adb():
    import os
    import shutil
    cands = [os.environ.get('ADB_PATH') or '',
             __import__('fishfleet.paths', fromlist=['adb_path']).adb_path(), 'adb']
    for c in cands:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    return 'adb'


def norm(text: str) -> str:
    """比对用的归一化：去空白、小写（中文不受影响）。"""
    return re.sub(r'\s+', '', str(text or '')).lower()


def dump(serial: str) -> list:
    """取当前界面元素树；失败抛 RuntimeError（调用方决定要不要转视觉）。"""
    try:
        from .mobile_gateway import call
        from .douyin_skill import elements
    except Exception as exc:  # pragma: no cover
        raise RuntimeError('元素树模块不可用：%s' % exc)
    try:
        return elements(call('mobile_list_elements_on_screen',
                             {'format': 'json'}, serial)) or []
    except Exception as exc:
        raise RuntimeError('读取元素树失败：%s' % exc)


def _box(node):
    c = (node or {}).get('coordinates') or {}
    try:
        x, y = float(c['x']), float(c['y'])
        w, h = float(c['width']), float(c['height'])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return {'x': x, 'y': y, 'w': w, 'h': h,
            'cx': int(x + w / 2), 'cy': int(y + h / 2)}


def _hay(node) -> str:
    return norm('%s %s %s' % (node.get('text') or '',
                              node.get('label') or node.get('desc') or '',
                              node.get('identifier') or node.get('id') or ''))


def _in_region(box, region, size):
    """region 用比例表达，例 {'xs':0.5,'xe':1,'ys':0,'ye':0.3}（左闭右开）。"""
    if not region or not box:
        return True
    w, h = size
    cy, cx = box['cy'], box['cx']
    if 'xs' in region and not (region['xs'] * w <= cx <= region['xe'] * w):
        return False
    if 'ys' in region and not (region['ys'] * h <= cy <= region['ye'] * h):
        return False
    return True


def match(nodes, names, region=None, size=None, need_clickable=False):
    """三层元素树匹配，返回 (hits, via)。hits 为 [(node, box)]。

    只返回「唯一」结果给上层自动点击；多于一个时 via 带 ambiguous 标记，
    由调用方决定是否放弃（宁可不点，也不瞎点）。
    """
    names = [norm(n) for n in names if str(n or '').strip()]
    if not names:
        return [], 'tree_empty'
    if size is None:
        size = (10 ** 6, 10 ** 6)

    def pick(exact):
        out = []
        for node in nodes:
            box = _box(node)
            if not box or not _in_region(box, region, size):
                continue
            if need_clickable and not (node.get('clickable') or node.get('clickable') is None):
                pass
            hay = _hay(node)
            for name in names:
                if (hay == name) if exact else (name and name in hay):
                    out.append((node, box))
                    break
        return out

    hits = pick(True)
    via = 'tree_exact'
    if not hits:
        hits = pick(False)
        via = 'tree_fuzzy'
    # 同一位置可能被父子节点重复命中：中心相距 16px 内算同一个元素
    uniq = []
    for node, box in hits:
        if any(abs(box['cx'] - u[1]['cx']) <= 16 and abs(box['cy'] - u[1]['cy']) <= 16
               for u in uniq):
            continue
        uniq.append((node, box))
    if len(uniq) > 1:
        via += '+ambiguous'
    return uniq, via


def _vision(serial, description, page_hint=''):
    """视觉层：截图 + 多模态 LLM。失败返回 None（不抛，交给人工）。"""
    try:
        from . import vision_locate as V
    except Exception:
        return None
    try:
        found = V.locate(serial, description, page_hint)
    except Exception:
        return None
    if not found.get('ok'):
        return None
    if float(found.get('confidence', 0)) < MIN_CONF:
        return None
    return found


def resolve(serial, names, page_hint='', region=None,
            allow_vision=True, size=None):
    """找元素，返回 {ok, x, y, via, confidence, target, node}。

    优先元素树（快、准、不花钱），树里确实没有才上图找。
    """
    if size is None:
        try:
            from . import ui_probe_targets as U
            geo = U.screen_profile(serial)
            size = (geo['width'], geo['height'])
        except Exception:
            size = (10 ** 6, 10 ** 6)
    nodes = []
    try:
        nodes = dump(serial)
    except RuntimeError:
        nodes = []
    hits, via = match(nodes, names, region=region, size=size)
    if len(hits) == 1:
        node, box = hits[0]
        return {'ok': True, 'x': box['cx'], 'y': box['cy'], 'via': via,
                'confidence': 1.0, 'target': names[0], 'node': node}

    # 树里没命中/歧义：先看看是不是被弹窗挡住了，清掉再试一次树
    if popup_hint(nodes):
        d = dismiss(serial)
        if d.get('ok'):
            try:
                nodes2 = dump(serial)
            except RuntimeError:
                nodes2 = []
            hits2, via2 = match(nodes2, names, region=region, size=size)
            if len(hits2) == 1:
                node, box = hits2[0]
                return {'ok': True, 'x': box['cx'], 'y': box['cy'],
                        'via': via2 + '+after_popup', 'confidence': 1.0,
                        'target': names[0], 'node': node}

    if not allow_vision:
        return {'ok': False, 'via': via, 'reason': '树里未唯一命中且已禁用视觉',
                'hits': len(hits)}

    # OCR 层：让视觉模型读屏上的文字清单，按文字匹配
    o = find_by_ocr(serial, names, region=region)
    if o.get('ok'):
        return o

    key = (serial, tuple(names), page_hint)
    now = time.time()
    if now - _LAST_VISION.get(key, 0) < VISION_COOLDOWN:
        return {'ok': False, 'via': 'vision_cooldown',
                'reason': '视觉尝试过于频繁，稍后再试'}
    _LAST_VISION[key] = now

    desc = '「%s」' % '」或「'.join(names)
    found = _vision(serial, '%s 这个可点击元素（按钮/图标/文字入口）' % desc,
                    page_hint)
    if not found:
        return {'ok': False, 'via': 'vision',
                'reason': '视觉未给出可信坐标（%s）' % desc}
    conf = float(found.get('confidence', 0) or 0)
    if conf < MIN_CONF:
        return {'ok': False, 'via': 'vision',
                'reason': '视觉置信度 %.2f 低于门限 %.2f' % (conf, MIN_CONF)}
    x, y = int(found['x']), int(found['y'])
    if region:
        w, h = size
        fake = {'cx': x, 'cy': y}
        if not _in_region(fake, region, (w, h)):
            return {'ok': False, 'via': 'vision',
                    'reason': '视觉命中点不在预期区域（x=%d,y=%d）' % (x, y)}
    return {'ok': True, 'x': x, 'y': y, 'via': 'vision',
            'confidence': float(found.get('confidence', 0)),
            'target': names[0], 'note': found.get('note', '')}


def describe(serial, question='这是什么页面？顶部标题和底部按钮分别是什么？'):
    """兜底分析：一次视觉调用，让模型描述当前页面（出错时立刻调用，快）。"""
    try:
        from . import vision_locate as V
    except Exception:
        return ''
    try:
        img_url, sw, sh = V.screenshot_b64(serial)
        from .server import _llm_chat
        system = ('你是手机界面分析器。用中文一句话说清：当前是什么页面、'
                  '关键按钮的文字与大致位置。不要编造。')
        content = [{'type': 'text', 'text': '%dx%d 屏幕。%s' % (sw, sh, question)},
                   {'type': 'image_url', 'image_url': {'url': img_url}}]
        ans = _llm_chat('auto', [{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': content}], 400)
        return (ans or '').strip()[:400]
    except Exception:
        return ''


def press_back(serial):
    p = subprocess.run([_adb(), '-s', serial, 'shell', 'input keyevent 4'],
                       capture_output=True, timeout=20)
    return p.returncode == 0


def popup_hint(nodes) -> str:
    """屏幕上像不像有弹窗：返回命中的提示词（空串表示没看出来）。"""
    for node in nodes or []:
        hay = '%s %s' % (node.get('text') or '',
                         node.get('label') or node.get('desc') or '')
        for word in POPUP_HINTS:
            if word and word in hay:
                return word
    return ''


def _tap_text(serial, nodes, words, region=None, size=None):
    hits, _via = match(nodes, words, region=region, size=size)
    if len(hits) != 1:
        return False
    node, box = hits[0]
    return tap_node(serial, node, box)


def find_close_icon(serial, nodes=None, size=None):
    """找弹窗的 ✕ 关闭按钮：多数是没文字的小图标，只能按「右上角 + 小尺寸」认。"""
    if nodes is None:
        try:
            nodes = dump(serial)
        except RuntimeError:
            return None
    if size is None:
        try:
            from . import ui_probe_targets as U
            geo = U.screen_profile(serial)
            size = (geo['width'], geo['height'])
        except Exception:
            size = (10 ** 6, 10 ** 6)
    w, h = size
    cands = []
    for node in nodes:
        b = _box(node)
        if not b:
            continue
        txt = str(node.get('text') or '').strip()
        desc = str(node.get('label') or node.get('desc') or '').strip()
        rid = str(node.get('identifier') or node.get('id') or '').lower()
        clickable = node.get('clickable') is True or 'button' in str(
            node.get('type') or '').lower() or rid.endswith('close')
        if not clickable and not desc and not txt:
            continue
        small = b['w'] <= w * 0.16 and b['h'] <= h * 0.09
        top_right = b['cx'] >= w * 0.62 and b['cy'] <= h * 0.42
        if not (small and top_right):
            continue
        score = 0
        if any(k in (txt + ' ' + desc).lower() for k in ('关闭', 'close', '✕', '×', 'x')):
            score += 3
        if rid.endswith('close') or 'close' in rid:
            score += 2
        # 越靠右上角越像 ✕
        score += 2 if (b['cx'] >= w * 0.82 and b['cy'] <= h * 0.22) else 0
        if not txt and not desc:
            score += 1          # 无文字图标：✕ 的典型特征
        cands.append((score, b['cx'], b['cy'], node, b))
    if not cands:
        return None
    cands.sort(key=lambda t: (-t[0], t[1] * -1, t[2]))
    score, cx, cy, node, b = cands[0]
    return {'ok': True, 'x': cx, 'y': cy, 'via': 'close_icon',
            'score': score, 'node': node}


def dismiss(serial, log=None, max_rounds=3, force=False):
    """弹窗兜底：✕ 图标优先 → 权限允许 → 系统返回 → 视觉找 ✕。

    force=True 时即使没识别到弹窗文字，也会尝试点右上角 ✕ 图标
    （页面识别失败但又被遮住时用）。
    """
    say = log or (lambda m: None)
    try:
        from . import ui_probe_targets as U
        geo = U.screen_profile(serial)
        size = (geo['width'], geo['height'])
    except Exception:
        size = (10 ** 6, 10 ** 6)

    for _round in range(max_rounds):
        try:
            nodes = dump(serial)
        except RuntimeError:
            return {'ok': False, 'how': '', 'reason': '读不到界面树'}
        word = popup_hint(nodes)

        # 1) 右上角 ✕ 图标（没文字也能认）——主人指出的那个
        close = find_close_icon(serial, nodes, size)
        if close and (word or force):
            tap_point(serial, close['x'], close['y'])
            time.sleep(0.7)
            say('弹窗兜底：点了右上角 ✕ 图标 (%d,%d)' % (close['x'], close['y']))
            force = False
            continue

        if not word:
            return {'ok': True, 'how': 'none', 'word': ''}

        # 2) 权限弹窗：点允许/同意，让流程继续
        if word in PERM_TEXTS:
            if _tap_text(serial, nodes, ['允许', '同意', '始终允许',
                                         '仅在使用中允许'], size=size):
                time.sleep(0.8)
                say('弹窗兜底：权限弹窗点「允许/同意」（命中词 %s）' % word)
                continue
        # 3) 系统返回
        press_back(serial)
        time.sleep(0.8)
        try:
            after = dump(serial)
        except RuntimeError:
            after = []
        if not popup_hint(after):
            say('弹窗兜底：系统返回键关掉了弹窗（命中词 %s）' % word)
            return {'ok': True, 'how': 'back', 'word': word}
        close = find_close_icon(serial, after, size)
        if close:
            tap_point(serial, close['x'], close['y'])
            time.sleep(0.7)
            say('弹窗兜底：返回无效，改点右上角 ✕ (%d,%d)'
                % (close['x'], close['y']))
            continue
        # 4) 视觉找 ✕
        found = _vision(serial,
                        '屏幕弹窗右上角的 ✕ 关闭按钮（通常是白色或灰色叉号图标）',
                        '弹窗')
        if found:
            tap_point(serial, found['x'], found['y'])
            time.sleep(0.8)
            say('弹窗兜底：视觉找到 ✕ 点 (%d,%d)' % (found['x'], found['y']))
            continue
        return {'ok': False, 'how': '', 'word': word,
                'reason': '✕/返回/视觉都没能清掉弹窗'}
    try:
        nodes = dump(serial)
    except RuntimeError:
        nodes = []
    left = popup_hint(nodes)
    return {'ok': not left, 'how': 'rounds', 'word': left}


def ocr_screen(serial, max_items=30):
    """OCR 层：让视觉模型把屏幕上的文字读成清单（文字 + 像素中心点）。

    本机没装本地 OCR 引擎，所以用多模态模型做识别；元素树读不到的自绘界面
    靠这一层。返回 [{'text','x','y'}]，失败给空列表。
    """
    try:
        from . import vision_locate as V
        from . import ui_probe_targets as U
        from .server import _llm_chat
    except Exception:
        return []
    try:
        img_url, sw, sh = V.screenshot_b64(serial)
        geo = U.screen_profile(serial)
        system = ('你是手机截图的 OCR。只输出一个 JSON 数组，不要解释不要 Markdown：'
                  '[{"text":"文字","x":0-1000整数,"y":0-1000整数}]。'
                  '按从上到下、从左到右，最多 %d 条；坐标是文字中心点。' % max_items)
        content = [{'type': 'text', 'text': '%dx%d 屏幕，读全部可见文字' % (sw, sh)},
                   {'type': 'image_url', 'image_url': {'url': img_url}}]
        ans = _llm_chat('auto', [{'role': 'system', 'content': system},
                                 {'role': 'user', 'content': content}], 700)
        items = _extract_list(ans or '')
        out = []
        for it in items[:max_items]:
            try:
                txt = str(it.get('text') or '').strip()
                if not txt:
                    continue
                out.append({'text': txt,
                            'x': int(round(float(it['x']) / 1000 * geo['width'])),
                            'y': int(round(float(it['y']) / 1000 * geo['height']))})
            except (KeyError, TypeError, ValueError):
                continue
        return out
    except Exception:
        return []


def _extract_list(text):
    """从模型回复里抠 JSON 数组（容忍代码块与前后废话）。"""
    if not text:
        return []
    cands = re.findall(r'```(?:json)?\s*(.+?)```', text, re.S)
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']' and depth:
            depth -= 1
            if depth == 0 and start is not None:
                cands.append(text[start:i + 1])
    for item in cands:
        try:
            v = json.loads(item)
        except (ValueError, TypeError):
            continue
        if isinstance(v, list) and all(isinstance(x, dict) for x in v):
            return v
    return []


def find_by_ocr(serial, names, region=None, items=None):
    """在 OCR 文字清单里找目标，返回 {ok,x,y,via,target}。"""
    names = [norm(n) for n in names if str(n or '').strip()]
    if not names:
        return {'ok': False, 'via': 'ocr', 'reason': '没有目标文案'}
    items = ocr_screen(serial) if items is None else items
    if not items:
        return {'ok': False, 'via': 'ocr', 'reason': 'OCR 没读到文字'}
    try:
        from . import ui_probe_targets as U
        geo = U.screen_profile(serial)
        size = (geo['width'], geo['height'])
    except Exception:
        size = (10 ** 6, 10 ** 6)
    hits = []
    for it in items:
        hay = norm(it['text'])
        if not any(n and n in hay for n in names):
            continue
        if region and not _in_region({'cx': it['x'], 'cy': it['y']}, region, size):
            continue
        hits.append(it)
    if len(hits) != 1:
        return {'ok': False, 'via': 'ocr',
                'reason': 'OCR 命中 %d 个（要求唯一）' % len(hits)}
    it = hits[0]
    return {'ok': True, 'x': it['x'], 'y': it['y'], 'via': 'ocr',
            'confidence': 0.8, 'target': it['text']}


def _right_column_numbers(items, size, key_x, key_y):
    """右侧竖排的纯数字（点赞/评论/收藏/分享计数），按 y 排序。"""
    w, h = size
    out = []
    for it in items:
        txt = str(it.get('text') or '').strip()
        if not re.fullmatch(r'\d{1,7}(?:\.\d)?\s*[万wWkK]?', txt):
            continue
        x, y = it[key_x], it[key_y]
        if x < w * 0.70 or not (h * 0.15 < y < h * 0.92):
            continue
        out.append({'text': txt, 'x': x, 'y': y})
    out.sort(key=lambda d: d['y'])
    return out


COMMENT_WORDS = (
    '评论', '評論', 'comment', 'comentario', 'commentaire', 'kommentar',
    'コメント', '댓글', 'bình luận', 'ความคิดเห็น', 'تعليق', 'komentar',
)

#: 底部输入条常见文案（点它也能进评论区）
INPUT_HINTS = ('写评论', '說點什麼', '说点什么', '留下你的评论', '善语结善缘',
               'say something', 'add a comment', 'add comment')


def _score_comment_candidate(txt, box, size, kind):
    """按证据打分：文案 > 位置 > 尺寸 > 邻近数字。不依赖单一形态。"""
    w, h = size
    low = (txt or '').lower()
    score, why = 0, []
    if any(k in low for k in COMMENT_WORDS):
        score += 40
        why.append('文案含评论')
    elif any(k in low for k in INPUT_HINTS):
        score += 35
        why.append('底部输入条')
    if kind == 'button':
        score += 15
        why.append('可点击按钮')
    if box['cx'] > w * 0.62:
        score += 15
        why.append('右侧竖排')
    elif box['cy'] > h * 0.82:
        score += 10
        why.append('底部输入区')
    if box['w'] <= w * 0.22 and box['h'] <= h * 0.10:
        score += 10
        why.append('图标尺寸')
    return score, why


def find_comment_entry(serial, nodes=None, log=None):
    """找回评论区的入口，返回**候选列表**（按可信度排序）。

    · ok=True 表示至少有一个候选；调用方逐个点击并自行验证是否真的打开评论区；
    · 全都没有才返回 ok=False，由调用方走 LLM 视觉兜底。
    """
    say = log or (lambda m: None)
    try:
        from . import ui_probe_targets as U
        geo = U.screen_profile(serial)
        size = (geo['width'], geo['height'])
    except Exception:
        size = (10 ** 6, 10 ** 6)
    if nodes is None:
        try:
            nodes = dump(serial)
        except RuntimeError:
            nodes = []

    cands = []
    for node in nodes:
        box = _box(node)
        if not box:
            continue
        txt = ' '.join(str(node.get(k) or '') for k in
                       ('text', 'label', 'desc')).strip()
        rid = str(node.get('identifier') or node.get('id') or '')
        kind = 'button' if (node.get('clickable') or 'utton' in
                            str(node.get('type') or '')) else 'node'
        score, why = _score_comment_candidate(txt, box, size, kind)
        if 'comment' in rid.lower():
            score += 20
            why.append('id含comment')
        if score <= 0:
            continue
        cands.append({'node': node, 'box': box, 'text': txt,
                      'score': score, 'why': '+'.join(why)})

    # 同位置去重（按钮节点 + 数字节点常常重叠）
    uniq, seen = [], set()
    for c in cands:
        key = (round(c['box']['cx'] / 24.0), round(c['box']['cy'] / 24.0))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    uniq.sort(key=lambda c: -c['score'])
    say('评论入口：候选 %d（去重后 %d），最高分 %s'
        % (len(cands), len(uniq), uniq[0]['score'] if uniq else 0))

    # 数字/OCR 辅助：右侧竖排数字，评论通常是"点赞下面那个"——只作为加分项
    try:
        items = [{'text': str(n.get('text') or ''), 'x': _box(n)['cx'], 'y': _box(n)['cy']}
                 for n in nodes if _box(n) and str(n.get('text') or '').strip()]
        nums = _right_column_numbers(items, size, 'x', 'y')
        for c in uniq:
            for n in nums:
                if 0 <= (n['y'] - c['box']['cy']) <= size[1] * 0.06 \
                        and abs(n['x'] - c['box']['cx']) < size[0] * 0.10:
                    c['score'] += 8
                    c['why'] += '+下方有数字'
                    break
        uniq.sort(key=lambda c: -c['score'])
    except Exception:
        pass

    out = []
    for c in uniq[:5]:
        say('  候选 %.0f分 %s (%.0f,%.0f) %s'
            % (c['score'], c['why'], c['box']['cx'], c['box']['cy'], c['text'][:14]))
        out.append({'x': c['box']['cx'], 'y': c['box']['cy'],
                    'score': c['score'], 'why': c['why'],
                    'text': c['text'][:30], 'node': c['node']})
    if out:
        return {'ok': True, 'via': 'scan', 'candidates': out,
                'x': out[0]['x'], 'y': out[0]['y'], 'target': '评论入口'}
    return {'ok': False, 'via': 'none', 'candidates': [],
            'reason': '页面里没扫到任何像评论入口的元素'}

def tap_point(serial, x, y):
    p = subprocess.run([_adb(), '-s', serial, 'shell',
                        'input tap %d %d' % (int(x), int(y))],
                       capture_output=True, timeout=30)
    return p.returncode == 0


def tap_node(serial, node, box=None):
    """优先用 MCP 的 ref 点（稳），拿不到 ref 才点坐标。"""
    ref = (node or {}).get('ref')
    if ref:
        try:
            from .mobile_gateway import call
            call('mobile_click_on_screen_at_coordinates', {'ref': ref}, serial)
            return True
        except Exception:
            pass
    box = box or _box(node or {})
    if not box:
        return False
    return tap_point(serial, box['cx'], box['cy'])


def heal_tap(serial, names, page_hint='', region=None, allow_vision=True,
             log=None, note=''):
    """自愈一步：找到就点，返回结果字典。log 是可选的日志回调。"""
    r = resolve(serial, names, page_hint=page_hint, region=region,
                allow_vision=allow_vision)
    if r.get('ok'):
        ok = tap_node(serial, r.get('node'), {'cx': r['x'], 'cy': r['y']})
        r['tapped'] = bool(ok)
        msg = '自愈命中「%s」（%s，置信 %.2f）→ 点 (%d,%d)' % (
            r.get('target', ''), r.get('via'), float(r.get('confidence') or 0),
            r['x'], r['y'])
    else:
        msg = '自愈未命中「%s」：%s' % ('/'.join(names), r.get('reason', ''))
    if note:
        msg = note + '：' + msg
    if log:
        try:
            log(msg)
        except Exception:
            pass
    r['message'] = msg
    return r
