"""TikTok 模板库（最小可用版）。

**为什么是"最小"**：TikTok 之前没有模板库，库里只有 155 份原始 dump 证据。
这次从这些 dump 反推出「强特征」页面，原则是——

    宁可判不出（返回 None），也绝不猜。

因为猜错页面比判不出更糟：调用方会照着错误页面点按钮，把流程带到沟里。

数据来源：7号机 SERIAL0001，TikTok（2026-11-05 采集，run#29/#37/#39）。

**已知局限**：
  · TikTok 的 resource-id 是混淆的三字母（lqb/lqe/llh…），同一版本内稳定、
    跨版本必变。这里只把 id 当作**辅助**证据，主判据一律用文字。
  · 文案随账号语言变化（实测同一台机上同时出现中文「你的限时动态」
    和日文「うちの猫を紹介します」），所以每个页面都尽量覆盖中/日/英。
  · 系统通知栏展开时会混进大量 ImageView（实测 231 元素的"假弹窗"），
    所以判定前先剔除系统 UI。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PKG = 'com.zhiliaoapp.musically'

RISK_NONE = 'none'
RISK_SEND = 'send'

EV_COLLECTED = 'collected'          # 仅从 dump 反推，未经脚本实跑验证


@dataclass(frozen=True)
class Target:
    """一个具名控件。"""
    name: str
    id: str = ''
    text: str = ''
    label_re: str = ''
    text_re: str = ''
    type: str = ''
    role: str = ''
    risk: str = RISK_NONE
    note: str = ''


@dataclass(frozen=True)
class Page:
    key: str
    name: str
    activity: str = ''
    identity: tuple[Target, ...] = ()
    targets: dict[str, Target] = field(default_factory=dict)
    evidence_level: str = EV_COLLECTED
    note: str = ''


# 系统 UI 的包名前缀：判定前先剔掉，否则通知栏会把页面"淹掉"
_SYS_PREFIX = ('com.android.systemui', 'com.samsung.android', 'com.android.launcher')


def _text(el: dict) -> str:
    # TikTok 的数字带不间断空格（实测 '1.8\xa0万'），不去掉的话所有
    # `^\d+$` 类的计数判据全部匹配不上。
    return str(el.get('text') or el.get('label') or '').replace('\xa0', '')


def _is_sys(el: dict) -> bool:
    ident = str(el.get('identifier') or el.get('id') or '')
    return any(ident.startswith(p) for p in _SYS_PREFIX)


def _blob(elements: list[dict]) -> str:
    # 必须用换行拼接：identify() 里大量 `^xxx$` + re.M 的判定，
    # 用空格拼成单行时行首行尾锚点全部失效（实测导致 153/155 判不出）。
    return '\n'.join(_text(e) for e in elements if not _is_sys(e))


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

RESUME_DIALOG = Page(
    key='resume_dialog', name='继续编辑弹窗（上次没发完的视频）',
    identity=(
        Target(name='继续编辑提示',
               text_re=r'继续编辑|繼續編輯|編集を続け|Continue editing|Keep editing'),
    ),
    targets={
        'prompt': Target(name='继续编辑提示',
                         text_re=r'继续编辑|繼續編輯|編集を続け|Continue editing'),
        'cancel': Target(name='取消', text_re=r'^(取消|キャンセル|Cancel)$'),
        'confirm': Target(name='确认', text_re=r'^(确认|確認|Continue)$', risk=RISK_SEND,
                          note='点它会进入编辑页，不点也不影响主流程'),
    },
    note='实测与小红书/FB 同款：上一轮发布中途退出后，再进 App 会弹这个。'
         '处理方式：按 BACK 或点右上角 × 关掉，**不要点「确认」**。',
)

POST_PAGE = Page(
    key='post_page', name='发布/编辑页',
    identity=(
        Target(name='限时动态文案', text_re=r'你的限时动态|Your Story'),
        Target(name='下一步', text_re=r'^(下一步|Next)$'),
    ),
    targets={
        'story': Target(name='限时动态', text_re=r'你的限时动态'),
        'next': Target(name='下一步', text_re=r'^(下一步|Next)$'),
        'caption': Target(name='文案输入', role='EditText'),
    },
    note='数据来自 run#39 的 publish 链（seq=7）。',
)

SEARCH = Page(
    key='search', name='搜索页',
    identity=(
        Target(name='搜索框', text_re=r'^(搜索|搜尋|Search)$'),
    ),
    targets={
        'input': Target(name='搜索输入框', role='EditText'),
        'cancel': Target(name='取消', text_re=r'^(取消|キャンセル|Cancel)$'),
        'clear': Target(name='清除', text_re=r'^(清除|クリア|Clear)$'),
    },
    note='实测 seq=11/14：有「搜索」标题 + 取消键；输入法弹出时会出现「自動填充」。',
)

SEARCH_RESULT = Page(
    key='search_result', name='搜索结果页',
    identity=(
        Target(name='搜索词回显', note='结果页顶部回显关键词，且带多个用户名'),
    ),
    targets={
        'tab_users': Target(name='用户标签', text_re=r'^(用户|ユーザー|Users)$'),
        'video_card': Target(name='视频卡片'),
    },
    note='结果页与搜索页的区别：结果页**没有取消键**，且成排出现用户名/视频卡。'
         '判定靠"搜索词回显 + 至少 3 个用户条目"，见 identify()。',
)

VIDEO_DETAIL = Page(
    key='video_detail', name='视频播放页',
    identity=(
        Target(name='评论计数', text_re=r'^\d+(\.\d+)?[万萬kmbKMB]?$'),
    ),
    targets={
        # 2026-11-05 实测（148 个视频页快照）：
        #   · 用「纯数字」当判据会命中 4~5 个元素（赞/评/转/藏都在竖排），
        #     hits>1 且 id 不同 → 被判成 extra，propose 建不出任何期望（实测 147 次 extra）。
        #   · 改用「固定 id + 纯数字」锁定：d60/c7z/e8_/md5 各覆盖 89% 快照；
        #     cim 覆盖 248%（一屏 2~3 个），不能当唯一判据。
        #   · 这四个 id 的**语义还没人工确认**（TK 的 id 是混淆的，跨版本也会变），
        #     所以命名保持中性，等主人看截图定名后再改 label。
        'count_d60': Target(name='计数·d60', id='d60',
                            text_re=r'^\d+', note='89% 视频页出现，语义待确认'),
        'count_c7z': Target(name='计数·c7z', id='c7z',
                            text_re=r'^\d+', note='89% 视频页出现，语义待确认'),
        'count_e8': Target(name='计数·e8_', id='e8_',
                           text_re=r'^\d+', note='89% 视频页出现，语义待确认'),
        'count_md5': Target(name='计数·md5', id='md5',
                            text_re=r'^\d+', note='89% 视频页出现，语义待确认'),
    },
    note='实测 seq=9：左侧竖排「点赞数/评论数/分享数」等纯数字计数，是该页最明显特征。'
         '纯文字判据会撞多个元素，必须配合 id 才能唯一命中。',
)

COMMENTS = Page(
    key='comments', name='评论区',
    identity=(
        Target(name='评论标题', text_re=r'\d+\s*(条|條|件)?\s*(评论|評論|コメント|comments?)'),
    ),
    targets={
        'close': Target(name='关闭', text_re=r'^(关闭|關閉|閉じる|Close)$'),
        'input': Target(name='评论输入框', role='EditText'),
        'reply': Target(name='回复', text_re=r'^(回复|回覆|返信|Reply)$'),
    },
    note='实测 seq 里评论弹层带「N 条评论」标题 + 底部输入框；'
         'id ce1/gradual_bottom 是该弹层的容器（辅助证据）。',
)

PROFILE = Page(
    key='profile', name='用户主页',
    identity=(
        Target(name='关注按钮', text_re=r'^(关注|追蹤|フォロー|Follow)$'),
    ),
    targets={
        # 2026-11-05：**不放 'follow'**。主页的推荐用户列表里每行都有「关注」，
        # 它是多命中（extra）而不是唯一控件，放进来只会把校对分数拉下来。
        # 页面判定仍用 identity 里的关注按钮，互不影响。
        'followers': Target(name='粉丝', text_re=r'(粉丝|粉絲|フォロワー|Followers)'),
        'likes': Target(name='获赞', text_re=r'(获赞|獲讚|いいね|Likes)'),
        'message': Target(name='发消息', text_re=r'^(发消息|傳訊息|メッセージ|Message)$'),
    },
    note='实测 16 个 profile 快照：主页一定有「关注/粉丝/获赞」三件套之一。',
)

DM_CHAT = Page(
    key='dm_chat', name='私信会话页',
    identity=(
        Target(name='晚安贴纸入口',
               text_re=r'发送晚安贴纸|傳送晚安貼紙|Send a greeting|スタンプを送信'),
    ),
    targets={
        # 2026-11-05 实测（run#50，4 个快照）：私信会话页与「用户主页」长得极像
        # （都有头像/昵称/@handle/关注按钮/粉丝数），唯一区分点是这条
        # 「发送晚安贴纸」——主页没有。之前没有这页，私信页被误判成
        # video_detail / profile，校对时拿视频页期望去比，直接判成一堆 extra。
        'greeting': Target(name='晚安贴纸',
                           text_re=r'发送晚安贴纸|傳送晚安貼紙|Send a greeting'),
        'follow': Target(name='关注', text_re=r'^(关注|追蹤|フォロー|Follow)$'),
        'fans': Target(name='粉丝数', text_re=r'^\d+(\.\d+)?[万萬kK]?$',
                       note='昵称下方紧跟的粉丝数'),
        'input': Target(name='输入框', role='EditText'),
        'send': Target(name='发送', text_re=r'^(发送|傳送|Send)$', risk=RISK_SEND,
                       note='输入框有内容时才出现'),
    },
    note='回复链的停点就在这里：草稿填好、发送键可见、**不点发送**。',
)


PAGES: dict[str, Page] = {
    p.key: p for p in (RESUME_DIALOG, DM_CHAT, POST_PAGE, SEARCH, SEARCH_RESULT,
                       VIDEO_DETAIL, COMMENTS, PROFILE)
}


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------

def _count_user_like(elements: list[dict]) -> int:
    """结果页特征：成排的 @用户名 / 昵称条目。"""
    n = 0
    for e in elements:
        if _is_sys(e):
            continue
        t = _text(e).strip()
        if t.startswith('@') and 2 <= len(t) <= 40:
            n += 1
    return n


def identify(elements: list[dict]) -> str | None:
    """判断当前在哪一页；判不出返回 None（调用方应回首页重来，不要盲点）。"""
    blob = _blob(elements)
    if not blob.strip():
        return None

    # 1) 弹窗最优先（浮层盖在别的页面上）
    if re.search(r'继续编辑|繼續編輯|編集を続け|Continue editing', blob):
        return 'resume_dialog'

    # 2) 私信会话页（与主页极像）
    #    2026-11-06 修：原来只认「发送晚安贴纸」。实测 run#540：这条提示只在
    #    **刚进私信的第一帧**出现，我们一把草稿填进去它就消失了（seq=1 有、seq=4 起没有），
    #    于是后面所有快照都被判成 profile —— 私信页的期望（含输入框）根本没参与过校对。
    #    真正的稳定特征是**输入框**：主页没有 EditText，私信页有。
    #    为免抢评论区（评论区也有 EditText），这里再排除「评论」字样。
    _has_edit = any('EditText' in str(e.get('type') or '')
                    for e in elements if not _is_sys(e))
    _looks_comment = re.search(r'(评论|評論|コメント|comments)', blob, re.I)
    if re.search(r'发送晚安贴纸|傳送晚安貼紙|Send a greeting|スタンプを送信', blob):
        return 'dm_chat'
    if _has_edit and not _looks_comment:
        return 'dm_chat'

    # 3) 发布页
    if re.search(r'你的限时动态|Your Story', blob):
        return 'post_page'
    if re.search(r'^(下一步|Next)$', blob, re.M) and re.search(r'添加|編集|Edit', blob):
        return 'post_page'

    # 3) 视频播放页的计数特征：先把「阿拉伯数字 + 单位」归一，
    #    再数有几段纯计数（点赞/评论/分享竖排）。
    nums = re.findall(r'^\d+(?:\.\d+)?[万萬kmbKMB]?$', blob, re.M)
    has_edit = any('EditText' in str(e.get('type') or '')
                   for e in elements if not _is_sys(e))

    # 4) 主页（关注/粉丝/获赞）
    #    必须排在 video_detail 之前：主页也有「1万」「2287」这类纯数字，
    #    放在后面会被视频页的"≥2 段数字"判据抢先（实测 seq=1 就是这样误判的）。
    if re.search(r'^(关注|追蹤|フォロー|Follow)$', blob, re.M) and \
            re.search(r'粉丝|粉絲|フォロワー|Followers|获赞|獲讚|いいね', blob):
        return 'profile'

    # 5) 评论区弹层：必须**同时**满足三个条件，光有「N 条评论」不算
    #    （视频页底部也有评论入口文字，只按标题文字判会把 125 个快照误判成评论区）
    if (has_edit
            and re.search(r'(评论|評論|コメント|comments)', blob, re.I)
            and re.search(r'^(关闭|關閉|閉じる|Close)$', blob, re.M)):
        return 'comments'

    if len(nums) >= 2:
        return 'video_detail'

    # 7) 搜索结果页：搜索词回显 + 多个用户条目（且没有取消键）
    if _count_user_like(elements) >= 3 and not re.search(r'^(取消|キャンセル|Cancel)$', blob, re.M):
        return 'search_result'

    # 8) 搜索页
    if re.search(r'^(搜索|搜尋|Search)$', blob, re.M):
        return 'search'

    return None


def page_of(elements: list[dict], activity: str = '') -> str | None:
    """与 identify 同义；保留 activity 参数以兼容其它平台的调用签名。"""
    return identify(elements)


def matches(el: dict, target: Target) -> bool:
    """按 Target 字段做「与」匹配。"""
    ident = str(el.get('identifier') or el.get('id') or '')
    short = ident.split(':id/')[-1] if ':id/' in ident else ident
    text = str(el.get('text') or '').strip()
    label = str(el.get('label') or '').strip()
    typ = str(el.get('type') or '')
    if target.id and short != target.id:
        return False
    if target.text and text != target.text:
        return False
    if target.text_re and not re.search(target.text_re, text):
        return False
    if target.label_re and not re.search(target.label_re, label):
        return False
    for key in ('type', 'role'):
        want = str(getattr(target, key, '') or '').lower()
        if want and want not in typ.lower():
            return False
    return bool(target.id or target.text or target.text_re
                or target.label_re or target.type or target.role)


def find(page_key: str, name: str, elements: list[dict]) -> dict | None:
    """在给定元素里找某页的某个控件。"""
    page = PAGES.get(page_key)
    if page is None:
        return None
    target = page.targets.get(name)
    if target is None:
        return None
    for el in elements:
        if matches(el, target):
            return el
    return None
