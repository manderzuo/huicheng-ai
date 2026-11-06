"""抖音 UI 模板库：把实测抓到的控件固化成可复用的选择器。

设计原则（来自实测教训）：
  · **不缓存坐标，也不缓存 ref**。ref 每次 dump 都会变，坐标换机会错位。
    这里只缓存 id / text / label 的正则，运行时用当次 dump 重新解析出 ref。
  · 每个页面先有 identity 锚点，确认“我现在真的在这个页面”，再动作。
  · 每个控件标 risk：
      none      —— 纯浏览/导航/输入，直接执行
      send      —— 会对外发出内容或改变账号对外状态，必须二次确认
      forbidden —— 涉及金钱或不可逆，永不自动化
  · 状态类控件（点赞/收藏/关注）的 label 自带状态词，可直接判断当前是否已选中。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PKG = 'com.ss.android.ugc.aweme'

RISK_NONE = 'none'
RISK_SEND = 'send'
RISK_FORBIDDEN = 'forbidden'


@dataclass(frozen=True)
class Target:
    """一个具名控件。匹配优先级：id > text 精确 > label 正则 > text 正则。"""
    name: str
    id: str = ''
    text: str = ''
    label_re: str = ''
    text_re: str = ''
    risk: str = RISK_NONE
    note: str = ''
    # 状态词：用于判断“当前是否已选中”，例如点赞的 未点赞/已点赞
    on_pattern: str = ''
    off_pattern: str = ''


@dataclass(frozen=True)
class Page:
    key: str
    name: str
    identity: tuple[Target, ...] = ()      # 命中任一即认为在该页
    targets: dict[str, Target] = field(default_factory=dict)
    note: str = ''


def _t(name, **kw):
    return Target(name=name, **kw)


# ---------------------------------------------------------------------------
# 1. feed 视频页
# ---------------------------------------------------------------------------
FEED = Page(
    key='feed', name='视频页（推荐流）',
    identity=(
        _t('底栏首页', id='0py', text='首页'),
        _t('点赞按钮', id='gxu'),
    ),
    targets={
        'like': _t('点赞', id='gxu', risk=RISK_SEND,
                   label_re=r'(未点赞|已点赞)，喜欢',
                   off_pattern=r'^未点赞', on_pattern=r'^已点赞',
                   note='label 形如「未点赞，喜欢1783，按钮」；0 赞时显示「喜欢赞」不是数字'),
        'comment_btn': _t('评论入口', id='e9n', risk=RISK_NONE,
                          label_re=r'^评论', note='打开评论区，纯浏览'),
        'favorite': _t('收藏', id='d_j', risk=RISK_SEND,
                       label_re=r'收藏', off_pattern=r'^未选中', on_pattern=r'^已选中'),
        'share': _t('分享', id='z0n', risk=RISK_SEND, label_re=r'^分享'),
        'follow': _t('关注作者', id='j_x', text='关注', risk=RISK_SEND),
        'author_avatar': _t('作者头像', id='user_avatar', risk=RISK_NONE,
                            note='点进作者主页'),
        'capture': _t('拍摄入口', id='1o+', risk=RISK_NONE),
        'tab_home': _t('底栏-首页', id='0py', text='首页', risk=RISK_NONE),
        'tab_message': _t('底栏-消息', id='0py', text='消息', risk=RISK_NONE),
        'tab_me': _t('底栏-我', id='0py', text='我', risk=RISK_NONE),
        # 2026-11-06 移除：feed/desc 部分视频没有描述；chat/quick_phrase 会多命中
        'desc': _t('作品描述', id='desc', risk=RISK_NONE),
        'author_name': _t('作者@名', id='title', text_re=r'^@', risk=RISK_NONE),
        'danmaku': _t('发弹幕', label_re=r'发弹幕', risk=RISK_SEND),
        # 2026-11-06 移除 feed/same_style：「同款」按钮只在部分视频出现，
        # 没有就整项落空（实测 run#95 掉到 0.97），它也不参与任何脚本动作。
        # 'same_style': _t('拍同款', text='拍同款', risk=RISK_NONE),
        'channel_hot': _t('频道-热点', id='5em', text='热点', risk=RISK_NONE),
        'channel_nearby': _t('频道-同城', id='5em', text='同城', risk=RISK_NONE),
        'channel_follow': _t('频道-关注', id='5em', text='关注', risk=RISK_NONE),
        'channel_rec': _t('频道-推荐', id='5em', text='推荐', risk=RISK_NONE),
        # 2026-11-06 移除 feed/music：首页多张卡片都有音乐入口，
        # 会命中多个（extra）而不是唯一控件。
        # 'music': _t('音乐', label_re=r'^音乐，', risk=RISK_NONE),
    },
    note='顶部频道切换会换掉整个 feed 内容，切完记得切回「推荐」再定位。',
)


# ---------------------------------------------------------------------------
# 2. 评论区
# ---------------------------------------------------------------------------
COMMENTS = Page(
    key='comments', name='评论区（展开）',
    identity=(
        _t('评论标签', id='e7+', text_re=r'^评论\s*\d+$'),
        _t('评论数标题', id='title', text_re=r'^\d+条评论$'),
        _t('关闭评论区', id='back_btn', label_re=r'^关闭$'),
    ),
    targets={
        'close': _t('关闭评论区', id='back_btn', label_re=r'^关闭$', risk=RISK_NONE),
        'tab_comment': _t('评论标签', id='e7+', text_re=r'^评论\s*\d+$', risk=RISK_NONE,
                          note='实测 40.8.0 中面板标题是该控件（text「评论 1284」），不是旧的 title'),
        'ai_digest': _t('AI解析标签', text='AI 解析', risk=RISK_NONE,
                        note='抖音自带评论汇总，未点开验证内容可信度'),
        'list': _t('评论列表', id='rj=', risk=RISK_NONE, note='RecyclerView；上滑可连续加载'),
        'related_search': _t('大家都在搜', text='大家都在搜：', risk=RISK_NONE),
        'expand': _t('放大评论区', id='kk0', label_re=r'放大评论区', risk=RISK_NONE),
        'count': _t('评论数', id='e7+', text_re=r'^评论\s*\d+$', risk=RISK_NONE),
        'item': _t('评论条目', id='fpb', risk=RISK_NONE,
                   note='label 形如「昵称,正文,时间, · 地区,回复 按钮,」；重复出现，须按容器与可见区域关联'),
        'author': _t('评论昵称', id='title', risk=RISK_NONE),
        'content': _t('评论正文', id='content', risk=RISK_NONE),
        'age': _t('评论时间', id='e8h', risk=RISK_NONE, note='形如 2小时前 / 10-25'),
        'region': _t('评论地区', id='e4k', risk=RISK_NONE, note='形如「 · 广东」'),
        'avatar': _t('评论者头像', id='avatar', risk=RISK_NONE,
                     note='label 形如「XX的头像」；点击进入该用户主页'),
        'like': _t('评论点赞', id='gw+', risk=RISK_SEND,
                   label_re=r'^赞', off_pattern=r'未选中', on_pattern=r'已选中'),
        'dislike': _t('踩', id='ddx', risk=RISK_SEND, label_re=r'^踩'),
        'reply': _t('回复', id='xs0', text='回复', risk=RISK_NONE,
                    note='点了进入回复模式，本身不发送'),
        'expand_replies': _t('展开N条回复', id='42x', label_re=r'^展开\d+条回复', risk=RISK_NONE),
        'input': _t('评论输入框', id='e1_', risk=RISK_NONE),
        'send': _t('发送评论', id='e6k', text='发送', risk=RISK_SEND),
    },
    note='实测：评论内容、昵称、时间、地区均可在 UI 树中读到，上滑可加载更多；'
         '窄容器会把文字高度压到十几像素，是否显示完整需按实际高度判断。',
)

COMMENT_INPUT = Page(
    key='comment_input', name='评论输入面板',
    identity=(
        _t('发送按钮', id='e6k', text='发送'),
        _t('评论输入框', id='e1_'),
    ),
    targets={
        'input': _t('输入框', id='e1_', risk=RISK_NONE),
        'send': _t('发送', id='e6k', text='发送', risk=RISK_SEND),
        'insert_image': _t('插入图片', label_re=r'^插入图片$', risk=RISK_NONE),
        'mention': _t('at朋友', label_re=r'^at$', risk=RISK_NONE),
        'emoji': _t('表情', label_re=r'^表情$', risk=RISK_NONE),
        'voice': _t('语音评论', label_re=r'^语音评论$', risk=RISK_NONE),
        'ai': _t('AI创作', label_re=r'^AI创作$', risk=RISK_NONE),
        'ai_parse': _t('AI解析', text='AI 解析', risk=RISK_NONE),
        'quick_emoji': _t('快捷表情', id='ied', risk=RISK_NONE),
        'reply_tab': _t('评论N标签', id='e7+', text_re=r'^评论\s*\d+$', risk=RISK_NONE,
                        note='回复模式下出现，点击可切到「同时发布为作品」并选视频'),
        'publish_as_work': _t('同时发布为作品', id='f-', text='同时发布为作品', risk=RISK_NONE),
    },
)


# ---------------------------------------------------------------------------
# 3. 拍摄页 / 抖音内相册
# ---------------------------------------------------------------------------
CAPTURE = Page(
    key='capture', name='拍摄页',
    identity=(
        _t('相册入口', id='cnv'),
        _t('拍摄按钮', label_re=r'^拍摄，按钮$'),
    ),
    targets={
        'close': _t('关闭', label_re=r'^关闭$', risk=RISK_NONE),
        'album': _t('相册', id='cnv', label_re=r'^相册$', risk=RISK_NONE,
                    note='点它可能弹「创作权限」；须在弹窗前点，详见 note'),
        'shoot': _t('拍摄', label_re=r'^拍摄，按钮$', risk=RISK_NONE),
        'segmented': _t('分段拍', text='分段拍', risk=RISK_NONE),
        'photo_mode': _t('照片模式', text='照片', risk=RISK_NONE),
        'video_mode': _t('视频模式', text='视频', risk=RISK_NONE),
        # 2026-11-06 移除 feed/music：首页多张卡片都有音乐入口，
        # 会命中多个（extra）而不是唯一控件。
        # 'music': _t('选择音乐', text='选择音乐', risk=RISK_NONE),
        'flip': _t('翻转摄像头', label_re=r'^切换到前置摄像头$', risk=RISK_NONE),
        'flash': _t('闪光灯', label_re=r'^闪光灯', risk=RISK_NONE),
        'text': _t('文字', text='文字', risk=RISK_NONE),
        'effect': _t('特效', label_re=r'^选择特效', risk=RISK_NONE),
        'live': _t('开直播', text='开直播', risk=RISK_FORBIDDEN, note='会开直播'),
    },
    note='相册入口的时序：点「拍摄」后必须零延迟点「相册」，中间任何 dump 都会让权限弹窗抢出来。',
)

DL_ALBUM = Page(
    key='album', name='抖音内相册选择',
    identity=(
        _t('标题', id='tv_title', text='所有照片'),
        _t('分类', id='3yu'),
    ),
    targets={
        'cancel': _t('取消', id='back_btn', label_re=r'^取消$', risk=RISK_NONE),
        'filter_all': _t('全部', id='3yu', text='全部', risk=RISK_NONE),
        'filter_video': _t('视频', id='3yu', text='视频', risk=RISK_NONE),
        'filter_image': _t('图片', id='3yu', text='图片', risk=RISK_NONE),
        'filter_gif': _t('动图', id='3yu', text='动图', risk=RISK_NONE),
        'multi_select': _t('多选', text='多选', risk=RISK_NONE),
        'thumbnail': _t('视频缩略图', id='gc1', risk=RISK_NONE),
        'next': _t('下一步', text='下一步', risk=RISK_NONE),
        'search': _t('搜索', id='iz7', risk=RISK_NONE),
    },
)


# ---------------------------------------------------------------------------
# 4. 编辑页
# ---------------------------------------------------------------------------
EDITOR = Page(
    key='editor', name='视频编辑页',
    identity=(
        _t('下一步', id='tkq', text='下一步'),
        _t('右侧工具栏', id='iv_icon'),
    ),
    targets={
        'back': _t('返回', id='am5', label_re=r'^返回$', risk=RISK_NONE),
        # 2026-11-06 移除 feed/music：首页多张卡片都有音乐入口，
        # 会命中多个（extra）而不是唯一控件。
        # 'music': _t('选择音乐', id='3tb', text='选择音乐', risk=RISK_NONE),
        'settings': _t('设置', id='iv_icon', label_re=r'^设置$', risk=RISK_NONE),
        'clip': _t('剪辑', id='iv_icon', label_re=r'^剪辑$', risk=RISK_NONE),
        'text': _t('文字', id='iv_icon', label_re=r'^文字$', risk=RISK_NONE),
        'topic': _t('话题', id='iv_icon', label_re=r'^话题$', risk=RISK_NONE),
        'sticker': _t('贴纸', id='iv_icon', label_re=r'^贴纸$', risk=RISK_NONE),
        'effect': _t('特效', id='iv_icon', label_re=r'^特效$', risk=RISK_NONE),
        'filter': _t('滤镜', id='iv_icon', label_re=r'^滤镜$', risk=RISK_NONE,
                     note='打开特效后右边才出现'),
        'more': _t('更多', id='iv_icon', label_re=r'^更多$', risk=RISK_NONE),
        'collapse': _t('收起', id='iv_icon', label_re=r'^收起$', risk=RISK_NONE),
        'next': _t('下一步', id='tkq', text='下一步', risk=RISK_NONE),
        'daily': _t('限时日常', text='限时日常', risk=RISK_NONE),
        'progress': _t('进度条', id='progress_bar', label_re=r'进度条', risk=RISK_NONE),
    },
)

EDITOR_SETTINGS = Page(
    key='editor_settings', name='编辑-高级设置',
    identity=(_t('标题', id='9a', text='高级设置'),),
    targets={
        'back': _t('返回', id='nur', label_re=r'^返回$', risk=RISK_NONE),
        'save_to_album': _t('发布后保存至相册', id='tv_title', text='发布后保存至相册',
                            label_re=r'已关闭|已开启', risk=RISK_NONE),
        'watermark': _t('保存带水印', id='tv_title', text='保存自己内容带水印', risk=RISK_NONE),
        'hd': _t('高清发布', id='tv_title', text='高清发布', risk=RISK_NONE),
        'daily_permission': _t('日常权限设置', id='tv_title', text='日常权限设置', risk=RISK_NONE),
        'danmaku_permission': _t('谁可以发弹幕', id='tv_title', text='谁可以发弹幕', risk=RISK_NONE),
        'hint': _t('当前值', id='tv_hint', risk=RISK_NONE, note='例如「所有人」'),
    },
    note='开关类控件的状态写在 ViewGroup 的 label 里，如「高清发布已开启」。',
)

EDITOR_CLIP = Page(
    key='editor_clip', name='编辑-剪辑',
    identity=(_t('取消', id='9d', text='取消'), _t('保存', id='9e', text='保存')),
    targets={
        'cancel': _t('取消', id='9d', text='取消', risk=RISK_NONE),
        'save': _t('保存', id='9e', text='保存', risk=RISK_NONE),
        'preview': _t('预览区', id='hh3', risk=RISK_NONE),
        'play': _t('播放', id='n52', label_re=r'^播放$', risk=RISK_NONE),
        'undo': _t('撤销', id='n54', label_re=r'^撤销$', risk=RISK_NONE),
        'redo': _t('恢复', id='n53', label_re=r'^恢复$', risk=RISK_NONE),
        'fullscreen': _t('全屏', id='n51', label_re=r'^全屏$', risk=RISK_NONE),
        'timecode': _t('时间码', id='2dh', risk=RISK_NONE,
                       note='label 形如「当前：0分0秒/总：0分11秒」'),
        'track': _t('素材轨', id='khz', risk=RISK_NONE, note='label 含「素材1,时长11.4秒」'),
        'original_audio': _t('原声', id='3f5', text_re=r'^原声', risk=RISK_NONE),
        'add_media': _t('添加素材', id='nla', label_re=r'^添加素材$', risk=RISK_NONE),
        'tab_clip': _t('剪辑', id='3=7', text='剪辑', risk=RISK_NONE),
        'tab_music': _t('音乐', id='3=7', text='音乐', risk=RISK_NONE),
        'tab_text': _t('文字', id='3=7', text='文字', risk=RISK_NONE),
        'tab_sticker': _t('贴纸', id='3=7', text='贴纸', risk=RISK_NONE),
        'tab_effect': _t('效果', id='3=7', text='效果', risk=RISK_NONE),
        'tab_filter': _t('滤镜', id='3=7', text='滤镜', risk=RISK_NONE),
    },
)

EDITOR_TEXT = Page(
    key='editor_text', name='编辑-文字',
    identity=(_t('确认', id='1_j', label_re=r'^确认$'),),
    targets={
        'confirm': _t('确认', id='1_j', label_re=r'^确认$', risk=RISK_NONE),
        'mention': _t('提醒朋友', id='mt8', text='提醒朋友', risk=RISK_NONE),
        'topic': _t('添加话题', id='mt6', text='添加话题', risk=RISK_NONE),
        'tts': _t('文本朗读', id='mt9', text_re=r'增加文本朗读|文本朗读', risk=RISK_NONE),
        'font_size': _t('字号滑杆', id='1_y', label_re=r'字号大小', risk=RISK_NONE),
        'tab_keyboard': _t('键盘', text='键盘', risk=RISK_NONE),
        'tab_template': _t('模板', text='模板', risk=RISK_NONE),
        'tab_font': _t('字体', text='字体', risk=RISK_NONE),
        'tab_style': _t('样式', text='样式', risk=RISK_NONE),
    },
)

EDITOR_TOPIC = Page(
    key='editor_topic', name='编辑-话题',
    identity=(_t('话题输入框', id='lhm'),),
    targets={
        'back': _t('返回', id='lhe', label_re=r'^返回$', risk=RISK_NONE),
        'input': _t('搜索框', id='lhm', risk=RISK_NONE),
        'cancel': _t('取消', id='lhn', text='取消', risk=RISK_NONE),
        'empty': _t('空结果', id='lhi', text='暂无相关话题', risk=RISK_NONE),
    },
)

EDITOR_STICKER = Page(
    key='editor_sticker', name='编辑-贴纸',
    identity=(_t('分类Tab', label_re=r'^(我的|热门|聊天表情|表情|抖音黄豆表情|小火人)$'),),
    targets={
        'back': _t('返回', id='mv8', label_re=r'^返回$', risk=RISK_NONE),
        'tab_mine': _t('我的', label_re=r'^我的$', risk=RISK_NONE),
        'tab_hot': _t('热门', label_re=r'^热门$', risk=RISK_NONE),
        'tab_chat': _t('聊天表情', label_re=r'^聊天表情$', risk=RISK_NONE),
        'tab_emoji': _t('表情', label_re=r'^表情$', risk=RISK_NONE),
        'tab_douyin': _t('抖音黄豆表情', label_re=r'^抖音黄豆表情$', risk=RISK_NONE),
        'tab_fire': _t('小火人', label_re=r'^小火人$', risk=RISK_NONE),
        'tab_new': _t('新信息化贴纸', label_re=r'^新信息化贴纸$', risk=RISK_NONE),
        'tab_draw': _t('手绘涂鸦', label_re=r'^手绘涂鸦', risk=RISK_NONE),
        'label': _t('贴纸文字', id='0yx', risk=RISK_NONE),
        'item': _t('贴纸项', label_re=r'贴纸$', risk=RISK_NONE),
    },
    note='贴纸与表情都是横向/纵向滚动列表，只抓到首屏，全量需滚动加载。',
)

EDITOR_EFFECT = Page(
    key='editor_effect', name='编辑-特效',
    identity=(_t('特效已展开', id='2n_', label_re=r'^特效，已展开$'),),
    targets={
        'current': _t('当前特效', id='3qc', risk=RISK_NONE),
        'cancel': _t('取消特效', id='mlf', label_re=r'^取消特效$', risk=RISK_NONE),
        'selected_marker': _t('横滑选中位置', id='zhh', label_re=r'横滑道具选中位置', risk=RISK_NONE),
        'item': _t('特效道具', id='0yk', risk=RISK_NONE,
                   note='无特效 / 超高清质感 / 超高清4K / 超自然好看 / 奶蓝生气小猫'),
    },
)


# ---------------------------------------------------------------------------
# 5. 发布页
# ---------------------------------------------------------------------------
PUBLISH = Page(
    key='publish', name='发布信息页',
    identity=(
        _t('标题输入', id='ip_'),
        _t('发作品', text='发作品'),
    ),
    targets={
        'back': _t('返回', id='b76', label_re=r'^返回，按钮$', risk=RISK_NONE),
        'preview': _t('预览', id='4yf', text='预览', risk=RISK_NONE),
        'cover': _t('封面', id='mj+', label_re=r'^图片$', risk=RISK_NONE),
        'ai_cover': _t('AI智能封面', id='een', label_re=r'AI智能封面', risk=RISK_NONE),
        'edit_cover': _t('编辑封面', id='mkb', text='编辑封面', risk=RISK_NONE),
        'title_input': _t('标题输入框', id='ip_', risk=RISK_NONE,
                          note='20 字上限，旁边有 x/20 计数'),
        'title_count': _t('标题字数', id='5cx', text_re=r'^\d+/\d+$', risk=RISK_NONE),
        'desc_input': _t('描述输入框', id='ibo', risk=RISK_NONE),
        'add_topic': _t('添加话题', id='bo_', label_re=r'^#添加话题，按钮$', risk=RISK_NONE),
        'add_mention': _t('@朋友', id='bo9', label_re=r'^@朋友，按钮$', risk=RISK_NONE),
        'expand': _t('展开', id='it+', label_re=r'^展开$', risk=RISK_NONE),
        'suggested_topic': _t('推荐话题', id='lgp', risk=RISK_NONE),
        'location': _t('选择地点', id='vso', label_re=r'^选择地点$', risk=RISK_NONE),
        'clear_location': _t('清除地点', id='x0r', label_re=r'^清除按钮$', risk=RISK_NONE),
        'save_tip': _t('保存提示', id='3qd', text='发布成功后将保存内容至本地', risk=RISK_NONE),
        'publish_work': _t('发作品', id='t5r', label_re=r'^发作品$', risk=RISK_SEND,
                           note='真正的发布动作'),
        'publish_daily': _t('限时日常', id='kje', label_re=r'^限时日常$', risk=RISK_SEND),
        'share': _t('分享', id='yka', label_re=r'^分享，按钮$', risk=RISK_SEND),
    },
    note='标题 ip_ 与描述 ibo 是两个独立输入框，互不影响。',
)


# ---------------------------------------------------------------------------
# 6. 消息 / 私信
# ---------------------------------------------------------------------------
MESSAGES = Page(
    key='messages', name='消息列表页',
    identity=(
        _t('标题', id='tv_title', text='消息'),
        _t('互动消息', label_re=r'^互动消息$'),
    ),
    targets={
        'sidebar': _t('侧边栏', id='rh9', label_re=r'^侧边栏$', risk=RISK_NONE),
        'search': _t('搜索', id='j_9', label_re=r'^搜索$', risk=RISK_NONE),
        'more': _t('更多面板', id='n2m', label_re=r'^更多面板$', risk=RISK_NONE),
        'quick_chat': _t('快捷会话', id='ef1', risk=RISK_NONE),
        'interaction': _t('互动消息', label_re=r'^互动消息$', risk=RISK_NONE),
        'session': _t('会话条目', label_re=r'我们已互相关注', risk=RISK_NONE,
                      note='点开进入私信；也可用昵称 label 定位'),
    },
)

CHAT = Page(
    key='chat', name='私信会话页',
    identity=(
        _t('输入框', id='msg_et'),
        _t('昵称', id='vws'),
    ),
    targets={
        # 2026-11-06 移除：只在有未读时出现，无未读就落空（run#80 掉到 0.94）
        # 'back': _t('返回', label_re=r'^返回未读\d+条消息$', risk=RISK_NONE),
        'nickname': _t('昵称', id='vws', risk=RISK_NONE),
        'call': _t('音视频通话', id='dmz', label_re=r'^音视频通话$', risk=RISK_SEND,
                   note='会直接向对方发起通话'),
        'more': _t('更多', label_re=r'^更多$', risk=RISK_NONE, note='进入聊天信息页'),
        'follow_in_chat': _t('关注', id='j+', text='关注', risk=RISK_SEND),
        'history_image': _t('历史图片', label_re=r'^图片$', risk=RISK_NONE),
        'safety_tip': _t('安全提示', label_re=r'未互相关注的陌生人', risk=RISK_NONE),
        'input': _t('输入框', id='msg_et', risk=RISK_NONE),
        'send': _t('发送', id='jan', label_re=r'^发送$', risk=RISK_SEND,
                   note='有内容时才出现'),
        'voice': _t('语音', id='mx2', label_re=r'^语音$', risk=RISK_NONE),
        'emoji': _t('表情', id='mx2', label_re=r'^表情$', risk=RISK_NONE),
        'text_mode': _t('文本', id='mx2', label_re=r'^文本$', risk=RISK_NONE),
        'more_panel': _t('更多面板', id='mx2', label_re=r'^更多面板$', risk=RISK_NONE),
        'close_panel': _t('关闭面板', label_re=r'^关闭面板$', risk=RISK_NONE),
        'quick_emoji': _t('快捷表情', id='ied', label_re=r'^表情\d+$', risk=RISK_NONE),
        # 2026-11-06 移除：feed/desc 部分视频没有描述；chat/quick_phrase 会多命中
        # 'quick_phrase': _t('快捷短语', label_re=r'^(早上好|比心|\[困\]|\[赞\]|\[捂脸\]|\[玫瑰\])',
        #                    risk=RISK_SEND,
        #                    note='点了立即发送，不需要再点发送按钮'),
        'enter_tip': _t('回车提示', id='tv_tips', risk=RISK_NONE),
    },
    note='从消息页进入有历史消息；从主页进入是新会话，多安全提示和快捷短语。',
)

CHAT_EMOJI = Page(
    key='chat_emoji', name='私信表情面板',
    identity=(
        _t('表情搜索', id='z2t', label_re=r'^表情搜索$'),
        _t('表情分类', id='1or'),
    ),
    targets={
        'search': _t('表情搜索', id='z2t', label_re=r'^表情搜索$', risk=RISK_NONE),
        'cat_classic': _t('经典表情', id='1or', label_re=r'经典表情', risk=RISK_NONE),
        'cat_custom': _t('自定义表情', id='1or', label_re=r'^自定义表情$', risk=RISK_NONE),
        'cat_interact': _t('互动表情', id='1or', label_re=r'^互动表情$', risk=RISK_NONE),
        'cat_cat': _t('敷衍熊', id='1or', label_re=r'^敷衍熊$', risk=RISK_NONE),
        'cat_dog': _t('可巧了有狗', id='1or', label_re=r'^可巧了有狗$', risk=RISK_NONE),
        'cat_kid': _t('抖音小朋友', id='1or', label_re=r'^抖音小朋友$', risk=RISK_NONE),
        'cat_baby': _t('乖巧宝宝', id='1or', label_re=r'^乖巧宝宝$', risk=RISK_NONE),
        'grid': _t('表情网格', id='iyc', risk=RISK_NONE),
        'all_emoji': _t('全部表情', id='iyy', text='全部表情', risk=RISK_NONE),
        'send': _t('发送', id='zk0', text='发送', risk=RISK_SEND),
    },
    note='分类 label 带「已选中，」前缀，可判断当前分类。',
)

CHAT_PANEL = Page(
    key='chat_panel', name='私信玩法面板',
    identity=(
        _t('相册', text='相册'),
        _t('红包', label_re=r'^红包$'),
    ),
    targets={
        'album': _t('相册', text='相册', risk=RISK_NONE, note='从相册发图片/视频'),
        'shoot': _t('拍摄', label_re=r'^拍摄$', risk=RISK_NONE),
        'call': _t('发起通话', label_re=r'^发起通话$', risk=RISK_SEND),
        'watch_together': _t('一起看', label_re=r'^一起看$', risk=RISK_SEND),
        'redpacket': _t('红包', label_re=r'^红包$', risk=RISK_FORBIDDEN),
        'location': _t('位置', label_re=r'^位置$', risk=RISK_SEND),
        'transfer': _t('转账', label_re=r'^转账$', risk=RISK_FORBIDDEN),
        'play_together': _t('一起玩', label_re=r'^一起玩$', risk=RISK_SEND),
        'media_item': _t('相册视频', id='iv_cover', risk=RISK_NONE,
                         note='label 形如「视频11秒拍摄时间：…」'),
        'media_duration': _t('视频时长', id='yr1', risk=RISK_NONE),
    },
)

CHAT_INFO = Page(
    key='chat_info', name='聊天信息页',
    identity=(_t('标题', id='vvw', text='聊天信息'),),
    targets={
        'back': _t('返回', id='znv', label_re=r'^聊天信息', risk=RISK_NONE),
        'mute': _t('免打扰', id='2jw', text='免打扰', risk=RISK_NONE),
        'pin': _t('置顶聊天', id='2jw', text='置顶聊天', risk=RISK_NONE),
        'spark': _t('连续聊天火花', id='2jw', text='连续聊天火花·合养精灵', risk=RISK_NONE),
        'show_spark': _t('显示火花', id='2jw', text='显示聊天火花和合养精灵', risk=RISK_NONE),
        'search_history': _t('查找聊天内容', id='2jw', text='查找聊天内容', risk=RISK_NONE),
        'background': _t('聊天背景', id='2jw', text='聊天背景', risk=RISK_NONE),
        'bubble': _t('聊天气泡', id='2jw', text='聊天气泡', risk=RISK_NONE),
        'clear': _t('清空聊天记录', id='2jw', text='清空聊天记录', risk=RISK_FORBIDDEN),
        'restore': _t('恢复聊天记录', id='2jw', text='恢复聊天记录', risk=RISK_FORBIDDEN),
        'report': _t('举报', id='2jw', text='举报', risk=RISK_FORBIDDEN),
    },
)


# ---------------------------------------------------------------------------
# 7. 用户主页
# ---------------------------------------------------------------------------
PROFILE = Page(
    key='profile', name='用户主页',
    identity=(
        _t('私信按钮', id='zma', label_re=r'^私信$'),
        _t('作品Tab', label_re=r'^作品,按钮'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回(顶部)?$', risk=RISK_NONE),
        'more': _t('更多', label_re=r'^更多$', risk=RISK_NONE,
                   note='举报/拉黑/分享，含 forbidden 项'),
        'tab_following': _t('顶部关注', id='j_t', text='关注', risk=RISK_NONE),
        'follow': _t('关注按钮', id='yi6', text='关注', risk=RISK_SEND),
        'dm': _t('私信', id='zma', label_re=r'^私信$', risk=RISK_NONE,
                 note='打开私信会话页'),
        'works_tab': _t('作品', label_re=r'^作品,按钮', risk=RISK_NONE),
        'nickname': _t('昵称', id='tk1', risk=RISK_NONE,
                       note='label 形如“昵称，复制名字和修改备注”'),
        'douyin_id': _t('抖音号', id='50g', text_re=r'^抖音号[:：]', risk=RISK_NONE,
                        note='形如“抖音号：dy465qlkpus8”，账号唯一标识，跨机定位作者必须用它，昵称会重名'),
        'works_count': _t('作品数', id='text1', text_re=r'^\d+$', risk=RISK_NONE,
                          note='作品 Tab 内的数字，形如 800'),
        'profile_search': _t('主页内搜索', label_re=r'^搜索$', risk=RISK_NONE,
                             note='点开后进入“搜索某作者发过的作品”，只搜该作者作品，不受全局搜索推荐影响'),
    },
    note='实测 2026-10-31：昵称(tk1)、抖音号(50g)、获赞/粉丝、IP 属地、作品数全部可读；'
         '右上角“搜索”按钮可只在该作者作品内检索。',
)


# ---------------------------------------------------------------------------
# 20. 搜索结果页（实测 40.8.0，2026-10-31，1号 SM-G975U1）
# ---------------------------------------------------------------------------
SEARCH = Page(
    key='search', name='搜索结果页',
    identity=(
        _t('搜索框', id='et_search_kw'),
        _t('筛选入口', id='iuy'),
    ),
    targets={
        'input': _t('搜索框', id='et_search_kw', risk=RISK_NONE,
                    note='EditText，text 值即当前关键词；输入后须重新取 ref 再点搜索'),
        'clear': _t('清空', id='btn_clear', risk=RISK_NONE),
        'submit': _t('搜索按钮', id='456', text='搜索', risk=RISK_NONE),
        'back': _t('返回', id='b63', risk=RISK_NONE),
        'tab': _t('分类标签', id='text1', risk=RISK_NONE,
                  note='android:id/text1；文本为 商品/直播/店铺/视频/用户/图文/团购/音乐；'
                       '选中态看父节点 ActionBar$Tab 的 selected；标签栏会横向滚动，顺序随滚动变化'),
        'filter': _t('筛选入口', id='iuy', label_re=r'^筛选', risk=RISK_NONE),
        'results': _t('结果列表', id='cd', risk=RISK_NONE, note='RecyclerView'),
    },
    note='分页面差异极大：视频结果文本可读，用户结果只暴露“关注按钮”，列表昵称/粉丝/简介不进 UI 树。',
)

SEARCH_FILTER = Page(
    key='search_filter', name='搜索筛选浮层',
    identity=(
        _t('查看结果', label_re=r'^查看结果'),
        _t('重置选项', label_re=r'^重置选项'),
    ),
    targets={
        'dismiss': _t('关闭筛选', label_re=r'^关闭筛选', risk=RISK_NONE),
        'apply': _t('查看结果', label_re=r'^查看结果', risk=RISK_NONE),
        'reset': _t('重置选项', label_re=r'^重置选项', risk=RISK_NONE),
        'sort': _t('排序依据', text='排序依据', risk=RISK_NONE,
                   note='选项：综合排序/最新发布/最多点赞/最多评论/最多收藏'),
        'publish_time': _t('发布时间', text='发布时间', risk=RISK_NONE,
                           note='选项：不限/一天内/一周内/半年内'),
        'duration': _t('视频时长', text='视频时长', risk=RISK_NONE,
                       note='选项：不限/1分钟以下/1-5分钟/5分钟以上'),
        'range': _t('搜索范围', text='搜索范围', risk=RISK_NONE,
                    note='选项：不限/关注的人/最近看过/还未看过'),
        'distance': _t('位置距离', text='位置距离', risk=RISK_NONE,
                       note='选项：不限/同城'),
        'option': _t('筛选项', label_re=r'^(已选中|未选中)，.+，按钮$', risk=RISK_NONE,
                     note='无资源 ID；同名“不限”出现多次，必须按所属分组标题与坐标区域归属，'
                          '不能全局唯一匹配；选中态写在 label 前缀'),
    },
    note='仅视频分类实测；商品/直播/店铺/用户/图文/团购的筛选项是否相同尚未验证。',
)

PROFILE_MORE = Page(
    key='profile_more', name='作者主页-更多菜单',
    identity=(
        _t('抖音号标题', id='xo6', text_re=r'^抖音号[:：]'),
        _t('关闭按钮', id='cancel_btn', label_re=r'^关闭$'),
    ),
    targets={
        'close': _t('关闭', id='cancel_btn', label_re=r'^关闭$', risk=RISK_NONE),
        'douyin_id': _t('抖音号', id='xo6', text_re=r'^抖音号[:：]', risk=RISK_NONE),
        'nickname': _t('昵称', id='xo4', risk=RISK_NONE),
        'share_card': _t('分享名片', id='desc', text='分享名片', risk=RISK_NONE,
                         note='抓 uid 的必经入口：点它才进分享面板'),
        'send_dm': _t('发私信', id='desc', text='发私信', risk=RISK_SEND),
        'report': _t('举报', id='desc', text='举报', risk=RISK_NONE),
        'block': _t('拉黑', id='desc', text='拉黑', risk=RISK_NONE),
        'remark': _t('设置备注', id='lnq', text='设置备注', risk=RISK_NONE),
        'hide': _t('不让她看', id='lnq', text='不让她看', risk=RISK_NONE),
        'add_friend': _t('加朋友', id='title', text='加朋友', risk=RISK_SEND),
        'follow': _t('关注', id='lnq', text='关注', risk=RISK_SEND),
    },
    note='实测 2026-11-05（1号机 41.0.0）：菜单项**共用 id**（desc/lnq/title），只能靠 text 区分。'
         '本页曾被误判成 search_video —— 因为菜单项的 desc 撞了 SEARCH_VIDEO 的锚点，'
         '故 search_video 的锚点已收紧为 dr1+5h3。',
)


SHARE_PANEL = Page(
    key='share_panel', name='分享面板（分享给…）',
    identity=(
        _t('分享给标题', id='zyy', text='分享给'),
        _t('复制链接', id='zwi', text='复制链接'),
        _t('取消', id='dn=', label_re=r'^取消$'),
    ),
    targets={
        'title': _t('分享给', id='zyy', text='分享给', risk=RISK_NONE),
        'cancel': _t('取消', id='dn=', label_re=r'^取消$', risk=RISK_NONE),
        'copy_link': _t('复制链接', id='zwi', text='复制链接', risk=RISK_NONE,
                        note='★ 抓 uid 就靠它：点完回剪贴板解析 v.douyin.com 短链'),
        'make_image': _t('生成图片', id='zwi', text='生成图片', risk=RISK_NONE),
        'send_dm': _t('私信', id='zwi', text='私信', risk=RISK_SEND,
                      note='★ 高危：「私信」与「复制链接」同一条操作行，误点直接改变对外状态'),
        'contact_avatar': _t('联系人头像', id='0in', risk=RISK_SEND,
                             note='点它=直接分享给该好友，属发送动作，永不自动化'),
        'contact_name': _t('联系人昵称', id='0i3', risk=RISK_NONE),
    },
    note='实测 2026-11-05：底部操作行**共用 id=zwi**（私信/复制链接/生成图片），只能靠 text 区分。'
         '本页此前不在模板库里，是采集链"判不出页面"的最大来源。',
)


SEARCH_VIDEO = Page(
    key='search_video', name='搜索结果-视频卡片',
    identity=(
        _t('卡片容器', id='dr1'),
        _t('时长', id='5h3'),
    ),
    targets={
        'card': _t('视频卡片', id='dr1', risk=RISK_NONE, note='重复出现，需按可见区域关联'),
        'cover': _t('封面', id='cover', risk=RISK_NONE, note='点击进入播放页'),
        # 2026-11-06 移除：feed/desc 部分视频没有描述；chat/quick_phrase 会多命中
        'desc': _t('视频描述', id='desc', risk=RISK_NONE),
        'duration': _t('时长', id='5h3', risk=RISK_NONE, note='形如 03:02'),
        'author': _t('作者名', id='--', risk=RISK_NONE),
        'age': _t('发布时间', id='3-t', risk=RISK_NONE, note='形如 6天前'),
        'like': _t('点赞数', id='phr', label_re=r'^(未点赞|已点赞)，喜欢', risk=RISK_SEND,
                   off_pattern=r'^未点赞', on_pattern=r'^已点赞'),
    },
    note='视频结果的文本信息（描述/作者/时间）在 UI 树中可读，与用户结果形成对比。',
)

SEARCH_USER = Page(
    key='search_user', name='搜索结果-用户卡片',
    identity=(
        _t('关注按钮', label_re=r'^关注按钮$'),
    ),
    targets={
        'follow': _t('关注按钮', label_re=r'^关注按钮$', risk=RISK_SEND,
                     note='无资源 ID；列表页每个用户卡片只有这一个可访问控件'),
    },
    note='关键限制：昵称、粉丝指数、简介、公司名、认证标识均不出现在 UI 树（截图里可见）。'
         '因此无法在列表页批量提取候选用户，只能逐个进主页读昵称(tk1)与抖音号(50g)，或另需 OCR。',
)

PROFILE_SEARCH = Page(
    key='profile_search', name='作者主页-作品搜索',
    identity=(
        _t('搜索框', id='et_search_kw'),
        _t('搜索提示', label_re=r'^搜索.+发过的作品$'),
    ),
    targets={
        'input': _t('搜索框', id='et_search_kw', risk=RISK_NONE,
                    note='只能搜当前作者自己的作品'),
        'clear': _t('清空', id='btn_clear', risk=RISK_NONE),
        'submit': _t('搜索按钮', id='456', text='搜索', risk=RISK_NONE),
        'back': _t('返回', id='b63', risk=RISK_NONE),
        'count': _t('结果统计', id='y2z', text_re=r'^找到该用户的', risk=RISK_NONE,
                    note='形如“找到该用户的45个相关视频”，说明作者作品检索生效'),
        'hint': _t('搜索提示', label_re=r'^搜索.+发过的作品$', risk=RISK_NONE),
    },
    note='实测 2026-10-31：在作者主页点右上角搜索后进入本页，提示“搜索XXX发过的作品”；'
         '输入标题能精准命中该作者的作品（实测“折叠苹果”→45个相关视频，目标在首屏），'
         '比全局搜索稳定，不受推荐流个性化影响。',
)

VIDEO_PLAYER = Page(
    key='video_player', name='视频播放页',
    identity=(
        _t('评论入口', id='e9n'),
        _t('作者@名', id='title', text_re=r'^@'),
    ),
    targets={
        'comment_entry': _t('评论入口', id='e9n', label_re=r'^评论', risk=RISK_NONE,
                            note='label 形如“评论1284，按钮”；打开评论区'),
        'like': _t('点赞', id='gxu', label_re=r'(未点赞|已点赞)，喜欢', risk=RISK_SEND,
                   off_pattern=r'^未点赞', on_pattern=r'^已点赞'),
        'favorite': _t('收藏', id='d_j', label_re=r'收藏', risk=RISK_SEND),
        'share': _t('分享', id='z0n', label_re=r'^分享', risk=RISK_SEND),
        'follow': _t('关注', id='j_x', text='关注', risk=RISK_SEND),
        'author_avatar': _t('作者头像', id='user_avatar', risk=RISK_NONE),
        'author_name': _t('作者@名', id='title', text_re=r'^@', risk=RISK_NONE),
        # 2026-11-06 移除：feed/desc 部分视频没有描述；chat/quick_phrase 会多命中
        'desc': _t('作品描述', id='desc', risk=RISK_NONE),
        'publish_time': _t('发布时间', id='4v4', label_re=r'^发布时间：', risk=RISK_NONE),
        'danmaku': _t('发弹幕', label_re=r'^发弹幕', risk=RISK_SEND),
        'fullscreen': _t('全屏观看', id='pag', label_re=r'^全屏观看', risk=RISK_NONE),
    },
)


PAGES: dict[str, Page] = {
    p.key: p for p in (
        FEED, COMMENTS, COMMENT_INPUT, CAPTURE, DL_ALBUM,
        EDITOR, EDITOR_SETTINGS, EDITOR_CLIP, EDITOR_TEXT,
        EDITOR_TOPIC, EDITOR_STICKER, EDITOR_EFFECT,
        PUBLISH, MESSAGES, CHAT, CHAT_EMOJI, CHAT_PANEL, CHAT_INFO, PROFILE,
        PROFILE_MORE, SHARE_PANEL,
        SEARCH, SEARCH_FILTER, SEARCH_VIDEO, SEARCH_USER, VIDEO_PLAYER,
        PROFILE_SEARCH,
    )
}


# ---------------------------------------------------------------------------
# 运行时匹配：从当次 dump 里解析出元素
# ---------------------------------------------------------------------------
def _eid(el) -> str:
    return (el.get('identifier') or '').replace(PKG + ':id/', '')


def _text(el) -> str:
    return (el.get('text') or '').strip()


def _label(el) -> str:
    return (el.get('label') or '').strip()


def matches(el, target: Target) -> bool:
    """按 id > text 精确 > label 正则 > text 正则 的顺序判定。"""
    if target.id and _eid(el) != target.id:
        return False
    if target.text and _text(el) != target.text:
        return False
    if target.text_re and not re.search(target.text_re, _text(el)):
        return False
    if target.label_re and not re.search(target.label_re, _label(el)):
        return False
    return bool(target.id or target.text or target.label_re or target.text_re)


def find(page_key: str, name: str, dump: list[dict]) -> dict | None:
    """在 dump 中定位某一页面的具名控件，返回原始元素（含当场 ref）。"""
    page = PAGES[page_key]
    target = page.targets.get(name)
    if target is None:
        raise KeyError('%s 没有 %s' % (page_key, name))
    hits = [el for el in dump if matches(el, target)]
    return hits[0] if hits else None


def page_of(dump: list[dict]) -> str | None:
    """判断当前 dump 属于哪个页面：命中锚点最多的优先。"""
    best, best_score = None, 0
    for key, page in PAGES.items():
        score = sum(1 for t in page.identity if any(matches(el, t) for el in dump))
        if score > best_score:
            best, best_score = key, score
    return best


def state_of(page_key: str, name: str, dump: list[dict]) -> bool | None:
    """状态型控件当前是开还是关；无法判断返回 None。"""
    target = PAGES[page_key].targets[name]
    el = find(page_key, name, dump)
    if el is None or not (target.on_pattern or target.off_pattern):
        return None
    blob = _label(el) + ' ' + _text(el)
    if target.on_pattern and re.search(target.on_pattern, blob):
        return True
    if target.off_pattern and re.search(target.off_pattern, blob):
        return False
    return None


def available(page_key: str, dump: list[dict]) -> list[tuple[str, str, str]]:
    """列出该页面此刻真正可用的控件：(语义名, 风险, 说明)。"""
    page = PAGES[page_key]
    out = []
    for name, target in page.targets.items():
        if find(page_key, name, dump) is not None:
            out.append((name, target.risk, target.note or target.name))
    return out


# ---------------------------------------------------------------------------
# 已实现的功能清单（跨页面的动作组合）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Flow:
    key: str
    name: str
    page: str            # 起点页面
    steps: tuple[str, ...]   # 形如 'page:target'，跨页面动作按顺序列出
    verify: str          # 成功判据
    risk: str = RISK_NONE
    works: bool = True   # 是否已实测跑通


FLOWS: tuple[Flow, ...] = (
    Flow('like', '给当前视频点赞', 'feed', ('feed:like',),
         'label 从「未点赞，喜欢N」变「已点赞，喜欢N+1」', RISK_SEND, True),
    Flow('unlike', '取消点赞', 'feed', ('feed:like',),
         'label 从「已点赞」变「未点赞」，计数 -1', RISK_SEND, True),
    Flow('favorite', '收藏视频', 'feed', ('feed:favorite',),
         'label 从「未选中」变「已选中」', RISK_SEND, True),
    Flow('follow', '关注作者', 'feed', ('feed:follow',),
         '按钮从「关注」变为已关注态', RISK_SEND, True),
    Flow('open_comments', '打开评论区', 'feed', ('feed:comment_btn',),
         '出现「N条评论」标题与评论列表', RISK_NONE, True),
    Flow('close_comments', '关闭评论区', 'comments', ('comments:close',),
         '回到 feed 视频页', RISK_NONE, True),
    Flow('post_comment', '发表评论', 'comments',
         ('comments:input', 'comment_input:send'),
         '评论数 +1 且列表首条含自己的昵称与内容', RISK_SEND, True),
    Flow('reply_comment', '回复某条评论', 'comments',
         ('comments:reply', 'comment_input:input', 'comment_input:send'),
         '回复嵌套出现在原评论下，label 带被回复人昵称', RISK_SEND, True),
    Flow('open_capture', '打开拍摄页', 'feed', ('feed:capture',),
         '出现「相册」「拍摄」按钮', RISK_NONE, True),
    Flow('album_pick', '抖音内相册选视频', 'capture',
         ('capture:album', 'album:thumbnail', 'album:next'),
         '进入编辑页，出现「下一步」', RISK_NONE, True),
    Flow('share_to_douyin', '系统分享视频到抖音', 'feed', ('feed:tab_home',),
         '出现「分享给朋友 / 发布到抖音」选择器（需 SEND intent）', RISK_SEND, True),
    Flow('editor_next', '编辑页进入发布页', 'editor', ('editor:next',),
         '出现标题输入 ip_、描述输入 ibo', RISK_NONE, True),
    Flow('fill_title', '填写标题', 'publish', ('publish:title_input',),
         'ip_ 的 text 变为所填内容，计数 x/20 同步', RISK_NONE, True),
    Flow('fill_desc', '填写描述', 'publish', ('publish:desc_input',),
         'ibo 的 text 变为所填内容', RISK_NONE, True),
    Flow('add_topic', '添加话题', 'publish', ('publish:add_topic',),
         '描述末尾追加 #话题，并弹出推荐话题列表', RISK_NONE, True),
    Flow('pick_location', '选择地点', 'publish', ('publish:location',),
         '出现推荐地点列表与清除按钮', RISK_NONE, True),
    Flow('publish_work', '发布作品', 'publish', ('publish:publish_work',),
         '跳到新作品页：发布时间「刚刚」，我的主页「私密作品，N」+1', RISK_SEND, True),
    Flow('editor_settings', '编辑页高级设置', 'editor', ('editor:settings',),
         '出现高清发布、水印、弹幕权限等开关', RISK_NONE, True),
    Flow('editor_clip', '进入剪辑', 'editor', ('editor:clip',),
         '出现取消/保存、时间轴、素材轨', RISK_NONE, True),
    Flow('editor_text', '添加文字', 'editor', ('editor:text',),
         '出现字号滑杆、确认、键盘/模板/字体/样式', RISK_NONE, True),
    Flow('editor_sticker', '添加贴纸', 'editor', ('editor:sticker',),
         '出现 8 个贴纸分类与贴纸列表', RISK_NONE, True),
    Flow('editor_effect', '添加特效', 'editor', ('editor:effect',),
         '出现「特效，已展开」与道具列表', RISK_NONE, True),
    Flow('open_messages', '进入消息页', 'feed', ('feed:tab_message',),
         '出现「消息」标题、会话列表', RISK_NONE, True),
    Flow('open_chat', '进入私信会话', 'messages', ('messages:session',),
         '出现输入框 msg_et 与昵称', RISK_NONE, True),
    Flow('send_dm_text', '发私信文字', 'chat',
         ('chat:input', 'chat:send'),
         '输入框清空，会话区出现新气泡', RISK_SEND, True),
    Flow('send_dm_emoji', '发私信表情', 'chat',
         ('chat:emoji', 'chat_emoji:send'),
         '表情模式下发送按钮变为 zk0', RISK_SEND, True),
    Flow('send_dm_media', '私信发相册内容', 'chat',
         ('chat:more_panel', 'chat_panel:album'),
         '出现相册视频列表', RISK_SEND, True),
    Flow('dm_from_profile', '从主页发起私信', 'profile', ('profile:dm',),
         '进入 ChatRoomActivity，出现 msg_et', RISK_NONE, True),
    Flow('chat_settings', '打开聊天信息', 'chat',
         ('chat:more', 'chat_info:mute'),
         '出现免打扰/置顶/清空/举报等设置项', RISK_NONE, True),
)


def implemented(only_verified: bool = True) -> list[Flow]:
    return [f for f in FLOWS if f.works or not only_verified]


def summary() -> dict:
    pages = len(PAGES)
    targets = sum(len(p.targets) for p in PAGES.values())
    flows = len(FLOWS)
    send = sum(1 for f in FLOWS if f.risk == RISK_SEND)
    forbidden = sum(1 for p in PAGES.values()
                    for t in p.targets.values() if t.risk == RISK_FORBIDDEN)
    return {'pages': pages, 'targets': targets, 'flows': flows,
            'send_flows': send, 'forbidden_targets': forbidden}