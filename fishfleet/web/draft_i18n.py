"""私信草稿自动本地化：默认开启、无人工干预、失败回填中文。

挂载点：extract_one 拿到 wanted（中文母版）之后、douyin_dm.draft 之前。
采集 render_draft 不变，继续产出中文母版；语种完全跟评论走，
不设人工强制目标语种。

失败一律回退中文母版，不抛错、不重试、不阻塞提取流程，
只在 evidence['i18n'] 留痕。
"""
from __future__ import annotations

import json
import re
import urllib.request
import uuid
from pathlib import Path

#: LLM 配置：与 server.LLM_CONFIG_PATH 指向同一个文件（huicheng-auto/data/llm.json）
LLM_CONFIG_PATH = Path(__file__).resolve().parents[2] / 'data' / 'llm.json'

#: 中文字符区间：CJK 统一表意文字 + 扩展 + 兼容 + 注音/假名不算中文。
_CJK_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002a6df]')
#: 必须原样保留的片段：@handle、链接、数字金额时间、话题标签。
_KEEP_RE = re.compile(r'(@[\w.]+|https?://\S+|www\.\S+|#\S+|\d[\d.,:%\-/]*\d|\d+)')


def _strip_keep(text: str) -> str:
    """去掉必须保留片段后剩下的可判定语种的正文。"""
    return _KEEP_RE.sub(' ', text or '')


def _cjk_ratio(text: str) -> float:
    body = _strip_keep(text)
    chars = [c for c in body if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _CJK_RE.match(c)) / len(chars)


def classify_comment(comment_text: str) -> str:
    """三态判定：'zh' 中文主体 / 'unknown' 太短或无字母无法判定 / 'foreign' 外文。

    短评论、纯表情、纯数字链接 → 'unknown'（回填中文，绝不猜）。

    ★阈值 0.35 而不是 0.5：实测「这个多少钱 how much」这种中英混写，
    中文字符占比约 0.38，按 0.5 会误判成外文、白翻一遍。
    只要中文占到三分之一以上就按中文主体处理（保守方向：宁可少翻）。
    """
    body = _strip_keep(comment_text)
    chars = [c for c in body if not c.isspace()]
    # 2026-11-03 15:50 修：原来是 4，导致孟加拉语/泰语等短评论被判 unknown
    # （实测 `রনি ভাই - Rony Vai` 回填了中文，等于给不懂中文的人发中文）。
    # 降到 2：两个字母就足够判断语种，纯表情/纯数字仍然进 unknown。
    if len(chars) < 2:
        return 'unknown'
    # 任何语言的字母都算（isalpha 覆盖拉丁/西里尔/阿拉伯/孟加拉/泰/天城文等）
    letters = [c for c in chars if c.isalpha()]
    if len(letters) < 2:
        return 'unknown'
    # 中文字符占比达到三分之一以上 → 视为中文主体，不翻译
    if _cjk_ratio(comment_text) >= 0.35:
        return 'zh'
    return 'foreign'


def needs_translation(comment_text: str) -> bool:
    """纯规则预判：只有明确外文才调 LLM；中文主体直接填中文。"""
    return classify_comment(comment_text) == 'foreign'


def _extract_first_json(text: str) -> str:
    """LLM 常带围栏/前后废话：只取第一个完整 JSON 对象。"""
    if not text:
        return ''
    start = text.find('{')
    if start < 0:
        return ''
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return ''


def _kept_tokens(text: str) -> set:
    return set(_KEEP_RE.findall(text or ''))


def _keeps_intact(source: str, translated: str) -> bool:
    """译文必须完整保留 @/链接/数字/标签，少一个就判失败。"""
    return _kept_tokens(source) <= _kept_tokens(translated)


SYSTEM_PROMPT = (
    '你是私信草稿翻译器。只做一件事：把中文母版草稿翻译成评论的语种。'
    '只返回一个 JSON 对象，不要 Markdown：{"target_lang":"en","text":"..."}。'
    'target_lang 用 ISO 639-1 小写代码（en/es/fr/de/ja/ko/ru/ar/pt/it 等），中文填 zh。'
    '规则：只翻译不发挥，不加称呼不加结尾不加 emoji；'
    '昵称、@handle、数字、金额、时间、链接、#标签原样保留，人名地名不硬翻；'
    '评论是中文（含中英混写但主体中文）时 target_lang 填 zh 且 text 原样返回。'
)


def default_llm_call(kind: str, messages: list, max_tokens: int = 512) -> str:
    """自带 LLM 调用（urllib 实现），让翻译在任何环境都能用。

    为什么不直接用 server._llm_chat：server.py 依赖 fastapi，
    脱离 Web 服务（命令行跑采集/回复）时导入会失败。
    这里复刻它的请求细节——网关要求 x-opencode-session 与 User-Agent，
    缺了会返回 400 MissingSessionID / 403 error code 1010。
    """
    with open(LLM_CONFIG_PATH, encoding='utf-8') as fh:
        whole = json.load(fh)
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


def localize_draft(comment_text: str, draft_zh: str, llm_call=None,
                   timeout_note: str = '') -> dict:
    """返回 {'text', 'target_lang', 'fallback', 'reason'}。

    fallback=True 表示回填中文母版。任何异常、超时、坏格式、
    保留片段丢失都走回退，绝不抛错。
    llm_call 省略时用 default_llm_call（自带 urllib 实现）。
    """
    if llm_call is None:
        llm_call = default_llm_call
    draft_zh = str(draft_zh or '').strip()
    comment_text = str(comment_text or '')
    if not draft_zh:
        return {'text': '', 'target_lang': '', 'fallback': True,
                'reason': 'empty_draft'}
    kind = classify_comment(comment_text)
    if kind == 'zh':
        return {'text': draft_zh, 'target_lang': 'zh', 'fallback': False,
                'reason': 'source_is_zh'}
    if kind == 'unknown':
        return {'text': draft_zh, 'target_lang': 'zh', 'fallback': True,
                'reason': 'source_unknown'}
    try:
        raw = llm_call('auto', [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': json.dumps({
                'comment': comment_text[:500],
                'draft_zh': draft_zh,
            }, ensure_ascii=False)},
        ], 600)
    except Exception as exc:
        return {'text': draft_zh, 'target_lang': '', 'fallback': True,
                'reason': 'llm_error:%s%s' % (type(exc).__name__, timeout_note)}
    try:
        data = json.loads(_extract_first_json(str(raw)))
    except Exception:
        return {'text': draft_zh, 'target_lang': '', 'fallback': True,
                'reason': 'bad_json'}
    lang = str(data.get('target_lang') or '').strip().lower()
    text = str(data.get('text') or '').strip()
    if not text or not re.fullmatch(r'[a-z]{2,3}', lang or ''):
        return {'text': draft_zh, 'target_lang': '', 'fallback': True,
                'reason': 'bad_shape'}
    if lang == 'zh':
        return {'text': draft_zh, 'target_lang': 'zh', 'fallback': False,
                'reason': 'llm_says_zh'}
    # 该译但和中文一字不差 → 模型偷懒，回退
    if text == draft_zh:
        return {'text': draft_zh, 'target_lang': '', 'fallback': True,
                'reason': 'identical_to_source'}
    if not _keeps_intact(draft_zh, text):
        return {'text': draft_zh, 'target_lang': '', 'fallback': True,
                'reason': 'kept_tokens_lost'}
    return {'text': text, 'target_lang': lang, 'fallback': False,
            'reason': 'ok'}
