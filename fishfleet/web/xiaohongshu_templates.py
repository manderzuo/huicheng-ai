"""小红书 UI 模板库：发布流程的实测控件与页面锚点。

实测环境（2026-10-31 22:21–22:23，+08:00）：
  Galaxy S10 / SM-G973U1 / Android 12 / 小红书 v.9.4.0.8a83615
  设备 192.168.31.214:5555（手机3）

设计原则（与 douyin_templates 保持一致，但有小红书特有的坑）：
  · **不缓存坐标，也不缓存 ref**。ref 每次 dump 都会变，坐标换机会错位。
  · **小红书绝大多数控件 id 都是 `0_resource_name_obfuscated`**，全 App 共用同一个
    字符串，等于没有 id。所以这里**不用 id 定位**，一律用
    Activity + text/label 精确值或正则 + 控件类型 组合判定，且要求唯一命中。
  · 每个页面先有 identity 锚点，确认"我现在真的在这个页面"，再动作。
  · 每个控件标 risk：
      none      —— 纯浏览/导航/输入，直接执行
      send      —— 会对外发出内容或改变账号对外状态，必须二次确认
      forbidden —— 涉及金钱或不可逆，永不自动化
  · 发布页的「发布笔记」按钮**空内容时不会 disabled**（实测），所以不能靠 disabled
    判断能否发布，必须在点击前自行校验标题/正文非空。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PKG = 'com.xingin.xhs'

RISK_NONE = 'none'
RISK_SEND = 'send'
RISK_FORBIDDEN = 'forbidden'

# 实测到的 Activity（发布链路 + 截流链路）
ACT_INDEX = 'com.xingin.xhs.index.v2.IndexActivityV2'
ACT_ALBUM = 'com.xingin.capa.lib.entrancev2.CapaAlbumActivity'
ACT_VIDEO_EDIT = 'com.xingin.capa.video_edit.VideoEditActivityV3'
ACT_POST = 'com.xingin.capa.post.platform.activity.CapaPostNotePlatformActivity'
ACT_SEARCH = 'com.xingin.alioth.search.GlobalSearchActivity'
ACT_NOTE_DETAIL = 'com.xingin.matrix.notedetail.NoteDetailActivity'
ACT_OTHER_PROFILE = 'com.xingin.matrix.v2.profile.newpage.NewOtherUserActivity'
ACT_CHAT = 'com.xingin.im.ui.activity.ChatActivity'


@dataclass(frozen=True)
class Target:
    """一个具名控件。

    匹配优先级：text 精确 > label 正则 > text 正则；`type` 是附加约束（同时满足）。
    小红书没有可用 id，所以这里刻意不提供 id 字段，避免误用。
    """
    name: str
    text: str = ''
    label_re: str = ''
    text_re: str = ''
    type: str = ''            # 控件类型包含该子串即满足，如 'Button' / 'EditText'
    risk: str = RISK_NONE
    note: str = ''


@dataclass(frozen=True)
class Page:
    key: str
    name: str
    activity: str = ''                      # 该页面对应的 Activity（空表示不校验）
    identity: tuple[Target, ...] = ()       # 命中任一即认为在该页（仅作辅助）
    targets: dict[str, Target] = field(default_factory=dict)
    note: str = ''


def _t(name, **kw):
    return Target(name=name, **kw)


# ---------------------------------------------------------------------------
# 1. 发布入口弹窗（首页底栏「发布」点开后的底部弹窗）
# ---------------------------------------------------------------------------
POST_ENTRY = Page(
    key='post_entry', name='发布入口弹窗', activity=ACT_INDEX,
    identity=(
        _t('从相册选择', text='从相册选择'),
        _t('写文字', text='写文字'),
    ),
    targets={
        'from_album': _t('从相册选择', text='从相册选择', risk=RISK_NONE),
        'camera': _t('相机', text='相机', risk=RISK_NONE,
                     note='会进相机；可能请求相机权限，权限弹窗须人工处理'),
        'write_text': _t('写文字', text='写文字', risk=RISK_NONE,
                         note='纯文字笔记入口，不走相册'),
        'cancel': _t('取消', text='取消', risk=RISK_NONE),
    },
    note='该弹窗与首页共处 IndexActivityV2，必须同时看到弹窗项才算命中，不能只看 Activity。',
)


# ---------------------------------------------------------------------------
# 2. 相册选择页
# ---------------------------------------------------------------------------
ALBUM = Page(
    key='album', name='相册选择页', activity=ACT_ALBUM,
    identity=(
        _t('关闭', label_re=r'^关闭$'),
        _t('分类-视频', text='视频'),
    ),
    targets={
        'close': _t('关闭', label_re=r'^关闭$', risk=RISK_NONE),
        'tab_all': _t('全部', text='全部', type='Button', risk=RISK_NONE),
        'tab_video': _t('视频', text='视频', type='Button', risk=RISK_NONE),
        'tab_photo': _t('照片', text='照片', type='Button', risk=RISK_NONE),
        'tab_live': _t('实况图', text='实况图', type='Button', risk=RISK_NONE),
        'tab_full': _t('全屏图', text='全屏图', type='Button', risk=RISK_NONE),
        'drafts': _t('草稿箱', text='草稿箱', risk=RISK_NONE),
    },
    note=(
        '素材网格每个卡片是 FrameLayout + ImageView(354x354)，视频右下角有时长 TextView（形如 0:05）。'
        '素材内容是用户相册隐私，**不得写入日志或资料**。'
    ),
)


# ---------------------------------------------------------------------------
# 3. 视频预处理页（选中素材后）
# ---------------------------------------------------------------------------
VIDEO_PREVIEW = Page(
    key='video_preview', name='视频预处理页',
    identity=(
        _t('一键成片', text='一键成片'),
        _t('下一步', label_re=r'^下一步$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回$', type='ImageButton', risk=RISK_NONE),
        'pick': _t('选择', text='选择', risk=RISK_NONE),
        'auto_movie': _t('一键成片', text='一键成片', risk=RISK_NONE,
                         note='自动套模板，会改变成片内容，发布任务不应使用'),
        'next': _t('下一步', label_re=r'^下一步$', risk=RISK_NONE),
    },
    note='该页有播放按钮、VideoSeekBar 时间轴（00:00–时长）。',
)


# ---------------------------------------------------------------------------
# 4. 视频编辑页
# ---------------------------------------------------------------------------
VIDEO_EDIT = Page(
    key='video_edit', name='视频编辑页', activity=ACT_VIDEO_EDIT,
    identity=(
        _t('特效', text='特效'),
        _t('截取', text='截取'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回$', type='ImageButton', risk=RISK_NONE),
        'music': _t('选择音乐', text='选择音乐', risk=RISK_NONE),
        'effect': _t('特效', text='特效', risk=RISK_NONE),
        'text': _t('文字', text='文字', risk=RISK_NONE),
        'mark': _t('标记', text='标记', risk=RISK_NONE),
        'sticker': _t('贴纸', text='贴纸', risk=RISK_NONE),
        'trim': _t('截取', text='截取', risk=RISK_NONE),
        'more': _t('更多', text='更多', risk=RISK_NONE),
        'next': _t('下一步', text_re=r'^下一步(?:\(\d+\))?$', risk=RISK_NONE,
                   note='text 形如「下一步」或「下一步(2)」；预处理页同名按钮用的是 label'),
    },
    note='自动化发布**不应**触碰特效/文字/贴纸，直接点下一步即可。',
)


# ---------------------------------------------------------------------------
# 4b. 图片编辑页（与视频编辑页同构，仅工具条文案不同）
# ---------------------------------------------------------------------------
IMAGE_EDIT = Page(
    key='image_edit', name='图片编辑页',
    identity=(
        _t('滤镜', text='滤镜'),
        _t('裁切', text='裁切'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回$', type='ImageButton', risk=RISK_NONE),
        'music': _t('选择音乐', text='选择音乐', risk=RISK_NONE),
        'filter': _t('滤镜', text='滤镜', risk=RISK_NONE),
        'text': _t('文字', text='文字', risk=RISK_NONE),
        'mark': _t('标记', text='标记', risk=RISK_NONE),
        'sticker': _t('贴纸', text='贴纸', risk=RISK_NONE),
        'crop': _t('裁切', text='裁切', risk=RISK_NONE),
        'more': _t('更多', text='更多', risk=RISK_NONE),
        'next': _t('下一步', text_re=r'^下一步(?:\(\d+\))?$', risk=RISK_NONE),
    },
    note='与 VIDEO_EDIT 同构：工具条是 滤镜/文字/标记/贴纸/裁切/更多，视频页是 特效/文字/标记/贴纸/截取/更多。',
)


# ---------------------------------------------------------------------------
# 5. 发布页（最终填表页）
# ---------------------------------------------------------------------------
POST_PAGE = Page(
    key='post_page', name='发布笔记页', activity=ACT_POST,
    identity=(
        _t('发布笔记', text='发布笔记'),
        _t('存草稿', text='存草稿'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回$', type='ImageButton', risk=RISK_NONE),
        'cover': _t('选封面', text='选封面', risk=RISK_NONE),
        'title_input': _t('标题输入框', type='EditText', risk=RISK_NONE,
                          note='页面上方那个 EditText；与正文框靠 y 坐标区分，见 skill 里的 pick_inputs'),
        'body_input': _t('正文输入框', type='EditText', risk=RISK_NONE,
                         note='标题下方的多行 EditText，实测高度 288px、位于 ScrollView 内'),
        'topic_btn': _t('话题', text='话题', risk=RISK_NONE),
        'user_btn': _t('用户', text='用户', risk=RISK_NONE),
        'vote_btn': _t('投票', text='投票', risk=RISK_NONE),
        'location': _t('标记地点', label_re=r'^标记地点$', risk=RISK_NONE),
        'visibility': _t('公开可见', text='公开可见', risk=RISK_SEND,
                         note='权限行，label「笔记公开可见」；改可见范围会改变笔记对外状态'),
        'add_component': _t('添加组件', label_re=r'^添加组件$', risk=RISK_NONE),
        'advanced': _t('高级选项', label_re=r'^高级选项$', risk=RISK_NONE),
        'save_draft': _t('存草稿', text='存草稿', risk=RISK_SEND,
                         note='写草稿不对外，但仍会改变账号内数据；实测会弹「确认保存笔记至草稿箱吗?」'),
        'publish': _t('发布笔记', text='发布笔记', type='Button', risk=RISK_SEND,
                      note='真正的发布动作。实测空内容时**不带 disabled**，必须自行校验内容非空'),
    },
    note=(
        '页面自上而下（图文页实测）：图片横向列表 + 加图 → 标题 EditText → 正文 EditText → '
        '话题推荐 → 话题/用户/投票 → 标记地点 + 附近地点 → 公开可见 → 添加组件 → 高级选项 → '
        '底部 存草稿 / 发布笔记。视频页同理，但顶部是单个视频预览 + 选封面，没有图片列表。'
    ),
)


# ---------------------------------------------------------------------------
# 6. 截流相关页面（搜索结果 / 用户结果 / 笔记详情 / 评论区 / 他人主页 / 私信）
#
# 全部按 2026-10-31 实测结构编写；小红书无可用 id，只能靠 text/label/type。
# ⚠️ 定位铁律：**昵称会大量重名，绝不用昵称做唯一键**，一律用「小红书号」。
#    实测搜「隔壁小马」返回 4+ 个同名账号；搜 95288842605 只命中 1 个并提示「无更多用户」。
# ---------------------------------------------------------------------------
SEARCH_RESULT = Page(
    key='search_result', name='搜索结果页', activity=ACT_SEARCH,
    identity=(
        _t('全部', text='全部'),
        _t('用户', text='用户'),
        _t('问一问', text='问一问'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回$', risk=RISK_NONE),
        'keyword': _t('当前关键词', type='TextView', risk=RISK_NONE,
                      note='顶栏显示当前查询词，动态'),
        'clear_all': _t('全部删除', label_re=r'^全部删除$', risk=RISK_NONE),
        'tab_all': _t('全部', text='全部', risk=RISK_NONE),
        'tab_user': _t('用户', text='用户', risk=RISK_NONE),
        'tab_goods': _t('商品', text='商品', risk=RISK_NONE),
        'tab_group': _t('群聊', text='群聊', risk=RISK_NONE),
        # 快捷筛选条在滚动列表内，位置会随内容上下移动，只能按文本查
        'sort_general': _t('综合', text='综合', risk=RISK_NONE),
        'sort_latest': _t('最新', text='最新', risk=RISK_NONE),
    },
    note='结果卡片无稳定 label；只能按封面/描述/作者/赞数的相对位置关联，不能全局取同名。',
)


USER_RESULT = Page(
    key='user_result', name='用户搜索结果',
    identity=(
        _t('用户', text='用户'),
        _t('小红书号前缀', text_re=r'^小红书号[：:]\s*\d+'),
    ),
    targets={
        'tab_user': _t('用户', text='用户', risk=RISK_NONE),
        'no_more': _t('无更多用户', text='无更多用户', risk=RISK_NONE),
        'xhs_id': _t('小红书号', text_re=r'^小红书号[：:]\s*\d+', risk=RISK_NONE,
                     note='唯一 ID 来源；形如「小红书号：95288842605」'),
        'fan_count': _t('粉丝数', text_re=r'^粉丝\s*[\d.]+[万亿]?$', risk=RISK_NONE),
    },
    note='用户结果直接显示小红书号（抖音读不到），可批量核对唯一 ID，是本方案的关键优势。',
)


NOTE_DETAIL = Page(
    key='note_detail', name='笔记详情页', activity=ACT_NOTE_DETAIL,
    identity=(
        _t('评论框', label_re=r'^评论框$'),
        # 2026-11-05 修：原来是 label_re=r'.+' + type=FrameLayout —— 几乎任何页面
        # 的 FrameLayout 都能命中，导致发布页/用户主页被误判成笔记详情。
        # 收紧为必须形如「笔记…」/「视频…」的内容区。
        _t('内容区', label_re=r'^(笔记|视频)', type='FrameLayout'),
    ),
    targets={
        'content': _t('内容区', type='FrameLayout', label_re=r'^(笔记|视频)',
                      note='label 含标题+正文；noteContentText 的 text 也可回读全文'),
        'author_name': _t('作者昵称', type='TextView', risk=RISK_NONE),
        'avatar': _t('作者头像', type='View', label_re=r'^头像,', risk=RISK_NONE),
        'follow': _t('关注', text='关注', risk=RISK_SEND),
        'comment_box': _t('评论框', label_re=r'^评论框$', risk=RISK_NONE),
        'like_btn': _t('点赞', label_re=r'^点赞\s*[\d.万亿]*$', type='Button', risk=RISK_SEND),
        'favorite_btn': _t('收藏', label_re=r'^收藏\s*[\d.万亿]*$', type='Button', risk=RISK_SEND),
        'comment_btn': _t('评论入口', label_re=r'^评论\s*\d+$', type='Button', risk=RISK_NONE,
                          note='点它展开评论区，纯浏览'),
    },
)


COMMENTS = Page(
    key='comments', name='评论区（详情页内）',
    identity=(
        _t('评论数', text_re=r'^共\s*\d+\s*条评论$'),
    ),
    targets={
        'total': _t('共N条评论', text_re=r'^共\s*\d+\s*条评论$', risk=RISK_NONE),
        'avatar': _t('评论者头像', type='View', risk=RISK_NONE,
                     note='点头像进对方主页，是拿小红书号的入口'),
        'nickname': _t('昵称', type='TextView', risk=RISK_NONE),
        'content': _t('正文', type='TextView', risk=RISK_NONE),
        'time_region_reply': _t('时间地区回复', type='TextView',
                                text_re=r'^\d{2}-\d{2}\s+.*回复$|^\d{2}-\d{2}\s*$', risk=RISK_NONE,
                                note='三段合一，需正则拆分；点它打开回复框'),
        'expand_replies': _t('展开回复', type='TextView', text_re=r'^展开\s*\d+\s*条回复$', risk=RISK_NONE),
        'author_tag': _t('作者标记', text='作者', risk=RISK_NONE),
        'first_tag': _t('首评标记', text='首评', risk=RISK_NONE),
    },
    note='没有汇总 label，昵称/正文/时间地区分别是独立 TextView，必须按容器与 y 坐标关联。',
)


OTHER_PROFILE = Page(
    key='other_profile', name='他人主页', activity=ACT_OTHER_PROFILE,
    identity=(
        _t('私信', text='私信'),
        _t('小红书号前缀', text_re=r'^小红书号[：:]\s*\d+'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回$', type='ImageView', risk=RISK_NONE),
        'nickname': _t('昵称', type='TextView', risk=RISK_NONE, note='仅展示，不作唯一键'),
        'xhs_id': _t('小红书号', text_re=r'^小红书号[：:]\s*\d+', risk=RISK_NONE,
                     note='**唯一键来源**'),
        'follow': _t('关注', text='关注', risk=RISK_SEND),
        'dm': _t('私信', text='私信', risk=RISK_SEND,
                 note='点它进会话页；真正发送在会话里'),
        'tab_note': _t('笔记', text='笔记', risk=RISK_NONE),
    },
)


CHAT = Page(
    key='chat', name='私信会话页', activity=ACT_CHAT,
    identity=(
        _t('输入框', type='EditText'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回$', risk=RISK_NONE),
        'nickname': _t('对方昵称', type='TextView', risk=RISK_NONE),
        'input': _t('输入框', type='EditText', risk=RISK_NONE,
                    note='实测 (158,1609) 664x105；带 value 字段可直接回读内容，比 text 更可靠'),
        'send': _t('发送', text='发送', type='TextView', risk=RISK_SEND,
                   note='实测是 **TextView 不是 Button**，约 (928,1619) 99x84。'
                        '空输入时该元素**根本不存在**，有内容才出现，清空后又消失；'
                        '判定条件是「发送键是否存在」，而不是看 disabled'),
        'quota_hint': _t('配额提示', text_re=r'^对方关注或回复你之前，最多只能发送\d+条消息$',
                         risk=RISK_NONE,
                         note='实测为 1 条：每个潜客只有一次机会，文案必须一次到位'),
        'quote_block': _t('引用块', text_re=r'^刚刚浏览对方的评论$', risk=RISK_NONE,
                          note='从评论区路径进入时可能出现，但不保证，不能假定一定有'),
    },
    note='首次私信有严格配额（实测 1 条）。发送前必须同时确认：目标号正确、文案已定稿、发送键确实存在。',
)


RESUME_DIALOG = Page(
    key='resume_dialog', name='继续编辑弹窗（残留草稿）',
    identity=(
        _t('继续编辑标题', text_re=r'^继续编辑.+吗[？?]$'),
    ),
    targets={
        # ⚠️ 这个 ❌ 是**纯图标**，没有 text/label，id 也只是混淆占位
        #    (`com.xingin.xhs:id/0_resource_name_obfuscated`)，**不能按文案或 id 定位**。
        #    实测几何（2026-11-05，1080x1920）：clickable=True 的 FrameLayout，
        #    74x74 @(799,478)，里面套一个 47x47 的 ImageView。
        #    可执行的定位规则在 resume_dialog.py：无文本 + clickable + 30~130px。
        'close': _t('关闭图标❌', type='FrameLayout', risk=RISK_NONE,
                    note='纯图标，无 text/label；几何 74x74@(799,478)，父 clickable；'
                         '定位靠 resume_dialog.py 的几何规则，不靠此处条件'),
        'save_draft': _t('存草稿', text='存草稿', risk=RISK_SEND,
                         note='会写草稿；不要用它关弹窗'),
        'go_edit': _t('去编辑', text='去编辑', risk=RISK_NONE,
                      note='会进编辑器；不要用它关弹窗'),
    },
    note='上一轮发布被中断（没选「不存草稿」）后，下次进入小红书必弹。'
         '实测 BACK **关不掉**（连按两次弹窗仍在），必须点右上角 ❌。'
         '主人 2026-11-05 定的顺序：1) 系统后退键 2) × 号。'
         '弹窗共 11 个元素，盖住整屏、吃掉所有点击，不清理会表现为「进不去搜索页」。',
)


PAGES = {p.key: p for p in (POST_ENTRY, ALBUM, VIDEO_PREVIEW, VIDEO_EDIT, IMAGE_EDIT, POST_PAGE,
                            SEARCH_RESULT, USER_RESULT, NOTE_DETAIL, COMMENTS, OTHER_PROFILE, CHAT,
                            RESUME_DIALOG)}


# ---------------------------------------------------------------------------
# 运行时匹配
# ---------------------------------------------------------------------------
def _text(el) -> str:
    return (el.get('text') or '').strip()


def _label(el) -> str:
    return (el.get('label') or '').strip()


def _type(el) -> str:
    return str(el.get('type') or '')


def matches(el, target: Target) -> bool:
    """text 精确 > label 正则 > text 正则；type 是附加约束。全部给定条件都要满足。"""
    if target.type and target.type not in _type(el):
        return False
    if target.text and _text(el) != target.text:
        return False
    if target.text_re and not re.search(target.text_re, _text(el)):
        return False
    if target.label_re and not re.search(target.label_re, _label(el)):
        return False
    return bool(target.text or target.label_re or target.text_re or target.type)


def find(page_key: str, name: str, dump) -> dict | None:
    """在 dump 中定位具名控件，返回原始元素（含当场 ref）；多个命中返回 None，不猜。"""
    page = PAGES[page_key]
    target = page.targets.get(name)
    if target is None:
        raise KeyError('%s 没有 %s' % (page_key, name))
    hits = [el for el in dump if matches(el, target)]
    return hits[0] if len(hits) == 1 else None


def find_all(page_key: str, name: str, dump) -> list:
    target = PAGES[page_key].targets[name]
    return [el for el in dump if matches(el, target)]


def page_of(dump, activity: str = '') -> str | None:
    """判断当前 dump 属于哪个页面：优先用 Activity 精确匹配，再看锚点得分。

    2026-11-05 修：以前 Activity 认得、但锚点没命中时会**回退去全局盲猜**，
    结果搜索页（GlobalSearchActivity，输入框是 EditText）被判成「私信会话页」
    （CHAT 的锚点就是 type=EditText），相册页被判成别家。实测采集链 3/66
    快照因此误判。现在 Activity 一旦认出来就不再盲猜。
    """
    if activity:
        owners = [key for key, page in PAGES.items() if page.activity and page.activity == activity]
        for key in owners:
            page = PAGES[key]
            # 同一 Activity 下可能有多个页面（如发布入口弹窗在首页），再用锚点确认
            if not page.identity or any(matches(el, t) for el in dump for t in page.identity):
                return key
        if owners:
            # Activity 明确认得，只是锚点没中（页面在加载/是变体）：
            # 返回该 Activity 的归属页面，绝不全局盲猜成别的页面。
            return owners[0]
    best, best_score = None, 0
    for key, page in PAGES.items():
        score = sum(1 for t in page.identity if any(matches(el, t) for el in dump))
        if score > best_score:
            best, best_score = key, score
    return best


def edit_texts(dump) -> list:
    """返回页面上所有 EditText，按 y 升序。

    小红书发布页的两个输入框（标题、正文）没有可用 id，只能靠纵向位置区分：
    标题在上、正文在下。这里是纯结构观察，不写入任何用户内容。
    """
    rows = []
    for el in dump:
        if 'EditText' not in _type(el):
            continue
        box = el.get('coordinates') or {}
        if not all(isinstance(box.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
            continue
        if box['width'] <= 0 or box['height'] <= 0:
            continue
        if not re.fullmatch(r'@e[1-9]\d*', str(el.get('ref', ''))):
            continue
        rows.append(el)
    rows.sort(key=lambda e: (e['coordinates']['y'], e['coordinates']['x']))
    return rows


def state_summary(dump) -> str:
    """只记录结构状态，不记录用户文案，便于日志排查。"""
    kinds = [page_of(dump, '') or 'unknown']
    edits = len(edit_texts(dump))
    has_publish = bool(find(POST_PAGE.key, 'publish', dump))
    has_next = bool(find(VIDEO_EDIT.key, 'next', dump)) or bool(find(VIDEO_PREVIEW.key, 'next', dump))
    return '页面=%s 输入框=%d 发布键=%s 下一步=%s' % (
        kinds[0], edits, '有' if has_publish else '无', '有' if has_next else '无')


# 2026-11-06：发布页的「添加组件 / 高级选项 / 可见范围」是**条件性控件**，
# 不同机型/版本不一定渲染（实测 run#128 三项全缺），且不参与任何脚本动作。
# 留着会让发布链固定掉到 0.77，实际流程却已经走到 awaiting_approval。
_OPTIONAL_XHS = {'post_page': ('add_component', 'advanced', 'visibility')}
for _pk, _names in _OPTIONAL_XHS.items():
    _page = PAGES.get(_pk)
    if _page is None or not getattr(_page, 'targets', None):
        continue
    for _n in _names:
        _page.targets.pop(_n, None) if isinstance(_page.targets, dict) else None
