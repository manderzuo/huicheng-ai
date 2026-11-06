"""抖音 B 端提取：把「中转站里的一条线索」变成「屏幕上一个已填好、停在发送键前的私信草稿」。

一次提取的完整链路：

    认领(lead_store.claim)                       —— 原子，两台手机不会拿到同一条
      → 定位到评论人本人（只用抖音号核验，昵称不参与身份）
      → 主页点「私信」打开会话             (douyin_dm.open_chat)
      → 填文案并核实「逐字一致 + 发送键已出现」 (douyin_dm.draft)
      → 回报 extract_result + 证据             (lead_store.record_extract)
      → 停住，等人在屏幕上按发送

**本模块不点击发送。** 发送是有意留白的一步：它需要人已确认目标、文案、配额三道闸门。

定位入口（`locator_entry`），可靠性从高到低：
  · ``profile_already_open`` 页面已经在目标主页（人工打开或上一段流程刚进来），只做核号。
    这是唯一"零猜测"的入口，也是验收时首选。
  · ``from_video_comments``  用 video_title/video_author 回到原视频 → 开评论区 →
    在预算内找唯一「昵称 + 完整正文」→ 点评论人头像进主页。依赖评论仍在预算屏数内。
  · ``search_account``       全局搜抖音号 → 用户 tab。**当前 UI 实测列表页只暴露「关注按钮」**，
    昵称/头像/简介都不在 UI 树里，无法自动进入主页。因此默认 fail closed，
    记 ``not_found_within_budget`` 并写明「入口缺失」，**绝不按关注按钮坐标推测点击**
    ——那既可能误关注（改变对外状态），也会点错人。
"""
from __future__ import annotations

import time

from . import douyin_dm
from .douyin_lead import (_box, _ref_ok, _text_of, _visible, by_id, is_author_comment,
                          page_kind)
from .douyin_skill import SkillError, foreground_activity, foreground_package

PROFILE_ALREADY_OPEN = 'profile_already_open'
FROM_VIDEO_COMMENTS = 'from_video_comments'
SEARCH_ACCOUNT = 'search_account'
ENTRIES = (PROFILE_ALREADY_OPEN, FROM_VIDEO_COMMENTS, SEARCH_ACCOUNT)

#: search_account 路径的可选校准参数：实测后得到"关注按钮上方多少像素是头像区"才可启用。
#: 默认 None = 永不尝试，避免误关注。
DEFAULT_USER_CARD_OFFSET = None

#: 用户搜索结果里点卡片的横向位置。实测 2026-11-01（1080x1920）：
#: 关注按钮占 x=828..1038，头像/昵称区在卡片左侧；取 x=150 时与按钮相距 680px，
#: 不可能误触关注。点击后进主页的命中率在目标号排序靠前时最好。
CARD_LEFT_X = 150
#: 安全间距：目标点必须离关注按钮左边界至少这么远，否则拒绝点击。
MIN_SIDE_GAP = 200
#: 一轮最多核验多少个候选主页（每次进出主页约 5 秒）。
#: 实测反复出现"号在搜索结果里排序靠后、首屏 7 个候选全不符"的情况
#: （Polo_man、21928682739），所以允许滚动多屏，把预算放宽到 15 个。
MAX_CANDIDATES = 15
#: 候选列表最多往下翻几屏。每屏约 5-7 个卡片。
MAX_CANDIDATE_SCREENS = 3


def _visible_list(tree, screen_size):
    return [el for el in tree if _visible(el, screen_size)]


def required_identity(lead):
    """提取前必须能从线索里拿到可核验的身份。

    原路：必须有 commenter_douyin_id（主页读到的抖音号文本）。
    备选（主人 2026-11-05 定）：新机读不到抖音号时，抓到的分享链接 uid
    同样能 100% 直达本人主页，允许以 uid 作为身份依据放行。
    """
    account = str(lead.get('commenter_douyin_id') or '').strip()
    if account:
        return account
    uid = str(lead.get('commenter_uid') or '').strip()
    if uid.isdigit():
        return ''          # 走 uid 直达；调用方据 commenter_uid 判落地
    raise SkillError('提取：该线索没有 commenter_douyin_id 也没有 uid，无法核验身份，拒绝用昵称取证')


def extract_one(*, lead, locate, read_tree, tap, type_text, press, pause, check,
                screen_size, dm_text='', attempts=6, clock=time.monotonic,
                tap_at=None):
    """执行一次提取并返回可入库的结果（不直接写库，便于离线测试与调用方决定收尾状态）。

    参数全是回调，真实设备版由 ``run_extract`` 注入；
    ``locate()`` 负责把设备导航到目标主页并返回**已核验主页**的 UI 树。
    """
    start = clock()
    account = required_identity(lead)
    evidence = {'expected_commenter_douyin_id': account,
                'nickname_not_used_as_identity': True}

    # 第二道防线：评论人就是视频作者本人（作者在自己视频下回复/置顶），
    # 是内容生产行为，不是客户线索，根本不该给他发私信。
    author_self, why = is_author_comment(lead)
    if author_self:
        evidence['skip_reason'] = '评论人是视频作者本人（%s）' % why
        return {'extract_result': 'skipped', 'status': 'skipped',
                'evidence': evidence, 'seconds': clock() - start}

    try:
        check()
        page = locate()
        evidence['locator_entry'] = getattr(locate, 'entry', 'injected')
        locator_trail = getattr(locate, 'evidence', None)
        if locator_trail:
            evidence['locator'] = locator_trail

        chat = douyin_dm.open_chat(
            page, expected_account=account, read_tree=read_tree, tap=tap,
            press=press, pause=pause, check=check, screen_size=screen_size,
            attempts=attempts)
        evidence['chat'] = {k: v for k, v in chat.items()
                            if k in ('profile_account', 'profile_nickname',
                                     'chat_nickname', 'safety_tip', 'send_present')}

        wanted = str(dm_text or lead.get('dm_text') or '').strip()
        if not wanted:
            evidence['draft'] = {'drafted': False, 'reason': '线索没有私信文案只打开了会话'}
            return {'extract_result': 'found', 'status': 'drafted',
                    'evidence': evidence, 'seconds': clock() - start}

        # 默认开启自动翻译：中文母版按评论语种本地化，失败回填中文。
        # 语种完全跟评论走，不设人工强制目标语种；翻译永不点发送。
        from . import draft_i18n
        comment_text = str(lead.get('comment_text') or lead.get('content') or '')
        try:
            from .server import _llm_chat
            localized = draft_i18n.localize_draft(comment_text, wanted, _llm_chat)
        except Exception as exc:
            localized = {'text': wanted, 'target_lang': '', 'fallback': True,
                         'reason': 'llm_import_error:%s' % type(exc).__name__}
        evidence['i18n'] = {
            'source_hint': comment_text[:80],
            'target_lang': localized.get('target_lang') or '',
            'draft_zh': wanted,
            'draft_localized': localized.get('text') or wanted,
            'translator': 'auto',
            'fallback': bool(localized.get('fallback')),
            'reason': localized.get('reason') or '',
        }

        # 点进会话后必须重新读树：不复用点击前的页面快照。
        session_page = read_tree()
        drafted = douyin_dm.draft(
            session_page, localized.get('text') or wanted, read_tree=read_tree, tap=tap,
            type_text=type_text, pause=pause, check=check,
            screen_size=screen_size, attempts=attempts,
            # 用坐标点输入框左侧，避免按 ref 点中心误触表情按钮
            tap_at=tap_at)
        evidence['draft'] = drafted['evidence']
        evidence['draft']['input_text'] = drafted['input_text']
        evidence['draft']['send_clicked'] = False
        return {'extract_result': 'found', 'status': 'drafted',
                'evidence': evidence, 'seconds': clock() - start}

    except SkillError as exc:
        message = str(exc)
        # 失败时同样要保留定位证据：locate() 抛错时，上面那行 evidence['locator']
        # 根本执行不到，导致"核验了 15 个候选"却查不到核验了谁，无法审计。
        locator_trail = getattr(locate, 'evidence', None)
        if locator_trail:
            evidence['locator'] = locator_trail
        result = 'ambiguous' if '不唯一' in message or '歧义' in message else 'error'
        if ('未核实进入会话页' in message or '不在预算' in message
                or '入口缺失' in message or '预算内未找到' in message
                # 搜不到该用户时，抖音的结果页根本不出「用户」tab（只剩综合/视频/
                # 直播/团购/图文/商品）。这是"没这个人"的正常信号，不是程序错误。
                or '没有找到唯一的「用户」tab' in message):
            result = 'not_found_within_budget'
        evidence['failure'] = {'type': type(exc).__name__, 'detail': message}
        return {'extract_result': result,
                'status': 'failed' if result != 'found' else 'drafted',
                'evidence': evidence, 'seconds': clock() - start}


def is_main_search(page):
    """是不是**主搜索页**（而非消息列表里的搜索）。

    两者都有搜索框、都满足 page_kind=='search'，区别在于主搜索页有分类 tab
    （综合/视频/用户/直播…，identifier 都是 android:id/text1）和搜索按钮（456）。
    消息搜索页没有这些——在里面搜抖音号永远搜不到用户。

    注意：分类 tab 有时加载慢，所以「有 tab」或「有搜索按钮」任一命中即认可，
    判太严会误把主搜索页当成消息搜索，反复按返回甚至退到桌面。
    """
    for el in page:
        ident = str(el.get('identifier', ''))
        if ident == 'android:id/text1' or ident.split('/')[-1] == '456':
            return True
    return False


def follow_state(items):
    """按位置记录「关注」类控件的状态：{(x, y): label}。

    用于点击前后对比：同一个位置上的按钮如果从「关注」变成「已关注」，
    说明这一下真的关注了人。**只比文字集合不够**——结果页里本来就有
    别人显示「已关注」，集合不变时会漏检。
    """
    state = {}
    for el in items:
        text = _text_of(el, 'label', 'text')
        if '关注' not in text:
            continue
        box = _box(el)
        if not box:
            continue
        state[(box['x'], box['y'])] = text
    return state


def newly_followed(before, after):
    """找出"点击前是关注、点击后变成已关注"的位置。"""
    changed = []
    for pos, label in after.items():
        old = before.get(pos)
        if old and '已关注' not in old and '已关注' in label:
            changed.append((pos, old, label))
    return changed


def account_locator(account, *, read_tree, tap, tap_at, type_text, press, pause, check,
                    screen_size, max_candidates=MAX_CANDIDATES,
                    max_screens=MAX_CANDIDATE_SCREENS,
                    card_left_x=CARD_LEFT_X, swipe=None, log=None,
                    activity=None):
    """按抖音号定位本人主页（2026-11-01 真机校准路径）。

    路径：进搜索页 → 输入抖音号 → 提交 → 切「用户」tab → 逐个候选点卡片左侧进主页
    → 读 `50g` 与预期逐字比对。列表页不暴露昵称/头像，所以身份只能进主页核。

    安全约束（每条都是实测得来）：
      · 点击点必须离关注按钮左边界 >= MIN_SIDE_GAP，否则直接拒绝——
        点错就是**误关注陌生人**，是改变对外状态的写操作，不可接受；
      · 每个候选必须真的进了可核实主页才继续，没进就停，不做补偿点击；
      · 核号不符 → BACK 回搜索结果页，确认回到搜索页才试下一个。
    """
    say = log or (lambda message: None)
    trail = {'entry': SEARCH_ACCOUNT, 'query': account, 'candidates_checked': []}

    def visible_now():
        return [el for el in read_tree() if _visible(el, screen_size)]

    def to_search():
        page = visible_now()
        # 只有**主搜索页**才算到位；消息列表里的搜索页也有 et_search_kw，
        # 但搜的是聊天记录，进去搜抖音号永远搜不到用户。
        if page_kind(page) == 'search' and is_main_search(page):
            return page

        def current_activity():
            """当前前台 Activity（拿不到返回空串）。"""
            if activity is None:
                return ''
            try:
                return str(activity() or '')
            except Exception:
                return ''

        def on_douyin_home(items):
            """是不是抖音**首页推荐 feed**。

            这里必须用 Activity 兜底：光看元素会把「消息页」「聊天信息页」
            「个人主页」都误判成首页（它们底部也有 tab 或含相似文字），
            误判后就会去点 (1010,170) 那个放大镜位置——在别的页面上纯属乱点。

            首页的 Activity 是 MainActivity；消息页是 ConversationListActivity；
            聊天信息页是 SingleChatProfileActivity2。
            """
            act = current_activity()
            if act:
                # 有 Activity 就按它判，最可靠
                if ('MainActivity' in act
                        and 'Conversation' not in act
                        and 'Chat' not in act):
                    return True
                # SplashActivity 会一直挂着不切换，但内容其实早已加载完成
                # （实测 329 个元素、page_kind=player）。这种"启动壳 + 内容已就绪"
                # 的状态必须当成首页处理，否则会被判成"非首页"而按 BACK，
                # 在 Splash 上按 BACK 就是**直接退出应用**。
                if 'splash' in act.lower() and len(items) >= 40:
                    return True
                return False
            # 拿不到 Activity 时退回元素判断（并排除消息页）
            draft_lines = sum(1 for el in items
                              if '草稿' in _text_of(el, 'label', 'text'))
            if draft_lines >= 2:
                return False
            tabs = ('首页', '首頁', '朋友', '消息', '我', '商城', '同城')
            for el in items:
                text = _text_of(el, 'label', 'text')
                ident = str(el.get('identifier', ''))
                box = _box(el)
                if ident.endswith(':id/home'):
                    return True
                if not box or box['y'] < 1400:
                    continue
                if text in tabs:
                    return True
            return False

        search_ids = ('et_search_kw', '456')

        def dismiss_resume_draft():
            """关掉「继续编辑作品吗？」残留草稿弹窗。

            2026-11-04 实测：上一轮发布被中断后，抖音启动/回到首页会弹这个
            模态框（存草稿 / 去编辑）。它**盖住整屏并吃掉所有点击**，导致
            "停在首页却点不动搜索"——抖音私信曾因此 20 条全失败。

            2026-11-05 主人定：用「系统后退键 → 界面右上角 ×」关掉，
            **绝不点「存草稿」「去编辑」**：点存草稿会留脏数据，点去编辑会进编辑器。
            """
            try:
                from . import resume_dialog as RD
                return RD.dismiss(visible_now, press, tap, pause, log=say)
            except Exception:
                return False

        # 最多退 4 次：主页→结果页→搜索页只要 2 次，从评论区开始也够。
        # 曾经写成 8 次，结果把抖音一路退到**桌面**（末页类型 unknown），
        # 反而彻底失败。
        for _ in range(4):
            # 先清掉可能盖住整屏的「继续编辑作品吗？」残留草稿弹窗
            if dismiss_resume_draft():
                page = visible_now()
            at_home = on_douyin_home(page)
            # 启动页（SplashActivity）：**绝不能按 BACK**，按了就是直接退出应用。
            # 这时元素可能已经加载出来、page_kind 也判成了 player，但 Activity
            # 还不是 MainActivity，会被当成"非首页"而按 BACK —— 一路退到桌面。
            # 正确处理：原地等它变成真正的页面。
            act_now = current_activity()
            # 只有"启动页 + 元素很少"才是真的在加载，此时原地等待。
            # 元素已经很多说明内容就绪（抖音的 SplashActivity 是常驻壳，
            # 不会切换成 MainActivity），这时要当首页处理、正常点搜索入口，
            # 而不是一直 continue 或者按 BACK（按 BACK 会退出应用）。
            if 'splash' in act_now.lower() and len(page) < 40:
                pause(1.5)
                page = visible_now()
                if page_kind(page) == 'search' and is_main_search(page):
                    return page
                continue
            if at_home:
                # 首页：优先认已知控件 id；退而求其次只认"顶部区域的搜索入口"
                cands = [el for el in page
                         if (str(el.get('identifier', '')).split('/')[-1] in search_ids
                             or ('搜索' in _text_of(el, 'label', 'text')
                                 and _box(el) and _box(el)['y'] < 300))
                         and _ref_ok(el) and _box(el)]
                if cands:
                    cands.sort(key=lambda el: _box(el)['y'])
                    tap(cands[0])
                    pause(1.5)
                    page = visible_now()
                    if page_kind(page) == 'search' and is_main_search(page):
                        return page
                # 在首页却找不到搜索入口：首页的放大镜图标在无障碍树里
                # **没有标签**（identifier 是混淆串），只能按位置点。
                # 实测坐标（1080×1920 截图量得）：放大镜中心约 (1010, 170)，
                # 即宽 93.5%、高 8.9% 处。点错的风险已被用户接受（比卡死好）。
                width, height = (screen_size or [1080, 1920])[:2]
                tap_at(int(width * 0.935), int(height * 0.089))
                pause(1.6)
                page = visible_now()
                if page_kind(page) == 'search' and is_main_search(page):
                    return page
                raise SkillError('定位：停在抖音首页且按位置点搜索未生效，已停止')
            # 非首页（评论区/播放页/个人主页）：只走系统返回，绝不点页面内容。
            # 返回后要**等页面真正加载**再判断：只等 1 秒常常读到过渡中的空页面，
            # 判成 unknown 就继续退，一路退到桌面（末页类型 unknown）。
            press('BACK')
            pause(1.6)
            page = visible_now()
            if page_kind(page) == 'search' and is_main_search(page):
                return page
            if page_kind(page) == 'unknown':
                pause(1.2)
                page = visible_now()
                if page_kind(page) == 'search' and is_main_search(page):
                    return page
        # 失败时带上当前页面类型，便于判断是卡在哪种页面
        raise SkillError('定位：未能进入抖音搜索页（末页类型 %s），已停止'
                         % page_kind(page))

    def locate():
        check()
        page = to_search()

        clear = [el for el in page if _text_of(el, 'label', 'text') == '清空']
        if clear:
            tap(clear[0])
            pause(0.6)
            page = visible_now()
        inputs = [el for el in page
                  if str(el.get('identifier', '')).endswith(':id/et_search_kw')]
        if len(inputs) != 1:
            raise SkillError('定位：搜索框不存在或不唯一，已停止')
        tap(inputs[0])
        pause(0.6)
        type_text(account)
        pause(0.6)

        page = visible_now()
        submit = [el for el in page
                  if _text_of(el, 'label', 'text') == '搜索'
                  and str(el.get('identifier', '')).endswith(':id/456')]
        if len(submit) != 1:
            # 降级：去掉版本相关的 id 后缀约束（不同版本后缀会变），
            # 只认顶部区域唯一可点的「搜索」（仍要求唯一，歧义就停）。
            cands = [el for el in page
                     if _text_of(el, 'label', 'text') == '搜索'
                     and _ref_ok(el) and _box(el)
                     and _box(el)['y'] < screen_size[1] * 0.25]
            if len(cands) == 1:
                submit = cands
        if len(submit) != 1:
            # 自愈：树里没有就按文案+顶部区域找，再不行上图让 LLM 看
            from . import ui_resolve as UR
            _heal = UR.heal_tap(serial, ['搜索', '搜索按钮'],
                                page_hint='抖音搜索输入页',
                                region={'xs': 0.45, 'xe': 1.0,
                                        'ys': 0.0, 'ye': 0.3})
            if not _heal.get('tapped'):
                raise SkillError(
                    '定位：搜索按钮不存在或不唯一已停止（自愈未成功：%s）'
                    % _heal.get('reason', ''))
        else:
            tap(submit[0])
        pause(2.5)
        page = visible_now()
        if page_kind(page) != 'search':
            raise SkillError('定位：提交搜索后没有停在搜索结果页，已停止')

        # 搜索结果页的分类 tab 有时还没渲染完，等它出现再点（实测会把
        # "没有找到唯一的用户 tab" 当成硬失败，实际只是慢了一拍）。
        tabs = []
        for attempt in range(4):
            tabs = [el for el in page
                    if str(el.get('identifier', '')) == 'android:id/text1'
                    and _text_of(el, 'text') == '用户']
            if len(tabs) == 1:
                break
            if attempt < 3:
                pause(1.0)
                page = visible_now()
        if len(tabs) != 1:
            raise SkillError('定位：没有找到唯一的「用户」tab（已等待页面渲染），已停止')
        tap(tabs[0])
        pause(2.0)
        page = visible_now()
        trail['tab'] = '用户'

        checked = 0
        for screen_index in range(max_screens if swipe is not None else 1):
            page = visible_now()
            buttons = [el for el in page
                       if '关注按钮' in _text_of(el, 'label', 'text') and _box(el)]
            buttons.sort(key=lambda el: _box(el)['y'])
            if not buttons:
                if screen_index == 0:
                    raise SkillError('定位：用户结果页没有可操作卡片入口，入口缺失')
                break
            trail['card_count'] = max(trail.get('card_count') or 0, len(buttons))
            trail['screens_scanned'] = screen_index + 1
            # 诊断用：记录整张结果页的元素结构，便于确认"该点哪个确定节点进主页"
            if not trail.get('left_elements'):
                trail['left_elements'] = [
                    {'ident': str(el.get('identifier', '')).split('/')[-1],
                     'label': _text_of(el, 'label', 'text')[:28],
                     'clickable': bool(el.get('clickable')),
                     'x': _box(el)['x'], 'y': _box(el)['y'],
                     'w': _box(el)['width'], 'h': _box(el)['height']}
                    for el in page if _box(el) and _box(el)['y'] < 1700
                ][:40]

            # 卡片左侧的头像坐标：优先用头像元素本身。
            # 踩过的坑：原来用「关注按钮的 y 往左推 150px」当作点击点，
            # 但那个位置在有些卡片上是**视频封面**，点下去只会把封面放大
            # （看起来像"点到了背景板"），并不会进主页。
            avatars = [el for el in page
                       if 'avatar' in str(el.get('identifier', '')).lower()
                       and _box(el)]

            for button in buttons:
                check()
                if checked >= max_candidates:
                    break
                box = _box(button)
                # 点卡片之前必须确认「此刻仍在搜索结果页」。
                # 踩过的坑：上一个候选核号不符后按了系统返回，但如果返回没回到
                # 结果页（仍停在别人的个人主页），这里就会在**主页上**按坐标盲点，
                # 看上去像"在人家主页上乱点、还把背景点开放大"。
                if page_kind(page) != 'search':
                    for _ in range(3):
                        press('BACK')
                        pause(1.4)
                        page = visible_now()
                        if page_kind(page) == 'search' and is_main_search(page):
                            break
                    if not (page_kind(page) == 'search' and is_main_search(page)):
                        raise SkillError(
                            '定位：点击卡片前不在搜索结果页（当前 %s），拒绝盲点，已停止'
                            % page_kind(page))
                # 在同一条卡片里找头像（y 最接近关注按钮、且位于按钮左侧）
                same_card = [a for a in avatars
                             if _box(a)['x'] < box['x']
                             and abs(_box(a)['y'] - box['y']) <= 260]
                if same_card:
                    avatar_box = _box(min(same_card, key=lambda el: abs(_box(el)['y'] - box['y'])))
                    target_x = int(avatar_box['x'] + avatar_box['width'] / 2)
                    target_y = int(avatar_box['y'] + avatar_box['height'] / 2)
                    tap_kind = 'avatar'
                else:
                    target_x = int(card_left_x)
                    target_y = int(box['y'] + box['height'] / 2)
                    tap_kind = 'fallback'
                if target_x >= box['x'] - MIN_SIDE_GAP:
                    raise SkillError('定位：卡片点击点离关注按钮过近，拒绝点击（防误关注）')
                # 第二道硬保护：点击点不能落在**任何**「关注」按钮的矩形里。
                # 只比"离按钮左边界多远"不够——布局特殊的卡片会把按钮放在别处，
                # 一旦点中就是关注了陌生人，属于不可接受的对外写操作。
                for el in page:
                    if '关注' not in _text_of(el, 'label', 'text'):
                        continue
                    b = _box(el)
                    if not b:
                        continue
                    if (b['x'] <= target_x <= b['x'] + b['width']
                            and b['y'] <= target_y <= b['y'] + b['height']):
                        raise SkillError(
                            '定位：点击点落在「关注」按钮矩形内（x=%s,y=%s），'
                            '拒绝点击（防误关注）' % (target_x, target_y))
                # 只点一个精确位置，**不做多位置重试**。
                # 血的教训：为了绕过"点到封面"而往更左边多试几个 x，结果在某些
                # 布局特殊的卡片上点到了「关注」按钮，真的把陌生人关注了——
                # 那是对外写操作，不可逆、不可接受。宁可这一条提取失败。
                profile = None
                ids = []
                tried = [target_x]
                # 点击「前」的状态快照：任何写操作都必须能对比前后差异，
                # 否则点错了系统还以为"只是没进主页"，会继续重试、错上加错。
                before_follow = follow_state(page)
                tap_at(target_x, target_y)
                pause(2.5)
                profile = visible_now()

                # 状态审计：同一个位置从「关注」变「已关注」= 真的关注了人。
                # 这是不可接受的对外写操作，立即停止（不自动取关，交给人处理）。
                changed = newly_followed(before_follow, follow_state(profile))
                if changed:
                    raise SkillError(
                        '定位：点击导致关注状态变化 %s → %s（位置 %s），判定为误关注，'
                        '立即停止。请人工检查并取关。'
                        % (changed[0][1], changed[0][2], changed[0][0]))
                ids = by_id(profile, '50g')
                checked += 1

                nick = by_id(profile, 'tk1')
                row = {'index': checked - 1, 'screen': screen_index + 1,
                       'button_y': box['y'], 'tap': [target_x, target_y],
                       'tap_kind': tap_kind, 'tap_tried': tried,
                       'account_nodes': len(ids)}
                if len(ids) != 1:
                    # 点空了（没进主页）：退回结果页继续试下一个候选，而不是整轮失败。
                    # 这不会改变任何对外状态，重试是安全的。
                    row['outcome'] = 'not_a_profile'
                    trail['candidates_checked'].append(row)
                    press('BACK')
                    pause(1.0)
                    page = visible_now()
                    if page_kind(page) != 'search':
                        raise SkillError('定位：点空候选后返回，不在搜索结果页，已停止')
                    continue

                match = douyin_dm.ACCOUNT_RE.fullmatch(_text_of(ids[0], 'text', 'label').strip())
                observed = match.group(1) if match else ''
                row['observed'] = observed
                row['nickname'] = _text_of(nick[0], 'text') if len(nick) == 1 else ''
                if observed and observed == account:
                    row['outcome'] = 'exact_account'
                    trail['candidates_checked'].append(row)
                    trail['verified'] = True
                    say('定位命中 %s（第 %d 个候选，第 %d 屏）'
                        % (account, len(trail['candidates_checked']), screen_index + 1))
                    return profile

                # 滚动重叠会导致同一个人被核验两次：标记出来，便于判断滚动是否给足
                prior = {c.get('observed') for c in trail['candidates_checked']}
                row['outcome'] = ('duplicate_candidate' if observed and observed in prior
                                  else 'account_mismatch')
                trail['candidates_checked'].append(row)
                press('BACK')
                pause(1.2)
                page = visible_now()
                if page_kind(page) != 'search':
                    raise SkillError('定位：候选不符后返回，不在搜索结果页，已停止')

            if checked >= max_candidates or swipe is None:
                break
            # 本屏没命中：往下翻一屏，让排序靠后的候选也能被核验。
            # 距离要给足（屏幕 1920）：滚太小会与上一屏大量重叠，
            # 实测 700 时出现同一个人被核验两次（人间烟火、牵牵手），白烧预算。
            swipe('up', 1300)
            pause(1.8)
            if page_kind(visible_now()) != 'search':
                raise SkillError('定位：滚动候选列表后不在搜索结果页，已停止')

        raise SkillError('定位：预算内未找到该抖音号（核验 %d 个候选 / %d 屏，不代表此人不存在）'
                         % (len(trail['candidates_checked']), trail.get('screens_scanned') or 1))

    locate.entry = SEARCH_ACCOUNT
    locate.evidence = trail
    return locate


def build_callbacks(serial, stop=None, invoke=None):
    """把 gateway 包装成 douyin_dm / douyin_extract 需要的回调集合。

    所有写动作都先查 stop：取消后不再写屏。``invoke`` 允许测试注入假设备。
    """
    from .douyin_lead import screen_dimensions
    from .douyin_skill import elements, foreground_package
    from .mobile_gateway import call as gateway_call

    def _call(tool, args):
        if invoke is not None:
            return invoke(tool, args)
        return gateway_call(tool, args, serial)

    def check():
        if stop is not None and stop.is_set():
            raise SkillError('任务已停止')

    def pause(seconds):
        check()
        if stop is not None:
            stop.wait(seconds)
        else:
            time.sleep(seconds)
        check()

    def read_tree():
        check()
        return elements(_call('mobile_list_elements_on_screen', {'format': 'json'}))

    def tap(el):
        check()
        ref = str((el or {}).get('ref') or '')
        if ref:
            return _call('mobile_click_on_screen_at_coordinates', {'ref': ref})
        box = (el or {}).get('coordinates') or {}
        if not box:
            raise SkillError('提取：点击目标既没有 ref 也没有坐标，已停止')
        return _call('mobile_click_on_screen_at_coordinates',
                     {'x': int(box['x'] + box['width'] / 2),
                      'y': int(box['y'] + box['height'] / 2)})

    def type_text(text):
        check()
        return _call('mobile_type_keys', {'text': text})

    def swipe(direction='up', distance=700):
        check()
        return _call('mobile_swipe_on_screen',
                     {'direction': direction, 'distance': int(distance),
                      'x': 540, 'y': 1500})

    def press(button):
        check()
        return _call('mobile_press_button', {'button': button})

    def tap_at(x, y):
        check()
        return _call('mobile_click_on_screen_at_coordinates',
                     {'x': int(x), 'y': int(y)})

    def size():
        return screen_dimensions(_call('mobile_get_screen_size', {}))

    return {
        'read_tree': read_tree, 'tap': tap, 'tap_at': tap_at, 'type_text': type_text,
        'press': press, 'swipe': swipe, 'pause': pause, 'check': check, 'size': size,
        'foreground': lambda: foreground_package(_call('mobile_get_foreground_app', {})),
        # 仅用于"BACK 无效"时的恢复：重启抖音回首页，不改任何对外状态
        'launch': lambda: _call('mobile_launch_app',
                                {'packageName': 'com.ss.android.ugc.aweme'}),
        # 彻底重启（先杀进程再启动）。只"拉到前台"清不掉卡住的状态，
        # 连续多次识别不到目标界面时必须真重启才能初始化。
        'restart': lambda: (_call('mobile_terminate_app',
                                  {'packageName': 'com.ss.android.ugc.aweme'}),
                            _call('mobile_launch_app',
                                  {'packageName': 'com.ss.android.ugc.aweme'})),
        'activity': lambda: foreground_activity(_call('mobile_get_foreground_app', {})),
        'by_id': lambda page, ident: by_id(_visible_list(page, size()), ident),
    }


def direct_locator(uid, *, serial, read_tree, check, pause, screen_size,
                   fallback, log=None):
    """用**数字 uid** 直达本人主页；落地页读不到抖音号就回落搜号。

    2026-11-05 起这是第一选择：搜号路径要「进搜索页→输入→切用户tab→
    逐个候选点进去核号」，任何一步 UI 变动都会失败；直达是 URL 级寻址，
    命中的就是同一个人，且不受列表页改版影响。
    安全性不降级：直达落地后仍要求主页能读到 抖音号(50g) 与昵称(tk1)，
    再由 extract_one 逐字核对，对不上就按原逻辑失败。
    """
    say = log or (lambda message: None)

    def locate():
        check()
        try:
            from . import deeplink as DL
            r = DL.open_profile(serial, 'douyin', uid, wait=8.0)
        except Exception:   # noqa: BLE001 直达异常一律回落
            r = {'ok': False}
        if r.get('ok'):
            page = [el for el in read_tree() if _visible(el, screen_size)]
            if by_id(page, '50g') and by_id(page, 'tk1'):
                say('直达命中主页：uid=%s' % uid)
                try:
                    from .deeplink import note
                    note('douyin direct HIT uid=%s act=%s' % (uid, (r.get('activity') or '').split('/')[-1]))
                except Exception:
                    pass
                return page
            say('直达落地但读不到抖音号/昵称，回落搜号')
            try:
                from .deeplink import note
                note('douyin direct LANDED-BUT-UNVERIFIED uid=%s' % uid)
            except Exception:
                pass
        else:
            say('直达失败（%s），回落搜号' % (r.get('reason') or '未知'))
            try:
                from .deeplink import note
                note('douyin direct MISS uid=%s reason=%s' % (uid, r.get('reason')))
            except Exception:
                pass
        return fallback()

    return locate


def run_extract(lead, serial, *, dm_text='', stop=None, invoke=None,
                entry=PROFILE_ALREADY_OPEN, locate=None):
    """真实设备版：注入回调 → 跑一次提取 → 返回结果。**不写库，不发送。**

    调用方负责 claim / record_extract；这里只负责"跑一次并如实说结果"。
    """
    hooks = build_callbacks(serial, stop=stop, invoke=invoke)
    screen_size = hooks['size']()
    # 2026-11-06 主人要求：**提取器也要自动启动/切换应用**，不能要求人工先切好。
    # 原来这里直接 raise「提取器不会启动/重启应用，请先人工切到目标页」，
    # 于是 2号 一开提取就失败。现在改成：不在前台就自动拉起来，等它到前台。
    if hooks['foreground']() != 'com.ss.android.ugc.aweme':
        print('  抖音不在前台，自动启动')
        try:
            hooks['launch']()
        except Exception as exc:                          # noqa: BLE001
            raise SkillError('提取：抖音不在前台且自动启动失败（%s）'
                             % type(exc).__name__) from exc
        for _ in range(12):                               # 最多等约 12 秒
            hooks['pause'](1.0)
            if hooks['foreground']() == 'com.ss.android.ugc.aweme':
                print('  抖音已到前台')
                break
        else:
            raise SkillError('提取：自动启动后抖音仍未到前台，已停止')
    if locate is None:
        if entry == SEARCH_ACCOUNT:
            account = str(lead.get('commenter_douyin_id') or '').strip()
            _uid = str(lead.get('commenter_uid') or '').strip()
            # 只有纯数字才是真 uid。字段里混进抖音号（如 8s0d2）时深链必然打不通，
            # 直接当没有 uid —— 省掉每次 8 秒的无效直达等待（2026-11-05 实测：
            # 池里 10 条有 2 条把抖音号写进了 uid 字段）。
            if _uid and not _uid.isdigit():
                _uid = ''
            if not account and not _uid:
                raise SkillError('提取：search_account 入口需要 commenter_douyin_id，已停止')
            if not account:
                # 备选（主人 2026-11-05 定）：新机读不到抖音号，但抓到了分享链接
                # uid —— 直接按 uid 直达本人主页，不做「搜号」这一步。
                locate = direct_locator(
                    _uid, serial=serial, read_tree=hooks['read_tree'],
                    check=hooks['check'], pause=hooks['pause'],
                    screen_size=screen_size, fallback=None)
            else:
                locate = account_locator(
                    account, read_tree=hooks['read_tree'], tap=hooks['tap'],
                    tap_at=hooks['tap_at'], type_text=hooks['type_text'],
                    press=hooks['press'], swipe=hooks['swipe'],
                    pause=hooks['pause'], check=hooks['check'],
                    screen_size=screen_size,
                    # 用前台 Activity 判断"是不是首页"，比看元素可靠
                    activity=hooks.get('activity'))
                # 采集时抓到过数字 uid 的，先用直达（第一选择），不行再搜号（兜底）
                if _uid:
                    locate = direct_locator(
                        _uid, serial=serial, read_tree=hooks['read_tree'],
                        check=hooks['check'], pause=hooks['pause'],
                        screen_size=screen_size, fallback=locate)
        elif entry == PROFILE_ALREADY_OPEN:
            def locate():
                # 当前页面就是（待核验的）主页；不搜索、不启动应用、不改页面。
                return hooks['read_tree']()
        else:
            raise SkillError('提取：入口 %s 尚未实测校准，拒绝按坐标猜测' % entry)
    locate.entry = entry
    result = extract_one(lead=lead, locate=locate, read_tree=hooks['read_tree'],
                         tap=hooks['tap'], type_text=hooks['type_text'],
                         press=hooks['press'], pause=hooks['pause'],
                         check=hooks['check'], screen_size=screen_size,
                         dm_text=dm_text, tap_at=hooks.get('tap_at'))
    # 进不去搜索页时"重启抖音再试一次"。
    # 实测：停在**无返回栈的个人主页**时，系统 BACK 完全无效（页面纹丝不动），
    # 只能靠重新启动应用回到首页。重启不改变任何对外状态，是安全的恢复手段。
    detail = str(((result.get('evidence') or {}).get('failure') or {}).get('detail') or '')
    # 这几种失败都是「页面状态被打乱」，重启抖音回到首页通常就能救回来：
    #   · 进不去搜索页（BACK 无效）
    #   · 结果页没有可操作卡片（搜索没出结果/被清空）
    #   · 点空候选后回不到结果页
    #   · 点击后离开了抖音界面
    # 重启不改变任何对外状态，是安全的恢复手段；只重试一次，避免反复折腾。
    recoverable = ('未能进入抖音搜索页', '没有可操作卡片入口',
                   '不在搜索结果页', '已离开抖音界面')
    if result.get('extract_result') != 'found' and any(k in detail for k in recoverable):
        try:
            # 必须**真重启**（terminate + launch）。
            # 踩过的坑：这里原来调的是 hooks['launch']（只把应用拉到前台），
            # 卡住的状态一点没清，launch_retry 形同虚设，用户侧表现为
            # "重启了抖音还是失败"。
            hooks['restart']()
            # 等到页面**真正就绪**再重试。
            # 判据只看"元素有没有加载出来"，**不看 Activity 名字**：
            # 实测抖音的 SplashActivity 会一直挂着不切换，但内容早就加载好了
            # （实测 329 个元素、page_kind=player）。按 Activity 判断会永远等不到，
            # 白白耗尽 30 秒然后失败。
            ready = False
            for _ in range(20):
                hooks['pause'](1.5)
                try:
                    page_now = hooks['read_tree']()
                    if len(page_now or []) >= 40 and page_kind(page_now) != 'unknown':
                        ready = True
                        break
                except Exception:
                    continue
            retry = extract_one(lead=lead, locate=locate, read_tree=hooks['read_tree'],
                                tap=hooks['tap'], type_text=hooks['type_text'],
                                press=hooks['press'], pause=hooks['pause'],
                                check=hooks['check'], screen_size=screen_size,
                                dm_text=dm_text, tap_at=hooks.get('tap_at'))
            ev = retry.setdefault('evidence', {})
            ev['launch_retry'] = True
            ev['restart_ready'] = ready
            result = retry
        except Exception:
            pass
    # 失败时再用前台 Activity 判断是不是被弹到了登录页：
    # 未登录账号点「私信」会跳 DYLoginActivity，而 UI 树里仍有抖音标识，
    # 只看元素判断不出来，会误报成"未核实进入会话页"。
    if result.get('extract_result') != 'found':
        try:
            activity = hooks['activity']() or ''
        except Exception:
            activity = ''
        if 'login' in activity.lower():
            result['evidence']['failure'] = {
                'type': 'LoginRequired',
                'detail': '抖音弹出了登录页（%s）：该设备很可能未登录抖音账号。'
                          '采集可以匿名进行，但私信必须登录后才能提取' % activity,
            }
    return result
