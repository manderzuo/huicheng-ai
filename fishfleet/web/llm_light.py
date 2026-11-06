"""轻量 LLM 调用（urllib 实现）。

为什么单独抽出来：
项目正式入口是 `fishfleet.web.server._llm_chat`，但它依赖 fastapi/httpx，
在这台机器的 Python 环境里导入会失败（No module named 'fastapi'）。
`data/_test_i18n.py` 里早就有一份 urllib 复刻，本模块把它抽出来共用，
这样翻译功能（draft_i18n）在任何环境都能跑，不依赖 Web 服务。

配置：`data/llm.json`，形如
    {"auto": {"url": "...", "key": "...", "model": "..."}}
"""
from __future__ import annotations

import json
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LLM_CONFIG = ROOT / 'data' / 'llm.json'

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(LLM_CONFIG, encoding='utf-8') as fh:
                _cache = json.load(fh)
        except Exception:
            _cache = {}
    return _cache


def llm_call(kind: str, messages: list, max_tokens: int = 512) -> str:
    """调用 LLM，返回文本。配置缺失或请求失败都会抛异常，由调用方回退。"""
    whole = _load()
    cfg = whole.get(kind) or {}
    url = (cfg.get('url') or '').rstrip('/')
    key = cfg.get('key') or ''
    model = cfg.get('model') or ''
    if not url or not key:
        raise RuntimeError('LLM 未配置')

    payload = json.dumps({
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': 0.2,
        'stream': False,
    }).encode()
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + key,
        'x-opencode-session': str(uuid.uuid4()),
        'User-Agent': 'FishFleet/1.0 (+https://localhost)',
        'Accept': 'application/json',
    }
    req = urllib.request.Request(url + '/chat/completions', data=payload,
                                 headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode('utf-8', 'ignore'))
    return body.get('choices', [{}])[0].get('message', {}).get('content', '')


def llm_available() -> bool:
    cfg = (_load().get('auto') or {})
    return bool(cfg.get('url') and cfg.get('key'))
