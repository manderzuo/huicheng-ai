"""小红书截流：A 端采集 → 生成可重定位指纹 → B 端凭指纹去回复/私信。

设计前提（2026-10-31 实测，手机3 / v.9.4.0.8a83615）：

1. **唯一键必须是「小红书号」，绝不能用昵称。**
   实测搜「隔壁小马」返回 4 个以上同名账号；搜 `95288842605` 只命中 1 个，并提示「无更多用户」。
   昵称只用于人工核对，任何靠昵称匹配的判定一律标为不可信。

2. **标题不能当主键。** 实测同一篇笔记标题从 `(3/100)` 变成 `(14/100)`，作者会改标题。

3. **小红书的用户搜索结果直接显示「小红书号：xxx」**，比抖音强（抖音列表读不到，只能逐个进主页）。
   所以本方案可以在用户列表批量核对唯一 ID。

4. **评论区没有汇总 label**，昵称/正文/「时间 地区 回复」是三个独立 TextView，
   只能按容器与 y 坐标关联（抖音记录里也是同样的结论）。

与抖音版的关系：流程骨架沿用 douyin_lead（关键词循环 → 筛选 → 逐个笔记 → 读评论 → 判定 → 冷却），
但产出物从「草稿」升级为「**可重定位指纹**」，让 A/B 两个账号可以在不同时间、不同设备上接力。

安全边界：本模块**不发送任何内容**。B 端只负责定位到人、打开会话、校验输入框，
发送由人工或上层确认后另行执行。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import xiaohongshu_templates as templates
from .mobile_gateway import MobileCallError, call

# execution 在 run() 内部才导入：本模块的纯解析函数要能被离线测试直接 import，
# 不能因为 web 依赖（fastapi 等）缺失就导不进来。

SKILL_ID = 'builtin:xiaohongshu-lead'
SKILL = {
    'id': SKILL_ID,
    'name': '小红书截流（A采集/B回复）',
    'description': ('按关键词采集评论、判定线索，并记录**小红书号**作为可重定位指纹；'
                    '支持凭号定位到人、打开私信会话、填入草稿。'
                    '首次私信只能发 1 条，真正发送不在本技能内自动执行。'),
}
PACKAGE = templates.PKG
RESULTS_DIR = Path(__file__).resolve().parents[2] / 'data' / 'lead_results'

RISKY_DEFAULT_EXCLUDE = ('招聘', '招人', '兼职', '免费送', '投诉', '代做', '刷单')


class SkillError(Exception):
    pass


# ---------------------------------------------------------------- MCP 解析
def _payload(result):
    """兼容两种返回：gateway.call 给 dict，adapter().call_tool 给 MCPResult 对象。"""
    if hasattr(result, 'result'):
        result = result.result
    return result or {}


def elements(result):
    for item in _payload(result).get('content', []):
        if item.get('type') != 'text':
            continue
        text = item.get('text', '')
        match = re.search(r'\[\s*\{.*\}\s*\]', text, re.S)
        if not match:
            try:
                value = json.loads(text)
            except (TypeError, ValueError):
                continue
        else:
            try:
                value = json.loads(match.group(0))
            except ValueError:
                continue
        if isinstance(value, list) and value and all(isinstance(e, dict) for e in value):
            return value
    raise SkillError('无法读取当前 UI 树，已停止，未猜测坐标')


def foreground_package(result):
    for item in _payload(result).get('content', []):
        if item.get('type') == 'text':
            match = re.search(
                r'Foreground app:.*?\((android|[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)\)',
                item.get('text', ''))
            if match:
                return match.group(1)
    raise SkillError('无法确认当前前台应用，已停止')


def foreground_activity(result) -> str:
    """从 `Foreground app: 小红书 (com.xingin.xhs), activity: xxx` 里取 activity。"""
    for item in _payload(result).get('content', []):
        if item.get('type') != 'text':
            continue
        match = re.search(r'activity:\s*([A-Za-z][\w.$]*)', item.get('text', ''))
        if match:
            return match.group(1)
    return ''


def _ref_ok(el) -> bool:
    return bool(re.fullmatch(r'@e[1-9]\d*', str((el or {}).get('ref', ''))))


def _box(el):
    c = (el or {}).get('coordinates') or {}
    if not all(isinstance(c.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
        return None
    if c['width'] <= 0 or c['height'] <= 0:
        return None
    return c


def _text(el) -> str:
    return (el.get('text') or '').strip()


def _label(el) -> str:
    return (el.get('label') or '').strip()


def _type(el) -> str:
    return str(el.get('type') or '')


# ---------------------------------------------------------------- 纯解析函数
# 实测：小红书号**不全是数字**，例 `992771452`、`iiiu0417o`、`5066803166` 都有；
# 所以只允许 [A-Za-z0-9_.-]，长度 3–30。写成纯 \d+ 会漏采带字母的号。
ID_RE = re.compile(r'^小红书号[：:]\s*([A-Za-z0-9_.\-]{3,30})$')
ID_VALUE_RE = re.compile(r'^[A-Za-z0-9_.\-]{3,30}$')
# 实测踩过：注销用户的占位文本会被解析成 "null"，必须当无效号丢弃
INVALID_IDS = ('null', 'undefined', 'none', 'nil', '0')
# 实测（2026-11-02）：评论区时间**不一定是「08-23」这种绝对日期**，
# 大量是相对时间：刚刚 / 3分钟前 / 2小时前 / 昨天 20:09 / 前天 / 5天前。
# 原来只认 MM-DD，导致这类评论整条被漏读（页面上有评论却解析出 0 条）。
TIME_RE = re.compile(
    r'^(?P<time>刚刚|\d+\s*分钟前|\d+\s*小时前|\d+\s*天前'
    r'|昨天(?:\s*\d{1,2}:\d{2})?|前天(?:\s*\d{1,2}:\d{2})?'
    r'|\d{4}-\d{2}-\d{2}|\d{2}-\d{2})'
    r'\s*(?P<region>[^\s]*)?\s*回复$')

# 用户列表里昵称上下方会出现这些行，取昵称时必须排除，否则会把粉丝数当名字
NICKNAME_BLOCKLIST = ('粉丝', '关注', '发过相关笔记', '小红书号')

# 评论区里混在正文/昵称位置的非评论文本（实测：展开回复、作者标记、头部提示）
# 注意：这里不放「作者」——它只是两字标签，用精确匹配处理即可。
# 放进前缀表会把「作者写得真好」这类正文误排除（实测已踩）。
COMMENT_MARK_PREFIXES = ('展开', '收起', '首评', '共 ', '回复', '爱评论', '点赞')

def _is_comment_mark(text) -> bool:
    """判断文本是不是评论区标记（不是正文/昵称）。"""
    return text == '作者' or text.startswith(COMMENT_MARK_PREFIXES)

# 商业推广笔记的标记。实测：这类笔记底部是「立即咨询 / N人已咨询」，
# 根本没有标准评论区，评论再多也采不到评论者，必须主动跳过。
AD_MARKS = ('立即咨询', '人已咨询', '获取报价', '免费咨询', '点击咨询', '私信咨询')


def is_ad(dump) -> bool:
    """判断当前笔记是不是商业推广页。"""
    texts = [_text(e) for e in dump]
    return any(mark in text for text in texts for mark in AD_MARKS)


def parse_xhs_id(text) -> str:
    """从「小红书号：95288842605」里取号；取不到或明显无效返回空串。"""
    match = ID_RE.match((text or '').strip())
    if not match:
        return ''
    value = match.group(1)
    if value.lower() in INVALID_IDS:
        return ''
    return value


def collect_xhs_ids(dump) -> list:
    """用户列表里所有「小红书号」及其上方昵称，按 y 排序。

    实测布局（以一张用户卡为例）：
        昵称       y≈395
        粉丝 1557  y≈457
        小红书号：992771452  y≈508
    昵称在号**上方约 110px**，不是同一行；粉丝行离得更近，必须排除。
    """
    rows = []
    for el in dump:
        value = parse_xhs_id(_text(el))
        if not value:
            continue
        box = _box(el)
        if not box:
            continue
        nickname, best = '', None
        for other in dump:
            if other is el or 'TextView' not in _type(other):
                continue
            text = _text(other)
            if not text or any(word in text for word in NICKNAME_BLOCKLIST):
                continue
            ob = _box(other)
            if not ob:
                continue
            dy = box['y'] - ob['y']          # 昵称在上方
            if not (30 <= dy <= 180):
                continue
            if abs(ob['x'] - box['x']) > 90:  # 同一张卡左对齐
                continue
            if best is None or dy < best:
                best, nickname = dy, text
        rows.append({'xhs_id': value, 'nickname': nickname, 'y': box['y']})
    rows.sort(key=lambda r: r['y'])
    return rows


def pick_unique_id(rows, xhs_id):
    """按小红书号做唯一命中；0 个或多个都视为不可确认。"""
    hits = [r for r in rows if r['xhs_id'] == xhs_id]
    return hits[0] if len(hits) == 1 else None


def parse_time_region(text):
    """「08-23 河南 回复」→ {'time':'08-23','region':'河南'}；不符合返回 None。"""
    match = TIME_RE.match((text or '').strip())
    if not match:
        return None
    return {'time': match.group('time'), 'region': (match.group('region') or '').strip()}


def read_comments(dump) -> list:
    """按「时间 地区 回复」锚点还原每条评论。

    实测（2026-11-01）：
      · 锚点上方**最近**的 TextView 就是正文，正确率高；
      · 昵称在正文上方约 25–90px，但同一区间还会混进「展开 N 条回复」、
        头部提示「爱评论的人运气都不差」、以及**上一条评论的正文**。
    因此昵称是**尽力而为**：能确认就填，不能确认就留空，绝不猜。
    真正用于定位的是小红书号，昵称只作展示。
    """
    anchors = []
    for el in dump:
        if 'TextView' not in _type(el):
            continue
        if not parse_time_region(_text(el)):
            continue
        box = _box(el)
        if box and _ref_ok(el):
            anchors.append((el, box))
    anchors.sort(key=lambda item: item[1]['y'])

    rows, seen, used_texts = [], set(), set()
    for anchor, box in anchors:
        # 正文：锚点上方最近的合格文本
        cands = []
        for other in dump:
            if other is anchor or 'TextView' not in _type(other):
                continue
            text = _text(other)
            if not text or _is_comment_mark(text):
                continue
            ob = _box(other)
            if not ob:
                continue
            dy = box['y'] - ob['y']
            if not (0 < dy <= 160):
                continue
            cands.append((ob['y'], text))
        if not cands:
            continue
        cands.sort()
        content_y, content = cands[-1]
        if not content:
            continue

        # 昵称：正文上方 25–90px，排除标记、排除已认定为其它评论正文的文本
        nick_cands = []
        for other in dump:
            if other is anchor or 'TextView' not in _type(other):
                continue
            text = _text(other)
            if not text or _is_comment_mark(text):
                continue
            if text == content or text in used_texts:
                continue
            ob = _box(other)
            if not ob:
                continue
            dy = content_y - ob['y']
            if not (25 <= dy <= 90):
                continue
            nick_cands.append((ob['y'], text))
        nick_cands.sort()
        nickname = nick_cands[-1][1] if nick_cands else ''

        meta = parse_time_region(_text(anchor)) or {}

        # 作者本人发言：小红书会在昵称旁标记「作者」小标签。
        # 这类评论人就是笔记作者，不需要联系，直接标记出来供上层过滤。
        #
        # 口径要严：标签本身非常短（就是「作者」两个字）。
        # 实测风险——以前用 startswith('作者')，像「作者写得真好」这种正文
        # 会被误判成作者本人发言，白白丢掉一条线索。所以限制长度 ≤ 4。
        is_author = False
        for other in dump:
            if 'TextView' not in _type(other):
                continue
            text = _text(other)
            if not (text == '作者' or (text.startswith('作者') and len(text) <= 4)):
                continue
            ob = _box(other)
            if not ob:
                continue
            dy = box['y'] - ob['y']
            if -60 <= dy <= 150:
                is_author = True
                break

        key = (nickname, content[:40])
        if key in seen:
            continue
        seen.add(key)
        used_texts.add(content)
        rows.append({
            'nickname': nickname,
            'content': content,
            'time': meta.get('time', ''),
            'region': meta.get('region', ''),
            'is_author': is_author,
            'y': box['y'],
        })
    return rows


def judge(comment, intent_words, exclude_words):
    """先排除再命中意向词；返回 (是否线索, 理由)。

    intent_words 为空 = 不筛意向、全量收录（与抖音一致、与界面「留空=全量收录」一致）。
    2026-11-03 修：旧实现留空时 hits 恒为空 → 一条都不命中，
    整个任务读了几百条评论却 0 条入中转站。
    """
    text = comment.get('content', '')
    for word in exclude_words:
        if word and word in text:
            return False, '命中排除词「%s」' % word
    if not [w for w in intent_words if w]:
        return True, '未设意向词：全量收录'
    hits = [w for w in intent_words if w and w in text]
    if hits:
        return True, '命中意向词：' + '、'.join(hits[:3])
    return False, ''


def render(template, lead):
    """占位符替换；不编造内容。"""
    if not (template or '').strip():
        return ''
    note = lead.get('note') or {}
    comment = lead.get('comment') or {}
    return (template.replace('{昵称}', comment.get('nickname', ''))
                    .replace('{评论}', comment.get('content', ''))
                    .replace('{作者}', note.get('author_name', ''))
                    .replace('{标题}', note.get('title', ''))
                    .replace('{关键词}', lead.get('keyword', '')))


def read_note(dump) -> dict:
    """读笔记标题与作者。

    实测（2026-11-01，图集笔记 / 手机3）：
      · 作者昵称在顶部 y≈175，紧邻「关注」按钮；
      · 标题与正文是**普通 TextView，没有 label**，位于图片下方 y≈1100 起，
        且**首屏常常还没渲染，必须先向上滑一次**；
      · 「共 N 条评论」是正文区的下边界，用它截断可避免把推荐内容当正文。
    """
    author = ''
    # 作者名与「关注」按钮同一行、且在它左侧；这是实测最稳的定位方式。
    follow = next((e for e in dump if _text(e) in ('关注', '已关注') and _box(e)), None)
    if follow is not None:
        fb = _box(follow)
        cands = []
        for el in dump:
            if 'TextView' not in _type(el):
                continue
            text = _text(el)
            if not text or text in ('关注', '已关注'):
                continue
            box = _box(el)
            if not box:
                continue
            if abs(box['y'] - fb['y']) > 50:
                continue
            if box['x'] >= fb['x']:
                continue
            cands.append((box['y'], -box['x'], text))
        if cands:
            cands.sort()
            author = cands[0][2]
    if not author:
        # 回退：顶部区域的第一个非噪声文本
        skip = ('搜索', '返回', '全部删除', '关注', '已关注', '分享', '举报', '说点什么...', '取消')
        head = sorted((e for e in dump if 'TextView' in _type(e) and _text(e)),
                      key=lambda e: (_box(e) or {}).get('y', 99999))
        for el in head:
            box = _box(el)
            if not box or box['y'] > 400:
                continue
            text = _text(el)
            if text in skip or re.fullmatch(r'[\d.%:]+', text):
                continue
            author = text
            break

    # 标题必须取「正文区」，行为边界（limit）取三者的最小值：
    #   1. 「共 N 条评论」那一行
    #   2. 第一条评论的时间锚点（「5天前 江苏 回复」这种）
    #   3. 底部「评论 N」入口按钮
    # 实测 BUG：以前只认「共 N 条评论」，那个文案在评论区折叠时并不存在，
    # 于是把评论区里「作者」标签旁边那行（也就是作者昵称）当成标题，
    # 导致 79 / 526 条记录的 note_title 实际是作者昵称。
    limit = None

    def shrink(candidate_y):
        nonlocal limit
        if candidate_y is None:
            return
        if limit is None or candidate_y < limit:
            limit = candidate_y

    for el in dump:
        text = _text(el)
        box = _box(el)
        if not box:
            continue
        if re.match(r'^共\s*\d+\s*条评论$', text):
            shrink(box['y'])
        if parse_time_region(text):
            # 评论正文在锚点**上方**约 160px 内，所以边界要再往上抬 180px，
            # 否则无正文的笔记会把第一条评论当成标题。
            shrink(box['y'] - 180)
        label = _label(el)
        if re.match(r'^评论\s*\d+$', label) or re.match(r'^评论\s*\d+$', text):
            shrink(box['y'])

    texts = []
    for el in dump:
        if 'TextView' not in _type(el):
            continue
        text = _text(el)
        if len(text) < 6 or text == '说点什么...':
            continue
        box = _box(el)
        if not box or box['y'] < 500:
            continue
        if limit is not None and box['y'] >= limit:
            continue
        if box['y'] > 1750:
            continue
        if text.startswith(('猜你想搜', '共 ', '不喜欢')):
            continue
        # 作者昵称本身不能当标题（评论区里它常带「作者」标签出现）
        if author and text.strip() == author.strip():
            continue
        if _is_comment_mark(text):
            continue
        texts.append((box['y'], text))
    texts.sort()
    title = texts[0][1].split('\n')[0][:120] if texts else ''
    return {'title': title, 'author_name': author}


def avatar_for_comment(dump, comment_y):
    """按评论的 y 找同一行左侧的头像（进对方主页拿小红书号的入口）。

    纯函数，便于离线测试。实测：头像为 60–130 的正方形、x < 220，
    与锚点的纵向距离在 230 以内。
    """
    best, best_dy = None, None
    for el in dump:
        if 'View' not in _type(el):
            continue
        box = _box(el)
        if not box:
            continue
        if abs(box['width'] - box['height']) > 8:
            continue
        if not (60 <= box['width'] <= 130):
            continue
        if box['x'] > 220:
            continue
        dy = abs(box['y'] - comment_y)
        if dy > 230:
            continue
        if best_dy is None or dy < best_dy:
            best, best_dy = el, dy
    return best


def read_profile_info(dump) -> tuple:
    """在他人主页的 dump 里读 (小红书号, 昵称)。

    昵称取「小红书号」行上方 20–140px 内最靠近的一条文本。
    """
    id_value, id_y = '', None
    for el in dump:
        value = parse_xhs_id(_text(el))
        if value:
            id_value = value
            box = _box(el)
            id_y = box['y'] if box else None
            break
    nickname = ''
    if id_y is not None:
        cands = []
        for el in dump:
            if 'TextView' not in _type(el):
                continue
            text = _text(el)
            if not text or '小红书号' in text:
                continue
            box = _box(el)
            if not box:
                continue
            dy = id_y - box['y']
            if not (20 <= dy <= 140):
                continue
            cands.append((box['y'], text))
        if cands:
            cands.sort()
            nickname = cands[-1][1]
    return id_value, nickname


def _norm_nick(value) -> str:
    """去掉 emoji、空格、标点，只留文字与数字，便于昵称比对。"""
    return re.sub(r'[^\w\u4e00-\u9fff]', '', str(value or '')).lower()


def nickname_status(profile_nick, comment_nick) -> str:
    """昵称比对三态：'match' / 'mismatch' / 'unknown'。

    实测教训：以前空值直接放行、且用子串包含判定，导致
    「主页读不到昵称」和「Alice2 vs Alice」都被当成一致。
    「读不到」是信息不足，必须和「明确一致」分开，
    所以这里返回三态，由调用方决定是丢弃还是标记未验证。
    """
    x, y = _norm_nick(profile_nick), _norm_nick(comment_nick)
    if not x or not y:
        return 'unknown'
    if x == y:
        return 'match'
    # 只接受「前缀关系且长度差 ≤ 2」：覆盖「兔子威武🐯 / 兔子威武」这类
    # 普通缀差异，同时挡住 Alice2 vs Alice 之外的真正不同的人。
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    if len(long) - len(short) <= 2 and long.startswith(short):
        return 'match'
    return 'mismatch'


def nickname_ok(profile_nick, comment_nick) -> bool:
    """兼容旧调用：只有明确一致才算 True，未知不算通过。"""
    return nickname_status(profile_nick, comment_nick) == 'match'


def build_fingerprint(note, comment):
    """B 端要用的最小指纹。昵称只留作人工核对，号才是键。"""
    return {
        'note_title': note.get('title', '')[:80],
        'note_author_name': note.get('author_name', ''),
        'note_author_xhs_id': note.get('author_xhs_id', ''),
        'comment_nickname': comment.get('nickname', ''),
        'comment_xhs_id': comment.get('xhs_id', ''),
        'comment_text': comment.get('content', '')[:120],
        'comment_time': comment.get('time', ''),
        'comment_region': comment.get('region', ''),
    }


# ---------------------------------------------------------------- 主流程
def run(task):
    from . import execution

    cfg = task.get('lead') or {}
    import time as _t
    try:
        from .time_filter import within_days as _within_days
        _max_age = int(cfg.get('max_age_days') or 0)
        _base_ts = float(cfg.get('task_created_at') or task.get('created_at') or _t.time())
    except Exception:
        _max_age, _base_ts = 0, _t.time()
    keywords = [str(k).strip() for k in (cfg.get('keywords') or []) if str(k).strip()]
    if not keywords:
        raise SkillError('请至少填一个关键词')

    intent_words = [str(w).strip() for w in (cfg.get('intent_words') or []) if str(w).strip()]
    # 意向词留空 = 不筛意向、全量收录。跟抖音一致：界面只要求关键词 +
    # 采集条数，不再因为没填意向词就把整条链路拦死（2026-11-03 调整）。
    exclude_words = [str(w).strip() for w in (cfg.get('exclude_words') or []) if str(w).strip()]
    exclude_words += [w for w in RISKY_DEFAULT_EXCLUDE if w not in exclude_words]

    notes_per_keyword = int(cfg.get('videos_per_keyword') or 5)
    comments_per_note = int(cfg.get('comments_per_video') or 20)
    # 采集目标（累计线索条数）：界面只填这一个数字，0 = 不限。
    target_comments = int(cfg.get('target_comments') or 0)
    # 2026-11-06 主人定：**不设上限**。
    # 之前是 notes_per_keyword=500、scroll_limit=30 屏，翻够就停；
    # 主人明确要求："不要设置上限，一直往下翻页，停止只有两个：
    #   1) 采集数量够了；2) 翻页翻到底了。"
    # 所以：
    #   · notes_per_keyword 不再参与循环上限（保留变量仅为日志兼容）；
    #   · scroll_limit 不再按屏数停，改成"连续 N 次滑动没有任何新卡片"才算到底。
    # 判断"到底"必须靠「有没有新卡片」，而不是"翻了第几屏"——因为列表可能
    # 一直有旧卡片复现（返回后回到顶部），按屏数停会误判成"翻完了"。
    MAX_EMPTY_SWIPES = 6      # 连续 6 次滑动都没出现新卡片 → 认定到底
    scroll_limit = MAX_EMPTY_SWIPES if target_comments else 8
    cooldown_every = max(1, int(cfg.get('cooldown_every') or 10))
    # 采集不冷却：0 表示不停；只有「回复」才用冷却（2026-11-03 调整）
    cooldown_seconds = max(0, int(cfg.get('cooldown_seconds') or 0))
    # 单篇笔记最多向下翻多少屏。12 屏约覆盖 30-60 条评论；
    # 实测评论很多的笔记（300+）翻不完，可按需调高，代价是单轮耗时变长。
    screen_limit = max(3, min(40, int(cfg.get('comment_screens') or 12)))
    # 方案调整（2026-11-01）：只在评论区采集信息，不在评论区回复，
    # 后续由另一账号私信联系。因此不再读取 reply_template。
    dm_tpl = str(cfg.get('dm_template') or '')

    serial = task['serial']
    stop = task['_stop']
    leads = task.setdefault('leads', [])
    stats = {'keywords': 0, 'notes': 0, 'comments': 0, 'leads': 0, 'ids': 0,
             'skipped': 0, 'pooled': 0, 'pool_dup': 0}
    # 实测（取消测试）：以前只在正常结束时才写 task['stats']，
    # 用户中途停止就完全看不到「已经采了多少」。这里直接挂同一个 dict，
    # 后续所有自增都会实时反映出去。
    task['stats'] = stats
    # 一开始就进入 executing，否则前端一直显示"排队中"（2026-11-03 修正）。
    task['status'] = 'executing'

    def check():
        if stop.is_set():
            raise SkillError('任务已停止')

    def to_pool_rows(items):
        """把采集结果转成中转站 redbook 表的字段。

        联系对象是**评论人**，所以 author_red_id 存评论人的小红书号；
        笔记作者号只作溯源。verify 如实带上核验状态。
        """
        return [{'comment_author': row.get('author') or '',
                 'comment_text': row.get('content') or '',
                 'region': row.get('region') or '',
                 'time_text': (row.get('comment') or {}).get('time') or '',
                 'note_title': row.get('note_title') or '',
                 'note_author': row.get('note_author') or '',
                 'author_red_id': row.get('xhs_id') or '',
                 'commenter_user_id': row.get('user_id') or '',
                 'note_id': '',
                 'keyword': row.get('keyword') or '',
                 'verify': row.get('verify') or '',
                 'dm_text': row.get('dm_draft') or ''} for row in items]

    def flush_pool(items):
        """每篇笔记结束就入池：任务中途失败也不会把已采到的线索全丢掉。"""
        if not items:
            return
        try:
            from . import lead_store
            rows = []
            no_id = 0
            unverified = 0
            batch_ids = set()
            for row in to_pool_rows(items):
                red_id = row.get('author_red_id') or ''
                if not red_id:
                    # 没有小红书号的线索无法私信联系，不入池，避免污染待办
                    no_id += 1
                    continue
                if (row.get('verify') or '') != 'match':
                    # 昵称读不到 = 没核验过。实测出现过「匿名」和真实昵称
                    # 拿到同一个小红书号，说明配对不可靠；发错人比少采更糟，
                    # 所以未核验的线索不进池，只保留在任务结果里供人工查看。
                    unverified += 1
                    continue
                # 同一批里也可能出现重复号（同一人多条评论），必须先在批内去重，
                # 否则 add_leads 只查库不查本批，两条都会插进去（已实测出现重复）。
                if red_id in batch_ids:
                    stats['pool_dup'] += 1
                    continue
                batch_ids.add(red_id)
                # 同一个人只进池一次：号已经有记录就跳过，避免重复私信
                if lead_store.exists_field('redbook', 'author_red_id', red_id):
                    stats['pool_dup'] += 1
                    continue
                rows.append(row)
            if no_id:
                note('跳过 %d 条没有小红书号的线索（无法联系）' % no_id)
            if unverified:
                note('跳过 %d 条未核验线索（昵称读不到，不进池）' % unverified)
            stats['unverified'] = stats.get('unverified', 0) + unverified
            if not rows:
                return
            pooled = lead_store.add_leads('redbook', rows, serial=serial)
            stats['pooled'] += pooled['added']
            stats['pool_dup'] += pooled['duplicated']
            if pooled['added']:
                note('已入中转站 %d 条' % pooled['added'])
        except Exception as exc:                          # noqa: BLE001
            note('写入中转站失败（稍后仍会补写）：%s' % exc)

    def pause(seconds):
        if stop.wait(seconds):
            check()

    def note(msg):
        execution.log(task, msg)

    def tree():
        check()
        return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))

    def tap(el):
        check()
        if not _ref_ok(el):
            raise SkillError('控件引用不可用，已停止')
        with task['_gate']:
            call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)
        _fg['t'] = 0.0

    def press(button):
        check()
        with task['_gate']:
            call('mobile_press_button', {'button': button}, serial)
        _fg['t'] = 0.0

    # 性能：一次单轮流程里 mobile_get_foreground_app 被调了 37 次，
    # 每次约 0.5-0.8 秒，是第二大开销。这里合并成一次调用同时取包名和 Activity，
    # 并做 0.8 秒短缓存；任何点击/按键都会让缓存失效。
    _fg = {'t': 0.0, 'pkg': '', 'act': ''}

    def fg_info(force=False):
        check()
        now = time.time()
        if not force and _fg['act'] and now - _fg['t'] < 0.8:
            return _fg['pkg'], _fg['act']
        raw = call('mobile_get_foreground_app', {}, serial)
        _fg.update(t=now, pkg=foreground_package(raw), act=foreground_activity(raw))
        return _fg['pkg'], _fg['act']

    def foreground():
        return fg_info()[0]

    def settle(times=6, gap=0.6):
        last = -1
        for _ in range(times):
            pause(gap)
            current = tree()
            if len(current) == last:
                return current
            last = len(current)
        return tree()

    def ensure_state(target='search', allow_restart=False):
        """把界面拉回目标状态。

        实测事故（2026-11-02）：评论区被展开成全屏后，收尾那段只看
        「有没有输入框」来决定按几次返回，结果判断失准，任务结束时界面
        停在白色评论区面板上，看起来就像「卡住了」。
        这里改用状态机识别 + 逐级回退，避免凭猜测连按 BACK。
        """
        try:
            from .xiaohongshu_recover import recover as _recover
        except Exception:                                # noqa: BLE001
            return True
        try:
            r = _recover(serial, target=target, max_steps=6,
                         log=note, allow_restart=allow_restart)
        except Exception:                                # noqa: BLE001
            return True
        if not r.get('ok'):
            note('界面没能回到%s（当前 %s）' % (target, r.get('to')))
        return bool(r.get('ok'))

    def cd_tick(counter):
        if cooldown_seconds and counter and counter % cooldown_every == 0:
            note('已处理 %d 次，按设置休息 %d 秒' % (counter, cooldown_seconds))
            pause(cooldown_seconds)

    # ---- 页面判别
    def find_by_text(dump, text, typ=None):
        hits = [e for e in dump if _text(e) == text and _ref_ok(e)
                and (typ is None or typ in _type(e))]
        return hits[0] if len(hits) == 1 else None

    def activity_now():
        return fg_info()[1]

    def reset_home():
        """回到小红书首页，保证每次任务从干净状态开始。

        实测教训：上一次任务可能停在搜索结果页/用户 Tab/私信会话/异常详情页，
        直接开搜会继承那些状态（甚至把关键词打进私信框）。
        有些页面 BACK 退不出来（DetailFeedActivity 只剩 4 个元素），
        所以退几层还不行就 HOME + 重新拉起。
        """
        for _attempt in range(3):
            for _ in range(4):
                if foreground() != PACKAGE:
                    break
                dump = tree()
                # 残留「是否继续编辑上次内容？」弹窗会盖住整屏、吃掉所有点击，
                # 不清理就会表现为"进不去搜索页"。清理顺序按主人定的：先 BACK，再点 ×。
                try:
                    from . import resume_dialog as RD
                    if RD.dismiss(tree, press, tap, pause, log=note):
                        dump = tree()
                except Exception:
                    pass
                home = next((e for e in dump if _text(e) == '首页' and _ref_ok(e)), None)
                if home is not None:
                    tap(home)
                    pause(2.0)
                    return True
                press('BACK')
                pause(1.0)
            # 仍然没回到首页：结合 HOME 键强制重启前台
            press('HOME')
            pause(1.5)
            call('mobile_launch_app', {'packageName': PACKAGE}, serial)
            pause(4.0)
        note('未能确认回到首页，继续尝试搜索流程')
        return False

    def goto_search():
        """进入真正的搜索页。

        审核教训（2026-11-01）：以前只判断「页面上有没有 EditText」，
        结果手机停在私信会话页时会把**私信输入框**当成搜索框，
        关键词被直接打进聊天草稿。现在必须以搜索页 Activity 为准。
        """
        for _ in range(8):
            act = activity_now()
            pkg = foreground()
            if pkg != PACKAGE:
                # 退到桌面了：重新拉起小红书
                call('mobile_launch_app', {'packageName': PACKAGE}, serial)
                pause(3.5)
                continue
            dump = tree()
            if 'GlobalSearchActivity' in act:
                if any('EditText' in _type(e) for e in dump):
                    return dump
                # 实测：已经搜过一次的结果页，搜索框是 TextView 而不是 EditText；
                # 必须先点它回到输入态，才能重新输入关键词。
                # 注意避开 y≈124 的行内「小红书号：xxx」（同样在顶部区域）。
                keyword_box = next(
                    (e for e in dump
                     if 'TextView' in _type(e) and _ref_ok(e)
                     and 130 <= (_box(e) or {}).get('y', 0) <= 165
                     and _text(e) and _text(e) != '搜索'
                     and not _text(e).startswith('小红书号')), None)
                if keyword_box is not None:
                    tap(keyword_box)
                    pause(1.2)
                    continue
                pause(1.0)
                continue
            # 不在搜索页：先试首页的搜索入口，否则退一层
            entry = next((e for e in dump if 'Button' in _type(e)
                          and (_label(e) == '搜索' or _text(e) == '搜索')
                          and _ref_ok(e)), None)
            if entry is not None:
                tap(entry)
                pause(1.6)
                continue
            press('BACK')
            pause(1.0)
        # 循环里没进到搜索页：HOME + 重新拉起后再试一次
        press('HOME')
        pause(1.5)
        call('mobile_launch_app', {'packageName': PACKAGE}, serial)
        pause(4.0)
        dump = tree()
        entry = next((e for e in dump if 'Button' in _type(e)
                      and (_label(e) == '搜索' or _text(e) == '搜索') and _ref_ok(e)), None)
        if entry is not None:
            tap(entry)
            pause(2.0)
            dump = tree()
            if any('EditText' in _type(e) for e in dump):
                return dump
        raise SkillError('未能进入小红书搜索页，已停止')

    def search_keyword(keyword):
        dump = goto_search()
        box = next((e for e in dump if 'EditText' in _type(e) and _ref_ok(e)), None)
        if box is None:
            raise SkillError('搜索框不存在，已停止')
        tap(box)
        pause(0.5)
        # 清空遗留内容，避免关键词叠加
        dump = tree()
        clear = find_by_text(dump, '全部删除') or next(
            (e for e in dump if _label(e) == '全部删除'), None)
        if clear is not None:
            tap(clear)
            pause(0.6)
        call('mobile_type_keys', {'text': keyword}, serial)
        pause(0.7)
        dump = tree()
        # 审核修正：搜索键实测是 TextView（不是 Button），且可能同时存在
        # 顶部「搜索」与页面内其它同文案控件。优先取按钮，其次取最靠上的那个。
        cands = [e for e in dump if _text(e) == '搜索' and _ref_ok(e)]
        if not cands:
            raise SkillError('搜索按钮不存在，已停止')
        buttons = [e for e in cands if 'Button' in _type(e)]
        if len(buttons) >= 1:
            submit = min(buttons, key=lambda e: (_box(e) or {'y': 9999})['y'])
        else:
            submit = min(cands, key=lambda e: (_box(e) or {'y': 9999})['y'])
        tap(submit)
        note('搜索「%s」' % keyword)
        dump = settle()
        # 实测：上一次搜索若切到「用户」Tab，再搜会停在用户列表，
        # 这里的卡片全是用户（文本形如「小红书号：xxx」），点下去只能进主页。
        # 必须在读完这一屏后确认是「全部」，再做任何卡片操作。
        if any(_text(e).startswith('小红书号') for e in dump):
            tab_all = next((e for e in dump if _text(e) == '全部' and _ref_ok(e)), None)
            if tab_all is not None:
                tap(tab_all)
                pause(2.2)
                dump = settle()
                note('已切回「全部」结果')
        return dump

    def switch_tab(name):
        dump = tree()
        tab = find_by_text(dump, name)
        if tab is None:
            raise SkillError('分类「%s」不存在或不唯一，已停止' % name)
        tap(tab)
        return settle()

    # ---- 结果卡片：用「描述文本」定位，再用坐标点封面
    #
    # 实测验到的三个坑（都在这里避开）：
    #   1. 顶部筛选条（y≈454–665）与活动 banner（y≈483）也有同尺寸图，
    #      点它们会打开筛选面板，后续就误判成「没有评论入口」；
    #   2. 卡片纵坐标每次都变（721 / 1203 / 1441），固定阈值不可靠；
    #   3. **不要主动点「全部」Tab**，一点就重新加载，之后用旧坐标必然落空。
    # 结论：以「长度 > 12 的描述文本」为锚点，点它上方约 400px 的封面区。
    NOISE = ('发笔记赢', '综合', '最新', '最多点赞', '最多评论', '重置', '收起',
             '问一问', '用户', '商品', '群聊', '猜你想搜',
             # 实测：搜索结果里混着「相关搜索：xxx」聚合卡，描述文本和真笔记一样长，
             # 进去后才发现不是笔记（累计 29 次）。这里直接按文案前置过滤。
             '相关搜索', '广告', '赞助')

    def note_cards(dump):
        cards = []
        for el in dump:
            if 'TextView' not in _type(el):
                continue
            text = _text(el)
            if len(text) <= 12:
                continue
            box = _box(el)
            if not box or box['y'] < 700:
                continue
            if any(word in text for word in NOISE):
                continue
            if text.startswith('小红书号'):
                continue          # 用户卡，不是笔记
            x_center = box['x'] + box.get('width', 0) / 2
            # 优先记下「描述正上方那张封面图」，点击时用它自己的中心点，
            # 比按固定偏移量推算可靠（实测有 17 次算偏了，点空或点到导航区）。
            cover = None
            best_gap = None
            for other in dump:
                if 'ImageView' not in _type(other):
                    continue
                ob = _box(other)
                if not ob or ob['width'] < 250 or ob['height'] < 250:
                    continue
                if abs((ob['x'] + ob['width'] / 2) - x_center) > 200:
                    continue
                gap = box['y'] - (ob['y'] + ob['height'])
                if 0 <= gap <= 280 and (best_gap is None or gap < best_gap):
                    best_gap, cover = gap, ob
            cards.append({'x': int(x_center),
                          'y': max(700, int(box['y'] - 400 + box.get('height', 40) / 2)),
                          'text_y': box['y'],
                          'cover': cover,
                          'title': text[:60]})
        seen, unique = set(), []
        for c in sorted(cards, key=lambda c: (c['text_y'], c['x'])):
            key = (round(c['x'] / 60), round(c['text_y'] / 60))
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)
        return unique

    # ---- 笔记详情：read_note 已提到模块级，便于离线测试同一份逻辑

    def declared_comments(dump):
        """页面上「共 N 条评论」的 N；读不到返回 0。用来衡量实际读到了多少。"""
        for el in dump:
            m = re.match(r'^共\s*(\d+)\s*条评论$', _text(el))
            if m:
                return int(m.group(1))
        return 0

    def open_comments():
        dump = settle()
        if any(re.match(r'^共\s*\d+\s*条评论$', t) for t in (_text(e) for e in dump)):
            return dump
        # 实测：评论入口有的是 Button「评论 65」，有的只是 TextView 文案，
        # 所以两种都认，取最靠下的那个（底部评论条）。
        cands = [e for e in dump if _ref_ok(e)
                 and 'NoteDetail' not in _type(e)
                 and (re.match(r'^评论\s*\d+$', _label(e)) or re.match(r'^评论\s*\d+$', _text(e)))]
        if not cands:
            raise SkillError('评论入口不存在，已停止')
        entry = max(cands, key=lambda e: (_box(e) or {'y': 0})['y'])
        tap(entry)
        for _ in range(6):
            pause(0.8)
            dump = tree()
            if any(re.match(r'^共\s*\d+\s*条评论$', t) for t in (_text(e) for e in dump)):
                return dump
        raise SkillError('评论区没有打开，已跳过该笔记')

    def collect_leads(limit, note_author='', first_dump=None):
        """逐屏读评论、**就地**取号，再滚动。

        审核修正（2026-11-01）：以前先把多屏评论全存下来、滚到底，
        再回头用评论当时的 y 坐标在当前屏找头像，前几屏的评论会被配到
        当前屏的人身上。现在每屏就地完成「读 → 判定 → 取号 → 校验」。

        会跳过「不值得联系」的人：
          · 笔记作者本人在评论区发言（带「作者」标签）；
          · 昵称与笔记作者一致的人（作者自己回自己）。
        作者不需要被私信，联系他没有任何意义。
        """
        found, seen, idle = [], set(), 0
        examined = 0
        skipped_author = 0
        skipped_badge = 0
        skipped_nick = 0
        samples = []
        empty_screens = 0
        depth = 0                 # 当前已经向下翻了几屏，用于取号后恢复位置
        max_screens = screen_limit   # 单篇最多翻几屏，防无底洞；可由配置覆盖
        author_key = _norm_nick(note_author) if note_author else ''
        screens = 0
        while len(found) < limit and screens < max_screens:
            check()
            screens += 1
            if first_dump is not None:
                dump = first_dump
                first_dump = None
            else:
                dump = tree()
            pending = None
            fresh_rows = 0
            for row in read_comments(dump):
                key = (row['nickname'], row['content'][:40])
                if key in seen:
                    continue
                seen.add(key)
                fresh_rows += 1
                examined += 1
                if len(samples) < 3:
                    samples.append(row['content'][:30])
                if row.get('is_author'):
                    skipped_author += 1
                    skipped_badge += 1
                    continue
                if author_key and _norm_nick(row['nickname']) == author_key:
                    skipped_author += 1
                    skipped_nick += 1
                    continue
                if _max_age:
                    try:
                        _ok, _ = _within_days((row.get('meta') or {}).get('time', '') or row.get('time', ''), _max_age, _base_ts)
                    except Exception:
                        _ok = True
                    if not _ok:
                        stats['skipped'] += 1
                        continue
                ok, why = judge(row, intent_words, exclude_words)
                if not ok:
                    continue
                pending = (row, why)
                break
            if pending is None:
                # 关键区别：这一屏「有没有新评论」和「有没有命中意向词」是两件事。
                # 以前连续 2 屏没命中就收工，导致页面共 300+ 条评论却只读了几条
                # （实测覆盖率仅 10.8%）。现在只要还能翻出新评论就继续，
                # 只有连续 2 屏一条新评论都没有，才认定到底了。
                if fresh_rows == 0:
                    empty_screens += 1
                    # 实测回退：把门槛从 2 提到 4 之后，覆盖率没升（15% vs 22%），
                    # 单轮耗时反而从 500 秒涨到 600 秒——说明评论列表有渲染上限，
                    # 多滑几屏也读不到更多人。所以恢复为 2，省时间。
                    if empty_screens >= 2:
                        break
                else:
                    empty_screens = 0
                idle += 1
                call('mobile_swipe_on_screen',
                     {'direction': 'up', 'distance': 600, 'x': 540, 'y': 1400}, serial)
                depth += 1
                pause(1.0)
                continue
            idle = 0
            row, why = pending
            # dump 是本轮刚取的，头像坐标与评论一一对应，不会跨屏错配
            xhs_id, why_fail, status, prof_nick, user_id = resolve_commenter_id(dump, row)
            # 实测：点头像进主页再返回后，评论区会跳回顶部。
            # 不恢复深度的话，下一屏读到的全是已见过的评论，会被误判「到底了」，
            # 导致页面共 300+ 条评论却只读到几条（覆盖率仅 10% 左右）。
            #
            # 恢复方式：按「刚处理过的那条评论」定位，而不是盲滑 depth 次。
            # 盲滑会因列表重新渲染而滑过头，结果又变成静默跳过中间评论。
            marker = (row.get('content') or '')[:30]
            # 性能：以前每滑一屏都 dump 一次（一次约 1 秒），翻 30 屏时光恢复就要 30 秒。
            # 改成每 3 屏检查一次，检查次数降到 1/3，最多多滑 2 屏（可接受）。
            # 性能：以前每 3 屏就 dump 一次探查（一次约 1 秒），一条线索要探 3-5 次。
            # 现在先盲滑到目标深度，只在末尾探 1 次；没找到再补滑 3 屏探第 2 次。
            # 恢复深度要设上限：实测 depth 能到 30，盲滑 32 次要 11 秒，
            # 而 15 屏之后能拿到的新评论已经很少，不划算。
            for _ in range(min(depth + 2, 15)):
                call('mobile_swipe_on_screen',
                     {'direction': 'up', 'distance': 600, 'x': 540, 'y': 1400}, serial)
                pause(0.35)
            probe = None
            try:
                probe = tree()
            except (SkillError, MobileCallError):
                probe = None
            if probe is not None and not any(
                    (r.get('content') or '')[:30] == marker for r in read_comments(probe)):
                for _ in range(3):
                    call('mobile_swipe_on_screen',
                         {'direction': 'up', 'distance': 600, 'x': 540, 'y': 1400}, serial)
                    pause(0.35)
            # 进了主页才知道真实昵称，再挡一次「作者本人」
            if author_key and _norm_nick(prof_nick) == author_key:
                skipped_author += 1
                continue
            found.append({'row': row, 'why': why, 'xhs_id': xhs_id,
                          'fail': why_fail, 'status': status,
                          'user_id': user_id})
        return {'found': found, 'examined': examined, 'samples': samples,
                'skipped_author': skipped_author,
                'skipped_badge': skipped_badge,
                'skipped_nick': skipped_nick}



    def resolve_commenter_id(dump, comment):
        """点头像进对方主页读小红书号，并做昵称自校验。

        返回 (号, 失败原因, 校验状态, 主页昵称, 24位user_id)：
          · 号为空 → 这条线索不可用；
          · 昵称明确不一致 → 丢弃（说明点错了人）；
          · 昵称读不到 → unknown，保留线索但标记「未验证」，不冒充已校验。

        2026-11-05 起：在主页上顺手抓 **24 位 user_id**（小红书号直达是空白页）。
        抓 ID 期间会离开主页去粘贴读回，抓完自动直达回同一主页，保证
        后面的 BACK → 评论面板 仍成立。抓失败不影响取号。
        """
        avatar = avatar_for_comment(dump, comment.get('y', 0))
        if avatar is None:
            return '', 'no_avatar', 'unknown', '', ''
        try:
            tap(avatar)
            # 实测：原来固定 pause(2.2) + settle()（约 3 秒、两次读控件树）+ BACK 后 pause(1.5)，
            # 单次取号光等待就要 6.7 秒。改成「先短等一次读号，读不到再补两次」，
            # 命中时只需约 2.2 秒；三次都读不到才放弃（与原来同样安全）。
            value, profile_nick = '', ''
            for delay in (1.3, 0.7, 0.7):
                pause(delay)
                value, profile_nick = read_profile_info(tree())
                if value:
                    break
            user_id = ''
            if value:
                try:
                    from . import id_capture as IC
                    cap = IC.capture_and_return(serial, 'xiaohongshu')
                    if cap.get('ok'):
                        user_id = str(cap.get('ident') or '')
                except Exception:   # noqa: BLE001 抓ID失败不能毁掉采集
                    user_id = ''
            press('BACK')
            pause(1.0)
        except (SkillError, MobileCallError):
            try:
                press('BACK')
                pause(1.0)
            except MobileCallError:
                pass
            return '', 'failed', 'unknown', '', ''
        if not value:
            return '', 'no_id', 'unknown', '', ''
        status = nickname_status(profile_nick, comment.get('nickname', ''))
        if status == 'mismatch':
            return '', 'nickname_mismatch:%s/%s' % (profile_nick[:12],
                                                    (comment.get('nickname') or '')[:12]), status, profile_nick, ''
        return value, '', status, profile_nick, user_id

    # ---- 主循环
    opened = False
    processed = set()
    counter = 0

    reset_home()

    for keyword in keywords:
        check()
        if target_comments and stats['leads'] >= target_comments:
            execution.log(task, '已入库 %d 条线索，达到目标 %d，停止' % (stats['leads'], target_comments))
            break
        stats['keywords'] += 1
        dump = search_keyword(keyword)
        taken, scroll_rounds = 0, 0

        # 2026-11-06 主人定：**不设上限**，一直往下翻，只有两个停点：
        #   ① 采集数量够了（target_comments）
        #   ② 真的翻到底（连续 MAX_EMPTY_SWIPES 次滑动都没有新卡片）
        # 原来这里是 `while taken < notes_per_keyword`（默认只有 5 篇！），
        # 而且翻够 30 屏就走"重新搜索同一关键词"——同一个词、同一个排序
        # 搜出来还是同一批笔记，全在 processed 里，5 次重搜纯空转，
        # 最后报"没有更多新笔记"。这就是主人看到的"没往下翻"。
        while True:
            check()
            if target_comments and stats['leads'] >= target_comments:
                execution.log(task, '已入库 %d 条线索，达到目标 %d，停止' % (stats['leads'], target_comments))
                break
            cards = [c for c in note_cards(dump) if c['y'] > 250]
            target = None
            for card in cards:
                # 去重键必须和登记键用同一套坐标：都用描述文本位置 text_y。
                # 以前查询用点击位置 y、登记用 text_y，两者差约 400px，去重形同失效。
                _txt = (card.get('text') or card.get('desc') or '').strip()[:40]
                stamp = (keyword, _txt) if _txt else (
                    keyword, round(card['x'] / 50), round(card['text_y'] / 50))
                if stamp in processed:
                    continue
                target = card
                break
            if target is None:
                scroll_rounds += 1
                if scroll_rounds > scroll_limit:
                    # 连续多次滑动都没出现新卡片 → 认定「翻到底」，正常收工。
                    # 不再重搜同一关键词（结果不变，纯浪费）。
                    note('关键词「%s」已翻到底（连续 %d 次滑动无新笔记，已入库 %d/%d 条线索）'
                         % (keyword, scroll_rounds - 1, stats['leads'], target_comments or 0))
                    break
                # 2026-11-05：原来固定滑 700px，一排卡片高约 920px，滑完上一排还在屏上，
                # 结果反复进入已采过的卡片。改成按屏高滑动（约 62%%），保证整排翻过去。
                try:
                    from . import ui_probe_targets as _U
                    _h = _U.screen_profile(serial)['height']
                except Exception:
                    _h = 1920
                call('mobile_swipe_on_screen',
                     {'direction': 'up', 'distance': int(_h * 0.62), 'x': 540,
                      'y': int(_h * 0.78)}, serial)
                pause(1.2)
                dump = tree()
                continue

            _tk = (target.get('text') or target.get('desc') or '').strip()[:40]
            processed.add((keyword, _tk) if _tk else (
                keyword, round(target['x'] / 50), round(target['text_y'] / 50)))

            # 点卡片：不同排版的卡片高度不一样，「描述上方 400px」不是每次都命中封面。
            # 实测一轮里会出现 1–2 次点到头像/用户卡，所以按几个偏移量重试，
            # 只要有一处能进笔记详情就继续，全都失败才跳过这张卡。
            entered = False
            seen_acts = []
            # 先按封面图自己的中心点试；没有封面图或没命中，再退回几个偏移量
            points = []
            if target.get('cover'):
                cb = target['cover']
                points.append((int(cb['x'] + cb['width'] / 2),
                               int(cb['y'] + cb['height'] / 2)))
            for offset in (400, 260, 540):
                points.append((target['x'], max(700, int(target['text_y'] - offset + 20))))
            for px, py in points:
                try:
                    call('mobile_click_on_screen_at_coordinates',
                         {'x': px, 'y': py}, serial)
                except (SkillError, MobileCallError):
                    break
                pause(2.4)
                if foreground() != PACKAGE:
                    break
                act = activity_now()
                # 实测：部分笔记（尤其视频/单图）打开的是 DetailFeedActivity，
                # 那是全屏浏览页，同样算进了笔记，不能当成失败丢掉。
                if 'NoteDetailActivity' in act or 'DetailFeedActivity' in act:
                    entered = True
                    break
                seen_acts.append(((px, py), act.rsplit('.', 1)[-1]))
                # 实测统计（190 次点击失败）：落到下面这几个页面的都是**推广类卡片**，
                # 不是笔记。换偏移量再点也一样，直接放弃这张卡，省下无效点击。
                #   SearchAgentPageActivity     AI 搜索介绍页
                #   ExternalWebViewActivityV2   外部广告落地页
                #   XhsReactTranslucentActivity 半透明推广层
                if any(bad in act for bad in ('SearchAgentPageActivity',
                                              'ExternalWebViewActivityV2',
                                              'XhsReactTranslucentActivity')):
                    break
                press('BACK')
                pause(1.0)
                dump = tree()
                if not note_cards(dump):
                    break
            if not entered:
                stats['skipped'] += 1
                note('多次点击仍未进入笔记详情，跳过；实际跳转到 %s' % (seen_acts or '未知'))
                ensure_state('search')
                # 落到广告页/网页后必须退回搜索结果，否则后续会一直在错页面打转
                for _ in range(4):
                    if 'GlobalSearchActivity' in activity_now():
                        break
                    press('BACK')
                    pause(1.0)
                dump = tree()
                continue
            detail = settle()
            meta = read_note(detail)
            if not meta['title']:
                # 实测：图集/图文笔记首屏常不渲染正文，先向上滑一次再读
                call('mobile_swipe_on_screen',
                     {'direction': 'up', 'distance': 700, 'x': 540, 'y': 1200}, serial)
                pause(1.5)
                detail = tree()          # 同步刷新，后面 is_ad 用的是最新画面
                meta = read_note(detail)
            if not meta['title']:
                # 无正文笔记（纯视频/纯图片）很常见，标题只是参考信息，
                # 真正有价值的是评论者。以前这里直接跳过，等于把整篇的线索扔掉。
                note('笔记没有正文（纯图/纯视频），继续采集评论')
                meta['title'] = ''
            if meta['title'].startswith('相关搜索'):
                stats['skipped'] += 1
                note('相关搜索卡，不是笔记，跳过')
                press('BACK')
                pause(1.2)
                dump = tree()
                continue
            if is_ad(detail):   # 复用刚读到的 dump，少一次控件树读取
                # 实测：评论最多的笔记常常是商业推广，这类没有标准评论区，直接跳过
                note('商业推广笔记，跳过：%s' % meta['title'][:24])
                stats['skipped'] += 1
                press('BACK')
                pause(1.2)
                dump = tree()
                continue

            note('笔记：%s（作者 %s）' % (meta['title'][:24], meta['author_name'] or '未知'))
            stats['notes'] += 1
            taken += 1

            try:
                try:
                    comments_page = open_comments()
                except (SkillError, MobileCallError):
                    # 评论区偶发没打开：重试一次再放弃该笔记（2026-11-03）
                    note('评论区没打开，重试一次')
                    pause(1.5)
                    comments_page = open_comments()
                outcome = collect_leads(comments_per_note, meta.get('author_name', ''),
                                        first_dump=comments_page)
                picked = outcome['found']
                stats['comments'] += outcome['examined']
                declared = declared_comments(comments_page)
                stats['declared'] = stats.get('declared', 0) + declared
                stats['author_skipped'] = stats.get('author_skipped', 0) + outcome['skipped_author']
                stats['author_badge'] = stats.get('author_badge', 0) + outcome.get('skipped_badge', 0)
                stats['author_nick'] = stats.get('author_nick', 0) + outcome.get('skipped_nick', 0)
                if outcome['skipped_author']:
                    note('跳过 %d 条作者本人评论（不需要联系）' % outcome['skipped_author'])
                if not picked and outcome['examined']:
                    note('读了 %d 条评论但没命中意向词；样本：%s'
                         % (outcome['examined'],
                            ' / '.join(outcome['samples']) or '(空)'))
            except (SkillError, MobileCallError) as exc:
                note('读取评论失败，跳过该笔记：%s' % exc)
                picked = []
                # 失败时界面可能停在评论面板/输入框，先退回结果页再继续
                ensure_state('search')

            for item in picked:
                row, why = item['row'], item['why']
                xhs_id, why_fail = item['xhs_id'], item['fail']
                counter += 1
                cd_tick(counter)
                if xhs_id:
                    stats['ids'] += 1
                else:
                    note('未取到「%s」的小红书号（%s），该条仅保留评论原文'
                         % (row['nickname'] or '匿名', why_fail or '未知原因'))
                lead = {
                    'keyword': keyword,
                    # 扁平字段与抖音版对齐，前端渲染可直接复用；xhs_id 是本方案的核心
                    'author': row['nickname'],
                    'content': row['content'],
                    'region': row['region'],
                    'reason': why,
                    'xhs_id': xhs_id,
                    'user_id': item.get('user_id') or '',
                    # match = 主页昵称与评论昵称一致；unknown = 读不到昵称，未验证。
                    # 不能把 unknown 当成已核验，前端要如实展示。
                    'verify': item['status'],
                    # 新方案只做「采集 + 后续私信」，不再产出评论区回复草稿
                    'note_title': meta.get('title', ''),
                    'note_author': meta.get('author_name', ''),
                    'note': meta,
                    'comment': dict(row, xhs_id=xhs_id),
                    'dm_draft': render(dm_tpl, {'keyword': keyword, 'note': meta,
                                                'comment': row}),
                }
                lead['fingerprint'] = build_fingerprint(meta, lead['comment'])
                leads.append(lead)
                stats['leads'] += 1
                note('命中线索：%s（%s）%s' % (row['nickname'] or '匿名', why,
                                             '，小红书号 ' + xhs_id if xhs_id else ''))
                # 边采边存：凑够 5 条立刻入池，不等整篇笔记跑完（2026-11-03）
                pending = [l for l in leads if not l.get('_pooled')]
                if len(pending) >= 5:
                    flush_pool(pending)
                    for l in pending:
                        l['_pooled'] = True

            # 每篇笔记采完就入中转站，避免任务中断丢线索
            flush_pool([l for l in leads if not l.get('_pooled')])
            for lead in leads:
                lead['_pooled'] = True

            # 回结果页：评论面板可能还展开着，用状态机退回，不靠猜 BACK 次数
            if not ensure_state('search'):
                note('离开笔记后没回到搜索结果，尝试重启应用兜底')
                ensure_state('search', allow_restart=True)
            dump = tree()

    task['status'] = 'completed'
    task['stats'] = stats
    flush_pool([l for l in leads if not l.get('_pooled')])
    for lead in leads:
        lead['_pooled'] = True
    for lead in leads:
        lead.pop('_pooled', None)      # 内部标记不进结果文件
    payload = {'task_id': task['id'], 'serial': serial, 'keywords': keywords,
               'stats': stats, 'leads': leads}
    save_result(task['id'], payload)
    note('采集结束：%d 关键词、%d 笔记、%d 条评论，命中 %d 条线索，取得 %d 个小红书号，'
         '入中转站 %d 条（重复跳过 %d）'
         % (stats['keywords'], stats['notes'], stats['comments'], stats['leads'],
            stats['ids'], stats['pooled'], stats['pool_dup']))
    # 结束原因写进任务，界面详情直接展示（2026-11-03 主人要求"状态透明"）
    if target_comments:
        if stats['leads'] >= target_comments:
            task['end_reason'] = '已达到入库目标：%d/%d 条线索（共读 %d 条评论）' % (stats['leads'], target_comments, stats['comments'])
        else:
            # 2026-11-06：不再写"反复搜索后仍没有更多新笔记"——那是旧逻辑的描述，
            # 而且没说明真正原因。现在把"翻到底"和"命中率"一起讲清楚，
            # 让主人一眼看出是**意向词太窄**还是**真没内容**。
            _read = stats['comments'] or 0
            _hit = stats['leads'] or 0
            _rate = (100.0 * _hit / _read) if _read else 0.0
            task['end_reason'] = (
                '目标未达成：已入库 %d/%d 条线索（共读 %d 条评论，命中率 %.1f%%）；'
                '原因：关键词「%s」列表已翻到底（连续多次滑动无新笔记），没有更多可采内容'
                % (_hit, target_comments, _read, _rate, ','.join(keywords[:3])))
    else:
        task['end_reason'] = '未设入库目标，按配置上限跑完：%d 条线索（读 %d 条评论）' % (stats['leads'], stats['comments'])
    if stats.get('leads') == 0 and intent_words:
        task['end_reason'] += '；意向词（%s）0 命中，可留空改为全量收录' % '、'.join(intent_words[:5])
    note('结束原因：' + task['end_reason'])


# ---------------------------------------------------------------- B 端：凭指纹定位
def ensure_app_home(serial, rounds=3):
    """把小红书强行拉回首页；导航卡死时兜底用。

    实测：停在私信会话页/详情页时，纯靠 BACK 可能退不出来，
    后续再想搜索就会报「搜索按钮不存在」。这里用 HOME + 重新拉起兜底。
    """
    for _ in range(rounds):
        try:
            call('mobile_launch_app', {'packageName': PACKAGE}, serial)
        except MobileCallError:
            pass
        time.sleep(3.0)
        for _ in range(3):
            try:
                dump = elements(call('mobile_list_elements_on_screen',
                                     {'format': 'json'}, serial))
            except (SkillError, MobileCallError):
                break
            home = next((e for e in dump if _text(e) == '首页' and _ref_ok(e)), None)
            if home is not None:
                call('mobile_click_on_screen_at_coordinates', {'ref': home['ref']}, serial)
                time.sleep(2.0)
                return True
            try:
                call('mobile_press_button', {'button': 'BACK'}, serial)
            except MobileCallError:
                break
            time.sleep(1.0)
        try:
            call('mobile_press_button', {'button': 'HOME'}, serial)
        except MobileCallError:
            pass
        time.sleep(1.5)
    return False


def relocate_by_id(serial, xhs_id, stop=None, gate=None):
    """B 端：用小红书号定位到唯一用户，返回该用户的昵称与主页是否打开。

    只定位、不发送。找不到唯一命中就返回 None，绝不按昵称兜底。
    """
    if not ID_VALUE_RE.fullmatch(str(xhs_id or '')):
        # 小红书号可能含字母（实测 iiiu0417o），不能只认数字
        raise SkillError('小红书号格式不合法：%r' % xhs_id)

    def check():
        if stop is not None and stop.is_set():
            raise SkillError('任务已停止')

    def tree():
        check()
        return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))

    def tap(el):
        check()
        if gate is not None:
            with gate:
                # 等锁期间可能被取消，拿到锁后再查一次
                check()
                call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)
        else:
            call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)

    # 导航到搜索页：先找搜索入口，找不到就逐层返回，最多 5 次；
    # 还不行就 HOME + 重新拉起兜底（实测停在会话页时纯 BACK 退不出来）
    #
    # 安全修正（2026-11-01）：以前只判断「页面上有 EditText」，
    # 私信会话页也有输入框，于是会把小红书号直接敲进**对方的聊天框**里。
    # 必须以 GlobalSearchActivity 为准。
    def on_search_page():
        try:
            return 'GlobalSearchActivity' in foreground_activity(
                call('mobile_get_foreground_app', {}, serial))
        except (SkillError, MobileCallError):
            return False

    dump = tree()
    ready = False
    for attempt in range(2):
        for _ in range(5):
            if on_search_page() and any('EditText' in _type(e) for e in dump):
                ready = True
                break
            entry = next((e for e in dump if 'Button' in _type(e)
                          and (_label(e) == '搜索' or _text(e) == '搜索')), None)
            if entry is not None:
                tap(entry)
                time.sleep(1.6)
                dump = tree()
                continue
            call('mobile_press_button', {'button': 'BACK'}, serial)
            time.sleep(1.2)
            dump = tree()
        if ready:
            break
        if attempt == 0:
            ensure_app_home(serial)
            dump = tree()
    if not ready:
        raise SkillError('未能进入搜索页，无法按小红书号定位')
    box = next((e for e in dump if 'EditText' in _type(e) and _ref_ok(e)), None)
    if box is None:
        raise SkillError('搜索框不存在')
    tap(box)
    time.sleep(0.5)
    call('mobile_type_keys', {'text': str(xhs_id)}, serial)
    time.sleep(0.7)
    dump = tree()
    # 搜索键实测可能是 TextView 而不是 Button，两种都认，取最靠上的
    cands = [e for e in dump if _text(e) == '搜索' and _ref_ok(e)]
    if not cands:
        raise SkillError('搜索按钮不存在')
    buttons = [e for e in cands if 'Button' in _type(e)]
    submit = (min(buttons, key=lambda e: (_box(e) or {'y': 9999})['y'])
              if buttons else min(cands, key=lambda e: (_box(e) or {'y': 9999})['y']))
    tap(submit)
    time.sleep(3.0)

    # 切用户 Tab
    dump = tree()
    tab = next((e for e in dump if _text(e) == '用户' and _ref_ok(e)), None)
    if tab is None:
        raise SkillError('「用户」分类不存在')
    tap(tab)

    # 等列表真正刷新：连续两次读到的号集合一致才认为加载完。
    # 实测教训（101 次压测）：固定 sleep 会读到切换前的旧内容，
    # 让明明存在的号被判成「零命中」。改成稳定性等待后复测 3/3 全部命中。
    last, current = None, None
    for _ in range(6):
        time.sleep(1.0)
        current = tree()
        ids = tuple(sorted(r['xhs_id'] for r in collect_xhs_ids(current)))
        if ids and ids == last:
            break
        last = ids
    dump = current if current is not None else tree()

    rows = collect_xhs_ids(dump)
    hit = pick_unique_id(rows, str(xhs_id))
    if hit is None:
        return None
    return hit


# ---------------------------------------------------------------- B 端：进会话
# 实测（2026-11-01）会话页 `com.xingin.im.ui.activity.ChatActivity`：
#   · 输入框 EditText 约 (158,1609) 664x105，带 value 字段可回读；
#   · 发送键是 **TextView「发送」不是 Button**，约 (928,1619) 99x84，
#     **空输入时元素根本不存在**，有内容才出现，清空后又消失；
#   · 固定提示「对方关注或回复你之前，最多只能发送1条消息」——
#     每个潜客只有一次机会，所以本模块**只做到「填好草稿、确认发送键存在」为止**，
#     真正点击发送必须由人工确认后另行执行。
QUOTA_RE = re.compile(r'^对方关注或回复你之前，最多只能发送(?P<n>\d+)条消息$')
# 手机端单次输入的实际上限：实测 1845 字整段失败、1000 字成功，
# 所以按 1000 兜底，超过就明确报错而不是静默截断。
INPUT_LIMIT = 1000


def session_info(dump) -> dict:
    """从会话页 dump 里读出配额、输入框、发送键状态。纯函数，便于离线测试。"""
    quota_limit, quota_hint = None, ''
    for el in dump:
        match = QUOTA_RE.match(_text(el))
        if match:
            quota_limit = int(match.group('n'))
            quota_hint = _text(el)
            break
    edit = next((e for e in dump if 'EditText' in _type(e)), None)
    send = next((e for e in dump if _text(e) == '发送'), None)
    # 实测 MCP 的 EditText 会同时给 text 与 value；value 更能代表输入框真实内容，
    # 输入较长或含换行时 text 偶有截断，所以优先取 value。
    input_value = ''
    if edit is not None:
        input_value = str(edit.get('value') or _text(edit) or '')
    return {
        'quota_limit': quota_limit,
        'quota_hint': quota_hint,
        'input_value': input_value,
        'input_box': _box(edit) if edit is not None else None,
        'send_present': send is not None,
        'send_box': _box(send) if send is not None else None,
    }


def open_chat(serial, xhs_id, stop=None, gate=None, user_id=''):
    """B 端：进对方主页 → 点私信 → 打开会话。

    2026-11-05 起优先走**直达**（`xhsdiscover://user/<24位user_id>`）：
    有 user_id 就直接开主页，省掉"搜索小红书号 → 找唯一命中 → 点进去"这一串，
    也就没有了搜错人的风险。没有 user_id 或直达失败，回落原来的 relocate_by_id。

    只打开会话，不输入、不发送。身份仍用主页上的小红书号二次核对。
    """
    def check():
        if stop is not None and stop.is_set():
            raise SkillError('任务已停止')

    def tree():
        check()
        return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))

    def tap(el):
        check()
        if gate is not None:
            with gate:
                # 等锁期间可能已被取消，拿到锁必须再查一次
                check()
                call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)
        else:
            call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, serial)

    def click(x, y):
        check()
        if gate is not None:
            with gate:
                check()
                call('mobile_click_on_screen_at_coordinates', {'x': x, 'y': y}, serial)
        else:
            call('mobile_click_on_screen_at_coordinates', {'x': x, 'y': y}, serial)

    direct = False
    if user_id:
        try:
            from . import deeplink as DL
            r = DL.open_profile(serial, 'xiaohongshu', user_id, wait=8.5)
            direct = bool(r.get('ok'))
        except Exception:   # noqa: BLE001 直达异常一律回落
            direct = False

    try:
        from .deeplink import note as _note
        _note('xhs direct %s user_id=%s' % ('HIT' if direct else 'MISS->search', user_id))
    except Exception:
        pass

    if not direct:
        hit = relocate_by_id(serial, xhs_id, stop=stop, gate=gate)
        if hit is None:
            return None
        # 号所在行上方约 150px 是昵称/头像区，点它进主页（实测稳定）
        click(300, max(200, int(hit['y'] - 150)))
        time.sleep(2.8)
    profile = tree()
    prof_id, prof_nick = read_profile_info(profile)
    # 审核修正：以前「读不到号」会被放行，等于可能对着别人主页点私信。
    # 现在必须拿到号且与目标完全一致，读不到同样视为不可确认。
    if not prof_id:
        raise SkillError('未能从对方主页读到小红书号，无法确认身份，已停止')
    if prof_id != str(xhs_id):
        raise SkillError('进入的主页小红书号与目标不符：%s != %s' % (prof_id, xhs_id))

    dm = next((e for e in profile if _text(e) == '私信'), None)
    if dm is None:
        # 对方关闭了私信权限——这是正常的业务情况，不是程序错误。
        # 以前这里抛异常，批量跑的时候一条就中断整批。
        # 现在返回结构化结果，调用方可以跳过这条继续下一条。
        return {'xhs_id': str(xhs_id), 'nickname': prof_nick, 'opened': False,
                'reason': 'no_dm_button',
                'activity': foreground_activity(call('mobile_get_foreground_app', {}, serial))}
    tap(dm)
    time.sleep(3.0)

    # 审核修正：以前不验证是否真的进了会话页，可能在任意页面填草稿。
    activity = foreground_activity(call('mobile_get_foreground_app', {}, serial))
    if 'ChatActivity' not in activity:
        raise SkillError('没有进入私信会话页（当前 %s），已停止' % (activity or '未知'))

    chat = tree()
    info = session_info(chat)
    if info['input_box'] is None:
        raise SkillError('会话页没有输入框，已停止')
    info.update({'xhs_id': str(xhs_id), 'nickname': prof_nick,
                 'activity': activity, 'opened': True})
    return info


def draft_message(serial, xhs_id, text, stop=None, gate=None, already_open=False,
                  expect_nickname='', user_id=''):
    """在会话里填入文案，并校验「输入内容正确 + 发送键确实出现」，然后停住。

    **不点发送。** 返回里 `drafted` 才是能不能进入人工确认的判据。

    审核修正（2026-11-01）：
      · 输入框已有内容时直接拒绝，避免把旧草稿污染成「旧+新」；
      · 分块输入每块都检查取消并持锁，取消后不再继续写屏；
      · 结尾同时要求 send_present，光有文字不算草稿就绪。

    already_open=True 表示调用方已经打开过会话：
    实测 BUG——以前本函数无条件再调一次 open_chat，人在会话页里它会回头
    重新搜索，报「搜索按钮不存在」，等于把已经打开的会话白白关掉。
    """
    if not (text or '').strip():
        raise SkillError('文案为空，不能进入会话')

    # 安全防护：换行符绝不能带进输入框。
    # 实测数据里就有评论正文带 \n（「你这把还好…\n[视频]3s」），
    # 部分输入法/应用会把回车当「发送」，那就等于替人按下发送键。
    # 这里统一把换行替换成空格，行为不变但绝不触发回车。
    original_text = text
    text = re.sub(r'[\r\n]+', ' ', text).strip()
    sanitized = text != original_text.strip()

    def check():
        if stop is not None and stop.is_set():
            raise SkillError('任务已停止')

    def tree():
        check()
        return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))

    def act(fn):
        """所有会写设备的动作统一走这里：等锁期间被取消也不执行。"""
        check()
        if gate is not None:
            with gate:
                check()
                return fn()
        return fn()

    if already_open:
        # 已经由调用方打开会话：只做一次活动校验，绝不重新导航
        activity = foreground_activity(call('mobile_get_foreground_app', {}, serial))
        if 'ChatActivity' not in activity:
            raise SkillError('当前不在私信会话页（%s），无法填草稿' % (activity or '未知'))
        state = {'nickname': '', 'xhs_id': str(xhs_id), 'opened': True}
    else:
        state = open_chat(serial, xhs_id, stop=stop, gate=gate, user_id=user_id)
        if state is None:
            return None
        if state.get('opened') is False:
            # 会话没打开（例如对方关闭了私信权限）：如实返回，不能当成草稿已就绪
            return {'xhs_id': str(xhs_id), 'nickname': state.get('nickname', ''),
                    'drafted': False, 'reason': state.get('reason') or 'not_opened'}
    check()

    dump = tree()
    edit = next((e for e in dump if 'EditText' in _type(e)), None)
    if edit is None:
        raise SkillError('会话页没有输入框')
    existing = str(edit.get('value') or _text(edit) or '').strip()
    if existing:
        raise SkillError('输入框已有内容（%s…），为避免污染旧草稿已停止'
                         % existing[:12])

    act(lambda: call('mobile_click_on_screen_at_coordinates', {'ref': edit['ref']}, serial))
    time.sleep(0.8)

    # 实测 BUG（2026-11-02）：以前按 1000 字分块连续输入，结果**只有第一块生效**，
    # 1845 字的草稿回读只有 1000 字，后半段整段丢失。
    # 网关单次上限是 2000 字，所以只要不超过它就一次写完；
    # 超过则明确报错，绝不静默截断（静默截断会让客户收到半句话）。
    if len(text) > INPUT_LIMIT:
        raise SkillError('私信文案 %d 字，超过单次输入上限 %d 字，请缩短后再发'
                         % (len(text), INPUT_LIMIT))
    act(lambda: call('mobile_type_keys', {'text': text}, serial))
    time.sleep(0.8)

    time.sleep(1.0)
    after = tree()
    info = session_info(after)
    info.update({'xhs_id': str(xhs_id), 'nickname': state.get('nickname', ''),
                 'intended_text': text, 'sanitized': sanitized})
    if info['input_value'].strip() != text.strip():
        info['drafted'] = False
        info['mismatch'] = '输入框内容与预期不一致'
    elif not info['send_present']:
        info['drafted'] = False
        info['mismatch'] = '输入框有内容但「发送」键未出现，页面可能未就绪'
    else:
        info['drafted'] = True
    return info


def save_result(task_id, payload):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    target = RESULTS_DIR / (re.sub(r'[^A-Za-z0-9_-]', '', task_id) + '.json')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)


def load_result(task_id):
    path = RESULTS_DIR / (re.sub(r'[^A-Za-z0-9_-]', '', task_id) + '.json')
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
