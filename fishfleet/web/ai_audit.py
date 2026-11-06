# -*- coding: utf-8 -*-
"""AI 终审：三道兜底都失败之后，才由 AI 判定"脚本哪里错了、该怎么做"。

流程（主人 2026-11-05 定）：
  脚本自己的兜底链先跑完 ——
    ① id 路（tk1/50g/fpb/avatar…）
    ② 文案路（label 解析、文本正则、多形态打分）
    ③ 视觉路（截图 + LLM 找点、视觉修复）
  **三条都错**时，才调用本模块把"现场 + 脚本试过什么 + 想要什么"
  交给 AI，让它给一次终极判定：现场该点什么。

产出只有两种，都不覆盖脚本：
  · 改道动作（fix）：AI 给出应点的坐标/文本，由调用方执行一次；
  · 判定（verdict）：说清脚本错在哪，写进 JSONL 审计日志，
    作为"这台机器需要哪些修正"的素材，等人拍板才可能回写代码。
"""
from __future__ import annotations

import json
import os
import time

FORBIDDEN = ('关注', '发送', '付款', '删除', '转账', '红包', '快捷短语',
             '取关', '购买', '支付', '下单')
_PATH = {'p': ''}


def _log_path():
    if not _PATH['p']:
        _PATH['p'] = os.path.join(os.path.dirname(__file__), '..', '..',
                                  'reports', 'ai_verdict_%s.jsonl'
                                  % time.strftime('%Y%m%d'))
    return _PATH['p']


def record(row):
    """一条终审记录：脚本想干什么 / 试过什么 / AI 判什么 / 最终怎么做。"""
    try:
        with open(_log_path(), 'a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    except Exception:
        pass


VERDICT_SYSTEM = (
    '你是自动化脚本的终审官。脚本连着试了三条兜底路都失败了，'
    '现在把现场交给你：请判断脚本错在哪，并给出**一步能纠正局面的动作**。'
    '只输出一个 JSON 对象，不要解释不要 Markdown：'
    '{"verdict":"一句话说清脚本错在哪","action":null或'
    '{"type":"tap","x":0,"y":0}或{"type":"swipe"}或{"type":"back"}或'
    '{"type":"launch","package":"包名"},"confidence":0-1}。'
    '坐标用屏幕像素，只点你确认为目标的元素；踩红线（关注/发送/付款/删除/'
    '转账/红包/快捷短语）永不建议。说不出就 action=null。'
)


def judge(serial, *, wanted, tried, log=None, dump=None, screenshot=True,
          extra=None):
    """兜底耗尽后的终审。返回 {verdict, action, confidence, raw_ok}。

    永不抛异常：任何失败都返回 action=None，调用方按原逻辑处理。
    wanted: 脚本想达成什么（如"点进评论人主页"）。
    tried:  已经试过哪些路（列表，含失败原因），给 AI 看避免重复。
    """
    def say(m):
        try:
            if log:
                log(m)
        except Exception:
            pass

    out = {'verdict': '', 'action': None, 'confidence': 0.0, 'raw_ok': False}
    try:
        nodes = dump if dump is not None else _dump(serial)
        w, h = _size(serial)
        brief = [{'ref': n.get('ref'), 'text': str(n.get('text') or '')[:20],
                  'label': str(n.get('label') or n.get('desc') or '')[:20],
                  'x': (n.get('coordinates') or {}).get('x'),
                  'y': (n.get('coordinates') or {}).get('y'),
                  'w': (n.get('coordinates') or {}).get('width'),
                  'h': (n.get('coordinates') or {}).get('height')}
                 for n in (nodes or [])[:60]]
        payload = {'wanted': wanted, 'tried': list(tried or []),
                   'screen': {'w': w, 'h': h},
                   'activity': _activity(serial),
                   'tree_brief': brief}
        if extra:
            payload['context'] = extra
        content = [{'type': 'text',
                    'text': json.dumps(payload, ensure_ascii=False)}]
        if screenshot:
            try:
                from . import vision_locate as V
                img, sw, sh = V.screenshot_b64(serial)
                content.append({'type': 'image_url',
                                'image_url': {'url': img}})
                payload['shot_size'] = [sw, sh]
                content[0]['text'] = json.dumps(payload, ensure_ascii=False)
            except Exception:
                pass
        from .server import _llm_chat
        raw = _llm_chat('auto', [{'role': 'system', 'content': VERDICT_SYSTEM},
                                 {'role': 'user', 'content': content}], 500)
        verdict = _extract_json(raw or '')
        if not verdict:
            record({'at': time.time(), 'serial': serial, 'wanted': wanted,
                    'tried': tried, 'verdict': 'AI无有效输出',
                    'action': None, 'raw': (raw or '')[:200]})
            say('AI终审无有效输出')
            return out
        act = verdict.get('action')
        if isinstance(act, dict):
            t = str(act.get('type') or '')
            if t == 'tap':
                blob = json.dumps(act, ensure_ascii=False)
                if any(x in blob for x in FORBIDDEN):
                    act = None
                else:
                    try:
                        nx, ny = int(act.get('x')), int(act.get('y'))
                    except (TypeError, ValueError):
                        act = None
                    else:
                        if not (0 <= nx < w and 0 <= ny < h
                                and ny >= int(h * 0.05)):
                            act = None
            elif t not in ('swipe', 'back', 'launch'):
                act = None
        out.update(verdict=str(verdict.get('verdict') or '')[:300],
                   action=act if isinstance(act, dict) else None,
                   confidence=float(verdict.get('confidence') or 0),
                   raw_ok=True)
        record({'at': time.time(), 'serial': serial, 'wanted': wanted,
                'tried': tried, 'verdict': out['verdict'],
                'action': out['action'], 'confidence': out['confidence']})
        say('AI终审：%s%s' % (out['verdict'][:80],
                              '；给出动作 %s' % json.dumps(out['action'], ensure_ascii=False)
                              if out['action'] else '；无动作'))
        return out
    except Exception as exc:                       # noqa: BLE001 永不炸
        say('AI终审异常（按原逻辑处理）：%s' % type(exc).__name__)
        return out


def _dump(serial):
    try:
        from . import ui_resolve as UR
        return UR.dump(serial) or []
    except Exception:
        return []


def _size(serial):
    try:
        from . import ui_probe_targets as U
        g = U.screen_profile(serial)
        return int(g['width']), int(g['height'])
    except Exception:
        return 1080, 1920


def _activity(serial):
    try:
        from .mobile_gateway import call
        from .douyin_skill import foreground_activity
        return foreground_activity(call('mobile_get_foreground_app', {}, serial))
    except Exception:
        return ''


def _extract_json(text):
    depth, start = 0, None
    for i, ch in enumerate(text or ''):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}' and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    v = json.loads(text[start:i + 1])
                except ValueError:
                    continue
                if isinstance(v, dict):
                    return v
    return None
