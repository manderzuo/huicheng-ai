"""抖音截流：按关键词搜索 → 应用筛选 → 逐个进视频 → 读评论 → 判定线索 → 生成草稿。

只读采集，不自动发送：发送必须在界面上逐条确认，本模块不含任何发送动作。
所有定位都来自当次 UI 树，不缓存坐标；命中不唯一或页面不符即停止，不猜测点击。
"""
from __future__ import annotations
import json
import re
import time
import uuid
from pathlib import Path

from . import douyin_templates as templates
from .douyin_skill import SkillError, elements, foreground_package
from .mobile_gateway import MobileCallError, call

SKILL_ID = 'builtin:douyin-lead'
SKILL = {
    'id': SKILL_ID,
    'name': '抖音截流（按关键词找客户）',
    'description': '搜索关键词、应用筛选、逐个看视频评论，筛出高意向线索并生成回复草稿；不自动发送。',
}
PACKAGE = 'com.ss.android.ugc.aweme'
PLATFORM = 'douyin'
RESULTS_DIR = Path(__file__).resolve().parents[2] / 'data' / 'lead_results'

SELECTABLE = ('sort', 'publish_time', 'duration', 'scope', 'distance')
RISKY_DEFAULT_EXCLUDE = ('招聘', '招人', '兼职', '免费送', '投诉')


# ---------------------------------------------------------------- 基础工具
def _ref_ok(el) -> bool:
    """只有形如 @e12 的引用才能点，避免把脏数据当坐标。"""
    return bool(re.fullmatch(r'@e[1-9]\d*', str((el or {}).get('ref', ''))))


def _box(el):
    c = (el or {}).get('coordinates') or {}
    if not all(isinstance(c.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
        return None
    if c['width'] <= 0 or c['height'] <= 0:
        return None
    return c


def _text_of(el, *keys):
    for key in keys:
        value = str((el or {}).get(key) or '').strip()
        if value:
            return value
    return ''


def pick_unique(tree, page, name):
    """在当次树中找唯一匹配；0 个或多个都视作无法确认。"""
    target = templates.PAGES[page].targets[name]
    hits = [e for e in tree if templates.matches(e, target) and _ref_ok(e)]
    if len(hits) != 1:
        return None
    return hits[0]


def pick_all(tree, page, name):
    target = templates.PAGES[page].targets[name]
    return [e for e in tree if templates.matches(e, target) and _ref_ok(e)]


def by_id(tree, element_id):
    suffix = PACKAGE + ':id/' + element_id
    return [e for e in tree if str(e.get('identifier') or '') == suffix and _ref_ok(e)]


def page_kind(tree) -> str:
    """用实测锚点判别当前页面，避免把页面 A 的操作套到页面 B。

    顺序很重要：播放页顶部也有 et_search_kw（「搜你想看的」），
    所以必须先认评论页/播放页，最后才认搜索页，否则播放页会被当成搜索页。
    """
    ids = {str(e.get('identifier') or '').rsplit(':id/', 1)[-1] for e in tree}
    if 'e7+' in ids and any('评论' in _text_of(e, 'text') for e in by_id(tree, 'e7+')):
        return 'comments'
    # 文案兜底（不依赖混淆 id）：评论容器 label 形如「昵称,正文,时间,地区,回复 按钮」
    for e in tree:
        if ITEM_RE.match(_text_of(e, 'label') or ''):
            return 'comments'
    if 'e9n' in ids and 'gxu' in ids:
        return 'player'
    # 文案兜底：播放页右侧是「…喜欢…按钮」「评论NNN，按钮」
    blob = ' '.join('%s %s' % (_text_of(e, 'text'), _text_of(e, 'label'))
                    for e in tree)
    if ('评论' in blob and '按钮' in blob
            and ('喜欢' in blob or '点赞' in blob)):
        return 'player'
    if 'et_search_kw' in ids:
        return 'search'
    if 'iuy' in ids:
        return 'search'
    return 'unknown'


# ---------------------------------------------------------------- 评论解析
ITEM_RE = re.compile(r'^(?P<author>[^,]*),(?P<content>.*?),(?P<age>[^,]*),\s*·?\s*(?P<region>[^,]*),\s*回复\s*按钮,?$', re.S)


def parse_comment_container(el):
    """从评论容器 label 拆出昵称/正文/时间/地区；格式不符返回 None，不硬拆。"""
    label = _text_of(el, 'label')
    if not label:
        return None
    match = ITEM_RE.match(label)
    if not match:
        return None
    row = {k: (v or '').strip() for k, v in match.groupdict().items()}
    if not row['content']:
        return None
    return row


def read_comments(tree):
    """按 y 排序返回当前可见评论；同一容器只取一次。

    2026-11-05：41号的控件 id 与基准机不同，`pick_all('comments','item')` 一条都命中不到
    （表现：评论区打开了却"新增 0 条评论"）。改为**先按 label 认容器**——
    能解析出昵称+正文的 label 就是评论容器，与 id 无关；解析不到再回退 id 规则。
    """
    rows, seen = [], set()
    containers = []
    for el in tree:
        label = _text_of(el, 'label')
        if label and parse_comment_container(el):
            containers.append(el)
    if not containers:
        containers = list(pick_all(tree, 'comments', 'item'))
    for el in containers:
        box = _box(el)
        if not box:
            continue
        parsed = parse_comment_container(el)
        if not parsed:
            continue
        key = (box['y'], parsed['author'], parsed['content'][:40])
        if key in seen:
            continue
        seen.add(key)
        parsed['y'] = box['y']
        rows.append(parsed)
    rows.sort(key=lambda r: r['y'])
    return rows


# ---------------------------------------------------------------- 视频卡片
def _contains(outer, inner):
    """inner 是否完整落在 outer 内（用于把 desc/时长归属到正确的卡片容器）。"""
    if not outer or not inner:
        return False
    return (outer['x'] <= inner['x'] and outer['y'] <= inner['y']
            and inner['x'] + inner['width'] <= outer['x'] + outer['width']
            and inner['y'] + inner['height'] <= outer['y'] + outer['height'])


def card_identity(tree, cover, box=None):
    """给视频卡片算一个"跨屏不变"的身份：描述文本 + 时长。

    实测 2026-11-01：搜索结果一旦滚动，**同一个视频的封面 y 坐标会变**，
    若按坐标判重，同一视频会被反复采集（10 次进入只有 5 个不同视频，
    约一半时间浪费在重复取号上）。渲染出来的标题和时长则相对稳定。

    取不到文本时返回 None，调用方退回坐标判重（宁可重复，不漏采）。
    """
    box = box or _box(cover)
    if not box:
        return None
    for container in by_id(tree, 'dr1'):
        cbox = _box(container)
        if not _contains(cbox, box):
            continue
        descs = [e for e in by_id(tree, 'desc') if _contains(cbox, _box(e))]
        durations = [e for e in by_id(tree, '5h3') if _contains(cbox, _box(e))]
        desc = _text_of(descs[0], 'text') if descs else ''
        duration = _text_of(durations[0], 'text') if durations else ''
        if desc:
            return ('card', desc, duration)
        # 描述取不到时，**不能只拿时长当身份**：大量视频时长都是 03:02 之类，
        # 会把这个视频误判成"已采过"从而漏采。此时退回坐标（宁可重复，不漏）。
    text = _text_of(cover, 'text', 'label')
    return ('cover', text) if text else None


def video_cards(tree):
    """取搜索结果里的视频卡片：容器 dr1 内的 cover，按 y 排序。

    每张卡片附带 ``identity``（描述+时长），供调用方跨屏去重。
    """
    cards, seen = [], set()
    for el in pick_all(tree, 'search_video', 'cover'):
        box = _box(el)
        if not box:
            continue
        identity = card_identity(tree, el, box)
        key = identity or ('xy', box['x'], box['y'])
        if key in seen:
            continue
        seen.add(key)
        cards.append({'el': el, 'x': box['x'], 'y': box['y'], 'identity': identity})
    cards.sort(key=lambda c: (c['y'], c['x']))
    return cards


def screen_dimensions(result):
    """只接受明确的屏幕像素尺寸；未知时拒绝，不猜设备分辨率。"""
    candidates = set()
    for item in (result or {}).get('content', []):
        if item.get('type') != 'text':
            continue
        text = item.get('text', '')
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            value = None
        if isinstance(value, dict):
            w, h = value.get('width'), value.get('height')
            if type(w) is int and type(h) is int and w > 0 and h > 0:
                candidates.add((w, h))
        for w, h in re.findall(r'(?:screen\s*(?:size|dimensions)\s*(?:is\s*)?[:：]?\s*)(\d+)\s*[x×]\s*(\d+)', text, re.I):
            if int(w) > 0 and int(h) > 0:
                candidates.add((int(w), int(h)))
    if len(candidates) != 1:
        raise SkillError('作者取号：屏幕尺寸无法唯一确认，已停止')
    return candidates.pop()


def _visible(el, screen_size):
    """只使用有效的屏内起点；排除层级中的屏外残留节点。"""
    box = _box(el)
    return bool(box and 0 <= box['x'] < screen_size[0]
                and 0 <= box['y'] < screen_size[1]
                and box['x'] + box['width'] <= screen_size[0]
                and box['y'] + box['height'] <= screen_size[1]
                and el.get('visible', True) is not False
                and el.get('displayed', True) is not False)


def _raw_text(el):
    # 身份快照保留空白和完整文本；展示用的 strip/截断不能用于返回校验。
    return str((el or {}).get('text') or '')


def player_snapshot(tree, screen_size):
    """快照是一次直接导航的局部证据，不声称标题/昵称全局唯一。"""
    visible = [e for e in tree if _visible(e, screen_size)]
    if (page_kind(visible) != 'player' or len(by_id(visible, 'e9n')) != 1
            or len(by_id(visible, 'gxu')) != 1):
        raise SkillError('作者取号：无法确认可见播放页，已停止')
    author = pick_unique(visible, 'video_player', 'author_name')
    desc = pick_unique(visible, 'video_player', 'desc')
    if (author is None or desc is None or not _raw_text(desc).strip()
            or not _raw_text(author).startswith('@') or not _raw_text(author)[1:]):
        raise SkillError('作者取号：播放页作者或完整标题不存在/不唯一，已停止')
    return {'author_text': _raw_text(author), 'desc': _raw_text(desc)}, author


def collect_author_from_player(page, *, read_tree, tap, press, pause, check,
                               screen_size, attempts=6):
    """原player→唯一作者→主页取号→BACK一次→原player；不搜索、不缓存。

    仅重读页面，不重放任何写动作；任一调用异常（包括unknown/stop）原样抛出，
    失败时不尝试额外BACK。昵称只做直接导航后的严格一致性校验。
    """
    check()
    snapshot, author = player_snapshot(page, screen_size)
    tap(author)
    account = None
    for attempt in range(attempts):
        check()
        current = [e for e in read_tree() if _visible(e, screen_size)]
        names, ids = by_id(current, 'tk1'), by_id(current, '50g')
        if len(names) == 1 and len(ids) == 1:
            nick = _text_of(names[0], 'text', 'label')
            # 部分版本的label附带固定无障碍说明；不模糊化真实昵称。
            if not _raw_text(names[0]):
                nick = re.sub(r'[，,]复制名字和修改备注$', '', nick)
            raw = _text_of(ids[0], 'text', 'label')
            match = re.fullmatch(r'抖音号[:：]\s*([A-Za-z0-9_][A-Za-z0-9_.-]{0,63})', raw)
            if nick == snapshot['author_text'][1:] and match:
                account = match.group(1)
                break
        if attempt + 1 < attempts:
            pause(0.8)
    if account is None:
        raise SkillError('作者取号：主页昵称/唯一抖音号未严格核实，已停止')
    check()
    press('BACK')
    for attempt in range(attempts):
        check()
        current = read_tree()
        try:
            returned, _ = player_snapshot(current, screen_size)
        except SkillError:
            check()  # stop不能当作页面尚未就绪而吞掉
        else:
            if returned == snapshot:
                return account
        if attempt + 1 < attempts:
            pause(0.8)
    raise SkillError('作者取号：BACK后未确认原播放页作者及完整标题，已停止')


def describe_current_video(tree):
    desc = pick_unique(tree, 'video_player', 'desc')
    author = pick_unique(tree, 'video_player', 'author_name')
    entry = pick_unique(tree, 'video_player', 'comment_entry')
    count = 0
    if entry is not None:
        match = re.search(r'评论\s*([\d.]+[万亿]?)', _text_of(entry, 'label'))
        if match:
            count = match.group(1)
    return {
        'desc': _raw_text(desc),
        'author': _raw_text(author).removeprefix('@'),
        'comment_count': count,
    }


# ---------------------------------------------------------------- 线索判定
#: 抖音给「视频作者本人」的评论打的徽章。评论容器解析出来后，正文会以这个前缀开头，
#: 例如 label「解构Part,作者,代号名字吧！…」→ author=解构Part、content=作者,代号名字吧！…
#: 实测 2026-11-01：池中 6 条此类评论，评论人昵称与视频作者昵称 6/6 完全重合，互相印证。
AUTHOR_BADGE = '作者,'

#: 单个视频最多扫描多少条评论。
#: 评论列表可能极长（实测见过 713 条评论、只命中 3 条），读到底要 16 分钟；
#: 命中率低于 1% 时继续滚动的边际收益很低。超过这个量就停止滚动该视频。
MAX_SCAN_COMMENTS = 300

#: 连续多少屏全是"已在池"的重复评论就停止。
#: 换关键词会命中同一批热门视频，此时该视频的评论早已全部入池，
#: 再读完只是白花时间（实测 126 条读 6 分钟却新增 0 条）。
MAX_DUPLICATE_SCREENS = 5

#: 同一个视频已经采到这么多条线索后，就不再重扫它的评论区。
#: 实测同一个视频第 1 轮出 15 条、第 4 轮只剩 1 条却仍要 8 分钟，
#: 因为评论要一屏一屏重新读。超过阈值直接跳过，把时间花在新视频上。
MAX_LEADS_PER_VIDEO = 30


def scan_budget_exhausted(scanned, taken):
    """按当前命中率判断是否还值得继续滚动读评论。

    固定上限两头不讨好：实测 713 条评论命中 3 条（0.4%）要读 16 分钟，
    而 145 条命中 15 条（10%）值得读到底。这里按真实命中率动态决定：
      · 前 80 条一律给足机会；
      · 一条都没命中 → 扫到 120 条放弃；
      · 命中率 < 2% → 扫到 180 条放弃；
      · 命中率不错 → 继续，直到硬上限。
    """
    # 2026-11-05：评论多的时候"没翻完就走了"——命中率低就 120/180 条收手。
    # 真正的"到底"判据在读数循环里（连续两屏没有新内容），这里只做防跑飞的硬上限，
    # 所以把额度整体放宽，让它在评论多时继续往下翻。
    if scanned < 200:
        return False
    if scanned >= MAX_SCAN_COMMENTS:
        return True
    rate = taken / float(scanned)
    if rate <= 0.0:
        return scanned >= 400
    if rate < 0.02:
        return scanned >= 600
    return False


def lead_store_count(platform, video_title):
    """统计某视频已采到的线索数；导入失败时按 0 处理（不阻塞采集）。"""
    try:
        from . import lead_store
        return lead_store.count_by_video(platform, video_title)
    except Exception:
        return 0


def is_author_comment(comment):
    """评论人是不是视频作者本人。

    作者在自己视频下回复/置顶，是内容生产行为，不是潜在客户，**不应进入线索池**。
    主判据是抖音的「作者」徽章（零成本、当前实测准确）；
    若采集时同时取了视频作者号，再用账号相等做第二重校验。
    """
    text = str(comment.get('content') or comment.get('comment_text') or '').strip()
    if text.startswith(AUTHOR_BADGE):
        return True, '评论带「作者」徽章'
    commenter = str(comment.get('commenter_douyin_id') or '').strip()
    video_author = str(comment.get('author_douyin_id') or '').strip()
    if commenter and video_author and commenter == video_author:
        return True, '评论人抖音号与视频作者相同'
    return False, ''


def judge(comment, intent_words, exclude_words):
    """规则判定：先排除作者本人，再排除词，最后命中意向词。

    intent_words 为空表示“不筛意向、全量收录”——这种模式用于验证跨机定位成功率，
    此时只要没命中排除词就算一条线索。
    """
    text = comment.get('content', '')
    author_self, why = is_author_comment(comment)
    if author_self:
        # 作者自己的评论不回复、不入池：这不是客户线索
        return False, '评论人是视频作者本人（%s），不回复' % why
    for word in exclude_words:
        if word and word in text:
            return False, '命中排除词「%s」' % word
    if not [w for w in intent_words if w]:
        return True, '未设意向词：全量收录'
    hits = [w for w in intent_words if w and w in text]
    if hits:
        return True, '命中意向词：' + '、'.join(hits[:3])
    return False, ''


def render_draft(template, comment, video):
    """只做占位符替换，不编造内容；模板为空则返回空。"""
    if not template.strip():
        return ''
    return (template.replace('{昵称}', comment.get('author', ''))
                    .replace('{评论}', comment.get('content', ''))
                    .replace('{作者}', video.get('author', ''))
                    .replace('{视频}', video.get('desc', '')))


# ---------------------------------------------------------------- 主流程
def run(task):
    from . import execution

    cfg = task.get('lead') or {}
    # 统一采集面板（leadx）字段兼容：sort_by -> sort
    if cfg.get('sort_by') and not cfg.get('sort'):
        cfg['sort'] = cfg.get('sort_by')
    try:
        from .time_filter import within_days as _within_days
        _max_age = int(cfg.get('max_age_days') or 0)
        _base_ts = float(cfg.get('task_created_at') or task.get('created_at') or time.time())
    except Exception:
        _max_age, _base_ts = 0, time.time()
    keywords = [str(k).strip() for k in (cfg.get('keywords') or []) if str(k).strip()]
    if not keywords:
        raise SkillError('请至少填一个关键词')

    intent_words = [str(w).strip() for w in (cfg.get('intent_words') or []) if str(w).strip()]
    # 留空 = 不筛意向、全量收录（用于验证跨机定位成功率）
    exclude_words = [str(w).strip() for w in (cfg.get('exclude_words') or []) if str(w).strip()]
    exclude_words += [w for w in RISKY_DEFAULT_EXCLUDE if w not in exclude_words]

    videos_per_keyword = int(cfg.get('videos_per_keyword') or 5)
    comments_per_video = int(cfg.get('comments_per_video') or 20)
    # 采集目标（累计线索条数）：界面只让用户填这一个数字。
    # 0 表示不限，按视频/评论上限跑完。
    target_comments = int(cfg.get('target_comments') or 0)
    # 2026-11-06 主人定：**不设上限**，一直往下翻，只有两个停点：
    #   ① 采集数量够了（target_comments）
    #   ② 真的翻到底（连续 MAX_EMPTY_SWIPES 次滑动都没有新卡片）
    # 原来这里是 videos_per_keyword=500 + scroll_limit=30 屏 + "重搜同一关键词 5 次"。
    # 同一个关键词、同一个排序重搜，结果还是同一批视频（全在 processed 里），
    # 5 次重搜纯空转，最后报"没有更多新视频"——跟小红书是同一个毛病。
    MAX_EMPTY_SWIPES = 6
    scroll_limit = MAX_EMPTY_SWIPES if target_comments else 8
    cooldown_every = max(1, int(cfg.get('cooldown_every') or 10))
    # 采集不冷却：0 表示不停；只有「回复」才用冷却（2026-11-03 调整）
    cooldown_seconds = max(0, int(cfg.get('cooldown_seconds') or 0))
    # 只读采集不发送，用户可以要求翻快点；0.6 表示各项等待压到 60%
    pace_factor = min(2.0, max(0.2, float(cfg.get('pace_factor') or 1.0)))
    reply_tpl = str(cfg.get('reply_template') or '')
    dm_tpl = str(cfg.get('dm_template') or '')

    serial = task['serial']
    stop = task['_stop']
    leads = task.setdefault('leads', [])
    stats = {'keywords': 0, 'videos': 0, 'comments': 0, 'leads': 0, 'skipped': 0}
    # 一开始就进入 executing：否则前端一直显示"排队中"，
    # 明明在采也像死掉了（2026-11-03 实测踩过）。
    task['status'] = 'executing'

    def check():
        if stop.is_set():
            raise SkillError('任务已停止')

    def to_pool_rows(items):
        """把采集结果转成中转站字段；不同平台只写自己表里有的列。"""
        return [{'comment_author': row.get('comment_author') or row.get('author') or '',
                 'comment_text': row.get('comment_text') or row.get('content') or '',
                 'commenter_douyin_id': row.get('commenter_douyin_id') or '',
                 'commenter_uid': row.get('commenter_uid') or '',
                 'commenter_nickname': row.get('commenter_nickname') or '',
                 'commenter_identity_source': row.get('commenter_identity_source') or '',
                 'commenter_identity_evidence': row.get('commenter_identity_evidence') or {},
                 'region': row.get('region') or '',
                 'time_text': row.get('time_text') or row.get('age') or '',
                 'video_title': row.get('video_title') or '',
                 'video_author': row.get('video_author') or '',
                 'author_douyin_id': row.get('author_douyin_id') or '',
                 'author_identity_source': row.get('author_identity_source') or '',
                 'author_identity_evidence': row.get('author_identity_evidence') or {},
                 'keyword': row.get('keyword') or '',
                 'filters': row.get('filters') or {},
                 'reply_text': row.get('reply_draft') or '',
                 'dm_text': row.get('dm_draft') or ''} for row in items]

    def flush_pool(items):
        """每个视频结束就入池：任务中途失败时，已采到的线索不会全部丢失。"""
        if not items:
            return
        try:
            from . import lead_store
            pooled = lead_store.add_leads(PLATFORM, to_pool_rows(items), serial=serial)
            if pooled['added']:
                execution.log(task, '已增量入池 %d 条' % pooled['added'])
        except Exception as exc:
            execution.log(task, '增量入池失败（稍后仍会统一补写）：%s' % exc)

    def pause(seconds):
        # 只读采集不发送，节奏可以按 pace_factor 压缩；页面没加载完时靠 settle 兜底
        if stop.wait(max(0.05, seconds * pace_factor)):
            check()

    def tree():
        """读界面树；uiautomator 偶发取不到层级，重试几次再放弃，避免整任务中断。"""
        check()
        last = None
        for attempt in range(3):
            check()
            try:
                return elements(call('mobile_list_elements_on_screen',
                                      {'format': 'json'}, serial))
            except (SkillError, MobileCallError) as exc:
                last = exc
                if attempt < 2:
                    pause(0.8)
        raise last

    def tap(el):
        check()
        if not _ref_ok(el):
            raise SkillError('控件引用不可用，已停止')
        # 2026-11-06 修（主人实测：第一个视频采完后，点第二个卡片"点歪了"）。
        # 实测日志：`mobile_click_on_screen_at_coordinates ... 未获取到 UI 层级`。
        # 原因：传 ref 时 MCP 要**先 dump 一次**才能把 @eN 解析成坐标，
        # 而采集流程里点卡片的那一刻，页面刚经历过 BACK 返回/滚动（界面在动），
        # dump 失败就整个点击失败；即便 dump 成功，ref 也是相对"那一刻"的树，
        # 页面再动一下就指到别处 → 点歪。
        # 现在优先用**元素自身的绝对坐标**点击：dump 只在卡片枚举时做一次，
        # 点击阶段不再依赖任何二次 dump。
        box = _box(el)
        if box:
            cx = int(box['x'] + box['width'] // 2)
            cy = int(box['y'] + box['height'] // 2)
            if cx > 0 and cy > 0:
                call('mobile_click_on_screen_at_coordinates',
                     {'x': cx, 'y': cy}, serial)
                return
        # 没有可用坐标才退回 ref（旧行为）
        call('mobile_click_on_screen_at_coordinates',
             {'ref': el['ref']}, serial)

    def press(button):
        check()
        call('mobile_press_button', {'button': button}, serial)

    def settle(times=6, gap=0.8):
        """仅等待可识别页面，不以动态元素数量推断页面稳定或动作成功。"""
        for _ in range(times):
            current = tree()
            if page_kind(current) != 'unknown':
                return current
            pause(gap)
        raise SkillError('页面无法识别，已停止；不将未知页面当作就绪')

    def wait_kind(kind, timeout=20.0):
        """按页面锚点等待。播放页元素数会持续变化，用"数量稳定"判断会误判。"""
        # 是轮询预算，不会中断已经开始的单次读取。
        end = time.monotonic() + timeout
        page = tree()
        while time.monotonic() < end:
            if page_kind(page) == kind:
                return page
            pause(0.8)
            page = tree()
        return page

    def foreground():
        return foreground_package(call('mobile_get_foreground_app', {}, serial))

    def cd_tick(counter):
        if not cooldown_seconds:
            return
        if counter and counter % cooldown_every == 0:
            execution.log(task, '已处理 %d 次，按设置休息 %d 秒' % (counter, cooldown_seconds))
            pause(cooldown_seconds)

    def launch_douyin():
        execution.log(task, '打开抖音')
        execution.execute(task, {'type': 'launch', 'package': PACKAGE})
        for _ in range(10):
            pause(0.8)
            if foreground() == PACKAGE:
                break
        else:
            raise SkillError('抖音没有进入前台，已停止')
        # 抖音刚启动时无障碍树往往还没建好，此时 dump 会返回
        # "未获取到 UI 层级"。前台已确认，这里再等树真正可读，
        # 避免刚启动就被当成"界面识别失败"整任务终止。
        last = None
        for attempt in range(4):
            pause(1.5 if attempt else 0.8)
            try:
                tree()
                return
            except MobileCallError as exc:
                last = exc
        raise SkillError('抖音已在前台但界面树持续不可读：%s' % last)

    def goto_search():
        """回到搜索页；找不到搜索入口就停止，不试探性乱点。

        两条纪律：
          · 每步只读一次树（读一次约 1.1 秒，是整条链路最贵的操作）；
          · **只在抖音首页才点搜索入口**，其它页面一律只用系统返回退出。
            实测在对方个人主页上，模糊匹配「文字含搜索」会选中最靠上的元素
            （左上角），一点就把头像/背景点开放大，而且反复重复。
            区分首页与个人主页的关键：**首页有底部导航栏的「首页」**。
        """
        current = tree()
        if page_kind(current) == 'search':
            return current

        def on_douyin_home(items):
            for el in items:
                text = _text_of(el, 'label', 'text')
                ident = str(el.get('identifier', ''))
                if text in ('首页', '首頁') or ident.endswith(':id/home'):
                    return True
            return False

        search_ids = ('et_search_kw', '456')
        for _ in range(8):
            current = tree()
            if page_kind(current) == 'search':
                return current
            # 「继续编辑作品吗？」残留草稿弹窗会盖住整屏并吃掉点击：
            # 2026-11-04 实测这会让"停在首页却点不动搜索"，采集直接失败。
            joined = ' '.join(_text_of(e, 'label', 'text') for e in current)
            if '继续编辑' in joined or '去编辑' in joined:
                closed = False
                for kw in ('存草稿', '保存草稿', '去编辑'):
                    for e in current:
                        if _text_of(e, 'label', 'text').strip() == kw and _ref_ok(e):
                            try:
                                tap(e)
                                pause(1.8)
                                closed = True
                            except Exception:
                                pass
                            break
                    if closed:
                        break
                if closed:
                    continue
            if on_douyin_home(current):
                cands = [e for e in current
                         if (str(e.get('identifier', '')).split('/')[-1] in search_ids
                             or ('搜索' in _text_of(e, 'label', 'text')
                                 and _box(e) and _box(e)['y'] < 300))
                         and _ref_ok(e) and _box(e)]
                if cands:
                    cands.sort(key=lambda e: _box(e)['y'])
                    tap(cands[0])
                    pause(1.5)
                    current = tree()
                    if page_kind(current) == 'search':
                        return current
            # 返回后要等页面真正加载再判断：只等 1 秒常读到过渡中的空页面，
            # 判成 unknown 就继续退，一路退到桌面，彻底失败。
            press('BACK')
            pause(1.6)
            current = tree()
            if page_kind(current) == 'search':
                return current
            if page_kind(current) == 'unknown':
                pause(1.2)
                current = tree()
                if page_kind(current) == 'search':
                    return current
        raise SkillError('未能进入抖音搜索页，已停止')

    def to_video_tab(page):
        """搜索结果必须停在「视频」tab。

        实测 2026-11-01：搜索提交后默认停在「综合」tab，而综合 tab 的卡片**不是**
        dr1/cover 结构（实测 dr1=0、cover=0），只有「视频」tab 才是 4 个 dr1 + 4 个 cover。
        不切 tab 会让 video_cards() 永远返回空，任务表现为"没有更多新视频"空跑。
        点击「视频」是幂等的：本来就在该 tab 时再点一次没有副作用。
        """
        tabs = [e for e in page
                if str(e.get('identifier', '')) == 'android:id/text1'
                and _text_of(e, 'text') == '视频' and _ref_ok(e) and _box(e)]
        if len(tabs) != 1:
            # 没有分类 tab（单类结果页）时不强行改动页面
            return page
        tap(tabs[0])
        pause(1.5)
        return settle()

    def search_keyword(keyword):
        page = goto_search()
        box = pick_unique(page, 'search', 'input')
        if box is None:
            raise SkillError('搜索框不存在或不唯一，已停止')
        tap(box)
        pause(0.6)
        # 先清空，避免关键词叠加
        page = tree()
        clear = pick_unique(page, 'search', 'clear')
        if clear is not None:
            tap(clear)
            pause(0.5)
        call('mobile_type_keys', {'text': keyword}, serial)
        pause(0.6)
        page = tree()
        submit = pick_unique(page, 'search', 'submit')
        if submit is None:
            # 自愈：树里没有就按文案+顶部区域找，再不行上图让 LLM 看
            from . import ui_resolve as UR
            _heal = UR.heal_tap(serial, ['搜索', '搜索按钮'],
                                page_hint='抖音搜索输入页',
                                region={'xs': 0.45, 'xe': 1.0,
                                        'ys': 0.0, 'ye': 0.3},
                                log=lambda m: execution.log(task, m))
            if not _heal.get('tapped'):
                raise SkillError(
                    '搜索按钮不存在或不唯一，已停止（自愈未成功：%s）'
                    % _heal.get('reason', ''))
        else:
            tap(submit)
        execution.log(task, '搜索「%s」' % keyword)
        # 综合 tab 没有 dr1/cover 卡片，必须切到「视频」tab 才能取到视频
        return to_video_tab(settle())

    def apply_filters(page):
        """按配置逐项选择筛选项；找不到该选项就跳过并说明，不瞎点。"""
        wanted = []
        for key in SELECTABLE:
            value = str(cfg.get(key) or '').strip()
            if value and value != '不限':
                wanted.append(value)
        if not wanted:
            return page
        entry = pick_unique(page, 'search', 'filter')
        if entry is None:
            raise SkillError('已指定筛选，但筛选入口不存在或不唯一，已停止')
        tap(entry)
        pause(1.2)
        overlay = tree()
        applied, missing = [], []
        for value in wanted:
            pattern = re.compile(r'^(未选中|已选中)，' + re.escape(value) + r'，按钮$')
            hits = [e for e in overlay if pattern.match(_text_of(e, 'label')) and _ref_ok(e)]
            if len(hits) != 1:
                missing.append(value)
                continue
            if _text_of(hits[0], 'label').startswith('已选中'):
                applied.append(value)
                continue
            tap(hits[0])
            pause(0.6)
            overlay = tree()
            selected = [e for e in overlay
                        if _text_of(e, 'label') == '已选中，' + value + '，按钮']
            if len(selected) != 1:
                raise SkillError('筛选点击后未核实选中状态：' + value + '；已停止')
            applied.append(value)
        if missing:
            raise SkillError('指定筛选未找到或不唯一：' + '、'.join(missing) + '；已停止')
        execution.log(task, '筛选已选：' + ('、'.join(applied) if applied else '无')
                      + ('；未找到：' + '、'.join(missing) if missing else ''))
        overlay = tree()
        apply_btn = pick_unique(overlay, 'search_filter', 'apply')
        if apply_btn is None:
            raise SkillError('“查看结果”按钮不存在或不唯一，已停止')
        tap(apply_btn)
        return settle()

    def open_comments():
        page = settle()
        if page_kind(page) == 'comments':
            return page
        entry = pick_unique(page, 'video_player', 'comment_entry')
        try:
            if entry is not None:
                tap(entry)
            else:
                # 兜底：右侧白色气泡图标（下面跟评论数）/右侧数字/OCR/视觉。
                # 抖音改版后 obfuscated id 与 label 都会变，死认 e9n 必然失效。
                from . import ui_resolve as UR
                hit = UR.find_comment_entry(
                    serial, page, log=lambda m: execution.log(task, m))
                if not hit.get('ok'):
                    raise SkillError(
                        '评论入口不存在或不唯一，已停止（兜底未命中：%s）'
                        % hit.get('reason', ''))

                def _opened(tries=6):
                    for _ in range(tries):
                        pause(0.5)
                        if page_kind(tree()) == 'comments':
                            return True
                    return False

                # 多形态扫描出的候选逐个试：能打开评论区就停
                opened = False
                for cand in (hit.get('candidates') or [])[:3]:
                    execution.log(task, '评论入口候选 %.0f分 %s → 点 (%d,%d)'
                                  % (cand.get('score', 0), cand.get('why', ''),
                                     cand['x'], cand['y']))
                    UR.tap_point(serial, cand['x'], cand['y'])
                    if _opened():
                        opened = True
                        break
                    execution.log(task, '  这个没打开评论区，试下一个')
                if not opened:
                    # 最后一道：LLM 直接看画面找入口（不预设形态）
                    execution.log(task, '规则扫不到/点不开，改用 LLM 视觉分析入口')
                    found = UR._vision(
                        serial,
                        '请找出「进入评论区的入口」：可能是右侧竖排的对话气泡图标、'
                        '底部写着「写评论/说点什么」的输入条、或任何带评论数字的按钮。'
                        '给出它的屏幕坐标，并在 note 里说明你看到的形态。',
                        '抖音视频播放页')
                    if not found:
                        raise SkillError('评论入口不存在或不唯一，已停止（LLM 也没找到）')
                    UR.tap_point(serial, int(found['x']), int(found['y']))
                    if not _opened(8):
                        raise SkillError('LLM 定位的入口点了也没打开评论区，已停止')
        except MobileCallError as exc:
            # uiautomator dump 偶发取不到层级（"未获取到 UI 层级"），是环境问题、
            # 与逻辑无关，实测退出该视频后自行恢复。**只有这一类**才降级为跳过视频；
            # 结果未知、连接异常等其它错误一律照旧停下，绝不重复点击。
            if exc.outcome_unknown or '界面识别' not in (exc.detail or ''):
                raise
            raise SkillError('评论区没有打开（界面识别偶发失败），已跳过该视频')
        for _ in range(6):
            pause(0.8)
            page = tree()
            if page_kind(page) == 'comments':
                return page
        raise SkillError('评论区没有打开，已跳过该视频')

    # 这些失败发生在**点击头像之前**：页面没被改动，跳过这一条是安全的。
    SAFE_SKIP_PREFIXES = ('评论者取号：可见原评论不存在或不唯一',
                          '评论者取号：评论容器内头像不存在',
                          # 昵称/正文为空：函数入口就拒绝，页面完全没动过
                          '评论者取号：完整昵称/正文或读取次数无效')
    # 这些失败发生在**已点进主页之后**，但失败原因说明"人已经找到了，只是主页没有可核验的
    # 抖音号"（官方号/机构号常见）。此时 BACK 一次回评论页是确定性路径，
    # 回退后再确认确实回到评论页，才允许跳过；否则一律停。
    NO_ACCOUNT_PREFIXES = ('评论者取号：主页昵称/唯一抖音号未严格核实',)

    def already_pooled(row, video):
        """这条评论是否已在池里（同一人+同一正文+同一视频）。

        取号要进一次主页、点两次，约 6.5 秒/条。重跑同一关键词时绝大多数评论是重复的，
        先查库能把这段时间全省下来。查重失败不作为阻塞理由，照常采集。
        """
        try:
            from . import lead_store
            return lead_store.exists_lead(PLATFORM, {
                'comment_author': row.get('author', ''),
                'comment_text': row.get('content', ''),
                'video_title': video.get('desc', ''),
            })
        except Exception as exc:
            execution.log(task, '取号前查重失败（继续采集）：%s' % exc)
            return False

    def collect_comments(limit, video):
        """上滑读评论；连续两次没有新内容就认为到底。"""
        rows, seen, empty_rounds = [], set(), 0
        duplicates = 0
        filtered = 0        # 被意向词/排除词过滤掉的数量（读到了但不收）
        scanned = 0         # 本视频已扫描的评论数（用于超长列表保护）
        last_duplicates = 0
        dup_rounds = 0      # 连续多少屏全是"已在池"的重复
        seen_texts = set()  # 本视频已见过的评论正文（用于压制刷屏文案）
        while len(rows) < limit:
            check()
            page = tree()
            fresh = 0
            # 本轮"新出现"的评论数（**含**因已在池中而跳过的），用于判断是否真的到底。
            # 不能只数"新增线索"：首屏往往只有 2 条评论，若这 2 条恰好都重复，
            # 用新增数判断会误判"到底"而漏掉后面几十条没采过的评论。
            fresh = 0
            for row in read_comments(page):
                key = (row['author'], row['content'])
                if key in seen:
                    continue
                fresh += 1
                # 昵称为空/正文缺失的评论无法核验身份，也不该入池：
                # 直接跳过，不进主页、不点任何控件（省一次 6.5 秒的往返）。
                if not str(row.get('author') or '').strip() \
                        or not str(row.get('content') or '').strip():
                    seen.add(key)
                    stats['skipped'] += 1
                    execution.log(task, '评论昵称或正文为空，已跳过')
                    continue
                # X天内过滤：以任务下发时间为基准（0=不限）
                if _max_age:
                    try:
                        ok_time, _ts = _within_days(row.get('age', ''), _max_age, _base_ts)
                    except Exception:
                        ok_time = True
                    if not ok_time:
                        seen.add(key)
                        stats['skipped'] += 1
                        stats['filtered_time'] = stats.get('filtered_time', 0) + 1
                        continue
                # 刷屏/水军：同一视频里多个账号发逐字相同的文案。
                # 实测见过 11 个不同账号发"来个上家，给个报价表…"，
                # 去重键含评论人，所以它们会全部入池——后续提取等于群发骚扰。
                content_key = str(row.get('content') or '').strip()
                if cfg.get('dedupe_by_text') and content_key in seen_texts:
                    seen.add(key)
                    stats['skipped'] += 1
                    stats['dup_text'] = stats.get('dup_text', 0) + 1
                    execution.log(task, '评论「%s」正文与本视频已收的重复，跳过刷屏'
                                  % str(row.get('author') or '')[:12])
                    continue
                seen_texts.add(content_key)
                if cfg.get('collect_commenter_id', False):
                    ok, reason = judge(row, intent_words, exclude_words)
                    if not ok:
                        seen.add(key)
                        filtered += 1
                        continue
                    # 取号前先查重：已经在池里的评论不必再进一次主页（约 6.5 秒）。
                    if already_pooled(row, video):
                        seen.add(key)
                        duplicates += 1
                        continue
                    from .douyin_commenter import collect_commenter_from_comment
                    # 每条都重新读树；不复用上次进主页之前的ref或y坐标。
                    current = tree()
                    task['_commenter_identity_pending'] = True
                    try:
                        identity = collect_commenter_from_comment(
                            current, row, read_tree=tree, tap=tap, press=press,
                            pause=pause, check=check, serial=serial,
                            log=lambda m: execution.log(task, '抓ID：%s' % m),
                            screen_size=screen_dimensions(call('mobile_get_screen_size', {}, serial)))
                    except SkillError as exc:
                        message = str(exc)
                        # 先排除"设备被切走"：抖音被切到后台时，读到的"主页"其实是桌面，
                        # 报出来会变成"主页昵称未核实"这种误导性归因。先查前台再下结论。
                        try:
                            still_front = foreground() == PACKAGE
                        except MobileCallError:
                            still_front = True   # 查不到就按原错误处理
                        if not still_front:
                            # 保留 _commenter_identity_pending，让外层立即停止：
                            # 设备被切走后，后面所有操作都会失败，不该继续"跳过视频"。
                            raise SkillError(
                                '取号中断：抖音已不在前台（设备被切到其它应用），'
                                '原错误：%s' % message)
                        if not message.startswith(SAFE_SKIP_PREFIXES + NO_ACCOUNT_PREFIXES):
                            # 已点进主页但失败原因不明：保留 _commenter_identity_pending，
                            # 让外层立即停下交给人核验页面。
                            raise
                        if message.startswith(NO_ACCOUNT_PREFIXES):
                            # 主页上没有可核验抖音号（官方号等）：先确定性回退到评论页，
                            # 回退后必须确认真的回到了评论页，否则仍然停止。
                            # 允许最多退两次：不同机型/版本的返回层级不完全一致
                            # （实测第三台设备一次 BACK 没回到评论页）。
                            for _ in range(2):
                                press('BACK')
                                pause(1.0)
                                if page_kind(tree()) == 'comments':
                                    break
                            else:
                                raise SkillError(
                                    '主页无可核验抖音号，回退两次仍未回到评论页，已停止')
                            reason = '主页未暴露可核验抖音号，已回退并跳过'
                        else:
                            reason = '未暴露可核实头像，跳过'
                        task['_commenter_identity_pending'] = False
                        seen.add(key)
                        stats['skipped'] += 1
                        execution.log(task, '评论「%s」取号失败，已跳过：%s（%s）'
                                      % (str(row.get('author') or '')[:12], reason, message))
                        continue
                    task['_commenter_identity_pending'] = False
                    row.update(identity)
                seen.add(key)
                rows.append(row)
                if len(rows) >= limit:
                    break
            if fresh == 0:
                empty_rounds += 1
                if empty_rounds >= 2:
                    break
            else:
                empty_rounds = 0
            # 整屏都是"已在池"的重复：同一个视频被重复搜到时（换关键词也会命中
            # 同一批视频），要把上百条评论读完才发现全重复。连续多屏没有一条
            # 新增命中就提前收手，避免白白花 6 分钟。
            round_dups = duplicates - last_duplicates
            last_duplicates = duplicates
            if fresh > 0 and round_dups == fresh and not rows:
                dup_rounds += 1
                if dup_rounds >= MAX_DUPLICATE_SCREENS:
                    execution.log(task, '连续 %d 屏全是已在池的重复评论，停止读该视频'
                                  % dup_rounds)
                    break
            else:
                dup_rounds = 0
            # 超长评论列表保护：按当前命中率动态决定是否还值得继续扫。
            scanned += fresh
            if len(rows) < limit and scan_budget_exhausted(scanned, len(rows)):
                execution.log(task, '已扫描 %d 条评论、命中 %d 条（命中率 %.1f%%），停止读该视频'
                              % (scanned, len(rows), 100.0 * len(rows) / max(1, scanned)))
                break
            call('mobile_swipe_on_screen',
                 {'direction': 'up', 'distance': 600, 'x': 540, 'y': 1500}, serial)
            pause(1.0)
        return rows[:limit], duplicates, filtered

    def close_comments():
        """收尾：关掉评论区。**失败不致命**——它只影响下一个视频的导航起点，
        而线索在这之前已经入池了。界面识别偶发失败时记录后继续，
        绝不能因为"关不上评论区"把整次采集判失败。
        """
        try:
            for _ in range(3):
                page = tree()
                if page_kind(page) != 'comments':
                    return
                close = pick_unique(page, 'comments', 'close')
                if close is None:
                    press('BACK')
                else:
                    tap(close)
                pause(0.8)
        except MobileCallError as exc:
            execution.log(task, '关闭评论区失败（不影响已采集数据，继续）：%s' % exc)
        except SkillError as exc:
            execution.log(task, '关闭评论区失败（不影响已采集数据，继续）：%s' % exc)

    def back_to_results():
        for _ in range(4):
            page = tree()
            if page_kind(page) == 'search':
                return page
            press('BACK')
            pause(0.9)
        raise SkillError('未能返回搜索结果页，已停止')

    # ---- 主循环
    launch_douyin()
    # 界面树失效时允许重启应用的次数（主人已授权）
    device_restarts = 0
    processed = set()
    # 已进入并处理过的视频（按读到的标题记）：卡片身份认不出时用它兜底去重
    taken_titles = set()
    counter = 0

    for keyword in keywords:
        check()
        if target_comments and stats['leads'] >= target_comments:
            execution.log(task, '已入库 %d 条线索，达到目标 %d，停止' % (stats['leads'], target_comments))
            break
        stats['keywords'] += 1
        page = search_keyword(keyword)
        page = apply_filters(page)
        taken = 0
        scroll_rounds = 0
        # 2026-11-06 主人定：**不设上限**，一直往下翻，只有两个停点：
        #   ① 采集数量够了；② 真的翻到底（连续多次滑动无新卡片）。
        # 不再用 videos_per_keyword 卡循环次数，也不再重搜同一关键词。
        while True:
            check()
            if target_comments and stats['leads'] >= target_comments:
                execution.log(task, '已入库 %d 条线索，达到目标 %d，停止' % (stats['leads'], target_comments))
                break
            cards = [c for c in video_cards(page) if c['y'] > 0]
            target = None
            for card in cards:
                # 用「描述+时长」判重，而不是屏幕坐标：滚动后同一视频坐标会变，
                # 按坐标判重会把同一个视频反复采（实测 10 次进入只 5 个不同视频）。
                stamp = (keyword,) + (card['identity'] or ('xy', card['x'], card['y']))
                if stamp in processed:
                    continue
                target = card
                break
            if target is None:
                scroll_rounds += 1
                if scroll_rounds > scroll_limit:
                    # 连续多次滑动都没出现新卡片 → 认定「翻到底」，正常收工。
                    # 不再重搜同一关键词（结果不变，纯浪费）。
                    execution.log(task, '关键词「%s」已翻到底（连续 %d 次滑动无新视频，已读 %d 条评论，已入库 %d/%d）'
                                  % (keyword, scroll_rounds - 1, stats['comments'],
                                     stats['leads'], target_comments or 0))
                    break
                call('mobile_swipe_on_screen',
                     {'direction': 'up', 'distance': 700, 'x': 540, 'y': 1500}, serial)
                pause(1.2)
                page = tree()
                if page_kind(page) != 'search':
                    page = back_to_results()
                continue
            scroll_rounds = 0
            processed.add((keyword,) + (target['identity']
                                        or ('xy', target['x'], target['y'])))
            # 2026-11-06：点卡片前先确认"当前还是搜索结果页"。
            # 从播放页返回时列表可能还在滑动/重排，此时用旧快照的坐标去点就会点歪
            # （主人实测：第一个视频采完，第二个卡片点到了别处）。
            # 这里宁可多花一次 dump，也不在动着的页面上盲点。
            try:
                _fresh = tree()
                if page_kind(_fresh) != 'search':
                    _fresh = back_to_results()
                page = _fresh
                # 用最新一帧重新定位这张卡（坐标会随滚动变化，不能用旧坐标）
                _same = None
                for _c in [x for x in video_cards(page) if x['y'] > 0]:
                    _stamp = (keyword,) + (_c['identity'] or ('xy', _c['x'], _c['y']))
                    if _c['identity'] and target['identity'] and _c['identity'] == target['identity']:
                        _same = _c
                        break
                if _same is not None:
                    target = _same
            except (SkillError, MobileCallError) as exc:
                execution.log(task, '   点击前校准失败（按旧坐标继续）：%s' % str(exc)[:60])
            tap(target['el'])
            video_leads = []
            identity_pending = bool(cfg.get('collect_author_id', True))
            transport_failed = False
            try:
                page = wait_kind('player', 20)
                if page_kind(page) != 'player':
                    raise SkillError('没有进入视频播放页，已跳过')
                author_id = ''
                if identity_pending:
                    try:
                        author_id = collect_author_from_player(
                            page, read_tree=tree, tap=tap, press=press, pause=pause, check=check,
                            screen_size=screen_dimensions(call('mobile_get_screen_size', {}, serial)))
                    except SkillError as exc:
                        # 作者号是"加分项"：默认跳过这个视频继续采（2026-11-03 改）；
                        # 严格模式（skip_author_failed=False）仍按旧约定整条停止。
                        if not cfg.get('skip_author_failed', True):
                            raise
                        execution.log(task, '  作者取号失败，作者号留空继续采评论：%s' % exc)
                        identity_pending = False
                        author_id = ''
                identity_pending = False
                video = describe_current_video(page)
                # 第二道去重：卡片身份判定失效时（某些卡片取不到 desc），
                # 在同一视频上会重复进入。这里用**读到的视频标题**兜底，
                # 已采过就直接退回结果页，避免再花 20×6.5 秒重复取号。
                title_key = (keyword, str(video.get('desc') or '').strip())
                if title_key[1] and title_key in taken_titles:
                    execution.log(task, '视频「%s」本关键词已采过，跳过重复'
                                  % title_key[1][:24])
                    page = back_to_results()
                    continue
                taken_titles.add(title_key)
                # 该视频历史上已经采够线索了：不再重扫评论区。
                # 实测同一视频第 4 轮只剩 1 条新线索，却仍要花近 8 分钟重读评论。
                try:
                    already = lead_store_count(PLATFORM, video.get('desc'))
                except Exception:
                    already = 0
                if already >= MAX_LEADS_PER_VIDEO:
                    execution.log(task, '视频「%s」已采到 %d 条线索，跳过重扫'
                                  % (title_key[1][:24] or '无描述', already))
                    page = back_to_results()
                    continue
                stats['videos'] += 1
                execution.log(task, '视频 %d：%s（作者 %s）'
                              % (stats['videos'], video['desc'][:30] or '无描述',
                                 video['author'] or '未知'))
                page = open_comments()
                comments, duplicates, filtered = collect_comments(comments_per_video, video)
                stats['comments'] += len(comments)
                stats['duplicates'] = stats.get('duplicates', 0) + duplicates
                stats['filtered'] = stats.get('filtered', 0) + filtered
                # 措辞必须准确：以前把"新增 0 条"写成"读到 0 条评论"，
                # 看日志的人会以为评论没加载出来（实际是被意向词过滤/已在池中）。
                execution.log(task, '新增 %d 条评论（意向词过滤 %d 条，已在池 %d 条）'
                              % (len(comments), filtered, duplicates))
                for comment in comments:
                    ok, reason = judge(comment, intent_words, exclude_words)
                    if not ok:
                        stats['skipped'] += 1
                        continue
                    leads.append({
                        'id': uuid.uuid4().hex[:12],
                        'keyword': keyword,
                        'comment_author': comment.get('author', ''),
                        'comment_text': comment.get('content', ''),
                        'commenter_douyin_id': comment.get('commenter_douyin_id', ''),
                        'commenter_uid': comment.get('commenter_uid', ''),
                        'commenter_nickname': comment.get('commenter_nickname', ''),
                        'commenter_identity_source': comment.get('commenter_identity_source', ''),
                        'commenter_identity_evidence': comment.get('commenter_identity_evidence', {}),
                        'author': comment.get('author', ''),
                        'content': comment.get('content', ''),
                        'time_text': comment.get('age', ''),
                        'age': comment.get('age', ''),
                        'region': comment.get('region', ''),
                        'reason': reason,
                        'video_title': video['desc'],
                        'video_desc': video['desc'],
                        'video_author': video['author'],
                        'author_douyin_id': author_id,
                        'author_identity_source': ('direct_player_profile_roundtrip' if author_id else ''),
                        'author_identity_evidence': ({
                            'player_author': '@' + video['author'],
                            'player_desc': video['desc'], 'profile_account': author_id,
                            'returned_snapshot_equal': True,
                        } if author_id else {}),
                        'filters': {k: cfg.get(k) for k in SELECTABLE if str(cfg.get(k) or '').strip()
                                    and str(cfg.get(k)).strip() != '不限'},
                        'reply_draft': render_draft(reply_tpl, comment, video),
                        'dm_draft': render_draft(dm_tpl, comment, video),
                        'status': 'pending',
                        'found_at': time.time(),
                    })
                    stats['leads'] += 1
                    video_leads.append(leads[-1])
                    execution.log(task, '线索：%s「%s」(%s)'
                                  % (comment.get('author', '未知'),
                                     comment.get('content', '')[:40], reason))
                    # 边采边存：每凑够 5 条立刻入池，不等整个视频跑完
                    # （2026-11-03 主人要求：避免长时间看不到入库）。
                    if len(video_leads) >= 5:
                        flush_pool(video_leads)
                        video_leads = []
                # 先入池、再收尾：收尾要读屏，界面识别偶发失败时不能把已采到的
                # 线索一起丢掉（之前 flush 在 close 之后，收尾一失败整个视频白采）。
                flush_pool(video_leads)
                close_comments()
            except MobileCallError as exc:
                detail = str(exc)
                # 界面树读不到（uiautomator dump 失败）是可恢复的：重启应用再试。
                # 主人已授权"允许重启手机上的应用"（2026-11-03）。
                if '界面识别' in detail and device_restarts < 2:
                    device_restarts += 1
                    execution.log(task, '界面树读取失败：%s' % detail)
                    execution.log(task, '处置：重启抖音应用后继续（第 %d/2 次）' % device_restarts)
                    try:
                        call('mobile_terminate_app', {'packageName': PACKAGE}, serial)
                        pause(2.0)
                        launch_douyin()
                        page = back_to_results()
                        execution.log(task, '应用已重启，继续采集')
                        transport_failed = False
                        continue
                    except Exception as exc2:
                        execution.log(task, '重启后仍不可用：%s' % exc2)
                        execution.log(task, '处置：放弃本视频，继续下一个（不打断整条任务）')
                        transport_failed = False
                        try:
                            page = back_to_results()
                        except Exception:
                            pass
                        continue
                transport_failed = True
                raise
            except SkillError as exc:
                if identity_pending or task.get('_commenter_identity_pending'):
                    raise
                check()
                task['collection_incomplete'] = True
                execution.log(task, '跳过该视频：' + str(exc))
                flush_pool(video_leads)
                close_comments()
            finally:
                if (not stop.is_set() and not identity_pending and not transport_failed
                        and not task.get('_commenter_identity_pending')):
                    # 收尾的"返回结果页"失败不该把整个任务判死：
                    # 线索在这之前已经 flush 入池了（实测 dump 偶发失效时，
                    # back_to_results 抛错会把已经采到数据的任务标成 failed）。
                    try:
                        back_to_results()
                        page = tree()
                    except (MobileCallError, SkillError) as exc:
                        execution.log(task, '返回结果页失败（不影响已采集数据）：%s' % exc)
            taken += 1
            counter += 1
            cd_tick(counter)

    if not leads:
        execution.log(task, '本次没有命中线索：检查意向关键词是否过窄，或该关键词下评论量太少')
    task['lead_stats'] = stats
    save_result(task['id'], {'task_id': task['id'], 'serial': serial, 'keywords': keywords,
                             'stats': stats, 'leads': leads})
    # 写入中转站：每个平台一张独立表，这里只写抖音表。
    try:
        from . import lead_store
        pooled = lead_store.add_leads(PLATFORM, to_pool_rows(leads), serial=serial)
        stats['pooled'] = pooled['added']
        execution.log(task, '已写入中转站（抖音）：新增 %d 条，重复 %d 条，跳过 %d 条'
                      % (pooled['added'], pooled['duplicated'], pooled['skipped']))
    except Exception as exc:
        execution.log(task, '写中转站失败（本地结果文件已保存，不影响本次采集）：%s' % exc)
    check()
    task['status'] = 'needs_review' if task.get('collection_incomplete') else 'completed'
    # 结束原因写进任务，界面详情直接展示（2026-11-03 主人要求"状态透明"）
    if target_comments:
        if stats['leads'] >= target_comments:
            task['end_reason'] = '已达到入库目标：%d/%d 条线索（共读 %d 条评论）' % (stats['leads'], target_comments, stats['comments'])
        else:
            # 2026-11-06：不再写"反复搜索后仍没有更多新视频"（旧逻辑描述），
            # 改成把「翻到底」+「命中率」讲清楚，便于判断是不是意向词太窄。
            _read = stats['comments'] or 0
            _hit = stats['leads'] or 0
            _rate = (100.0 * _hit / _read) if _read else 0.0
            task['end_reason'] = (
                '目标未达成：已入库 %d/%d 条线索（共读 %d 条评论，命中率 %.1f%%）；'
                '原因：关键词「%s」列表已翻到底（连续多次滑动无新视频），没有更多可采内容'
                % (_hit, target_comments, _read, _rate, ','.join(keywords[:3])))
    else:
        task['end_reason'] = '未设入库目标，按配置上限跑完：%d 条线索（读 %d 条评论）' % (stats['leads'], stats['comments'])
    execution.log(task, '采集结束（存在未核验项时需复核）：%d 个关键词、%d 个视频、%d 条评论，命中 %d 条线索'
                  % (stats['keywords'], stats['videos'], stats['comments'], stats['leads']))
    execution.log(task, '结束原因：' + task['end_reason'])


def save_result(task_id, payload):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    target = RESULTS_DIR / (task_id + '.json')
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)


def load_result(task_id):
    path = RESULTS_DIR / (re.sub(r'[^A-Za-z0-9_-]', '', task_id) + '.json')
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
