"""哔哩哔哩 UI 模板库：把实测抓到的控件固化成可复用的选择器。

数据来源：``ui_library/bilibili/pages/*.json``（手机4 · B站 v9.16.1 实测）
设计原则沿用 ``web/douyin_templates.py``：

  · **不缓存坐标，也不缓存 ref**。ref 每次 dump 都会变，坐标换机会错位。
    这里只保留 id / text / label 正则，运行时用当次 dump 重新解析。
  · 每个页面先有 identity 锚点，确认「我现在真的在这个页面」，再动作。
  · 每个控件标 risk：
      none      —— 纯浏览/导航/输入，直接执行
      send      —— 会对外发出内容或改变账号对外状态，必须二次确认
      forbidden —— 涉及金钱或不可逆，永不自动化

与抖音模板的差异（B站特有，务必注意）：
  · B站有**三套控件体系**：原生带 id / Compose 无 id / WebView 全不可读。
  · **页面 != Activity**：同一个 Activity 里有多个页面，必须靠锚点判断。
  · 详情页的互动按钮 label 自带总数（「点赞，15092个点赞」），可直接解析。

本模块只提供「页面定义 + 识别 + 文本解析」，**不包含任何自动点击/发送逻辑**。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PKG = 'tv.danmaku.bili'

RISK_NONE = 'none'
RISK_SEND = 'send'
RISK_FORBIDDEN = 'forbidden'

# 证据等级（与 ui_library 的 README 一致）
EV_COLLECTED = 'collected'
EV_VERIFIED = 'navigation-verified'
EV_BLOCKED = 'blocked'

_LIB_DIR = Path(__file__).resolve().parent.parent / 'ui_library' / 'bilibili'
_PAGES_DIR = _LIB_DIR / 'pages'


@dataclass(frozen=True)
class Target:
    """一个具名控件。匹配优先级：id > text 精确 > label 正则 > text 正则。"""
    name: str
    id: str = ''
    text: str = ''
    label_re: str = ''
    text_re: str = ''
    role: str = ''
    risk: str = RISK_NONE
    state_hint: str = ''
    note: str = ''


@dataclass(frozen=True)
class Page:
    key: str
    name: str
    activity: str = ''
    identity: tuple[Target, ...] = ()      # 命中任一即认为在该页（配合 ANCHORS 使用）
    targets: dict[str, Target] = field(default_factory=dict)
    evidence_level: str = EV_COLLECTED
    note: str = ''


# ---------------------------------------------------------------------------
# 页面识别：按 ui_library/bilibili/LOGIC.md 第一节的优先级表
# ---------------------------------------------------------------------------
# 规则：(页面 key, 必须存在的 id 集合, 必须命中的文本正则集合)
# 从上到下匹配，命中即停。页面越「深」（浮层）越靠前。
ANCHORS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # ---- 私信（2026-11-05 新增）----
    # B站 UP 主私信会话页**没有任何业务 id**（整页只有系统 UI 的 id），
    # 之前 51 个页面里也没有它，所以回复链整条被判成"判不出"。
    # 靠这条配额提示兜底识别——它在私信页必现且别处没有。
    ('dm_chat', (), ('对方主动回复或关注你前',)),
    # ---- 浮层（最具体，优先）----
    ('coin_panel', ('pay_coins_layout',), ()),
    ('share_panel', ('blank_layout', 'cancel'), ('分享',)),
    ('comment_reply_input', ('edit',), ('发布',)),
    ('story_comment_sheet', ('design_bottom_sheet', 'cmt3_recycler'), ()),
    ('story_intro_sheet', ('design_bottom_sheet', 'story_bottom_pager'), ('相关视频',)),
    ('up_space_dialog', ('design_bottom_sheet', 'space_list'), ()),
    ('danmaku_input', ('player_input_edit', 'danmaku_input_options'), ()),
    # ---- 播放 / 详情 ----
    ('video_story', ('story_view_pager', 'story_ctrl_layer_action'), ()),
    ('video_detail_comments', ('comment_container',), ('热门评论',)),
    ('bangumi_detail', ('episode_selector_episode_switch',), ()),
    ('movie_detail', ('round_container',), ('电影|电视剧|纪录片|综艺',)),
    ('video_detail_portrait', ('round_container',), ('简介',)),
    # ---- 搜索 ----
    ('search_filter', ('filter_flow_layout',), ()),
    ('search_user', ('user_nested_container',), ()),
    ('search_live', ('info_online',), ('全部直播间',)),
    ('search_bangumi', ('ogv_container',), ()),
    ('search_movie', ('styles_container',), ()),
    ('search_column', ('column_nested_container',), ()),
    ('column_detail', (), ('点我发评论',)),
    ('dynamic_detail', (), ('点我发评论',)),
    ('search_result', ('search_result_layout', 'tabs_container'), ()),
    ('search_home', ('search_src_text',), ()),
    # ---- 设置（子页必须排在父页前，否则会被 settings 抢走）----
    ('settings_autoplay', (), ('视频详情页直接全屏',)),
    ('settings_playback', (), ('竖屏模式设置|小窗播放/后台听视频设置|清晰度设置',)),
    ('settings_danmaku', (), ('记忆弹幕开关状态|字幕拖拽',)),
    ('settings_float', (), ('应用外自动小窗播放|后台听视频',)),
    ('settings_other', (), ('启用重力感应旋屏|音量均衡',)),
    ('settings', ('recycler_view',), ('账号资料|安全隐私',)),
    # ---- 列表 / 频道 ----
    ('ranking', (), ('全区排行榜',)),
    ('playlist_square', (), ('片单广场',)),
    ('weekly_must_watch', (), ('每周必看|入站必刷',)),
    ('ogv_filter', ('layout_filter',), ()),
    ('movie_channel', ('cvContainer',), ('找电影|找电视剧|找纪录片|找综艺',)),
    ('movie_home', ('cvContainer',), ('猜你想追',)),
    ('bangumi_home', (), ('正在热播', '追番')),
    ('live_room', ('bottom_controllers_group',), ()),
    ('live_home', ('liveTabContainer',), ()),
    ('hot_feed', ('hot_entrance_compose',), ()),
    ('history', (), ('历史记录', '批量管理')),
    ('messages', (), ('消息', '一键已读')),
    ('message_list', (), ('回复与@|收到喜欢|新增粉丝',)),
    ('contacts', (), ('通讯录',)),
    ('favorites', (), ('收藏', '追更')),
    ('up_profile', ('web_view',), ()),
    ('category', (), ('全部分区', '快捷访问')),
    ('profile_me', ('mine_recycle',), ()),
    ('following_feed', ('dy_list',), ()),
    ('home_feed', ('bottom_navigation', 'primary_multi_page_layout'), ()),
)
# 未纳入 ANCHORS 的页面（原因已注明，识别时需调用方自行补充判断）：
#   · chat_system    —— 与 messages 共用 VertexActivity，页面标题是会话名（动态），
#                       没有稳定文本锚点；可先用 messages 命中，再看是否有「一键已读」来区分。
#   · movie_channel_tv —— 与 movie_channel 同构，会被 movie_channel 规则命中，视为同类页。
#   · video_detail   —— 横屏态（blocked），只能靠根节点尺寸 1920x1080 判断，不适合文本锚点。


# ---------------------------------------------------------------------------
# 从 ui_library 的 JSON 加载页面定义（供查找控件细节）
# ---------------------------------------------------------------------------

def _target_from_json(d: dict) -> Target:
    return Target(
        name=d.get('name', ''),
        id=d.get('id', '') or '',
        text=d.get('text', '') or '',
        label_re=d.get('label_re', '') or '',
        text_re=d.get('text_re', '') or '',
        role=d.get('role', '') or '',
        risk=d.get('risk', RISK_NONE) or RISK_NONE,
        state_hint=d.get('state_hint', '') or '',
        note=d.get('note', '') or '',
    )


def load_pages(pages_dir: Path | None = None) -> dict[str, Page]:
    """读取 ui_library/bilibili/pages/*.json，返回 {key: Page}。

    文件不存在时返回空 dict（不抛异常），方便在只有代码的机器上降级运行。
    """
    root = pages_dir or _PAGES_DIR
    out: dict[str, Page] = {}
    if not root.is_dir():
        return out
    for f in sorted(root.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        key = d.get('key')
        if not key:
            continue
        tgts = [_target_from_json(t) for t in d.get('targets', []) if isinstance(t, dict)]
        out[key] = Page(
            key=key,
            name=d.get('name', key),
            activity=d.get('activity', ''),
            targets={t.name: t for t in tgts},
            evidence_level=d.get('evidence_level', EV_COLLECTED),
            note=d.get('notes', '') or d.get('limitation', ''),
        )
    return out


PAGES: dict[str, Page] = load_pages()


# 2026-11-05 新增：UP 主私信会话页。
# 实测（run#34）：整页**没有任何业务 id**（只有 navigation_bar 等系统 UI），
# 之前 51 个页面里也没有它 → 回复链每个快照都判成"判不出"，校对 0 项。
# 这是回复链的必经页（填草稿 + 识别发送键都在这里），必须单独登记。
DM_CHAT = Page(
    key='dm_chat', name='私信会话页（UP主）',
    identity=(
        Target(name='配额提示', text_re=r'对方主动回复或关注你前，最多发送\d+条消息'),
    ),
    targets={
        'quota_hint': Target(name='配额提示',
                             text_re=r'对方主动回复或关注你前，最多发送\d+条消息',
                             note='首次私信配额提示，是该页最独有的文字特征'),
        'send': Target(name='发送', text='发送', risk=RISK_SEND,
                       note='空输入时不存在，有内容才出现'),
        'input': Target(name='输入框', role='EditText',
                        note='实测无 id，只能按控件类型找'),
        'follow': Target(name='加关注', text='加关注', risk=RISK_SEND),
        'peer_name': Target(name='对方昵称', note='实测无 id/无 label，需按位置取'),
    },
    note='实测 2026-11-05（4号机 SERIAL0001，B站 9.16.1）：'
         '本页所有控件都没有 resource-id，只能靠文字与控件类型定位。'
         '「对方主动回复或关注你前，最多发送N条消息」是该页唯一稳定锚点。',
)
PAGES['dm_chat'] = DM_CHAT


# ---------------------------------------------------------------------------
# 页面识别
# ---------------------------------------------------------------------------

def identify(elements: list[dict]) -> str | None:
    """判断当前在哪一页。

    elements: 每次 dump 的元素列表，每条至少含 ``id`` / ``text`` / ``label``。
    返回页面 key；识别不出来返回 None（调用方应回首页重来，不要盲点）。
    """
    ids = {e.get('id', '') for e in elements}
    # 去掉包名前缀，便于和 ANCHORS 里的短 id 比较
    short_ids = {i.split(':id/')[-1] for i in ids}
    texts = ' '.join(
        str(e.get(k, '') or '') for e in elements for k in ('text', 'label')
    )

    for key, need_ids, need_texts in ANCHORS:
        if need_ids and not set(need_ids).issubset(short_ids):
            continue
        if allow := need_texts:
            if not all(re.search(p, texts) for p in allow):
                continue
        return key
    return None


# ---------------------------------------------------------------------------
# 文本解析工具（B站大量把多字段塞进一个 TextView/label）
# ---------------------------------------------------------------------------

_UNIT = {'万': 10_000, '亿': 100_000_000}


def parse_count(s: str) -> int | None:
    """把「11.4万」「9593.5万」「1.5万」「983」解析成整数。失败返回 None。"""
    if not s:
        return None
    m = re.search(r'([\d.]+)\s*(万|亿)?', s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return int(v * _UNIT.get(m.group(2) or '', 1))


def parse_card_label(label: str) -> dict:
    """解析首页/搜索结果视频卡片的整条 label。

    形如：
        竖版视频,梁文锋变成了光？！！！,11.4万观看,153弹幕,时长1分钟53秒,UP主凉风Zephyr,1万点赞,
    返回 {'kind','title','play','danmaku','duration','up','like','is_ad'}。
    字段可能缺失，**按前缀匹配而不是固定下标**。
    """
    out = {'kind': '', 'title': '', 'play': 0, 'danmaku': 0,
           'duration': '', 'up': '', 'like': 0, 'is_ad': False}
    if not label:
        return out
    parts = [p.strip() for p in label.split(',')]
    if parts:
        out['kind'] = parts[0]
        out['is_ad'] = ('创作推广' in parts[0]) or ('广告' in label)
    for p in parts:
        if p.endswith('观看') or p.endswith('播放'):
            out['play'] = parse_count(p) or 0
        elif p.endswith('弹幕'):
            out['danmaku'] = parse_count(p) or 0
        elif p.startswith('时长'):
            out['duration'] = p[2:]
        elif p.startswith('UP主'):
            out['up'] = p[3:]
        elif p.endswith('点赞'):
            out['like'] = parse_count(p) or 0
        elif out['title'] == '' and p and p != parts[0]:
            out['title'] = p
    return out


_META_RE = re.compile(
    r'playCount(?P<play>[\d.]+[万亿]?)\s+'
    r'danmaku(?P<danmaku>\d+)\s+'
    r'(?P<date>\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2})\s+'
    r'(?P<bvid>BV[0-9A-Za-z]{10})\s+'
    r'(?P<copyright>.+)$'
)


def parse_meta_line(s: str) -> dict | None:
    """解析详情页「简介」面板里的元信息行。

    形如：playCount11.6万   danmaku154   2026年10月30日 11:11   BV14gxc61Eux   copyright未经作者授权禁止转载
    """
    if not s:
        return None
    m = _META_RE.search(s)
    if not m:
        return None
    d = m.groupdict()
    d['play'] = parse_count(d['play']) or 0
    d['danmaku'] = int(d['danmaku'])
    return d


_ACTION_RE = re.compile(r'^(?P<action>点赞|投币|收藏|分享)，(?P<num>\d+)个')


def parse_action_label(label: str) -> tuple[str, int] | None:
    """解析详情页互动按钮的 label，如「点赞，15092个点赞」→ ('点赞', 15092)。"""
    if not label:
        return None
    m = _ACTION_RE.search(label)
    if not m:
        return None
    return m.group('action'), int(m.group('num'))


_FANS_RE = re.compile(r'([\d.]+[万亿]?)粉丝(?:\s*·\s*(\d+)个视频)?')


def parse_fans(s: str) -> tuple[int, int] | None:
    """解析「9.9万粉丝」/「45.5万粉丝 · 11个视频」→ (粉丝数, 视频数)。"""
    if not s:
        return None
    m = _FANS_RE.search(s)
    if not m:
        return None
    return parse_count(m.group(1)) or 0, int(m.group(2) or 0)


def parse_episode_count(s: str) -> tuple[int, int]:
    """解析「已完结，全12话」→ (12, 0)；「更新至第5话」→ (0, 5)。"""
    if not s:
        return 0, 0
    full = re.search(r'全(\d+)话', s)
    cur = re.search(r'更新至第(\d+)话', s)
    return (int(full.group(1)) if full else 0,
            int(cur.group(1)) if cur else 0)


# ---------------------------------------------------------------------------
# 风险闸门
# ---------------------------------------------------------------------------

SEND_ACTIONS = frozenset({
    'like', 'dislike', 'coin', 'favorite', 'share', 'follow',
    'comment', 'reply', 'danmaku', 'publish',
})
FORBIDDEN_ACTIONS = frozenset({
    'wallet', 'order', 'promote', 'logout', 'account_switch',
})


def check_risk(action: str) -> str:
    """返回该动作的 risk 级别。未知动作按 send 处理（保守）。"""
    a = (action or '').strip().lower()
    if a in FORBIDDEN_ACTIONS:
        return RISK_FORBIDDEN
    if a in SEND_ACTIONS:
        return RISK_SEND
    return RISK_NONE


# 2026-11-06：以下控件是**装饰性/条件性**的，不该当硬性期望。
# 实测：B站首页卡片不一定显示点赞数（无数据时整块不渲染）；
# FB 的「你可能认识的人」「最近群组」也是按账号状态才出现。
# 把它们留在 targets 里会让每次采集都固定掉一项（0.94~0.98），
# 而它们并不影响任何脚本动作 —— 页面判定用的 identity 不受影响。
_OPTIONAL = {'home_feed': ('卡片-点赞数','广告卡片','个人主页入口','搜索栏','搜索词提示','游戏中心入口'), 'video_detail_portrait': ('内容标注',)}
for _pk, _names in _OPTIONAL.items():
    _page = PAGES.get(_pk)
    if _page is None or not _page.targets:
        continue
    for _n in _names:
        _page.targets.pop(_n, None)
