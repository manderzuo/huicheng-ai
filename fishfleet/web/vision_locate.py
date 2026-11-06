# -*- coding: utf-8 -*-
"""视觉定位兜底（只在新机接入模拟测试里用，日常任务不走这里）。

触发条件：无障碍树里找不到预期元素（混淆 id/新版布局/特殊弹窗）。
流程：adb 截图 → 缩到 768 宽 → LLM 看图找元素中心（0-1000 坐标 + 置信度）
    → 换算像素 → 反算 dp → 门禁（置信度/越界/偏离过大一律转人工，绝不硬写）。
"""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import time

CONF_THRESHOLD = 0.6       # 置信度低于此不自动入库，转人工
MAX_DP_DRIFT = 60.0         # 与注册表默认差超过此转人工（防认错元素）
SHOT_MAX_W = 768


def _adb():
    import os
    cands = [os.environ.get('ADB_PATH') or '',
             __import__('fishfleet.paths', fromlist=['adb_path']).adb_path(), 'adb']
    import shutil
    for c in cands:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    return 'adb'


def screenshot_b64(serial: str, timeout: int = 20) -> str:
    """adb 截屏 → 缩放 → data URL（给多模态 LLM 用）。"""
    from PIL import Image
    p = subprocess.run([_adb(), '-s', serial, 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=timeout)
    raw = p.stdout or b''
    if len(raw) < 10000:
        raise RuntimeError('截屏失败（空数据）')
    img = Image.open(io.BytesIO(raw)).convert('RGB')
    if img.width > SHOT_MAX_W:
        img = img.resize((SHOT_MAX_W, int(img.height * SHOT_MAX_W / img.width)))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=72)
    return ('data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode(),
            img.width, img.height)


def _extract_json(text: str):
    """从模型回复里抠第一个 JSON 对象（容忍代码块/前后废话）。"""
    if not text:
        return None
    fenced = re.findall(r'```(?:json)?\s*(.+?)```', text, re.S)
    cands = list(fenced)
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}' and depth:
            depth -= 1
            if depth == 0 and start is not None:
                cands.append(text[start:i + 1])
    for item in cands:
        try:
            v = json.loads(item)
        except (ValueError, TypeError):
            continue
        if isinstance(v, dict) and 'x' in v and 'y' in v:
            return v
    return None


def locate(serial: str, description: str, page_hint: str = '',
          timeout: int = 150) -> dict:
    """让 LLM 在当前屏上找东西。返回 {ok, x, y, dp, confidence, note, raw}。

    description 例："抖音首页右上角放大镜搜索图标"。
    只定位不点击；入库由 onboard 按门禁决定。
    """
    from . import ui_probe_targets as U
    geo = U.screen_profile(serial)
    img_url, sw, sh = screenshot_b64(serial)
    try:
        from .server import _llm_chat
    except Exception as exc:
        raise RuntimeError('LLM 模块不可用：%s' % exc)
    system = ('你是手机 UI 定位器。只看图找元素中心，不编造。'
              '只输出一个 JSON 对象不要解释不要 Markdown：'
              '{"x":0-1000整数,"y":0-1000整数,"confidence":0-1,"note":"依据"}。'
              '找不到就 confidence 给 0。')
    user_text = ('当前手机屏幕（%dx%d，已缩放注意比例）：%s。'
                 '请给出该元素的中心点。' % (sw, sh, page_hint + '；' + description
                                              if page_hint else description))
    content = [{'type': 'text', 'text': user_text},
               {'type': 'image_url', 'image_url': {'url': img_url}}]
    answer = _llm_chat('auto', [{'role': 'system', 'content': system},
                                {'role': 'user', 'content': content}],
                       max(300, min(800, timeout * 4)))
    v = _extract_json(answer or '')
    if not v:
        return {'ok': False, 'reason': '模型未返回可用坐标', 'raw': (answer or '')[:200]}
    try:
        rx = max(0, min(1000, float(v['x'])))
        ry = max(0, min(1000, float(v['y'])))
        conf = max(0.0, min(1.0, float(v.get('confidence', 0))))
    except (TypeError, ValueError):
        return {'ok': False, 'reason': '坐标不是数字', 'raw': (answer or '')[:200]}
    x = int(round(rx / 1000 * geo['width']))
    y = int(round(ry / 1000 * geo['height']))
    return {'ok': True, 'x': x, 'y': y, 'confidence': round(conf, 3),
            'note': str(v.get('note', ''))[:100]}


def validate_and_convert(serial: str, key: str, found: dict) -> dict:
    """门禁：置信度/越界/漂移过大一律转人工；通过才给 dp。"""
    from . import ui_probe_targets as U
    t = U.TARGETS.get(key)
    if not t:
        return {'ok': False, 'reason': '未登记的坐标：%s' % key}
    if not found.get('ok'):
        return {'ok': False, 'reason': found.get('reason', '定位失败')}
    conf = float(found.get('confidence', 0))
    if conf < CONF_THRESHOLD:
        return {'ok': False, 'reason': '置信度 %.2f 低于门限（转人工）' % conf,
                'human_review': True, 'found': found}
    geo = U.screen_profile(serial)
    x, y = int(found['x']), int(found['y'])
    if not (0 <= x < geo['width'] and 0 <= y < geo['height']):
        return {'ok': False, 'reason': '坐标越界（转人工）',
                'human_review': True, 'found': found}
    dx, dy = U.point_to_anchor(geo, t['anchor'], x, y)
    base = t.get('dp') or (0.0, 0.0)
    drift = ((dx - float(base[0])) ** 2 + (dy - float(base[1])) ** 2) ** 0.5
    if drift > MAX_DP_DRIFT:
        return {'ok': False, 'reason': '漂移 %.1fdp 过大疑似认错元素（转人工）' % drift,
                'human_review': True, 'found': found,
                'dp_candidate': [dx, dy], 'dp_default': list(base)}
    return {'ok': True, 'x': x, 'y': y, 'dp': [dx, dy],
            'dp_default': list(base), 'drift': round(drift, 1),
            'confidence': conf, 'note': found.get('note', '')}
