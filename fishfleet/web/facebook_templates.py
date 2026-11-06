"""Facebook (com.facebook.katana) 页面/控件资料库。

**这是采集资料，不是完整任务实现。** 已采集 ≠ 已验证；已验证 ≠ 通用。
Facebook 会按账号、邮箱验证状态、地区、语言、内容、AB 实验改变页面结构；
同一台设备不同时间打开也可能少/多几个控件。

实测要点（2026-11-01，Galaxy S10 / Android 12 / katana 530.0.0.48.74）：
  · 资源 ID 大面积被裁成 `com.facebook.katana:id/(name removed)`，**不能作为定位依据**。
    唯一可靠的锚点是无障碍 label / text（英文与繁体混排，随 app 语言变化）。
    因此所有匹配都用正则，并保留中英两套写法。
  · 底栏 tab 的 label 自带序号与角标，形如 `Home, tab 1 of 4`、
    `Notifications, tab 4 of 4, 2 new`。角标数字是动态的，正则必须容忍缺省。
  · 抽屉菜单的文字要等异步渲染完成才会出现在层级里；过早 dump 会看到
    只有图标、没有 text 的 ViewGroup。**先等再 dump，不要据此断言"控件无文字"**。
  · 通知详情走内嵌 WebView，原生层级只剩一个 WebView 节点，控件不可采集。
  · 未安装 Messenger 时，消息入口进入的是安装引导页（DiodeMessengerActivity），
    不是会话列表。

匹配优先级：label/text 精确 > label 正则 > text 正则。ID 仅在同页出现
唯一且非 `(name removed)` 时作辅助，不单独使用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PKG = 'com.facebook.katana'

RISK_NONE = 'none'            # 纯浏览、可逆导航
RISK_SEND = 'send'            # 对外发出内容或改变账号对外状态，必须二次确认
RISK_FORBIDDEN = 'forbidden'  # 金钱、授权、安装、删除等不可逆，永不自动执行

# 覆盖等级
COVERED = 'collected'   # 层级已采集（控件字段真实读到）
VERIFIED = 'verified'   # 跳转已实测（点进去核对过目标页）
PARTIAL = 'partial'     # 进入过，但内容/控件不全（加载失败、WebView、空态）
BLOCKED = 'blocked'     # 无法进入（需安装/登录/权限）


@dataclass(frozen=True)
class Target:
    """一个具名控件。不存 ref 与坐标。"""
    name: str
    label_re: str = ''
    text_re: str = ''
    id: str = ''
    type_re: str = ''          # 按控件类型定位（如 EditText / WebView / AutoCompleteTextView）
    risk: str = RISK_NONE
    note: str = ''
    on_pattern: str = ''
    off_pattern: str = ''
    # 结构性重复：列表里的条目、每行都有的按钮（加入/前往/是/否）。
    # 这些多命中是正常的，不能当定位不稳定；但仍须按容器或可视区域关联。
    repeated: bool = False


@dataclass(frozen=True)
class Page:
    key: str
    name: str
    activity_hint: str = ''            # 活动名提示；通常只到 Activity 粒度
    identity: tuple[Target, ...] = ()   # 命中任一即候选，需与 activity 联合降低误判
    # 互斥锚点：必须同时命中，命中即直接判为该页，压过任何计数。
    # 用于抽屉这类"底层页面还留在层级里"的覆盖层。
    exclusive: tuple[Target, ...] = ()
    targets: dict[str, Target] = field(default_factory=dict)
    coverage: str = COVERED
    entry: str = ''                     # 实测进入方式
    note: str = ''


def _t(name: str, **kw) -> Target:
    return Target(name=name, **kw)


# ---------------------------------------------------------------------------
# 1. 首页动态流
# ---------------------------------------------------------------------------
HOME = Page(
    key='home', name='首页动态流',
    activity_hint='FbMainTabActivity',
    identity=(
        # 2026-11-02 08:20 实测：底栏从 **4 个 tab 变成 6 个**，文案也换成了中文
        # `首頁，6個頁籤中的第1個`。所以只按 `tab 1 of 4` 会彻底匹配不上。
        # 这里用"名称 + 逗号"的前缀，兼容语言与 tab 数量的变化。
        _t('底栏首页tab', label_re=r'^(Home, tab \d+ of \d+|首頁[，,])'),
        _t('发帖输入框', label_re=r'^(Make a post on Facebook|在 Facebook )'),
        _t('动态区', label_re=r'^(Stories|限時動態)$'),
        # 顶栏 logo 只在首页出现（其它 tab 页顶栏是标题文本），
        # 且不随信息流滚动消失——用于兜住"滚到底后锚点全滚走"的情况。
        _t('Facebook logo', label_re=r'^(Facebook logo|facebook 標誌)$'),
        # 23:18 实测：首页往下滚后**顶栏和底栏 tab 会一起隐藏**（沉浸式），
        # 上面所有锚点全部消失。这里补两个信息流自身的特征作为兜底锚点。
        _t('广告归属标记', text_re=r'^Sponsored•(Shared with|分享對象)[:：] '),
        _t('隐藏广告', label_re=r'^Hide ad$'),
    ),
    targets={
        # 2026-11-02 08:20 实测：新版顶栏全变中文（繁体）：
        #   Facebook 功能表 / facebook 標誌 / 建立，點按兩下… / 搜尋Facebook / 發訊息
        # 旧版英文（Facebook menu / Search Facebook / Create, … / Messaging）也保留。
        # 2026-11-02 12:45 实测：顶栏文案又简化成 `功能表` / `搜尋`（去掉了 Facebook 前缀）
        'menu': _t('抽屉菜单', label_re=r'^(Facebook menu|Facebook 功能表|功能表)$',
                   risk=RISK_NONE,
                   note='左上汉堡；点击后抽屉从左侧推出，底层首页仍在层级中'),
        'search': _t('搜索', label_re=r'^(Search( Facebook)?|搜尋( ?Facebook)?)$',
                     risk=RISK_NONE,
                     note='实测进入搜索页（ImmersiveActivity）'),
        'create': _t('新建', label_re=r'^(Create,|建立，)', risk=RISK_NONE,
                     note='旧版 label 形如「Create, Double tap to create a new post, story, or reel」，'
                          '新版是中文「建立，點按兩下可建立新貼文、限時動態或 Reel」'),
        'messaging': _t('消息入口', label_re=r'^(Messaging|發訊息)$', risk=RISK_NONE,
                        note='未装 Messenger 时进入安装引导页，不是会话列表'),
        # 2026-11-02 08:20：底栏是 6 个 tab（首頁/Reels/朋友/社團/通知/個人檔案），
        # 文案中文、格式 `名称，6個頁籤中的第N個`；旧版 4 个 tab 用英文 `Home, tab 1 of 4`。
        # 两种都保留，避免一次界面更新就全盘失效。
        'tab_home': _t('底栏-首页',
                       label_re=r'^(Home, tab \d+ of \d+|首頁[，,])', risk=RISK_NONE),
        'tab_reels': _t('底栏-Reels',
                        label_re=r'^(Reels, tab \d+ of \d+|Reels[，,])', risk=RISK_NONE),
        'tab_friends': _t('底栏-好友',
                          label_re=r'^(Friends, tab \d+ of \d+|朋友[，,])', risk=RISK_NONE),
        'tab_groups': _t('底栏-社团',
                         label_re=r'^(Groups, tab \d+ of \d+|社團[，,])', risk=RISK_NONE,
                         note='**新版新增的底部 tab**（旧版没有）'),
        'tab_notifications': _t('底栏-通知',
                                label_re=r'^(Notifications, tab \d+ of \d+|通知[，,])',
                                risk=RISK_NONE,
                                note='未读时 label 末尾带「, N new」或「，N 個新項目」'),
        'tab_profile': _t('底栏-个人档案',
                          label_re=r'^(Profile, tab \d+ of \d+|個人檔案[，,])',
                          risk=RISK_NONE,
                          note='**新版新增的底部 tab**（旧版没有）'),
        'composer': _t('发帖入口',
                       label_re=r'^(Make a post on Facebook|在 Facebook |在想些什麼？)',
                       risk=RISK_NONE,
                       note='实测打开 Create post 编辑器。文案变过三次：'
                            '`Make a post on Facebook` → `在 Facebook 撰寫貼文` → `在想些什麼？`'),
        'composer_media': _t('发帖带图',
                             label_re=r'^(Select photos or videos for your post|'
                                      r'選擇要用於貼文的相片或影片)',
                             risk=RISK_NONE),
        'stories': _t('限时动态区', label_re=r'^(Stories|限時動態)$', risk=RISK_NONE),
        'create_story': _t('创建限时动态', label_re=r'^Create (a )?story', risk=RISK_SEND,
                           note='会对外发布，未点击'),
        'profile_entry': _t('个人主页入口', label_re=r'^Go to profile$', risk=RISK_NONE,
                            note='实测进入个人主页（ImmersivePortraitActivity）'),
        'verify_email': _t('邮箱验证提示', text_re=r'^確認電子郵件地址$', risk=RISK_FORBIDDEN,
                           note='账号级操作，出现说明邮箱未验证；本机实测出现过'),
        'add_email': _t('新增邮箱', text_re=r'^新增其他電子郵件地址$', risk=RISK_FORBIDDEN,
                        note='账号级操作（绑邮箱/验证），不可逆且涉及凭证，未点击'),
        # 2026-11-02 04:24 实测：首页会弹出"确认邮箱"横幅（覆盖在信息流上方）。
        'email_banner': _t('邮箱确认横幅', text_re=r'^再點擊一下就能確認',
                           risk=RISK_FORBIDDEN,
                           note='横幅文案里带**账号真实邮箱**，属敏感信息，'
                                '模板里只按前缀匹配、不记录具体地址'),
        'email_banner_desc': _t('邮箱横幅说明',
                                text_re=r'^新增電子郵件地址到帳號可讓你輕鬆重設密碼',
                                risk=RISK_NONE),
        'email_banner_close': _t('关闭邮箱横幅', label_re=r'^Close$', risk=RISK_NONE,
                                 note='只关横幅、不改账号状态；本次也没点'),
        'feed_list': _t('动态列表', id='android:id/list', risk=RISK_NONE,
                        note='根 RecyclerView；单条动态的容器无独立语义，需按可视区域关联'),
    },
    coverage=VERIFIED,
    entry='App 启动后默认页',
    note='顶栏顺序：菜单 / logo / 新建 / 搜索 / 消息。底栏 4 项。'
         'Stories 与好友推荐都会随账号变化出现或消失。',
)


# ---------------------------------------------------------------------------
# 2. 抽屉菜单
# ---------------------------------------------------------------------------
MENU = Page(
    key='menu', name='左侧抽屉菜单',
    activity_hint='FbMainTabActivity',
    identity=(
        _t('关闭遮罩', label_re=r'^Close$'),
        _t('更多/收起', text_re=r'^(See more|See less)$'),
        # 2026-11-02 04:29 实测：菜单滚动后 `Settings & privacy` 节头会滚出屏幕，
        # exclusive 失效、只剩 Close 一个锚点，页面被判成 home（危险）。
        # 这里补多个菜单项作为计数锚点，保证任何滚动位置都够分。
        _t('菜单项-动态回顾', text_re=r'^動態回顧$'),
        _t('菜单项-社团', text_re=r'^社團$'),
        _t('菜单项-我的珍藏', text_re=r'^我的珍藏$'),
        _t('菜单项-寻找朋友', text_re=r'^尋找朋友$'),
        _t('菜单项-动态消息', text_re=r'^動態消息$'),
        _t('菜单项-活动', text_re=r'^活動$'),
    ),
    exclusive=(
        _t('关闭遮罩', label_re=r'^Close$'),
        _t('设置区标题', label_re=r'^Settings & privacy, header\.'),
    ),
    targets={
        'close': _t('关闭菜单', label_re=r'^Close$', risk=RISK_NONE,
                    note='抽屉右侧半透明遮罩；也可用系统返回键'),
        'profile_card': _t('当前账号', label_re=r'，請查看你的個人檔案$',
                           risk=RISK_NONE, repeated=True,
                           note='昵称是账号动态值，只按后缀「，請查看你的個人檔案」锚定，'
                                '不把具体账号写进模板；实测同一控件暴露两层'),
        'profile_switcher': _t('个人档案切换', label_re=r'^開啟個人檔案切換工具$', risk=RISK_NONE),
        'create_page': _t('建立粉丝专页', label_re=r'^建立 Facebook 粉絲專頁$',
                          risk=RISK_NONE, repeated=True,
                          note='实测同一控件暴露两层'),
        'memories': _t('动态回顾', text_re=r'^動態回顧$', risk=RISK_NONE),
        'saved': _t('我的珍藏', text_re=r'^我的珍藏$', risk=RISK_NONE),
        'groups': _t('社团', text_re=r'^社團$', risk=RISK_NONE,
                     note='实测进入社團页（探索 tab）'),
        'reels': _t('Reel', text_re=r'^Reel$', risk=RISK_NONE),
        'feed': _t('动态消息', text_re=r'^動態消息$', risk=RISK_NONE),
        'find_friends': _t('寻找朋友', text_re=r'^尋找朋友$', risk=RISK_NONE,
                           note='实测进入好友页（带 Back，与底栏好友同一套结构）'),
        'games': _t('游戏', text_re=r'^遊戲$', risk=RISK_NONE),
        'events': _t('活动', text_re=r'^活動$', risk=RISK_NONE),
        'finds': _t('好物特蒐', text_re=r'^好物特蒐$', risk=RISK_NONE,
                    note='需先 See more 展开才出现；实测进入 Finds 页'),
        'birthdays': _t('生日', text_re=r'^生日$', risk=RISK_NONE),
        'likes': _t('赞', text_re=r'^讚$', risk=RISK_NONE,
                    note='11-01 展开后见过；**11-02 05:19 复核：展开菜单里已没有这一项**'),
        'pages': _t('粉丝专页', text_re=r'^粉絲專頁$', risk=RISK_NONE,
                    note='展开后出现；已实测进入'),
        'see_more': _t('展开更多', text_re=r'^See more$', risk=RISK_NONE,
                       note='展开后变成 See less；新增 好物特蒐/生日/讚/粉絲專頁'),
        'see_less': _t('收起', text_re=r'^See less$', risk=RISK_NONE),
        'help_support': _t('帮助与支持', label_re=r'^Help & support', risk=RISK_NONE,
                           note='折叠节，点击展开子项；23:10 实测展开成功'),
        'help_header': _t('帮助与支持（已展开）',
                          label_re=r'^Help & support, header\. Section is expanded',
                          risk=RISK_NONE),
        'support': _t('支持', text_re=r'^Support$', risk=RISK_NONE, note='未进入'),
        'report_problem': _t('报告问题', text_re=r'^Report a problem', risk=RISK_SEND,
                             note='会提交反馈给 Facebook，未进入'),
        'safety': _t('安全', text_re=r'^Safety$', risk=RISK_NONE, note='未进入'),
        'terms': _t('条款与政策', text_re=r'^Terms & Policies$', risk=RISK_NONE,
                    note='未进入'),
        'settings_privacy': _t('设置与隐私', label_re=r'^Settings & privacy', risk=RISK_NONE,
                               note='折叠节；账号与隐私操作入口，未进入'),
        'settings_header': _t('设置与隐私（已展开）',
                              label_re=r'^Settings & privacy, header\. Section is expanded',
                              risk=RISK_NONE,
                              note='展开后 label 变成 "Section is expanded"；再点折叠'),
        'settings': _t('设置', text_re=r'^Settings$', risk=RISK_FORBIDDEN,
                       note='总设置入口；里面含改密码/停用账号/退出登录，'
                            '本次只采集条目、未进入'),
        'privacy_center': _t('隐私中心', text_re=r'^Privacy Center$', risk=RISK_NONE,
                             note='只观察，未进入'),
        'device_requests': _t('装置要求', text_re=r'^裝置要求$', risk=RISK_FORBIDDEN,
                              note='涉及设备授权，未进入'),
        'recent_ad_activity': _t('近期广告动态', text_re=r'^近期廣告動態$', risk=RISK_NONE,
                                 note='只观察，未进入'),
        'orders_payments': _t('订单和付款', text_re=r'^訂單和付款$', risk=RISK_FORBIDDEN,
                              note='交易链路，未进入'),
        'link_history': _t('链接历史', text_re=r'^Link history$', risk=RISK_NONE,
                           note='只观察，未进入'),
        'upgrade': _t('升级', text_re=r'^升級$', risk=RISK_FORBIDDEN,
                      note='含 公開據點 / Meta 驗證 卡片；涉及付费验证，未点击'),
        'meta_products': _t('Meta 旗下产品', text_re=r'^Meta 旗下產品$', risk=RISK_NONE,
                            note='含 Edits / Threads / Instagram / Messenger 外链'),
    },
    coverage=VERIFIED,
    entry='首页左上 label「Facebook menu」',
    note='菜单行是 2 列网格 ViewGroup，text 需等渲染完成。'
         '**菜单项的内容和顺序会变**：2026-11-01 20:51 看到的是'
         '「動態回顧/我的珍藏/社團/Reel/動態消息/尋找朋友/遊戲/活動」；'
         '11-02 03:30 同一个号变成「社團/動態回顧/尋找朋友/好物特蒐/我的珍藏/Reel/活動/動態消息」'
         '——`遊戲` 消失、`好物特蒐` 提前、顺序全变。'
         '所以**只能按文本定位，绝不能用"第 N 个格子"或坐标**。'
         '等几秒仍找不到的项（如当时的 `遊戲`）视为该账号当前没有这个入口。'
         '**展开后（See more）当前只剩 `好物特蒐`/`活動`/`粉絲專頁`/`生日` 四项，'
         '`讚` 也不在了**（11-02 05:19 复核）。',
)


# ---------------------------------------------------------------------------
# 3. 搜索页
# ---------------------------------------------------------------------------
SEARCH = Page(
    key='search', name='搜索页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('返回', label_re=r'^(Back|返回)$'),
        _t('无历史', text_re=r'^沒有近期搜尋紀錄$'),
        # 2026-11-02 00:08 实测：有搜索历史时页面结构完全不同，
        # 只有「近期」分组 + 历史条目，没有"无历史"文案。
        _t('近期分组', text_re=r'^近期$'),
        _t('查看全部历史', text_re=r'^查看所有近期搜尋紀錄$'),
        # 2026-11-02 00:41 实测第三种状态：刚搜完返回时，只剩「输入框 + Clear text + 返回」，
        # 既没有"无历史"文案也没有"近期"分组。
        # 注意只用 Clear text 一个锚点：如果连 EditText 也算，会把「社团搜索结果页」
        # 这类同样带搜索框的页面压过去（实测踩过）。
        _t('清空输入', label_re=r'^Clear text$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'input': _t('搜索输入框', type_re=r'^EditText$', risk=RISK_NONE,
                    note='EditText，进入即 focused；无文本无 label，只能按类型+焦点定位'),
        'empty': _t('无历史提示', text_re=r'^沒有近期搜尋紀錄$', risk=RISK_NONE),
        'recent_group': _t('近期分组', text_re=r'^近期$', risk=RISK_NONE),
        'see_all_history': _t('查看全部历史', text_re=r'^查看所有近期搜尋紀錄$',
                              risk=RISK_NONE),
        'history_item': _t('历史条目', risk=RISK_NONE, repeated=True,
                           note='label 就是历史关键词本身（动态）；'
                                '点它会重新搜索并写入历史，未点击'),
        'more_options': _t('该词更多选项', text_re=r'^此搜尋字詞的更多選項$',
                           risk=RISK_NONE),
        'pymk': _t('你可能认识的朋友', text_re=r'^你可能認識的朋友$', risk=RISK_NONE),
        'see_all_pymk': _t('查看全部推荐好友', text_re=r'^查看所有你可能認識的朋友$',
                           risk=RISK_NONE),
        # 2026-11-02 04:13 实测：搜索空态下方的推荐好友卡片带「移除」按钮。
        'pymk_entry': _t('推荐好友', risk=RISK_NONE, repeated=True,
                         note='label 就是姓名/头衔（动态），不能当锚点'),
        'pymk_invite': _t('发送交友邀请', label_re=r'^傳送交友邀請給 ', risk=RISK_SEND,
                          repeated=True, note='会发出好友请求，未点击'),
        'pymk_remove': _t('移除推荐', label_re=r'^移除$', risk=RISK_NONE, repeated=True,
                          note='只是把该推荐从列表里去掉（本地行为），不影响账号对外状态'),
    },
    coverage=PARTIAL,
    entry='首页顶栏 label「Search Facebook」',
    note='**四种状态都要能认**（都判为 search）：'
         '①无历史（`沒有近期搜尋紀錄` + 好友推荐）；'
         '②有历史（`近期` 分组 + 关键词条目 + `此搜尋字詞的更多選項`）；'
         '③刚搜完返回（只剩输入框 + `Clear text` + 返回）；'
         '④输入中（弹出建议词列表，建议项是 Button，label 就是建议词本身）。'
         '第 ④ 种的建议词是动态内容、**没有稳定标签**，只能按"输入框下方的前几个 Button"关联。'
         '结果页见 search_results。',
)


# ---------------------------------------------------------------------------
# 3b. 搜索结果页（2026-11-01 21:00 实测补充）
# ---------------------------------------------------------------------------
SEARCH_RESULTS = Page(
    key='search_results', name='搜索结果页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('分类标签', label_re=r'^All search results, \d+ of \d+$'),
        _t('任一分类标签', label_re=r' search results, \d+ of \d+$'),
        _t('结果反馈', text_re=r'^這些搜尋結果是否實用？$'),
        _t('清空输入', label_re=r'^Clear text$'),
    ),
    # 有分类标签就一定是结果页：搜索页永远不会有 "search results, N of M"。
    # 这样即使搜索页和结果页都只剩「输入框 + Clear + 返回」，也不会互相误判。
    exclusive=(
        _t('任一分类标签', label_re=r' search results, \d+ of \d+$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'input': _t('搜索框（带关键词）', type_re=r'^EditText$', risk=RISK_NONE,
                    note='text 就是当前关键词，属动态值'),
        'clear': _t('清空关键词', label_re=r'^Clear text$', risk=RISK_NONE),
        'tab_any': _t('结果分类标签', label_re=r' search results, \d+ of \d+$', risk=RISK_NONE,
                      repeated=True,
                      note='实测 7 个：All / Reels / People / Groups / Events / Pages / ?；'
                           '第 7 个默认在屏幕外，需横向滑动'),
        'tab_all': _t('全部结果', label_re=r'^All search results, \d+ of \d+$', risk=RISK_NONE),
        'tab_reels': _t('Reels 结果', label_re=r'^Reels search results, \d+ of \d+$',
                        risk=RISK_NONE,
                        note='**序号不要写死**：关键词不同，分类顺序和数量都会变'
                             '（搜 marketplace 时是 2/7，搜漲知識 时是 6/7）'),
        'tab_people': _t('用户结果', label_re=r'^People search results, \d+ of \d+$',
                         risk=RISK_NONE),
        'tab_groups': _t('社团结果', label_re=r'^Groups search results, \d+ of \d+$',
                         risk=RISK_NONE),
        'tab_events': _t('活动结果', label_re=r'^Events search results, \d+ of \d+$',
                         risk=RISK_NONE),
        'tab_pages': _t('主页结果', label_re=r'^Pages search results, \d+ of \d+$',
                        risk=RISK_NONE),
        'tab_marketplace': _t('Marketplace 结果',
                              label_re=r'^Marketplace search results, \d+ of \d+$',
                              risk=RISK_NONE,
                              note='不是每个关键词都会出现 Marketplace 分类；'
                                   '屏幕外时需横向滑动分类条'),
        'see_all': _t('查看全部', text_re=r'^查看全部$', risk=RISK_NONE, repeated=True),
        'result_item': _t('结果条目（社团）', label_re=r'^.+,\s*(公開|私密)',
                          risk=RISK_NONE, repeated=True,
                          note='label 形如「名称, 公開 · 51 萬位成員 · 一天 90 則以上貼文」；'
                               '名称与人数都是动态内容，不能当锚点'),
        'event_item': _t('结果条目（活动）', label_re=r'^.+,\s*\d+\s*人將參加$',
                         risk=RISK_NONE, repeated=True,
                         note='label 形如「名称, 4 人將參加」；活动名与人数动态'),
        'interested': _t('有兴趣（活动）', label_re=r'^有興趣', risk=RISK_SEND,
                         repeated=True,
                         note='形如「有興趣, <活动名>」；会改变账号对外状态，未点击'),
        'page_item': _t('结果条目（主页）', label_re=r'^.+位追蹤者$',
                        risk=RISK_NONE, repeated=True,
                        note='label 形如「名称, 已驗證, 零售公司 · 199 萬位追蹤者」；'
                             '「已驗證」是可选片段'),
        'follow_page': _t('追踪主页', text_re=r'^追蹤$', risk=RISK_SEND, repeated=True,
                          note='关注主页会改变账号对外状态，未点击'),
        'reel_item': _t('结果条目（Reel）', label_re=r'^.+,\s*\d+\s*週\.\s',
                        risk=RISK_NONE, repeated=True,
                        note='label 形如「作者, 14 週. 标题」；2 列网格布局'),
        'post_item': _t('结果条目（帖子）', label_re=r'^.+,\s*\d+\s*[週天月]\.\s',
                        risk=RISK_NONE, repeated=True,
                        note='形如「謝文甲, 2 週. 正文…」；**点话题标签也会落到这里**，'
                             '因为这个版本没有独立话题页（见文档）'),
        'sound_toggle': _t('视频声音开关', label_re=r'^Video sound toggle$', risk=RISK_NONE),
        'end_of_results': _t('结果结束', text_re=r'^End of results$', risk=RISK_NONE,
                             note='列表到底的标记，可作为"已滑到底"的判据'),
        'join': _t('加入（社团）', text_re=r'^加入$', risk=RISK_SEND, repeated=True,
                   note='**高危重叠区**：搜索结果条目内的「加入」按钮与条目本体重叠，'
                        '2026-11-01 21:02 用固定坐标点击条目时误触，导致真实加入了一个公开社团。'
                        '只能按元素 ref 或该元素自身中心点点击，且点前要核对 label。'),
        'joined': _t('已加入（前往）', text_re=r'^前往$', risk=RISK_NONE, repeated=True,
                     note='「加入」成功后会变成「前往」，可作为加入动作的结果判据'),
        'feedback_q': _t('结果是否有用', text_re=r'^這些搜尋結果是否實用？$', risk=RISK_NONE),
        'feedback_yes': _t('有用', text_re=r'^是$', risk=RISK_NONE, repeated=True,
                           note='Button 与其内部 View 都会命中，属于结构性重复'),
        'feedback_no': _t('没用', text_re=r'^否$', risk=RISK_NONE, repeated=True),
        'related': _t('相关搜索', risk=RISK_NONE, repeated=True,
                      note='底部推荐词按钮，label 就是关键词（动态）；'
                           '不要把它当固定锚点'),
        'scope_hint': _t('可见范围提示', text_re=r'^搜尋結果只會包含你能查看的內容',
                         risk=RISK_NONE),
    },
    coverage=VERIFIED,
    entry='搜索页输入关键词并回车（实测关键词 marketplace）',
    note='**输入关键词会写入账号搜索历史。** 本轮为主人授权的自选关键词测试。'
         '结果条目点进去会跳社团/主页；「加入」按钮为 send 级，未点击。',
)


# ---------------------------------------------------------------------------
# 3c. 搜索结果筛选面板（2026-11-01 23:02 实测，Groups 分类）
# ---------------------------------------------------------------------------
SEARCH_FILTERS = Page(
    key='search_filters', name='搜索筛选面板',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('应用筛选', text_re=r'^Show results$'),
        _t('城市', text_re=r'^城[巿市],\s*(任何地方)?$'),
        _t('重置', text_re=r'^Reset$'),
    ),
    targets={
        'reset': _t('重置', text_re=r'^Reset$', risk=RISK_NONE,
                    note='没有任何筛选时是 disabled'),
        'city': _t('城市', text_re=r'^城[巿市],', risk=RISK_NONE,
                    note='形如「城巿, 任何地方」；点开是城市选择页'),
        'nearby': _t('在我附近', text_re=r'^在我附近$', risk=RISK_NONE,
                     note='CompoundButton 开关；可能触发定位权限'),
        'public_group': _t('公开社团', text_re=r'^公開社團$', risk=RISK_NONE,
                           note='CompoundButton 开关'),
        'my_groups': _t('我的社团', text_re=r'^我的社團$', risk=RISK_NONE,
                        note='CompoundButton 开关'),
        'show_results': _t('显示结果', text_re=r'^Show results$', risk=RISK_NONE,
                           note='应用筛选并关闭面板，纯浏览'),
    },
    coverage=COVERED,
    entry='搜索结果页分类视图右上 `Filters`',
    note='这是**底部弹层**，占屏幕下半，底层搜索结果仍在层级中。'
         '本次只采集 Groups 分类的面板；People/Events/Pages/Reels 的筛选项未必相同。'
         '开关与城市选择均未点击。',
)


# ---------------------------------------------------------------------------
# 3d. 社团帖筛选面板（2026-11-02 01:21 实测，Group Posts 视图）
# ---------------------------------------------------------------------------
GROUP_POSTS_FILTERS = Page(
    key='group_posts_filters', name='社团帖筛选面板',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('应用筛选', text_re=r'^Show results$'),
        _t('最新贴文开关', text_re=r'^最新貼文$'),
        _t('贴文来源', text_re=r'^貼文來源, '),
    ),
    targets={
        'reset': _t('重置', text_re=r'^Reset$', risk=RISK_NONE),
        'latest': _t('最新贴文', text_re=r'^最新貼文$', risk=RISK_NONE,
                     note='CompoundButton 开关'),
        'seen': _t('你看过的贴文', text_re=r'^你看過的貼文$', risk=RISK_NONE,
                   note='CompoundButton 开关'),
        'date': _t('发布日期', text_re=r'^發佈日期, ', risk=RISK_NONE,
                   note='形如「發佈日期, 任何日期」'),
        'source': _t('贴文来源', text_re=r'^貼文來源, ', risk=RISK_NONE,
                     note='形如「貼文來源, 你的社團」'),
        'location': _t('标注地点', text_re=r'^標註的地點, ', risk=RISK_NONE),
        'show_results': _t('显示结果', text_re=r'^Show results$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='社团搜索 → `Group Posts` → 顶部 `N applied filters`',
    note='**筛选面板内容会随分类变化**：这里是「最新貼文/你看過的貼文/發佈日期/貼文來源/'
         '標註的地點」，而搜索结果页 Groups 分类的 `Filters` 面板是'
         '「城巿/在我附近/公開社團/我的社團」。两者都有 `Reset` + `Show results`，'
         '只能靠各自的专属项区分。开关均未点击。',
)



# ---------------------------------------------------------------------------
# 3e. 活动搜索筛选面板（2026-11-02 02:28 实测，Events 分类）
# ---------------------------------------------------------------------------
EVENTS_FILTERS = Page(
    key='events_filters', name='活动筛选面板',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('应用筛选', text_re=r'^Show results$'),
        _t('日期', text_re=r'^日期, '),
        _t('类别', text_re=r'^類別, '),
    ),
    targets={
        'reset': _t('重置', text_re=r'^Reset$', risk=RISK_NONE,
                    note='无筛选时 disabled'),
        'date': _t('日期', text_re=r'^日期, ', risk=RISK_NONE,
                   note='形如「日期, 任何時間」'),
        'location': _t('地点', text_re=r'^地點, ', risk=RISK_NONE,
                       note='形如「地點, 任何地方」'),
        'category': _t('类别', text_re=r'^類別, ', risk=RISK_NONE,
                       note='形如「類別, 所有類別」'),
        'friends_liked': _t('获朋友好评', text_re=r'^獲朋友好評$', risk=RISK_NONE,
                            note='CompoundButton 开关'),
        'show_results': _t('显示结果', text_re=r'^Show results$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='搜索结果页 Events 分类的 `Filters`',
    note='**第三套筛选面板**：Groups 分类是「城巿/在我附近/公開社團/我的社團」，'
         'Group Posts 是「最新貼文/你看過的貼文/發佈日期/貼文來源/標註的地點」，'
         '这里是「日期/地點/類別/獲朋友好評」。三者都有 `Reset` + `Show results`，'
         '**只能靠各自的专属项区分**。开关与下拉均未点击。',
)



# ---------------------------------------------------------------------------
# 3f. 活动筛选-日期选择（2026-11-02 02:31 实测）
# ---------------------------------------------------------------------------
EVENTS_DATE_FILTER = Page(
    key='events_date_filter', name='活动筛选-日期',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('任何时间', text_re=r'^任何時間$'),
        _t('本周末', text_re=r'^本週末$'),
        _t('明天', text_re=r'^明天$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'reset': _t('重置', text_re=r'^Reset$', risk=RISK_NONE),
        'any_time': _t('任何时间', text_re=r'^任何時間$', risk=RISK_NONE,
                       note='实测默认选中'),
        'today': _t('今天', text_re=r'^今天$', risk=RISK_NONE),
        'tomorrow': _t('明天', text_re=r'^明天$', risk=RISK_NONE),
        'this_week': _t('本周', text_re=r'^本週$', risk=RISK_NONE),
        'this_weekend': _t('本周末', text_re=r'^本週末$', risk=RISK_NONE),
        'next_week': _t('下周', text_re=r'^下週$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='`Events` 筛选面板 → 点「日期, 任何時間」',
    note='单选列表（RadioButton），默认选中 `任何時間`。'
         '选项文案随语言变化，别把中文当唯一锚点。'
         '只打开看一眼，**没有改选任何日期**。',
)



# ---------------------------------------------------------------------------
# 3g. 活动筛选-类别选择（2026-11-02 02:33 实测）
# ---------------------------------------------------------------------------
EVENTS_CATEGORY_FILTER = Page(
    key='events_category_filter', name='活动筛选-类别',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('所有类别', text_re=r'^所有類別$'),
        _t('艺术', text_re=r'^藝術$'),
        _t('慈善活动', text_re=r'^慈善活動$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'reset': _t('重置', text_re=r'^Reset$', risk=RISK_NONE),
        'all': _t('所有类别', text_re=r'^所有類別$', risk=RISK_NONE,
                  note='实测默认选中'),
        'option': _t('类别选项', type_re=r'^RadioButton$', risk=RISK_NONE,
                     repeated=True,
                     note='可滚动长列表（藝術/慈善活動/喜劇/手工藝/舞蹈/飲料…）；'
                          '文案随语言变化，别写死某一项'),
    },
    coverage=PARTIAL,
    entry='`Events` 筛选面板 → 点「類別, 所有類別」',
    note='单选长列表，只采到首屏 7 项（`所有類別` 默认选中 + 6 个类别），'
         '**列表可继续下滑，后续类别未采集**。只打开看过，未改选。',
)



# ---------------------------------------------------------------------------
# 3h. 搜索历史条目菜单（2026-11-02 03:49 实测）
# ---------------------------------------------------------------------------
SEARCH_HISTORY_MENU = Page(
    key='search_history_menu', name='搜索历史条目菜单',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('删除历史', label_re=r'^刪除, 從搜尋紀錄移除此項目。$'),
        _t('钉选条件', label_re=r'^釘選此搜尋條件'),
    ),
    targets={
        'close': _t('关闭遮罩', label_re=r'^Close$', risk=RISK_NONE),
        'keyword': _t('历史词', risk=RISK_NONE,
                      note='label 就是搜索词（动态），不能当锚点'),
        'delete': _t('删除该条历史', label_re=r'^刪除, 從搜尋紀錄移除此項目。$',
                     risk=RISK_NONE,
                     note='只删本机搜索历史、不影响账号对外状态；本次未点'),
        'pin': _t('钉选此搜索条件', label_re=r'^釘選此搜尋條件', risk=RISK_NONE,
                  note='最多钉选 3 个；未点'),
    },
    coverage=COVERED,
    entry='搜索页有历史时 → 条目右侧「此搜尋字詞的更多選項」',
    note='底部弹层（遮罩 y≈1130 起）。两个选项都是**本地行为**（删历史/钉选），'
         '不改账号对外状态，所以标 none 而不是 send。'
         '只打开看过，**没有删除也没有钉选**。',
)



# ---------------------------------------------------------------------------
# 3i. 搜索历史全量页（2026-11-02 03:54 实测）
# ---------------------------------------------------------------------------
SEARCH_HISTORY_ALL = Page(
    key='search_history_all', name='搜索历史（全量）',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('历史纪录标题', text_re=r'^歷史紀錄$'),
        # 选中态会变成 `全部 Selected`，切走后变回 `全部`；两种都要认。
        _t('全部排序', label_re=r'^全部( Selected)?$'),
        _t('清空全部', label_re=r'^Clear all search history'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'tab_all': _t('全部', label_re=r'^全部( Selected)?$', risk=RISK_NONE,
                      note='默认选中；选中时 label 追加 ` Selected`'),
        'tab_searches': _t('搜寻', label_re=r'^搜尋( Selected)?$', risk=RISK_NONE),
        'tab_views': _t('浏览数', label_re=r'^瀏覽數( Selected)?$', risk=RISK_NONE),
        'empty': _t('空态', text_re=r'^沒有內容可查看！$', risk=RISK_NONE,
                    note='该账号搜索历史功能被停用，所以恒为空'),
        'clear_all': _t('清空全部历史', label_re=r'^Clear all search history',
                        risk=RISK_NONE,
                        note='实测 label 带 `\\n已停用`——**搜索历史功能对当前账号是关闭的**；'
                             '只清本机历史、不改对外状态，本次未点'),
        'keyword': _t('历史词', risk=RISK_NONE, repeated=True,
                      note='label 就是搜索词（动态）'),
    },
    coverage=PARTIAL,
    entry='搜索页有历史时 → `查看所有近期搜尋紀錄`',
    note='顶部三个排序 tab（`全部`/`搜尋`/`瀏覽數`）+ `歷史紀錄` 标题 + '
         '`Clear all search history`。'
         '**根因已查明（04:00）**：切到「搜尋」tab 后 `Clear all search history` 带 **`已停用`**，'
         '说明**该账号的搜索历史功能是关闭的**——所以列表永远为空、搜完回头也没记录。'
         '这不是"历史被莫名清空"，是功能被禁用。'
         '**有历史时的条目结构仍未知**，等级保持 partial。',
)



# ---------------------------------------------------------------------------
# 4. 好友页
# ---------------------------------------------------------------------------
FRIENDS = Page(
    key='friends', name='好友页',
    activity_hint='',
    identity=(
        _t('标题', label_re=r'^Friends, Heading$'),
        _t('好友请求区', text_re=r'^Friend requests$'),
        _t('分段-建议', text_re=r'^建議$'),
        # 2026-11-02 05:39 实测：切到「你的朋友」分段后是**空态页**，
        # 既没有 `Friends, Heading` 也没有 `Friend requests`，判定变 None。
        # 补两个该状态下一定有的锚点。
        _t('你的朋友', text_re=r'^你的朋友$'),
        _t('好友空态', text_re=r'^When you become friends with people on Facebook'),
    ),
    # 「你可能认识的朋友」全量页也有 Confirm/Delete 请求和 People you may know，
    # 靠计数会和这里打平（都 3 分）。`Friends, Heading` 是好友页独有的，用它定死。
    exclusive=(
        _t('标题', label_re=r'^Friends, Heading$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE,
                   note='从底栏进入时无 Back；从「尋找朋友」进入时有'),
        'title': _t('标题', label_re=r'^Friends, Heading$', risk=RISK_NONE),
        'search': _t('搜索', label_re=r'^Search$', risk=RISK_NONE),
        'seg_suggested': _t('分段-建议', text_re=r'^建議$', risk=RISK_NONE),
        'seg_friends': _t('分段-你的朋友', text_re=r'^你的朋友$', risk=RISK_NONE,
                          note='**当前账号好友数为 0**，切过去是空态'
                               '（`When you become friends with people on Facebook, '
                               'they\'ll appear here.`）；有好友时的列表结构未覆盖'),
        'empty_friends': _t('好友空态',
                            text_re=r'^When you become friends with people on Facebook',
                            risk=RISK_NONE,
                            note='该账号没有好友'),
        'friend_requests': _t('好友请求区', text_re=r'^Friend requests$', risk=RISK_NONE),
        'see_all': _t('查看全部请求', text_re=r'^See all$', risk=RISK_NONE),
        'request_profile': _t('请求者主页', label_re=r"^View .+'s profile$", risk=RISK_NONE),
        'confirm_request': _t('确认好友请求', label_re=r"^Confirm .+'s friend request$",
                              risk=RISK_SEND,
                              note='会建立好友关系，未点击'),
        'delete_request': _t('删除好友请求', label_re=r"^Delete .+'s friend request$",
                             risk=RISK_SEND, note='会移除他人请求，未点击'),
        'pymk': _t('你可能认识', text_re=r'^People you may know$', risk=RISK_NONE),
        'upload_contacts': _t('开启联络人上传', text_re=r'^開啟聯絡人上傳功能以找到朋友$',
                              risk=RISK_FORBIDDEN, note='涉及通讯录授权，未点击'),
        'add_friend': _t('加好友', label_re=r'^Add friend$', risk=RISK_SEND),
    },
    coverage=COVERED,
    entry='底栏「Friends, tab 2 of 4」或菜单「尋找朋友」',
    note='页面主体是英文（Friends / Friend requests / People you may know），'
         '分段按钮却是繁体（建議／你的朋友），说明 FB 文案并非统一语言，'
         '正则必须同时覆盖。',
)


# ---------------------------------------------------------------------------
# 4b. 「你可能认识的朋友」全量页（2026-11-02 02:40 实测）
# ---------------------------------------------------------------------------
PYMK_ALL = Page(
    key='pymk_all', name='你可能认识的朋友（全量）',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('推荐区', text_re=r'^People you may know$'),
        _t('联络人上传提示', text_re=r'^開啟聯絡人上傳功能以找到朋友$'),
        _t('确认请求', label_re=r"^Confirm .+'s friend request$"),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'request_profile': _t('请求者主页', label_re=r"^View .+'s profile$",
                              risk=RISK_NONE, repeated=True),
        'confirm_request': _t('确认好友请求',
                              label_re=r"^Confirm .+'s friend request$",
                              risk=RISK_SEND, repeated=True,
                              note='会建立好友关系，未点击'),
        'delete_request': _t('删除好友请求',
                             label_re=r"^Delete .+'s friend request$",
                             risk=RISK_SEND, repeated=True,
                             note='会移除他人请求，未点击'),
        'pymk': _t('推荐区', text_re=r'^People you may know$', risk=RISK_NONE),
        'upload_contacts': _t('开启联络人上传',
                              text_re=r'^開啟聯絡人上傳功能以找到朋友$',
                              risk=RISK_FORBIDDEN, note='通讯录授权，未点击'),
        'user': _t('推荐用户', label_re=r'^.+$', risk=RISK_NONE, repeated=True,
                   note='推荐用户条目 label 就是名字（动态），'
                        '和「加好友」按钮配对出现，不能按名字定位'),
        'add_friend': _t('加好友', label_re=r'^Add friend$', risk=RISK_SEND,
                         repeated=True),
    },
    coverage=COVERED,
    entry='搜索页「查看所有你可能認識的朋友」',
    note='**和「好友页」（friends）不是同一页**：这里没有 `Friends, Heading` 和分段按钮，'
         '是纯列表（好友请求 + 推荐用户 + 联络人上传提示）。'
         '只采集结构，**没有确认/删除任何请求、没有加好友、没有开启联络人上传**。',
)



# ---------------------------------------------------------------------------
# 4c. 好友请求全量页（2026-11-02 03:09 实测）
# ---------------------------------------------------------------------------
FRIEND_REQUESTS_ALL = Page(
    key='friend_requests_all', name='好友请求（全量）',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('请求区标题', text_re=r'^Friend requests$'),
        _t('排序', label_re=r'^Sort$'),
        _t('确认请求', label_re=r"^Confirm .+'s friend request$"),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'sort': _t('排序', label_re=r'^Sort$', risk=RISK_NONE,
                   note='请求列表排序；点开是排序选项，未点'),
        'request_profile': _t('请求者主页', label_re=r"^View .+'s profile$",
                              risk=RISK_NONE, repeated=True),
        'confirm_request': _t('确认好友请求',
                              label_re=r"^Confirm .+'s friend request$",
                              risk=RISK_SEND, repeated=True,
                              note='会建立好友关系，未点击'),
        'delete_request': _t('删除好友请求',
                             label_re=r"^Delete .+'s friend request$",
                             risk=RISK_SEND, repeated=True,
                             note='会移除他人请求，未点击'),
    },
    coverage=COVERED,
    entry='好友页 `Friend requests` 区的 `See all`',
    note='**三页长得像**：好友页（有 `Friends, Heading` + 分段按钮）、'
         '本页（有 `Friend requests` + `Sort`，无分段按钮）、'
         '「你可能认识的朋友」全量页（`People you may know` + 联络人上传提示）。'
         '本页**多出来的 `Sort` 是它的关键区分点**。'
         '只采集结构，没有确认/删除任何请求、没有改排序。',
)



# ---------------------------------------------------------------------------
# 4d. 好友请求排序面板（2026-11-02 02:45 实测）
# ---------------------------------------------------------------------------
REQUEST_SORT_PANEL = Page(
    key='request_sort_panel', name='好友请求排序',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('最新优先', text_re=r'^Newest request first$'),
        _t('最旧优先', text_re=r'^Oldest request first$'),
    ),
    targets={
        'default': _t('默认排序', text_re=r'^Default$', risk=RISK_NONE),
        'newest': _t('最新优先', text_re=r'^Newest request first$', risk=RISK_NONE),
        'oldest': _t('最旧优先', text_re=r'^Oldest request first$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='好友请求全量页 → `Sort`',
    note='底部弹层，三个 ViewGroup 选项（不是 RadioButton）。'
         '改排序只影响本地视图顺序，不改账号状态，所以都标 none；'
         '本次只打开看过，**没有改选**。',
)



# ---------------------------------------------------------------------------
# 5. Reels
# ---------------------------------------------------------------------------
REELS = Page(
    key='reels', name='Reels 短视频',
    activity_hint='',
    identity=(
        _t('底栏Reels选中', label_re=r'^Reels, tab 3 of 4'),
        _t('Reels 标题', text_re=r'^Reels$'),
    ),
    targets={
        'title': _t('Reels 标题', text_re=r'^Reels$', risk=RISK_NONE),
        'menu': _t('菜单', label_re=r'^Facebook menu$', risk=RISK_NONE),
        'create_reel': _t('创建 Reel', label_re=r'^Create (a )?reel', risk=RISK_SEND,
                          note='对外发布，未点击'),
        'search': _t('搜索', label_re=r'^Search$', risk=RISK_NONE),
        'reels_profile': _t('我的 Reels 主页', label_re=r'^Navigate to your Reels profile$',
                            risk=RISK_NONE),
        'video_card': _t('视频卡', label_re=r'^Video tab details$', risk=RISK_NONE,
                         note='整屏可点区域；卡片本身 label 是标题（动态）'),
        'follow': _t('关注作者', label_re=r'^Follow .+$', risk=RISK_SEND,
                     note='形如「Follow 5-Minute Crafts」'),
        'reactions': _t('赞/表情', label_re=r'^\d+(\.\d+)?[KM]? reactions$', risk=RISK_SEND,
                        repeated=True,
                        note='数量动态；实测同一个计数暴露两层按钮，不能当固定锚点'),
        'comments': _t('评论', text_re=r'^\d+\s*(comments|則留言|條留言|条留言|則評論|条评论)$', risk=RISK_NONE,
                       note='数量动态'),
        'share': _t('分享', label_re=r'^(Share|分享), ', risk=RISK_SEND),
        'save': _t('保存', text_re=r'^Save$', risk=RISK_SEND),
        'more': _t('更多', label_re=r'^More$', risk=RISK_NONE),
        'seekbar': _t('播放进度', type_re=r'^SeekBar$', risk=RISK_NONE,
                      note='SeekBar，label 是资源号'),
        'author_profile': _t('作者头像', label_re=r"^Author's profile$", risk=RISK_NONE),
        # 2026-11-01 23:31：点视频卡后进入同页沉浸播放，浮出播放控件
        'rewind': _t('后退 10 秒', label_re=r'^Rewind 10 seconds$', risk=RISK_NONE),
        'play': _t('播放/暂停', label_re=r'^(Play|Pause)$', risk=RISK_NONE,
                   note='实测点视频后出现，形如 Play；播放中会变 Pause'),
        'forward': _t('前进 10 秒', label_re=r'^Fast forward 10 seconds$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='底栏「Reels, tab 3 of 4」',
    note='只有一条视频卡被采集；下滑换视频的逻辑与更多控制未覆盖。'
         '进度条与作者名都是动态值。',
)


# ---------------------------------------------------------------------------
# 5b. 分享面板（2026-11-01 23:34 实测，Reels 的 Share）
# ---------------------------------------------------------------------------
SHARE_PANEL = Page(
    key='share_panel', name='分享面板',
    activity_hint='FbMainTabActivity',
    identity=(
        _t('立即分享', label_re=r'^Share now$'),
        _t('分享到', text_re=r'^Share to$'),
        _t('选择目标', label_re=r'^Select the destination to share\.$'),
    ),
    targets={
        'close': _t('关闭', label_re=r'^Close$', risk=RISK_NONE,
                    note='本次用它关闭面板，未分享'),
        'notice': _t('分享提示', text_re=r'^你分享的連結為你專屬', risk=RISK_NONE,
                     note='原文：你分享的連結為你專屬，且可能會用於改善你看到的建議和廣告'),
        'learn_more': _t('了解详情', text_re=r'^瞭解詳情$', risk=RISK_NONE),
        'destination_hint': _t('目标提示',
                               label_re=r'^Select the destination to share\.$',
                               risk=RISK_NONE),
        'audience': _t('可见范围', label_re=r'^(所有人|朋友|只限本人)$', risk=RISK_SEND,
                       note='分享可见范围；改动会改变对外状态'),
        'feeling': _t('感受/活动', label_re=r'^Open feeling/activity screen$',
                      risk=RISK_NONE),
        'tag_friends': _t('标注好友', label_re=r'^Tag friends$', risk=RISK_NONE),
        'share_now': _t('立即分享（最终确认）', label_re=r'^Share now$',
                        risk=RISK_FORBIDDEN,
                        note='**最终确认键，永不自动点击**。它紧挨着一排目标按钮，'
                             '误点即对外发布且不可撤销，因此按 forbidden 处理而不是 send'),
        'share_to': _t('分享到（分组）', text_re=r'^Share to$', risk=RISK_NONE),
        'your_story': _t('分享到限时动态', label_re=r'^Your story$', risk=RISK_SEND,
                         note='会对外发布，未点击'),
        'messenger': _t('分享到 Messenger', label_re=r'^Messenger$', risk=RISK_SEND,
                        note='未装 Messenger，点了也只会进安装引导'),
        'copy_link': _t('复制链接', label_re=r'^Copy link$', risk=RISK_NONE,
                        note='只写系统剪贴板，不改账号状态；本次未点'),
        'group': _t('分享到社团', label_re=r'^Group$', risk=RISK_SEND),
        'text': _t('分享到短信', label_re=r'^Text$', risk=RISK_SEND),
        'more': _t('更多分享目标', label_re=r'^More options for Share to$',
                   risk=RISK_NONE),
    },
    coverage=VERIFIED,
    entry='Reels/帖子详情点「Share, N shares」',
    note='**这是底部弹层**，占屏幕下半。为了采集结构，本轮显式授权"只打开、不确认"，'
         'dump 完立刻用 Close 关闭，**没有点 Share now、没有选任何目标**。'
         '自动化若需要它，必须遵守：打开 → 采集/校验 → 关闭，全程不碰 Share now。',
)



# ---------------------------------------------------------------------------
# 5c. 限时动态编辑器（2026-11-02 00:31 实测，从分享面板 Your story 进入）
# ---------------------------------------------------------------------------
STORY_EDITOR = Page(
    key='story_editor', name='限时动态编辑器',
    activity_hint='InspirationCameraActivity',
    identity=(
        _t('设置菜单', label_re=r'^Story Settings Menu$'),
        _t('标注人物', label_re=r'^Tap to mention people$'),
        _t('贴纸', label_re=r'^Sticker\.$'),
    ),
    targets={
        'music': _t('音乐', label_re=r'^Music$', risk=RISK_NONE),
        'effects': _t('特效', label_re=r'^Effects$', risk=RISK_NONE),
        'mention': _t('标注人物', label_re=r'^Tap to mention people$', risk=RISK_NONE),
        'sticker': _t('贴纸', label_re=r'^Sticker\.$', risk=RISK_NONE),
        'settings': _t('故事设置菜单', label_re=r'^Story Settings Menu$', risk=RISK_NONE,
                       note='可见范围、保存等设置；未点开'),
        'share': _t('发布限时动态', text_re=r'^(Share|分享)$', risk=RISK_FORBIDDEN,
                    note='**最终发布键**。这里必须比普通 Share 更严：'
                         '它是发布动作而不是打开面板，所以按 forbidden 处理，'
                         '再叠加了「分享目标」那些 send 级项，越权风险太高'),
    },
    coverage=COVERED,
    entry='帖子/Reels 分享面板 → `Your story`',
    note='Activity `com.facebook.inspiration.activity.InspirationCameraActivity`。'
         '**打开后立刻退出，没有编辑、没有发布**；退出时弹出「Discard photo?」确认框，'
         '选放弃后回到原帖。',
)


# ---------------------------------------------------------------------------
# 5d. 限时动态放弃确认框（2026-11-02 00:32 实测）
# ---------------------------------------------------------------------------
STORY_DISCARD = Page(
    key='story_discard', name='放弃限时动态确认',
    activity_hint='InspirationCameraActivity',
    identity=(
        _t('标题', text_re=r'^Discard photo\?'),
        _t('放弃按钮', label_re=r'^Discard photo$'),
    ),
    exclusive=(
        _t('标题', text_re=r'^Discard photo\?'),
        _t('放弃按钮', label_re=r'^Discard photo$'),
    ),
    targets={
        'title': _t('标题', text_re=r'^Discard photo\?', risk=RISK_NONE,
                    note='原文：Discard photo? You\'ll lose this photo and any changes'),
        'discard': _t('放弃', label_re=r'^Discard photo$', risk=RISK_NONE,
                      note='本次选择的项：丢弃并退出，未发布'),
    },
    coverage=VERIFIED,
    entry='限时动态编辑器里按返回键',
    note='**覆盖层**，编辑器元素仍在层级里，用 exclusive 互斥锚点判定。'
         '只采到「放弃」一个出口，未见到"继续编辑/保存草稿"之类的选项。',
)



# ---------------------------------------------------------------------------
# 6. 通知列表
# ---------------------------------------------------------------------------
NOTIFICATIONS = Page(
    key='notifications', name='通知列表',
    activity_hint='',
    identity=(
        _t('标题', label_re=r'^(Notifications, Heading|通知, Heading)$'),
        _t('底栏通知选中',
           label_re=r'^(Notifications, tab \d+ of \d+|通知[，,])'),
        # 2026-11-02 08:42 实测：新版分组是 `今天` / `先前的通知`，
        # 条目时间写成 `10小時`、已读是 `已讀`（旧版 `9h` / `Read`）。
        _t('日期分组', text_re=r'^(今天|新通知)$'),
    ),
    targets={
        'title': _t('标题', label_re=r'^Notifications, Heading$', risk=RISK_NONE),
        'menu': _t('菜单', label_re=r'^Facebook menu$', risk=RISK_NONE),
        'search': _t('搜索', label_re=r'^Search$', risk=RISK_NONE),
        'new_group': _t('新通知分组', text_re=r'^新通知$', risk=RISK_NONE),
        # 05:02 实测新增的两个分组（随时间的推移出现）
        'today_group': _t('分组-今天', text_re=r'^今天$', risk=RISK_NONE),
        'earlier_group': _t('分组-先前的通知', text_re=r'^先前的通知$', risk=RISK_NONE),
        'item': _t('通知条目', label_re=r'\.\s*\d+[hdwm]\s*\.\s*(Unread)?$', risk=RISK_NONE,
                   repeated=True,
                   note='label 形如「<正文>. 9h. Unread」；正文是账号相关内容，不能当锚点'),
        'item_unread': _t('未读通知', label_re=r'\.\s*Unread$', risk=RISK_NONE,
                          repeated=True,
                          note='未读会有多条（05:09 实测同时 2 条），按后缀识别'),
    },
    coverage=VERIFIED,
    entry='底栏「Notifications, tab 4 of 4」',
    note='进入后底栏角标「, 2 new」消失——这是本地已读状态，不是账号对外变化。'
         '**分组会随时间推移出现**（05:02 实测）：'
         '一开始只有 `新通知` 一组；过了几小时后变成 `新通知` / `今天` / `先前的通知` 三组。'
         '所以"只有一组"是当时的快照，**不要写死**。'
         '条目 label 统一是`<正文>. <时间>. <Read|Unread>`，正文与时间都是账号相关内容，'
         '不可当锚点；用后缀 `. <时间>. Unread` 识别条目，用 `Unread` 区分已读。'
         '分页加载（下滑到底自动加载更早）**仍未观察到**。',
)


# ---------------------------------------------------------------------------
# 7. 通知详情（WebView）
# ---------------------------------------------------------------------------
NOTIFICATION_DETAIL = Page(
    key='notification_detail', name='通知详情',
    activity_hint='',
    identity=(
        _t('返回', label_re=r'^Back$'),
        _t('网页容器', type_re=r'^WebView$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'webview': _t('网页容器', type_re=r'^WebView$', risk=RISK_NONE,
                      note='类型 WebView；内部控件不进原生层级，无法按 label 定位'),
    },
    coverage=PARTIAL,
    entry='通知列表点任一条目',
    note='**落点取决于通知类型**（2026-11-02 00:29 实测）：'
         '社团类通知（如「歡迎加入 Marketplace」）直接进**原生**社团详情页，'
         '可正常读控件；而账号安全类通知（如「登入異常」）进内嵌 WebView，'
         '原生层级只剩一个 WebView 节点。所以不能一概说"通知详情不可采"。',
)


# ---------------------------------------------------------------------------
# 8. 发帖编辑器
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 6b. Meta 账号说明页（2026-11-02 05:07 实测）
# ---------------------------------------------------------------------------
META_ACCOUNT_INFO = Page(
    key='meta_account_info', name='Meta 账号说明',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('标题', text_re=r'^關於 Meta 帳號$'),
        _t('问答1', text_re=r'^Meta 帳號是什麼？$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'title': _t('标题', text_re=r'^關於 Meta 帳號$', risk=RISK_NONE),
        'subtitle': _t('副标题', text_re=r'^輕鬆存取和控制你選擇的所有應用程式和裝置$',
                       risk=RISK_NONE),
        'qa': _t('问答项', text_re=r'^Meta 帳號是什麼？$', risk=RISK_NONE),
        'body': _t('说明正文', text_re=r'^這是可以在 Facebook、Instagram 或 AI 眼鏡',
                   risk=RISK_NONE, note='长文本，内容随版本变'),
        'learn_more': _t('了解更多', text_re=r'^看看你可以在哪些地方使用 Meta 帳號$',
                         risk=RISK_NONE, note='会跳外链/更多说明，未点'),
    },
    coverage=PARTIAL,
    entry='通知里「Meta 帳號即將推出」类通知',
    note='**修正之前"账号安全类通知进 WebView 不可读"的结论**（00:29 记的）：'
         '这次同类通知落点是**可读的原生说明页**（有标题/副标题/问答/正文/了解更多按钮）。'
         '所以"某类通知一定不可读"也站不住脚，**落点要按实际类型再看**。'
         '只读了结构，未点「了解更多」。',
)


CREATE_POST = Page(
    key='create_post', name='发帖编辑器',
    activity_hint='ComposerActivity',
    identity=(
        _t('标题', text_re=r'^Create post$'),
        _t('发布键', text_re=r'^POST$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'title': _t('标题', text_re=r'^Create post$', risk=RISK_NONE),
        'post_btn': _t('发布', text_re=r'^POST$', risk=RISK_SEND,
                       note='正文为空时 disabled——这是"是否已填写"的天然判据；'
                            '发布前必须主人确认'),
        'audience': _t('可见范围', label_re=r'^(所有人|公開|朋友|只限本人)$', risk=RISK_SEND,
                       note='改可见范围会改变对外状态'),
        'ai_label': _t('AI 标签', label_re=r'^AI label', risk=RISK_NONE,
                       note='形如「AI label off. Collapsed」'),
        'add_album': _t('添加相册', text_re=r'^Add album$', risk=RISK_NONE),
        'share_instagram': _t('同步到 Instagram', label_re=r'^Share to Instagram\.',
                              risk=RISK_SEND, note='形如「Share to Instagram. Off.」'),
        'body_input': _t('正文输入', type_re=r'^AutoCompleteTextView$', risk=RISK_NONE,
                         note='AutoCompleteTextView，无 label；按类型+位置定位'),
        'add_photo': _t('相片／影片', text_re=r'^相片／影片$', risk=RISK_NONE),
        'tag_people': _t('标注用户', text_re=r'^標註用戶$', risk=RISK_NONE),
        'feeling': _t('感受／活动', text_re=r'^感受／活動$', risk=RISK_NONE),
        'checkin': _t('打卡', text_re=r'^打卡$', risk=RISK_NONE),
        'live': _t('直播视讯', text_re=r'^直播視訊$', risk=RISK_SEND),
        'bg_color': _t('背景色彩', text_re=r'^背景色彩$', risk=RISK_NONE),
        'bg_hide_all': _t('收起背景选项', label_re=r'^Hide all background options$',
                          risk=RISK_NONE, note='输入正文后才出现'),
        'bg_none': _t('无背景', label_re=r'^No background$', risk=RISK_NONE,
                      note='默认选中'),
        'bg_option': _t('背景模板', label_re=r'^(?!No background$).*(，背景|background)$',
                        risk=RISK_NONE, repeated=True,
                        note='纯色 / 渐层 / AI 生成背景；label 常被截断。'
                             '用负向前瞻把「No background」排除在外'),
        'bg_show_all': _t('展开全部背景', label_re=r'^Show all background options$',
                          risk=RISK_NONE),
        'add_more': _t('添加更多', label_re=r'^Add more to your post$', risk=RISK_NONE,
                       note='输入正文后出现的建议入口'),
    },
    coverage=VERIFIED,
    entry='首页「Make a post on Facebook」',
    note='Activity `com.facebook.composer.activity.ComposerActivity`。'
         '**只打开、只输入、未发布**，退出时选了 Discard post，未留草稿。'
         '23:13 实测：正文为空时 `POST` 是 disabled，输入任意文字后立刻变可用——'
         '这是判断"是否已填写"的天然安全闸门。'
         '相册、标注、打卡、直播、背景模板的子流程均未进入。',
)


# ---------------------------------------------------------------------------
# 8b. 放弃草稿对话框（2026-11-01 23:13 实测）
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 8b. 新版发帖编辑器（2026-11-03 17:30 实测）
# ---------------------------------------------------------------------------
COMPOSER_V2 = Page(
    key='composer_v2', name='新版发帖编辑器',
    activity_hint='composer.activity.ComposerActivity',
    identity=(
        _t('关闭', label_re=r'^關閉$'),
        _t('下一步', label_re=r'^下一步$'),
    ),
    exclusive=(
        _t('关闭', label_re=r'^關閉$'),
        _t('下一步', label_re=r'^下一步$'),
    ),
    targets={
        'close': _t('关闭', label_re=r'^關閉$', risk=RISK_NONE),
        'next': _t('下一步', label_re=r'^下一步$', risk=RISK_NONE,
                   note='进入预览/设置页；**不是发布键**，发布在下一页'),
        'gallery': _t('图库', label_re=r'^圖庫$', risk=RISK_NONE),
        'gif': _t('GIF', label_re=r'^GIF$', risk=RISK_NONE),
        'live': _t('直播', label_re=r'^直播$', risk=RISK_SEND,
                   note='会开直播，未点'),
        'status': _t('生活近况', label_re=r'^生活近況$', risk=RISK_NONE),
        'mention': _t('提及用户', label_re=r'^用戶。1／3。', risk=RISK_NONE),
        'location': _t('加地点', label_re=r'^地點。2／3。', risk=RISK_NONE),
        'feeling': _t('感受或活动', label_re=r'^感受或活動。第3項', risk=RISK_NONE),
    },
    coverage=VERIFIED,
    entry='首页「在想些什麼？」',
    note='**这是新版流程，和旧版 create_post 不一样**：旧版编辑器里直接有 POST，'
         '新版是「编辑 → 下一步 → 预览页发佈」。'
         '文案也换成了「在想些什麼？」（不再是「在 Facebook 撰寫貼文」）。'
         '**没有输入、也没有点下一步以外的任何键**；退出时选了「捨棄貼文」。',
)


COMPOSER_PREVIEW = Page(
    key='composer_preview', name='发帖预览/设置页',
    activity_hint='composer.preview.activity.ComposerPreviewActivity',
    identity=(
        _t('发布键', label_re=r'^發佈$'),
        _t('分享对象', label_re=r'^貼文分享對象'),
    ),
    exclusive=(
        _t('发布键', label_re=r'^發佈$'),
        _t('分享对象', label_re=r'^貼文分享對象'),
    ),
    targets={
        'publish': _t('发布', label_re=r'^發佈$', risk=RISK_SEND,
                      note='**真正的发布键**。点下去就对外可见，只识别不点击'),
        'audience': _t('分享对象', label_re=r'^貼文分享對象', risk=RISK_NONE,
                       note='显示当前范围，如「朋友」「所有人」'),
        'crosspost': _t('同时分享到', label_re=r'^同時分享到', risk=RISK_SEND,
                        note='会同步到其他平台，未点'),
        'back': _t('返回', label_re=r'^返回$', risk=RISK_NONE),
    },
    coverage=VERIFIED,
    entry='发帖编辑器 →「下一步」',
    note='**发佈不可撤销**：点下去帖子就对分享对象可见。'
         '本页还带隐私范围（貼文分享對象）和跨平台同步开关。'
         '**只打开看过，没有点發佈、没有改隐私、没有开同步**。',
)


DRAFT_DIALOG = Page(
    key='draft_dialog', name='放弃草稿对话框',
    activity_hint='ComposerActivity',
    identity=(
        _t('标题', text_re=r'^Want to finish your post later\?$'),
        _t('放弃', label_re=r'^Discard post'),
    ),
    exclusive=(
        _t('标题', text_re=r'^Want to finish your post later\?$'),
        _t('放弃', label_re=r'^Discard post'),
    ),
    targets={
        'title': _t('标题', text_re=r'^Want to finish your post later\?$', risk=RISK_NONE),
        'save_draft': _t('存为草稿',
                         label_re=r"^Save as draft, You'll receive a notification",
                         risk=RISK_SEND,
                         note='会留下草稿并产生一条通知；本次没选'),
        'discard': _t('放弃', label_re=r'^Discard post', risk=RISK_NONE,
                      note='本次选择的项：丢弃正文并退出，不留草稿'),
        'continue_editing': _t('继续编辑', label_re=r'^Continue editing', risk=RISK_NONE),
    },
    coverage=VERIFIED,
    entry='发帖编辑器里已有正文时按系统返回键',
    note='**覆盖层**：对话框盖在 ComposerActivity 上，编辑器元素仍在层级里，'
         '所以用 exclusive 互斥锚点判定（标题 + Discard post 同时命中）。'
         '自动化发帖时如果中途退出，必须显式处理这个对话框，否则会卡住。',
)


# ---------------------------------------------------------------------------
# 9. 动态详情 + 评论区
# ---------------------------------------------------------------------------
POST_DETAIL = Page(
    key='post_detail', name='动态详情／评论区',
    activity_hint='',
    identity=(
        _t('关闭', label_re=r'^Close$'),
        _t('评论输入框', label_re=r'^Write a comment'),
    ),
    targets={
        'close': _t('关闭', label_re=r'^Close$', risk=RISK_NONE),
        'reactions': _t('表情计数', text_re=r'^\d+$', risk=RISK_SEND, repeated=True,
                        note='纯数字，页面上可能不止一处；点开是表情面板，选择会改变对外状态'),
        'shares': _t('分享数', text_re=r'^\d+\s*(shares|次分享|則分享)$', risk=RISK_NONE),
        'comment_input': _t('写评论', label_re=r'^Write a comment', risk=RISK_NONE,
                            note='AutoCompleteTextView；输入后发送属于 send'),
        'like_comment': _t('赞某条评论', label_re=r"^Like .+'s comment button",
                           risk=RISK_SEND, repeated=True,
                           note='每条评论都有，必须按容器定位到具体那条'),
        'reply_comment': _t('回复某条评论', label_re=r"^Reply to .+'s comment",
                            risk=RISK_SEND, repeated=True,
                            note='每条评论都有；回复会对外发出，未点击'),
        'comment_list': _t('评论列表', type_re=r'^RecyclerView$', risk=RISK_NONE,
                           note='RecyclerView；单条评论无独立 ID，靠 label 前缀与可视区域关联。'
                                '**评论作者名没有稳定特征，不要试图给作者建 target**——'
                                '早期用 ^[^,]+$ 兜底，一次命中 8 个元素，属于危险正则'),
        'view_replies': _t('展开更多回复', label_re=r'^View \d+ more replies to ',
                           risk=RISK_NONE, repeated=True,
                           note='形如「View 41 more replies to 刘刚…」；纯浏览，可安全展开'),
        'comment_reactions': _t('评论表情数', label_re=r'^\d+ reactions$',
                                risk=RISK_SEND, repeated=True,
                                note='点开会弹表情面板；选择会改变对外状态'),
        'comment_preview': _t('回复预览', risk=RISK_NONE,
                              note='形如「<名字>, <回复内容>」；内容动态'),
        'show_media': _t('照片与视频', label_re=r'^Show photos and videos$', risk=RISK_NONE),
        'gif': _t('GIF 搜索', label_re=r'^Open GIF search$', risk=RISK_NONE),
        'stickers': _t('贴纸', label_re=r'^Show stickers$', risk=RISK_NONE),
        'send': _t('发送评论', text_re=r'^Send$', risk=RISK_SEND,
                   note='**未输入时 disabled**——与发帖的 POST 一样，是发送前的安全闸门；'
                        '从未点击'),
    },
    coverage=COVERED,
    entry='首页动态点「N comments」',
    note='已采集：作者、正文、时间、计数、Like/Reply 按钮、底部输入框。'
         '**没有点赞、没有回复、没有发送任何评论。**',
)


# ---------------------------------------------------------------------------
# 10. 个人主页
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 8c. 单条动态详情页（2026-11-01 23:19 实测，视频帖）
# ---------------------------------------------------------------------------
POST_FULL = Page(
    key='post_full', name='单条动态详情页',
    activity_hint='',
    identity=(
        _t('静音开关', label_re=r'^Mute video: '),
        _t('静音提示', text_re=r'^Tap to mute$'),
        _t('观看数', text_re=r'^\d+(\.\d+)?[KM]? views$'),
        # 2026-11-02 01:01 实测：静音控件会随播放状态隐藏，只剩观看数一个锚点就判不出。
        # 视频容器和互动栏更稳定，补进来。
        _t('视频容器', label_re=r'^Video$'),
        _t('互动栏评论', text_re=r'^(Comment|留言|評論|评论)$'),
    ),
    targets={
        'video': _t('视频容器', label_re=r'^Video$', risk=RISK_NONE, repeated=True,
                    note='实测同一视频会暴露两个 FrameLayout「Video」，属结构性重复'),
        'mute': _t('静音开关', label_re=r'^Mute video: ', risk=RISK_NONE,
                   note='形如「Mute video: Off」'),
        'sound_toggle': _t('声音开关', label_re=r'^Toggle video sound$', risk=RISK_NONE),
        'reactions': _t('表情计数', text_re=r'^\d+(\.\d+)?[KM]?$', risk=RISK_SEND,
                        repeated=True,
                        note='点开表情面板；选择会改变对外状态'),
        'comments': _t('评论数', text_re=r'^\d+\s*(comments|則留言|條留言|条留言|則評論|条评论)$', risk=RISK_NONE),
        'shares': _t('分享数', text_re=r'^\d+\s*(shares|次分享|則分享)$', risk=RISK_NONE),
        'views': _t('观看数', text_re=r'^\d+(\.\d+)?[KM]? views$', risk=RISK_NONE,
                    note='详情页比信息流多出来的指标'),
        'like': _t('赞', label_re=r'(^Like button\. Double tap|「讚」按鈕\.|「赞」按钮\.)', risk=RISK_SEND),
        'comment': _t('评论入口', text_re=r'^(Comment|留言|評論|评论)$', risk=RISK_NONE),
        'share': _t('分享', text_re=r'^(Share|分享)$', risk=RISK_SEND),
        'author_story': _t('作者限时动态', label_re=r"^Open .+'s story", risk=RISK_NONE),
        'post_time': _t('发布时间', text_re=r'•(Shared with|分享對象)[:：] ', risk=RISK_NONE),
        'see_more': _t('更多操作', label_re=r'^See more options$', risk=RISK_NONE),
        'hide_post': _t('隐藏帖子', label_re=r'^Hide post$', risk=RISK_NONE),
        'post_text': _t('正文', risk=RISK_NONE, note='内容动态；「… more」可展开'),
        'more': _t('展开正文', text_re=r'^more$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='信息流里点开某条动态（本次是滑动手势进入的视频帖）',
    note='**与「动态详情／评论区」（post_detail）不是同一页**：'
         '那一页是评论浮层，有 Close 和 Write a comment；'
         '这一页是整屏动态，顶栏被 Like/Comment/Share 取代，多了观看数、'
         '静音开关。只采集了视频帖，图文帖的详情结构未验证。',
)


# ---------------------------------------------------------------------------
# 8d. 贴纸面板（2026-11-01 23:40 实测，从评论区 Show stickers 打开）
# ---------------------------------------------------------------------------
STICKER_PANEL = Page(
    key='sticker_panel', name='贴纸面板',
    activity_hint='FbMainTabActivity',
    identity=(
        _t('贴纸搜索', label_re=r'^Sticker search button$'),
        _t('贴纸商店', label_re=r'^Sticker store$'),
        _t('贴纸页签', text_re=r'^(Happy|In love|Sad|Eating)$'),
    ),
    targets={
        'search': _t('贴纸搜索', label_re=r'^Sticker search button$', risk=RISK_NONE),
        'store': _t('贴纸商店', label_re=r'^Sticker store$', risk=RISK_NONE),
        'category': _t('贴纸分类',
                       text_re=r'^(喜怒哀樂|94愛尷尬|哥倆好|一起來聊聊|美好日子|工作心情)$',
                       risk=RISK_NONE, repeated=True,
                       note='实测 6 个分类横向排列；名称随语言/地区变化，'
                            '不要把具体分类名当固定锚点'),
        'tab': _t('贴纸页签', text_re=r'^(Search|Happy|In love|Sad|Eating)$',
                  risk=RISK_NONE, repeated=True),
        'item': _t('贴纸', risk=RISK_NONE, repeated=True,
                   note='label 是贴纸描述，如「Thumbs Up, girl giving thumbs up」；'
                        '点击只把贴纸放进评论框、不会发送，但仍需清空后再退出，'
                        '所以调用方要负责收尾'),
    },
    coverage=COVERED,
    entry='评论区底部 `Show stickers`',
    note='**整屏覆盖层**：打开后评论区的 Close / Write a comment 全部从层级消失，'
         '只剩贴纸面板，所以必须独立成页，不能挂在 post_detail 下。'
         '本次只打开查看，没有点任何贴纸。',
)


# ---------------------------------------------------------------------------
# 8e. GIF 面板（2026-11-01 23:44 实测，从评论区 Open GIF search 打开）
# ---------------------------------------------------------------------------
GIF_PANEL = Page(
    key='gif_panel', name='GIF 面板',
    activity_hint='FbMainTabActivity',
    identity=(
        _t('GIF 搜索框', label_re=r'^Enter search terms to search for GIFs$'),
        _t('GIF 结果', label_re=r'GIF( by .+)?$'),
    ),
    targets={
        'input': _t('GIF 搜索框',
                    label_re=r'^Enter search terms to search for GIFs$',
                    risk=RISK_NONE,
                    note='EditText；placeholder 就是它的 label'),
        'search_icon': _t('搜索图标', type_re=r'^ImageButton$', risk=RISK_NONE,
                          note='无 label，只能按类型定位；同层可能还有拖动手柄'),
        'item': _t('GIF 结果', label_re=r'GIF( by .+)?$', risk=RISK_NONE, repeated=True,
                   note='label 形如「Love You Reaction GIF by Bounce」或「Dance Dancing GIF」；'
                        '点它会插入评论框、不发送，但仍需清空后再退出'),
    },
    coverage=COVERED,
    entry='评论区底部 `Open GIF search`',
    note='**底部覆盖层**：打开后评论区的 Close / Write a comment 全部消失，'
         '所以和贴纸面板一样必须独立成页。面板本身没有可见的关闭按钮，'
         '退出靠系统返回键。本次只打开查看、未点任何 GIF。',
)


# ---------------------------------------------------------------------------
# 8f. 图文帖详情页（2026-11-02 00:19 实测）
# ---------------------------------------------------------------------------
POST_PHOTO = Page(
    key='post_photo', name='图文帖详情页',
    activity_hint='',
    identity=(
        _t('图片', label_re=r'^圖[像片]$'),
        _t('评论数', text_re=r'^\d+\s*(comments|則留言|條留言|条留言|則評論|条评论)$'),
        _t('链接卡片', text_re=r'^(Shared Link|分享連結|分享链接)[:：] '),
    ),
    targets={
        'image': _t('图片', label_re=r'^圖[像片]$', risk=RISK_NONE),
        'poll_question': _t('互动投票问题', risk=RISK_NONE,
                            note='形如「你是否已經有相關產品？」——帖子可以挂互动投票单元，'
                                 '问题文案是动态内容'),
        'poll_option': _t('投票选项', text_re=r'^(有|籌備中|沒有|是|否)$',
                          risk=RISK_SEND, repeated=True,
                          note='**投票会记录在账号上**，属对外动作，未点击'),
        'link_card': _t('链接卡片', text_re=r'^(Shared Link|分享連結|分享链接)[:：] ', risk=RISK_NONE,
                        note='形如「Shared Link: 表單, SHOPLINE - 全方位開網店平台」'),
        'link_title': _t('链接标题', risk=RISK_NONE, note='内容动态，不设正则'),
        'learn_more': _t('了解详情', text_re=r'^瞭解詳情$', risk=RISK_NONE),
        'reactions': _t('表情计数', text_re=r'^\d+$', risk=RISK_SEND, repeated=True),
        'comments': _t('评论数', text_re=r'^\d+\s*(comments|則留言|條留言|条留言|則評論|条评论)$', risk=RISK_NONE),
        'shares': _t('分享数', text_re=r'^\d+\s*(shares|次分享|則分享)$', risk=RISK_NONE),
        'like': _t('赞', label_re=r'(^Like button\. Double tap|「讚」按鈕\.|「赞」按钮\.)', risk=RISK_SEND),
        'comment': _t('评论入口', text_re=r'^(Comment|留言|評論|评论)$', risk=RISK_NONE),
        'share': _t('分享', text_re=r'^(Share|分享)$', risk=RISK_SEND),
        'author_avatar': _t('作者头像', label_re=r' Profile picture$', risk=RISK_NONE),
        'post_time': _t('发布时间', text_re=r'•(Shared with|分享對象)[:：] ', risk=RISK_NONE),
    },
    coverage=PARTIAL,
    entry='信息流里点开图文帖子',
    note='**和视频帖详情（post_full）不同**：没有 Mute video / views / Video 容器，'
         '判定必须靠「圖像 + N comments + Like button」组合。'
         '本次样本还带回一个**互动投票单元**（`你是否已經有相關產品？` + 有/籌備中/沒有），'
         '这是帖子内嵌组件，不是页面级控件。未投票、未点赞。',
)


# ---------------------------------------------------------------------------
# 8h. 社团发帖框（2026-11-02 00:36 实测）
# ---------------------------------------------------------------------------
GROUP_COMPOSER = Page(
    key='group_composer', name='社团发帖框',
    activity_hint='ComposerActivity',
    identity=(
        _t('可见范围-社团', label_re=r'^(公開社團|私密社團)$'),
        _t('发布键', text_re=r'^POST$'),
        _t('贴文标题', text_re=r'^貼文標題$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'post_btn': _t('发布（社团帖）', text_re=r'^POST$', risk=RISK_SEND,
                       note='**空正文时 disabled**；与个人发帖同一套闸门'),
        'audience': _t('可见范围', label_re=r'^(公開社團|私密社團)$', risk=RISK_SEND,
                       note='社团帖的可见范围是社团属性（公開社團/私密社團），'
                            '和个人发帖的「所有人」不是一回事'),
        'ai_label': _t('AI 标签', label_re=r'^AI label', risk=RISK_NONE),
        'photo_video': _t('相片／影片', text_re=r'^相片／影片$', risk=RISK_NONE),
        'tag_people': _t('标注用户', text_re=r'^標註用戶$', risk=RISK_NONE),
        'camera': _t('相机', text_re=r'^相機$', risk=RISK_NONE,
                     note='个人发帖里没有这个入口'),
        'title_field': _t('贴文标题', text_re=r'^貼文標題$', risk=RISK_NONE,
                          note='社团帖特有的标题字段入口'),
        'checkin': _t('打卡', text_re=r'^打卡$', risk=RISK_NONE),
        'feeling': _t('感受／活动', text_re=r'^感受／活動$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='社团详情页「留個言吧……」',
    note='**与个人发帖框（create_post）不同**：标题位置显示的是**社团名**而不是 '
         '`Create post`；可见范围是 `公開社團` 而不是 `所有人`；'
         '多了 `相機` 和 `貼文標題`，少了 `Add album`/`Share to Instagram`/`背景色彩`。'
         '两者 Activity 相同（ComposerActivity），只能靠这些差异区分。'
         '本轮只打开看结构、未输入、未发布。',
)



# ---------------------------------------------------------------------------
# 8i. 长按表情托盘（2026-11-02 01:01 实测）
# ---------------------------------------------------------------------------
REACTION_TRAY = Page(
    key='reaction_tray', name='长按表情托盘',
    activity_hint='FbMainTabActivity',
    identity=(
        _t('托盘视图', type_re=r'^RopeStyleUFIDockView$'),
        _t('取消', label_re=r'^Cancel$'),
    ),
    # 只有托盘会出现这个自定义 View 类型，命中即判定。
    exclusive=(
        _t('托盘视图', type_re=r'^RopeStyleUFIDockView$'),
    ),
    targets={
        'like': _t('赞', label_re=r'^Like$', risk=RISK_SEND, repeated=True),
        'love': _t('爱心', label_re=r'^Love$', risk=RISK_SEND, repeated=True),
        'care': _t('抱抱', label_re=r'^Care$', risk=RISK_SEND, repeated=True),
        'haha': _t('哈哈', label_re=r'^Haha$', risk=RISK_SEND, repeated=True),
        'wow': _t('哇', label_re=r'^Wow$', risk=RISK_SEND, repeated=True),
        'sad': _t('难过', label_re=r'^Sad$', risk=RISK_SEND, repeated=True),
        'angry': _t('生气', label_re=r'^Angry$', risk=RISK_SEND, repeated=True),
        'cancel': _t('取消', label_re=r'^Cancel$', risk=RISK_NONE,
                     note='实测这个元素是 1x1 的隐藏节点，点它不一定有效；'
                          '关托盘更可靠的是按系统返回键'),
    },
    coverage=VERIFIED,
    entry='**长按**帖子/Reels 的 `Like button…`（约 900ms）',
    note='表情项是自定义 View 类型 `RopeStyleUFIDockView`，label 就是 `Like`/`Love`/`Care`/'
         '`Haha`/`Wow`/`Sad`/`Angry`，7 个横向排开。'
         '**选择任一表情都会改变账号对外状态，全部按 send 处理**。'
         '本轮只长按弹出托盘看了眼结构，随后按返回关闭，**没有选任何表情**。'
         '注意：长按后 `Mute video` 这类控件会暂时隐藏，页面判定会因此短暂失效。',
)



# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 8g. 帖子更多操作菜单（2026-11-02 00:22 实测）
# ---------------------------------------------------------------------------
POST_MORE_MENU = Page(
    key='post_more_menu', name='帖子更多操作菜单',
    activity_hint='FbMainTabActivity',
    identity=(
        _t('为什么看到', text_re=r'^Why am I seeing this post\?$'),
        _t('隐藏贴文', label_re=r'^隱藏貼文'),
        _t('更多菜单关闭', label_re=r'^Close$'),
    ),
    exclusive=(
        _t('为什么看到', text_re=r'^Why am I seeing this post\?$'),
        _t('隐藏贴文', label_re=r'^隱藏貼文'),
    ),
    targets={
        'close': _t('关闭菜单', label_re=r'^Close$', risk=RISK_NONE),
        'why_seeing': _t('为什么看到这条', text_re=r'^Why am I seeing this post\?$',
                         risk=RISK_NONE),
        'more_like_this': _t('感兴趣', label_re=r'^有興趣 你會在動態消息看到更多',
                             risk=RISK_SEND,
                             note='会改变推荐偏好（账号级设置），未点击'),
        'less_like_this': _t('不感兴趣', label_re=r'^沒興趣 你會在動態消息看到更少',
                             risk=RISK_SEND, note='会改变推荐偏好，未点击'),
        'save': _t('保存', label_re=r'^Save (reel|post|video)', risk=RISK_SEND,
                   note='加入收藏，改变账号状态，未点击'),
        'cast': _t('投屏', label_re=r'^Cast reel', risk=RISK_NONE,
                   note='会查找附近设备；未点击'),
        'hide': _t('隐藏贴文', label_re=r'^隱藏貼文', risk=RISK_SEND,
                   note='改变 feed 内容，未点击'),
        'report': _t('举报', label_re=r'^檢舉 ', risk=RISK_FORBIDDEN,
                     note='**举报是单向不可撤销的对外动作**，永不自动点击'),
        'notify': _t('开启此贴通知', label_re=r'^Turn on notifications for this post',
                     risk=RISK_SEND, note='改变通知设置，未点击'),
        # 2026-11-02 04:38 实测：菜单里还有「加入收藏」，会改变信息流优先级。
        'favorite': _t('加入收藏', label_re=r'^Add .+ to Favorites', risk=RISK_SEND,
                       note='会把该专页加入收藏、影响信息流排序；未点击'),
    },
    coverage=VERIFIED,
    entry='帖子详情右上「See more options」',
    note='**底部弹层**，底层帖子元素仍在层级里，所以用 exclusive 互斥锚点判定'
         '（`Why am I seeing this post?` + `隱藏貼文` 同时命中）。'
         '本次只打开采集结构，随后关闭，**没有点任何一项**。'
         '注意菜单里混着 Reel 专属项（`Save reel`/`Cast reel`/`檢舉 Reel`），'
         '说明这条素材被当作 Reel 处理，但页面判定仍是图文帖详情。',
)


# ---------------------------------------------------------------------------
# 8i. 「你可能错过的帖子」详情（2026-11-02 04:17 实测）
# ---------------------------------------------------------------------------
MISSED_POST = Page(
    key='missed_post', name='你可能错过的帖子',
    activity_hint='FbMainTabActivity',
    identity=(
        _t('标题', label_re=r'^你可能錯過的貼文, Heading$'),
        _t('返回', label_re=r'^Back$'),
        _t('搜索', label_re=r'^Search$'),
    ),
    exclusive=(
        _t('标题', label_re=r'^你可能錯過的貼文, Heading$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'search': _t('搜索', label_re=r'^Search$', risk=RISK_NONE),
        'more_options': _t('更多选项', label_re=r'^See more options$', risk=RISK_NONE),
        'more': _t('展开正文', text_re=r'^more$', risk=RISK_NONE),
        'see_translation': _t('查看翻译', text_re=r'^See translation$', risk=RISK_NONE),
        'author': _t('作者', label_re=r' page Profile picture$', risk=RISK_NONE,
                     note='形如「<名字> page Profile picture」，名字动态'),
        'photo': _t('图片', label_re=r'^Photo$', risk=RISK_NONE, repeated=True),
    },
    coverage=PARTIAL,
    entry='通知列表里「你可能錯過的貼文」类通知',
    note='**和图文帖详情（post_photo）长得像但不是同一页**：'
         '它多了顶部标题 `你可能錯過的貼文, Heading`，'
         '且这次 dump 里**没有点赞数/评论数/互动栏**（可能还没加载或该页不显示）。'
         '只用标题做 exclusive 判定，避免与其它单帖页混淆。'
         '未点赞、未评论、未分享。',
)


PROFILE = Page(
    key='profile', name='个人主页',
    activity_hint='ImmersivePortraitActivity',
    identity=(
        _t('封面', label_re=r'^Cover Photo$'),
        _t('编辑个人档案', label_re=r'^編輯個人檔案$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'search': _t('搜索', label_re=r'^搜尋$', risk=RISK_NONE),
        'cover': _t('封面相片', label_re=r'^Cover Photo$', risk=RISK_NONE),
        'edit_cover': _t('编辑封面', label_re=r'^編輯封面相片$', risk=RISK_SEND),
        'avatar': _t('大头贴', label_re=r'^大頭貼照$', risk=RISK_NONE),
        'edit_avatar': _t('编辑大头贴', label_re=r'^編輯大頭貼照$', risk=RISK_SEND),
        'note': _t('建立便利贴', label_re=r'^建立便利貼', risk=RISK_SEND,
                   note='会对外展示'),
        'profile_switcher': _t('切换个人档案', label_re=r'^開啟個人檔案切換工具$',
                               risk=RISK_NONE, repeated=True,
                               note='实测同一控件暴露两层，属结构性重复'),
        'add_story': _t('新增到限时动态', label_re=r'^新增到限時動態$', risk=RISK_SEND),
        'edit_profile': _t('编辑个人档案', label_re=r'^編輯個人檔案$', risk=RISK_SEND),
        'tab_all': _t('分页-全部', label_re=r'^全部, 1 of 3$', risk=RISK_NONE),
        'tab_photos': _t('分页-相片', label_re=r'^相片, 2 of 3$', risk=RISK_NONE),
        'tab_reels': _t('分页-Reel', label_re=r'^Reel, 3 of 3$', risk=RISK_NONE),
        # 2026-11-02 00:13：个人主页的分页是**页内切换**，不像社团分页会跳独立页。
        # 切到「相片」后页内多出两个子分类。
        'sub_photos': _t('子分类-相片', text_re=r'^相片$', risk=RISK_NONE,
                         note='只在「相片」分页出现；与分页按钮「相片, 2 of 3」不是同一个控件'),
        'sub_tagged': _t('子分类-被标注', text_re=r'^你被標註在內的相片$', risk=RISK_NONE),
        'pymk': _t('你可能认识', text_re=r'^People you may know$', risk=RISK_NONE),
    },
    coverage=VERIFIED,
    entry='首页 label「Go to profile」',
    note='顶栏与页面主体都吃到了。时间线内容、相片页、Reels 页签未进入。',
)


# ---------------------------------------------------------------------------
# 10b. 他人个人主页（2026-11-01 22:51 实测补充）
# ---------------------------------------------------------------------------
PERSON_PROFILE = Page(
    key='person_profile', name='他人个人主页',
    activity_hint='ImmersivePortraitActivity',
    identity=(
        _t('发送讯息', text_re=r'^發送訊息$'),
        _t('加朋友', text_re=r'^加朋友$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'search': _t('搜索', label_re=r'^搜尋$', risk=RISK_NONE),
        'cover': _t('封面相片', label_re=r'^Cover Photo$', risk=RISK_NONE),
        'avatar': _t('大头贴', label_re=r'^大頭貼照$', risk=RISK_NONE),
        'name': _t('对方名字', risk=RISK_NONE,
                   note='名字是动态内容；顶栏与正文各有一处，不能当锚点'),
        'add_friend': _t('加朋友', text_re=r'^加朋友$', risk=RISK_SEND,
                         note='改变好友关系，未点击'),
        'message_btn': _t('发送讯息', text_re=r'^發送訊息$', risk=RISK_SEND,
                          note='**点它会进入 Messenger 安装引导**（实测 22:52 落到 '
                               'com.facebook.oxygen.appmanager StubInstallActivity）；'
                               '未安装 Messenger 时此路不通'),
        'tab_all': _t('分页-全部', label_re=r'^全部, 1 of 3$', risk=RISK_NONE),
        'tab_photos': _t('分页-相片', label_re=r'^相片, 2 of 3$', risk=RISK_NONE),
        'tab_reels': _t('分页-Reel', label_re=r'^Reel, 3 of 3$', risk=RISK_NONE),
        'posts': _t('对方帖子区', label_re=r"'s posts$", risk=RISK_NONE,
                    note='形如「<名字>&#39;s posts」，名字动态'),
        'post_time': _t('帖子时间', text_re=r'•(Shared with|分享對象)[:：] ', risk=RISK_NONE,
                        note='形如「2025年3月10日•(Shared with|分享對象)[:：] 所有人」'),
        'see_more': _t('更多操作', label_re=r'^See more options$', risk=RISK_NONE),
        'photo': _t('照片', label_re=r'^Photo$', risk=RISK_NONE),
    },
    coverage=VERIFIED,
    entry='People 搜索结果点条目；或任意头像/名字',
    note='与自己主页的区别：没有「編輯個人檔案」，出现「加朋友」「發送訊息」。'
         '两者 Activity 相同（ImmersivePortraitActivity），必须靠按钮区分。',
)


# ---------------------------------------------------------------------------
# 10c. Messenger 安装提示（新版 Activity，2026-11-01 22:52）
# ---------------------------------------------------------------------------
MESSENGER_INSTALL = Page(
    key='messenger_install', name='Messenger 安装提示',
    activity_hint='StubInstallActivity',
    identity=(
        _t('标题', text_re=r'^安裝Messenger$'),
        _t('标志', label_re=r'^Messenger標誌$'),
        _t('大小', text_re=r'^預估大小'),
    ),
    targets={
        'back': _t('返回', label_re=r'^返回$', risk=RISK_NONE),
        'logo': _t('Messenger 标志', label_re=r'^Messenger標誌$', risk=RISK_NONE),
        'title': _t('标题', text_re=r'^安裝Messenger$', risk=RISK_NONE),
        'subtitle': _t('副标题', text_re=r'^與你最愛的對象建立聯繫', risk=RISK_NONE),
        'size': _t('预估大小', text_re=r'^預估大小', risk=RISK_NONE),
        'detail': _t('查看详情', text_re=r'^查看詳情$', risk=RISK_NONE),
        'mobile_data': _t('行动数据', type_re=r'^CheckBox$', risk=RISK_NONE),
        'install': _t('安装', text_re=r'^安裝$', risk=RISK_FORBIDDEN,
                      note='安装应用=环境变更，必须主人确认；本次没点'),
    },
    coverage=BLOCKED,
    entry='点他人主页「發送訊息」，或首页「Messaging」',
    note='前台包名是 `com.facebook.appmanager`（不是 katana），'
         'Activity `com.facebook.oxygen.appmanager.ui.landing.StubInstallActivity`。'
         '**未装 Messenger 时私信链路在此终止**；这是功能被产品限制，不是控件缺失。',
)



# ---------------------------------------------------------------------------
# 11. 社团
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 9d. 自己主页的照片页（2026-11-02 03:37 实测）
# ---------------------------------------------------------------------------
PROFILE_PHOTOS = Page(
    key='profile_photos', name='自己主页-照片页',
    activity_hint='PandoraTabPagerActivity',
    identity=(
        _t('关于你的照片 tab', text_re=r'^PHOTOS OF YOU, tab 1 of 3$'),
        _t('上传 tab', text_re=r'^UPLOADS, tab 2 of 3$'),
        _t('相簿 tab', text_re=r'^ALBUMS, tab 3 of 3$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'tab_photos_of_you': _t('关于你的照片', text_re=r'^PHOTOS OF YOU, tab 1 of 3$',
                                risk=RISK_NONE),
        'tab_uploads': _t('上传', text_re=r'^UPLOADS, tab 2 of 3$', risk=RISK_NONE,
                          note='实测默认选中'),
        'tab_albums': _t('相簿', text_re=r'^ALBUMS, tab 3 of 3$', risk=RISK_NONE),
        'photo': _t('照片格', label_re=r'^Photo$', risk=RISK_NONE, repeated=True,
                    note='**所有照片格 label 都是 `Photo`**，没有描述，只能按位置关联'),
    },
    coverage=PARTIAL,
    entry='自己主页 → `相片, 2 of 3` 分页 → `相片` 子分类',
    note='**这是独立全屏页**：Activity 是 `com.facebook.photos.pandora.ui.PandoraTabPagerActivity`'
         '（不是主页那个 ImmersivePortraitActivity，03:55 实测修正）。'
         '封面/编辑按钮都消失，只剩 Back + 三个 tab + 照片网格。'
         '**三个 tab 是横向 ViewPager 分页**：切到 ALBUMS 后未显示的页坐标会变成负数'
         '（如 `Photo` 出现在 x=-1080），所以按坐标点击必须过滤不可见元素。'
         'tab 名是**英文**（`PHOTOS OF YOU`/`UPLOADS`/`ALBUMS`），与界面其它中文混排。'
         '照片格 label 全是 `Photo`，无法按内容定位。'
         '三个 tab 都切换过，未点开任何照片。',
)


# ---------------------------------------------------------------------------
# 9e. 照片查看器（2026-11-02 07:25 实测）
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 9f. 照片更多菜单（2026-11-02 08:04 实测）
# ---------------------------------------------------------------------------
PHOTO_MORE_MENU = Page(
    key='photo_more_menu', name='照片更多菜单',
    activity_hint='PandoraTabPagerActivity',
    identity=(
        _t('设为封面', text_re=r'^Make cover photo$'),
        _t('删除照片', text_re=r'^Delete photo$'),
    ),
    targets={
        'make_cover': _t('设为封面', text_re=r'^Make cover photo$', risk=RISK_SEND,
                         note='会改主页封面，属对外展示变更；未点'),
        'edit_caption': _t('编辑说明', text_re=r'^Edit caption$', risk=RISK_SEND,
                           note='会修改照片说明文字；未点'),
        'delete': _t('删除照片', text_re=r'^Delete photo$', risk=RISK_FORBIDDEN,
                     note='**删除不可逆**，永不自动点击'),
        'save_phone': _t('保存到手机', text_re=r'^Save to phone$', risk=RISK_NONE,
                         note='会往相册写文件，但不改账号内容；未点'),
        'share_external': _t('外部分享', text_re=r'^Share external$', risk=RISK_SEND,
                             note='会调起外部分享面板；未点'),
    },
    coverage=COVERED,
    entry='照片查看器 → `More`',
    note='底部弹层，五项：设为封面 / 编辑说明 / **删除照片** / 保存到手机 / 外部分享。'
         '**`Delete photo` 标 forbidden**（不可逆）；其余按对外影响分级。'
         '只打开看过，**一项都没点**。',
)


PHOTO_VIEWER = Page(
    key='photo_viewer', name='照片查看器',
    activity_hint='PandoraTabPagerActivity',
    identity=(
        _t('序号', label_re=r'^\d+ of \d+$'),
        _t('标记模式', label_re=r'^Tagging mode$'),
        _t('标记朋友', label_re=r'^Tag more friends$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'photo': _t('图片', label_re=r'^Photo$', risk=RISK_NONE),
        'index': _t('第几张', label_re=r'^\d+ of \d+$', risk=RISK_NONE,
                    note='**单数是 `1 of 6`**（无前缀），和搜索结果页的 '
                         '`All search results, 1 of 7` 不是一种格式'),
        'tagging_mode': _t('标记模式', label_re=r'^Tagging mode$', risk=RISK_SEND,
                           note='进入给照片打标记的模式，会改账号内容；未点'),
        'location': _t('地点', label_re=r'^Location$', risk=RISK_NONE,
                       note='会打开地点选择，未点'),
        'more': _t('更多', label_re=r'^More$', risk=RISK_NONE),
        'author': _t('作者', risk=RISK_NONE, note='label 就是名字（动态）'),
        'date': _t('拍摄日期', text_re=r'^\d{4}年\d{1,2}月\d{1,2}日$', risk=RISK_NONE),
    },
    coverage=PARTIAL,
    entry='自己主页 → 相片 → 点任意照片',
    note='全屏查看器，Activity 仍是 `PandoraTabPagerActivity`。'
         '顶部：Back / `1 of 6` / `Tagging mode` / `Location` / `More`；'
         '底部是作者名与拍摄日期。**标记模式、地点、更多都未点**。'
         '左右滑动切换照片、双击缩放等手势未验证。',
)


# ---------------------------------------------------------------------------
# 9g. 私信会话（2026-11-02 17:38 实测）
# ---------------------------------------------------------------------------
DM_THREAD = Page(
    key='dm_thread', name='私信会话',
    activity_hint='MsysThreadViewActivity',
    identity=(
        # 2026-11-06 同修：原来也用 text_re，匹配不到；改 label_re + type_re。
        _t('输入框', label_re=r'^輸入訊息$', type_re=r'EditText'),
        _t('发送键', label_re=r'^傳送$'),
        _t('附件', label_re=r'^其他附件選項$'),
    ),
    targets={
        # 2026-11-06 修：原来写的是 `text_re=r'^輸入訊息$'`，**永远匹配不到**。
        # 实测 dump：输入框是 `type=EditText`、`label='輸入訊息'`，
        # 而 `text` 里装的是**我们填进去的草稿内容**（如「您好，看到您的留言很有意思…」）。
        # 判据写在 text 上等于一条也中不了 → 私信链的「输入框」从来没被识别、
        # 更没被校对过（dm_thread 只剩 send/attach/sticker 三项就是这原因）。
        # 现在改成 label_re + type_re（_hit 是 OR，任一命中即算）。
        'input': _t('消息输入框', label_re=r'^輸入訊息$', type_re=r'EditText',
                    risk=RISK_NONE,
                    note='EditText，在 y≈818（键盘弹出后布局上移）；点屏幕下部可唤出。'
                         '**它的 text 就是草稿内容**，校对时可据此确认草稿真的填进去了'),
        'send': _t('发送键', label_re=r'^(傳送|發送👍?)$', risk=RISK_SEND,
                   note='**真正把私信发出去**，只识别不点击。'
                        '**文案会变**：输入框为空时是 `傳送`，输入内容后变成 `發送👍`'),
        'attach': _t('附件', label_re=r'^其他附件選項$', risk=RISK_NONE,
                     note='展开后：開啟相機。/ 開啟相片圖庫。/ 開啟錄音器。'),
        'camera': _t('相机', label_re=r'^開啟相機。$', risk=RISK_SEND,
                     note='进拍照，未点'),
        'gallery': _t('相册', label_re=r'^開啟相片圖庫。$', risk=RISK_SEND,
                      note='进选图，未点'),
        'recorder': _t('录音', label_re=r'^開啟錄音器。$', risk=RISK_SEND,
                       note='进录音，未点'),
        'sticker': _t('表情贴图', label_re=r'^開啟貼圖、表情符號和 GIF 鍵盤。$',
                      risk=RISK_NONE),
        'cancel_reply': _t('取消回复', label_re=r'^取消回覆訊息。$', risk=RISK_NONE),
        'hint': _t('打招呼提示', text_re=r'^打聲招呼。$', risk=RISK_NONE,
                   note='会话为空时显示；**预制招呼语不是真实历史消息**'),
    },
    coverage=VERIFIED,
    entry='对方主页 → `發送訊息`；或评论作者名 → 主页 → 發送訊息',
    note='Activity 是 `MsysThreadViewActivity`（FB 内置消息，**不需要装 Messenger**）。'
         '**这一页无障碍信息很贫瘠**：整屏只有约 10 个 FB 元素且多数 label 为空，'
         '能读到的只有输入框、傳送、附件、贴图、取消回复这几项。'
         '输入框在 y≈818，**不在屏幕最底部**（键盘弹出后布局上移）。'
         '**`傳送` 标 send，从未点击**。'
         '会话里出现的"你好 👋 / 林立偉，你好！"是 **FB 预制的打招呼用语**，'
         '不是真实历史消息——采集时不要把它当成对方说过的话。',
)


GROUPS = Page(
    key='groups', name='社团（探索）',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('推荐区', text_re=r'^Suggested for you$'),
        _t('建社团', label_re=r'^Create group$'),
        # 2026-11-02 01:24 实测：加入社团后默认 tab 会变成「為你推薦」，
        # 原来的「探索」布局（Suggested for you + Create group）不再出现，
        # 页面会因此判不出来。补上另外两个 tab 的锚点。
        _t('为你推荐 tab', text_re=r'^為你推薦$'),
        _t('Your groups 区', text_re=r'^Your groups$'),
        # 「你的社團」tab 也是这一页的固有元素（04:17 实测）；
        # 社团搜索结果页那边已经不再用这个文本当锚点，不会互相干扰。
        _t('你的社团 tab', text_re=r'^你的社團$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'create_group': _t('创建社团', label_re=r'^Create group$', risk=RISK_NONE),
        'settings': _t('社团设置', label_re=r'^Settings$', risk=RISK_NONE),
        'search_groups': _t('搜索社团', label_re=r'^Search groups$', risk=RISK_NONE),
        'tab_for_you': _t('为你推荐', text_re=r'^為你推薦$', risk=RISK_NONE,
                          note='加入社团后的默认 tab'),
        'tab_your_groups': _t('你的社团', text_re=r'^你的社團$', risk=RISK_NONE),
        'tab_explore': _t('探索', text_re=r'^探索$', risk=RISK_NONE,
                          note='未加入任何社团时的默认 tab'),
        'your_groups': _t('你的社团区', text_re=r'^Your groups$', risk=RISK_NONE),
        'see_all_groups': _t('查看全部社团', text_re=r'^See all$', risk=RISK_NONE),
        'group_feed': _t('社团动态区', text_re=r'^來自你的社團$', risk=RISK_NONE),
        # 2026-11-02 04:17 实测：「你的社團」tab 的额外控件
        'create_group_alt': _t('建立社团（英文）', text_re=r'^Create a group$',
                               risk=RISK_NONE,
                               note='同一功能在「你的社團」tab 下用英文文案'),
        'search_in_groups': _t('社团内搜索', label_re=r'^Search$', risk=RISK_NONE),
        'sort_by': _t('排序方式', label_re=r'^Sort by: ', risk=RISK_NONE,
                      note='形如「Sort by: Most visited」，动态'),
        'sort_groups': _t('社团排序', label_re=r'^Sort groups$', risk=RISK_NONE),
        'card': _t('社团卡', label_re=r'^Suggested group: ', risk=RISK_NONE,
                   note='label 形如「Suggested group: 名称. 公開社團 · 2.7 萬位成員」，'
                        '名称与人数都是动态内容'),
        'hide': _t('隐藏推荐', label_re=r'^Hide suggested group: ', risk=RISK_NONE),
        'join': _t('加入社团', label_re=r'^Join group: ', risk=RISK_SEND,
                   note='会改变账号对外状态，未点击'),
    },
    coverage=VERIFIED,
    entry='抽屉菜单「社團」',
    note='**三个 tab 的锚点都要留**：未加入社团时默认「探索」'
         '（有 `Suggested for you` + `Create group`）；加入过社团后默认「為你推薦」'
         '（`為你推薦` + `Your groups` + `來自你的社團`），两套界面完全不同。'
         '只采集了列表与推荐，社团内发帖未做。',
)


# ---------------------------------------------------------------------------
# 11b. 社团详情页（2026-11-01 23:52 实测）
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 10d. 社团排序面板（2026-11-02 04:37 实测）
# ---------------------------------------------------------------------------
GROUP_SORT_PANEL = Page(
    key='group_sort_panel', name='社团排序',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('排序标题', text_re=r'^Sort groups$'),
        _t('最近访问', text_re=r'^Most visited$'),
        _t('名称排序', text_re=r'^Groups A-Z$'),
    ),
    targets={
        'close': _t('关闭遮罩', label_re=r'^Close$', risk=RISK_NONE),
        'most_visited': _t('最近访问', text_re=r'^Most visited$', risk=RISK_NONE),
        'managed': _t('我管理的社团', text_re=r'^Groups you manage$', risk=RISK_NONE,
                      note='RadioButton 与内部 View 双层命中，属结构性重复'),
        'az': _t('按名称', text_re=r'^Groups A-Z$', risk=RISK_NONE,
                 note='RadioButton 与内部 View 双层命中，属结构性重复'),
    },
    coverage=COVERED,
    entry='社团页「你的社團」tab → `Sort groups`',
    note='底部弹层，三个 RadioButton。**文案是英文**（`Most visited`/`Groups you manage`/'
         '`Groups A-Z`），与页面其它中文混排。只改本地排序、不改账号状态，全标 none；'
         '本次只打开看过，**没有改选**。',
)


# ---------------------------------------------------------------------------
# 10e. 社团帖更多菜单（2026-11-02 06:00 实测）
# ---------------------------------------------------------------------------
GROUP_POST_MORE_MENU = Page(
    key='group_post_more_menu', name='社团帖更多菜单',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('举报给管理员', label_re=r'^Report to group admins$'),
        _t('保存 Reel', label_re=r'^Save reel'),
    ),
    targets={
        'close': _t('关闭遮罩', label_re=r'^Close$', risk=RISK_NONE),
        'save_reel': _t('保存 Reel', label_re=r'^Save reel', risk=RISK_SEND,
                        note='加入收藏，会改账号数据；未点'),
        'report_post': _t('举报贴文', label_re=r'^檢舉貼文', risk=RISK_FORBIDDEN,
                          note='**举报单向不可撤销**，永不自动点击'),
        'report_admin': _t('举报给社团管理员',
                           label_re=r'^Report to group admins', risk=RISK_SEND,
                           note='会把内容上报给管理员，属对外动作；未点'),
        'notify': _t('开启此贴通知',
                     label_re=r'^Turn on notifications for this post', risk=RISK_SEND,
                     note='改变通知设置；未点'),
        'copy_link': _t('复制链接', label_re=r'^Copy link$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='社团详情页里某条帖子的 `See more options`',
    note='**和帖子详情页的 `post_more_menu` 不是同一个菜单**：那边有 '
         '`Why am I seeing this post?` 等项，这边换成了'
         '`Report to group admins`（社团特有）+ `Save reel`。'
         '内容不同、也没有共同的 exclusive 锚点，所以单独建模（06:00 判定失败后拆出来的）。'
         '只打开看过，**没有保存/举报/开通知**。',
)


GROUP_DETAIL = Page(
    key='group_detail', name='社团详情页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('社团封面', label_re=r'^Cover photo of group '),
        _t('成员数', label_re=r'^.+,\s*(公開|私密)社團 · '),
        _t('发言入口', label_re=r'^留[個个]言吧'),
        # 2026-11-02 07:15 实测：切到「討論區」分页后，封面/成员数都不在，
        # 只剩发言框与讨论区排序，判定会变 None。补这两个。
        _t('讨论区排序', text_re=r'^最相關$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'search': _t('社团内搜索', label_re=r'^Search in ', risk=RISK_NONE,
                     note='形如「Search in Marketplace」；社团名是动态的'),
        'cover': _t('封面', label_re=r'^Cover photo of group ', risk=RISK_NONE),
        'title': _t('社团名与人数', label_re=r'^.+,\s*(公開|私密)社團 · ', risk=RISK_NONE,
                    note='形如「Marketplace, 公開社團 · 830K members」；名称与人数动态'),
        'join': _t('加入社团', label_re=r'^Join .+ group$', risk=RISK_SEND,
                   note='**会改变账号对外状态**。21:02 曾在搜索结果里误触加入过一个社团，'
                        '此后点击条目一律走"避开高风险矩形"的安全点'),
        'joined': _t('已加入社团', label_re=r'^已加入「.+」社團$', risk=RISK_SEND,
                     repeated=True,
                     note='**加入/未加入是两种按钮**：未加入显示 `Join X group`，'
                          '已加入显示 `已加入「 X 」社團`。后者点开是成员菜单（可退出社团），'
                          '退出同样改变对外状态，未点击'),
        'tab_reel': _t('Reel', text_re=r'^Reel$', risk=RISK_NONE),
        'tab_discussion': _t('讨论区', text_re=r'^討論區$', risk=RISK_NONE),
        'tab_photos': _t('相片', text_re=r'^相片$', risk=RISK_NONE),
        'tab_albums': _t('相簿', text_re=r'^相簿$', risk=RISK_NONE),
        'tab_events': _t('活动', text_re=r'^活動$', risk=RISK_NONE),
        'tab_files': _t('档案', text_re=r'^檔案$', risk=RISK_NONE),
        'composer': _t('社团内发言', label_re=r'^留[個个]言吧', risk=RISK_SEND,
                       note='点开是发帖框，最终会对外发布，未点击'),
        'photo_video': _t('照片/视频', label_re=r'^Photo/Video$', risk=RISK_NONE),
        # 2026-11-02 06:17 实测：「加入」在英文语境下写作 `Join <社团名> group`，
        # 和推荐卡里的 `Join group: <名称>` 是两种格式，别只认一种。
        'join_en': _t('加入社团（英文）', label_re=r'^Join .+ group$', risk=RISK_SEND,
                      note='会真实加入社团；文案是 `Join <名称> group`，与推荐卡的 '
                           '`Join group: <名称>` 不同；未点击'),
        'sort': _t('排序', text_re=r'^最相關$', risk=RISK_NONE,
                   note='讨论区排序；点开是排序选项'),
        'post_time': _t('帖子时间', text_re=r'•(Shared with|分享對象)[:：] ', risk=RISK_NONE),
        'see_more': _t('更多操作', label_re=r'^See more options$', risk=RISK_NONE),
    },
    coverage=COVERED,
    entry='搜索结果点社团条目（本次用安全点避开「加入」按钮）',
    note='**Activity 仍是被搜索页共用的 ImmersiveActivity**，只能靠社团专属锚点判定。'
         '只采集了「討論區」默认视图；Reel/相片/相簿/活動/檔案 五个分页未进入。'
         '「留個言吧……」是发言入口，属 send 级，未点击。',
)



# ---------------------------------------------------------------------------
# 11c. 社团相片页（2026-11-01 23:55 实测）
# ---------------------------------------------------------------------------
GROUP_PHOTOS = Page(
    key='group_photos', name='社团相片页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('标题', text_re=r'^Photos$'),
        _t('照片格子', label_re=r'^View post$'),
        _t('返回', label_re=r'^Back$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'title': _t('标题', text_re=r'^Photos$', risk=RISK_NONE),
        'item': _t('照片格子', label_re=r'^View post$', risk=RISK_NONE, repeated=True,
                   note='**所有格子 label 都是「View post」，没有任何图片描述**，'
                        '无法按内容定位；只能按顺序/可见区域关联'),
    },
    coverage=COVERED,
    entry='社团详情页「相片」分页',
    note='**点分页会离开社团页**：进入独立全屏页（只有 Photos 标题 + Back + 格子），'
         '社团封面/成员数/六个页签全部消失，所以必须独立成页。'
         '只采集了首屏格子，未点开任何照片。',
)



# ---------------------------------------------------------------------------
# 11d. 社团活动页（2026-11-01 23:57 实测）
# ---------------------------------------------------------------------------
GROUP_EVENTS = Page(
    key='group_events', name='社团活动页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('分组标题', text_re=r'^近期活動$'),
        _t('参加人数', text_re=r'^\d+\s*人(會參加|有興趣)'),
        _t('兴趣按钮', label_re=r'^對 .+ 有興趣'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'title': _t('近期活动', text_re=r'^近期活動$', risk=RISK_NONE),
        'item': _t('活动条目', risk=RISK_NONE, repeated=True,
                   note='活动标题与时间都是动态内容，**没有稳定特征**，'
                        '不设正则；只能靠「有興趣/分享」按钮反推条目位置'),
        'interested': _t('有兴趣', label_re=r'^對 .+ 有興趣$', risk=RISK_SEND,
                         repeated=True,
                         note='会改变账号对外状态，未点击'),
        'share': _t('分享活动', label_re=r'^分享 ', risk=RISK_SEND, repeated=True),
        'attendees': _t('参加/感兴趣人数', text_re=r'^\d+\s*人(會參加|有興趣)',
                        risk=RISK_NONE, repeated=True,
                        note='人数动态，不能当锚点'),
        'cover': _t('活动封面', label_re=r'^封面相片', risk=RISK_NONE, repeated=True),
    },
    coverage=COVERED,
    entry='社团详情页「活動」分页',
    note='与相片页一样，点分页会**离开社团页**进入独立全屏页，'
         '社团封面/六个页签全部消失。只采集了首屏两条活动，未点任何「有興趣」。',
)



# ---------------------------------------------------------------------------
# 11e. 社团档案页（2026-11-01 23:59 实测）
# ---------------------------------------------------------------------------
GROUP_FILES = Page(
    key='group_files', name='社团档案页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('标题', text_re=r'^Files$'),
        _t('新增档案', label_re=r'^Add file button$'),
        _t('空态', text_re=r"^This group doesn't have any files yet\.$"),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'title': _t('标题', text_re=r'^Files$', risk=RISK_NONE),
        'add_file': _t('新增档案', label_re=r'^Add file button$', risk=RISK_SEND,
                       note='会上传文件到社团，属对外动作，未点击'),
        'profile': _t('显示个人档案', label_re=r'^Show profile$', risk=RISK_NONE),
        'empty': _t('空态', text_re=r"^This group doesn't have any files yet\.$",
                    risk=RISK_NONE,
                    note='该社团目前没有文件；有文件时的列表结构未覆盖'),
    },
    coverage=PARTIAL,
    entry='社团详情页「檔案」分页',
    note='独立全屏页，社团页签全部消失。实测该社团为空，'
         '只采到 `Add file button` 与空态文案，文件列表、下载、预览均未覆盖。',
)



# ---------------------------------------------------------------------------
# 11f. 社团相簿页（2026-11-02 00:01 实测）
# ---------------------------------------------------------------------------
GROUP_ALBUMS = Page(
    key='group_albums', name='社团相簿页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('新建相簿', label_re=r'^Create album button$'),
        _t('相簿条目', label_re=r'\d+ photos$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'create_album': _t('新建相簿', label_re=r'^Create album button$',
                           risk=RISK_SEND,
                           note='会往社团里建相簿，属对外动作，未点击'),
        'profile': _t('显示个人档案', label_re=r'^Go to profile$', risk=RISK_NONE),
        'album': _t('相簿', label_re=r'\d+ photos$', risk=RISK_NONE, repeated=True,
                    note='形如「Guitar, 2 photos」；相簿名与张数都是动态内容'),
    },
    coverage=COVERED,
    entry='社团详情页「相簿」分页',
    note='独立全屏页（有 Back、无可见标题文本）。只采集了相簿列表，'
         '未点开任何相簿、未新建。相簿详情与照片网格未覆盖。',
)



# ---------------------------------------------------------------------------
# 11g. 社团 Reel 页（2026-11-02 00:04 实测）
# ---------------------------------------------------------------------------
GROUP_REELS = Page(
    key='group_reels', name='社团 Reel 页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('Reel 格', label_re=r'^(Muted )?Reel,\d+ plays$'),
        _t('返回', label_re=r'^Back$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'item': _t('Reel 格', label_re=r'^(Muted )?Reel,\d+ plays$',
                   risk=RISK_NONE, repeated=True,
                   note='3 列网格，label 形如「Reel,167 plays」「Muted Reel,68 plays」；'
                        '**没有标题或作者**，只能按位置关联'),
    },
    coverage=COVERED,
    entry='社团详情页「Reel」分页',
    note='独立全屏页，只有网格 + Back，**连页面标题文本都没有**。'
         '只能靠「Reel,N plays」+ Back 两个锚点判定。'
         '01:56 已点开其中一条验证跳转（进入 group_reel_view），'
         '但播放/点赞/评论仍未操作。',
)



# ---------------------------------------------------------------------------
# 11h. 社团搜索结果页（2026-11-02 00:44 实测）
# ---------------------------------------------------------------------------
GROUP_SEARCH_RESULTS = Page(
    key='group_search_results', name='社团搜索结果页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('Groups 页签', text_re=r'^Groups$'),
        _t('Group Posts 页签', text_re=r'^Group Posts$'),
        # 切到 Group Posts 后页面换成筛选 chip 布局，上面两个页签会消失。
        # 这几条 chip 是 posts 视图的主要可辨识特征（页面滚动时不一定都在，
        # 所以多列几项，靠"任一命中"累加）。
        _t('已应用筛选', text_re=r'^\d+ applied filters$'),
        # 注意：不要用 `你的社團` 当锚点——社团页的 tab 也有同样文字，会互相干扰。
        _t('内容类型 chip', text_re=r'^Posts$'),
        _t('最新贴文 chip', text_re=r'^最新貼文$'),
        _t('发布日期 chip', text_re=r'^發佈日期$'),
    ),
    # 这两个页签同时出现 → 一定是社团搜索结果页（普通搜索页没有它们）。
    # 之前靠计数会和 search 打成并列（都 2 分），判不出来。
    exclusive=(
        _t('Groups 页签', text_re=r'^Groups$'),
        _t('Group Posts 页签', text_re=r'^Group Posts$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'tab_groups': _t('社团页签', text_re=r'^Groups$', risk=RISK_NONE),
        'tab_posts': _t('社团帖页签', text_re=r'^Group Posts$', risk=RISK_NONE),
        'filters': _t('已应用筛选', text_re=r'^\d+ applied filters$', risk=RISK_NONE,
                      note='点开是筛选面板'),
        'chip_posts': _t('内容类型-Posts', text_re=r'^Posts$', risk=RISK_NONE),
        'chip_latest': _t('最新贴文', text_re=r'^最新貼文$', risk=RISK_NONE),
        'chip_seen': _t('你看过的贴文', text_re=r'^你看過的貼文$', risk=RISK_NONE),
        'chip_date': _t('发布日期', text_re=r'^發佈日期$', risk=RISK_NONE),
        'chip_your_groups': _t('你的社团', text_re=r'^你的社團$', risk=RISK_NONE),
        'post_item': _t('帖子格', label_re=r'^.+,\s*\d+\s*[天週月]',
                        risk=RISK_NONE, repeated=True,
                        note='label 形如「Marketplace, 3 天. 正文…」；2 列网格，正文动态'),
        'item': _t('社团条目', label_re=r'^.+,\s*(公開|私密) · \d', risk=RISK_NONE,
                   repeated=True,
                   note='label 形如「marketplace, 公開 · 22 萬位成員 · 一天 90 則以上貼文」；'
                        '注意这里的人数后缀跟搜索结果页略不同（少「社團」二字）'),
        'join': _t('加入', text_re=r'^加入$', risk=RISK_SEND, repeated=True),
        'joined': _t('前往', text_re=r'^前往$', risk=RISK_NONE, repeated=True,
                     note='已加入的社团显示「前往」'),
    },
    coverage=COVERED,
    entry='社团页右上 `Search groups`（该页输入框不进无障碍层级，见下方说明）',
    note='**重要限制**：社团搜索页的输入框（placeholder `Search groups`）'
         '**完全不在无障碍层级里**——截图能看到、键盘也弹了，但 dump 只有 Back。'
         '只能靠"键盘已弹出、焦点在那个框"直接打字，不能按元素定位。'
         '已给 flow 脚本加了这种兜底路径。',
)



# ---------------------------------------------------------------------------
# 11i. 社团"查看全部"列表页（2026-11-02 01:24 实测）
# ---------------------------------------------------------------------------
SEE_ALL_GROUPS = Page(
    key='see_all_groups', name='社团查看全部列表',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('筛选按钮', label_re=r'^Filters$'),
        _t('城市 chip', text_re=r'^城[巿市]$'),
        _t('公开社团 chip', text_re=r'^公開社團$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'filters': _t('筛选', label_re=r'^Filters$', risk=RISK_NONE),
        'chip_groups': _t('类型-社团', text_re=r'^Groups$', risk=RISK_NONE),
        'chip_city': _t('城市', text_re=r'^城[巿市]$', risk=RISK_NONE),
        'chip_nearby': _t('在我附近', text_re=r'^在我附近$', risk=RISK_NONE,
                          note='可能触发定位权限'),
        'chip_public': _t('公开社团', text_re=r'^公開社團$', risk=RISK_NONE),
        'chip_my': _t('我的社团', text_re=r'^我的社團$', risk=RISK_NONE),
        'item': _t('社团条目', label_re=r'^.+,\s*(公開|私密) · \d', risk=RISK_NONE,
                   repeated=True),
        'join': _t('加入', text_re=r'^加入$', risk=RISK_SEND, repeated=True),
        'joined': _t('前往', text_re=r'^前往$', risk=RISK_NONE, repeated=True),
    },
    coverage=COVERED,
    entry='搜索结果页社团分组的「查看全部」',
    note='**和搜索结果页的 Groups 分类不是同一页**：这里顶部多一排筛选 chip'
         '（`Filters`/`Groups`/`城巿`/`在我附近`/`公開社團`/`我的社團`）。'
         '一开始它被判成了 `search`（因为同样带 Back + Clear text），已按 chip 组合单独建模。'
         'chips 只观察、未点击。',
)



# ---------------------------------------------------------------------------
# 11j. 社团 Reel 播放页（2026-11-02 01:56 实测）
# ---------------------------------------------------------------------------
GROUP_REEL_VIEW = Page(
    key='group_reel_view', name='社团 Reel 播放页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('选择观众', label_re=r'^Pick viewer content to show$'),
        _t('我的 Reels 主页', label_re=r'^Navigate to your Reels profile$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'pick_viewer': _t('选择观众内容', label_re=r'^Pick viewer content to show$',
                          risk=RISK_NONE,
                          note='看起来是调整"给谁看"的入口，未点开'),
        'search': _t('搜索', label_re=r'^Search$', risk=RISK_NONE),
        'reels_profile': _t('我的 Reels 主页',
                            label_re=r'^Navigate to your Reels profile$', risk=RISK_NONE),
        # 2026-11-02 07:08 实测：内容加载出来后会有下面这些（之前只看到 4 个控件是加载中）
        'video_details': _t('视频详情', label_re=r'^Video details$', risk=RISK_NONE),
        'author_avatar': _t('作者头像', label_re=r"^Author's profile$", risk=RISK_NONE),
        'audio_info': _t('音频信息', label_re=r'· 原始音訊$', risk=RISK_NONE,
                         note='形如「Marketplace · 原始音訊」，含作者名（动态）'),
    },
    coverage=PARTIAL,
    entry='社团详情页「Reel」分页 → 点任意 Reel 格',
    note='**内容会异步加载**：刚点开时只有 Back / 选择观众 / 搜索 / 我的 Reels 主页 4 个控件'
         '（01:56 那次就是抓在加载中），等一会儿才会出现 `Video details`、作者头像、'
         '`· 原始音訊` 等。**社团 Reel 与专页 Reel 落到的是同一页**（07:08 实测复用）。'
         '视频本体的播放控制仍不进无障碍层级，所以能做的是"认出这页 + 读作者/音频信息"。',
)



# ---------------------------------------------------------------------------
# 11l. 活动主页（2026-11-02 02:54 实测，抽屉「活動」）
# ---------------------------------------------------------------------------
EVENTS_HOME = Page(
    key='events_home', name='活动主页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('本週 tab', text_re=r'^本週$'),
        _t('朋友 tab', text_re=r'^朋友$'),
        _t('本地 tab', text_re=r'^本地$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'city': _t('城市', risk=RISK_NONE,
                   note='label 就是城市名（动态）；本次是 `Xinjin`'),
        'tab_recommended': _t('为你推荐', text_re=r'^為你推薦$', risk=RISK_NONE,
                              note='实测默认选中'),
        'tab_local': _t('本地', text_re=r'^本地$', risk=RISK_NONE),
        'tab_week': _t('本周', text_re=r'^本週$', risk=RISK_NONE),
        'tab_friends': _t('朋友', text_re=r'^朋友$', risk=RISK_NONE),
        'tab_following': _t('追踪中', text_re=r'^追蹤中$', risk=RISK_NONE,
                            note='默认在屏幕最右边（可能被裁），需横向滑动才完整'),
    },
    coverage=PARTIAL,
    entry='抽屉菜单「活動」',
    note='顶部是城市按钮 + 5 个筛选 tab（`為你推薦`/`本地`/`本週`/`朋友`/`追蹤中`），'
         '都是 Button + 内部 TextView 的双层结构。'
         '**列表区两次 dump 都是空的**（该账号/地区没有推荐活动，或仍在加载），'
         '所以活动卡片结构、点开详情都未覆盖，覆盖等级保持 partial。',
)



# ---------------------------------------------------------------------------
# 11m. 我的珍藏（2026-11-02 03:43 实测，抽屉「我的珍藏」）
# ---------------------------------------------------------------------------
SAVED_ITEMS = Page(
    key='saved_items', name='我的珍藏',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('全部 tab', label_re=r'^全部，\d+／\d+$'),
        _t('珍藏分类 tab', text_re=r'^珍藏分類$'),
        _t('贴文 tab', label_re=r'^貼文，\d+／\d+$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'tab_all': _t('全部', label_re=r'^全部，\d+／\d+$', risk=RISK_NONE,
                      note='实测默认选中；**编号用全角「／」不是 `of`**'),
        'tab_reel': _t('Reel', label_re=r'^Reel，\d+／\d+$', risk=RISK_NONE),
        'tab_posts': _t('贴文', label_re=r'^貼文，\d+／\d+$', risk=RISK_NONE),
        'tab_marketplace': _t('Marketplace', label_re=r'^Marketplace，\d+／\d+$',
                              risk=RISK_NONE),
        'tab_collections': _t('珍藏分类', label_re=r'^珍藏分類，\d+／\d+$',
                              risk=RISK_NONE),
        'tab_any': _t('任一分类 tab', label_re=r'，\d+／\d+$', risk=RISK_NONE,
                      repeated=True,
                      note='共 6 个，最后一个默认在屏幕外'),
        'search': _t('搜索珍藏', label_re=r'^搜尋$', risk=RISK_NONE),
        'collections_group': _t('珍藏分类区', text_re=r'^珍藏分類$', risk=RISK_NONE,
                                repeated=True,
                                note='标题和内部 View 都会命中，属结构性重复'),
        'new_collection': _t('新的珍藏分类', label_re=r'^新的珍藏分類$',
                             risk=RISK_SEND, note='新建分类会改账号数据，未点击'),
        'create_collection': _t('建立新珍藏分类', label_re=r'^建立新珍藏分類$',
                                risk=RISK_SEND, note='同上，未点击'),
        'recent_group': _t('近期储存', text_re=r'^近期儲存$', risk=RISK_NONE),
        'empty': _t('空态', text_re=r'^在這裡重新探索你的珍藏內容',
                    risk=RISK_NONE,
                    note='该账号当前没有保存内容'),
    },
    coverage=PARTIAL,
    entry='抽屉菜单「我的珍藏」',
    note='**编号格式和搜索结果页不一样**：这里是 `全部，1／6`（全角逗号+全角斜杠），'
         '而搜索结果页是 `All search results, 1 of 7`（半角+of）。'
         '**不能复用同一个正则**，否则一边永远匹配不上。'
         '列表是**空态**：该账号没有保存内容，只有 `珍藏分類`/`近期儲存` 两个分组标题、'
         '`建立新珍藏分類` 入口和提示文案。有内容时的卡片结构未覆盖。',
)



# ---------------------------------------------------------------------------
# 11n. 生日页（2026-11-02 03:07 实测，抽屉「生日」）
# ---------------------------------------------------------------------------
BIRTHDAYS = Page(
    key='birthdays', name='生日',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('空态文案', text_re=r'^你朋友的生日會顯示在這裡。$'),
        _t('返回', label_re=r'^Back$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'empty': _t('空态', text_re=r'^你朋友的生日會顯示在這裡。$', risk=RISK_NONE,
                    note='该账号当前没有可显示的好友生日'),
    },
    coverage=PARTIAL,
    entry='抽屉菜单 See more 后「生日」',
    note='**极简页**：整屏只有一个空态文案 + Back，没有任何生日条目、'
         '也没有月份/分组导航。有生日好友时的列表结构未覆盖，'
         '所以只能做到"认出这页"，不假设它长这样。',
)



# ---------------------------------------------------------------------------
# 11o. 粉丝专页（2026-11-02 05:00 实测，抽屉 See more 后「粉絲專頁」）
# ---------------------------------------------------------------------------
FACEBOOK_PAGES = Page(
    key='facebook_pages', name='粉丝专页',
    activity_hint='ImmersiveActivity',
    identity=(
        _t('已追踪 tab', text_re=r'^已追蹤的粉絲專頁$'),
        _t('最近浏览分组', text_re=r'^最近瀏覽過的粉絲專頁$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'tab_create': _t('建立', text_re=r'^建立$', risk=RISK_SEND,
                         note='点进去是创建专页流程，未点击'),
        'tab_followed': _t('已追踪的粉丝专页', text_re=r'^已追蹤的粉絲專頁$',
                           risk=RISK_NONE),
        'tab_invite': _t('邀请', text_re=r'^邀請$', risk=RISK_SEND,
                         note='邀请他人点赞专页，属对外动作，未点击'),
        'recent_group': _t('最近浏览过的粉丝专页',
                           text_re=r'^最近瀏覽過的粉絲專頁$', risk=RISK_NONE),
        'page_item': _t('专页名称', risk=RISK_NONE, repeated=True,
                        note='label 就是专页名（动态），不能当锚点'),
        'category': _t('专页类别', risk=RISK_NONE, repeated=True,
                       note='形如「媒體／新聞機構」，动态'),
        'followers': _t('追踪人数', text_re=r'^[\d,]+\s*人在追蹤$', risk=RISK_NONE,
                        repeated=True, note='动态'),
        'send_message': _t('发送讯息', text_re=r'^發送訊息$', risk=RISK_SEND,
                           repeated=True,
                           note='会进 Messenger 引导（本机未装），未点击'),
    },
    coverage=PARTIAL,
    entry='抽屉菜单 See more 后「粉絲專頁」',
    note='顶部三个 tab：`建立`/`已追蹤的粉絲專頁`/`邀請`；下面是'
         '`最近瀏覽過的粉絲專頁` 卡片（专页名/类别/追踪人数/發送訊息）。'
         '只采到「最近浏览」一组，**已追踪列表、专页详情、创建流程都未覆盖**。'
         '卡片里的名称/类别/人数都是动态内容。',
)



# ---------------------------------------------------------------------------
# 11p. 粉丝专页详情（2026-11-02 05:00 实测）
# ---------------------------------------------------------------------------
PAGE_DETAIL = Page(
    key='page_detail', name='粉丝专页详情',
    activity_hint='ImmersivePortraitActivity',
    identity=(
        # 2026-11-02 06:28 实测：`Cover Photo` 与 `相片, N of N` 在个人主页也有，
        # 用它们会和 profile / person_profile 打平（各 2 分）、页面判 None。
        # 这里**只用专页独有的锚点**：`位追蹤者` / `人追蹤中` / `提及, N of 5`。
        _t('追踪者数', label_re=r'^[\d.,]+\s*萬?\s*位追蹤者$'),
        _t('追踪中人数', label_re=r'^[\d.,]+\s*人追蹤中$'),
        _t('提及分页', label_re=r'^提及, \d+ of \d+$'),
        # 专页是 5 个分页（个人主页 3 个），用 `of 5` 区分；页数若变需重新核对。
        _t('内容分页', label_re=r'^相片, \d+ of 5$'),
    ),
    # 首屏不一定有分页 tab、滚动后又不一定有追踪者数，所以不用 exclusive，
    # 靠上面四个**专页独有**锚点计数（任意两个同时命中即可判定）。
    exclusive=(),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'cover': _t('封面', label_re=r'^Cover Photo$', risk=RISK_NONE),
        'avatar': _t('头像（含限时动态）', label_re=r'^有未讀限時動態的大頭貼照$',
                     risk=RISK_NONE, note='会打开限时动态，未点击'),
        'name': _t('专页名', risk=RISK_NONE,
                   note='label 就是专页名（动态）'),
        'verified': _t('验证徽章', label_re=r'^驗證標章$', risk=RISK_NONE,
                       note='未验证的专页没有这个元素'),
        'category': _t('类别', risk=RISK_NONE, note='如「傢俱」，动态'),
        'followers': _t('追踪人数', label_re=r'^[\d.,]+\s*萬?\s*位追蹤者$',
                        risk=RISK_NONE),
        'following': _t('追踪中人数', label_re=r'^[\d.,]+\s*人追蹤中$', risk=RISK_NONE),
        'bio_more': _t('查看更多简介', text_re=r'^查看更多$', risk=RISK_NONE),
        # 专页有 5 个分页（个人主页只有 3 个，多出「活動」「提及」）
        'tab_all': _t('全部', label_re=r'^全部, \d+ of \d+$', risk=RISK_NONE),
        'tab_photos': _t('相片', label_re=r'^相片, \d+ of \d+$', risk=RISK_NONE),
        'tab_reels': _t('Reel 分页', label_re=r'^Reel, \d+ of \d+$', risk=RISK_NONE),
        'tab_events': _t('活动分页', label_re=r'^活動, \d+ of \d+$', risk=RISK_NONE),
        'tab_mentions': _t('提及分页', label_re=r'^提及, \d+ of \d+$', risk=RISK_NONE,
                           note='**专页独有**，个人主页没有这个分页'),
        'posts_section': _t('帖子区', text_re=r"^.+''s posts$|^.+’s posts$",
                            risk=RISK_NONE, note='形如「Marketplace\'s posts」，名字动态'),
        # 「提及」分页里的内容（05:08 实测）
        'mention_post': _t('提及帖卡', risk=RISK_NONE, repeated=True,
                           note='作者+正文+链接预览组合，内容是动态的'),
        'shared_link': _t('分享链接预览', label_re=r'^(Shared Link|分享連結|分享链接)[:：] ', risk=RISK_NONE,
                          note='会展示预览图/标题'),
        'open_link': _t('打开外链', text_re=r'^OPEN$', risk=RISK_NONE,
                        note='点了会跳浏览器/外部页面，**未点击**'),
        # 「活動」分页（05:22 实测）
        'past_events': _t('过去的活动', text_re=r'^過去的活動$', risk=RISK_NONE),
        'event_card': _t('活动卡', label_re=r'^.+的封面相片', risk=RISK_NONE,
                         repeated=True,
                         note='label 形如「<活动名> 的封面相片 未提供相片說明」，名字动态'),
        # 「相片」分页（05:31 实测）
        'photo_grid': _t('照片格', label_re=r'^Photo$', risk=RISK_NONE, repeated=True,
                         note='瀑布流布局：首图 717x594 大图，其余 355x352；'
                              'label 全是 `Photo`，无法按内容定位'),
        'follow': _t('追踪专页', text_re=r'^追蹤$', risk=RISK_SEND, repeated=True,
                     note='会改变对外关注关系，未点击'),
        # 2026-11-02 06:52 实测：专页有联系方式按钮（WhatsApp/电话/网站等）。
        'contact_whatsapp': _t('WhatsApp 联系', text_re=r'^WhatsApp$', risk=RISK_NONE,
                               note='会跳转 WhatsApp/外部 App，**未点击**'),
        'contact_any': _t('联系按钮', text_re=r'^(WhatsApp|致電|電子郵件|網站)$',
                          risk=RISK_NONE, repeated=True,
                          note='会跳外部；具体有哪些取决于专页填了哪些联系方式'),
        # 2026-11-02 06:17 实测：继续下滑会到「关于」信息区（网站/电话）再往后才是帖子流。
        'about_website': _t('关于-网站', label_re=r'^[\w.-]+\.(com|org|net|py|io)(\.[a-z]{2})?$',
                            risk=RISK_NONE,
                            note='形如 marketplace.com.py；会跳外部，未点'),
        'post_time': _t('帖子时间', text_re=r'•(Shared with|分享對象)[:：] ', risk=RISK_NONE, repeated=True),
        # 「Reel」分页（06:51 实测）：3 列网格，label 形如 `Reel,385 plays`
        'reel_item': _t('Reel 格', label_re=r'^Reel,[\d,]+\s*plays$', risk=RISK_NONE,
                        repeated=True,
                        note='和社团 Reel 网格同样格式；这里元素类型是 Button'),
    },
    coverage=PARTIAL,
    entry='搜索结果 Pages 分类 → 点主页条目',
    note='结构：封面图 + 头像（可能带限时动态环）+ 专页名/验证徽章/类别 + '
         '追踪人数/追踪中人数 + 简介 + `查看更多` + `追蹤` 按钮。'
         '**和「他人个人主页」（person_profile）很像**，区别是这里一定有专页专属的'
         '`位追蹤者` 计数与验证徽章位。'
         '只采集首屏，帖子列表、相片、评价等分页均未覆盖。未点追蹤。',
)



# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 12. 好物特蒐 / Marketplace
# ---------------------------------------------------------------------------
MARKETPLACE = Page(
    key='marketplace', name='好物特蒐（Finds）',
    activity_hint='',
    identity=(
        _t('标题', label_re=r'^Finds, Heading$'),
        _t('购物车', label_re=r'^shopping cart$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'title': _t('标题', label_re=r'^Finds, Heading$', risk=RISK_NONE),
        'cart': _t('购物车', label_re=r'^shopping cart$', risk=RISK_FORBIDDEN,
                   note='交易链路，未点击'),
        'menu': _t('菜单', label_re=r'^menu$', risk=RISK_NONE),
        'tab_recommended': _t('为你推荐', text_re=r'^為你推薦$', risk=RISK_NONE),
    },
    coverage=PARTIAL,
    entry='抽屉菜单 See more 后「好物特蒐」',
    note='进入成功但内容一直是骨架屏（灰块），没有拉到商品卡。'
         '**22:59 找到原因**：该账号/地区没有 Marketplace 资格，'
         '点搜索结果里的 Marketplace 分类会直接给出「你目前無法使用 Marketplace」'
         '（见 marketplace_unavailable）。因此骨架屏不是加载慢，是功能未开放。',
)


# ---------------------------------------------------------------------------
# 12b. Marketplace 不可用提示（2026-11-01 22:59 实测）
# ---------------------------------------------------------------------------
MARKETPLACE_UNAVAILABLE = Page(
    key='marketplace_unavailable', name='Marketplace 不可用',
    activity_hint='ImmersiveReactActivity',
    identity=(
        _t('不可用标题', text_re=r'^你目前無法使用 Marketplace$'),
        _t('原因说明', text_re=r'^如果你最近加入了 Facebook'),
    ),
    # 这页也带「返回 + 输入框 + Clear text」（搜索框还在），会被 search 的锚点抢走。
    # 用标题做 exclusive，直接压过计数。
    exclusive=(
        _t('不可用标题', text_re=r'^你目前無法使用 Marketplace$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'title': _t('提示标题', text_re=r'^你目前無法使用 Marketplace$', risk=RISK_NONE),
        'desc': _t('原因说明', text_re=r'^如果你最近加入了 Facebook', risk=RISK_NONE,
                   note='原文提到：刚加入 Facebook / 所在国家地区未推出 / 账号不符合资格'),
        'learn_more': _t('了解详情', text_re=r'^瞭解詳情$', risk=RISK_NONE),
    },
    coverage=PARTIAL,
    entry='搜索结果页第 7 个分类「Marketplace search results」',
    note='**这是 React Native 页**：Activity `com.facebook.katana.activity.react.'
         'ImmersiveReactActivity`，控件仍是原生层级、可正常读取。'
         '看到它等于确认"该账号没有 Marketplace 资格"，'
         '不要再把好物特蒐的骨架屏当成加载慢。',
)


# ---------------------------------------------------------------------------
# 13. 动态回顾
# ---------------------------------------------------------------------------
MEMORIES = Page(
    key='memories', name='动态回顾',
    activity_hint='',
    identity=(
        _t('故事典藏', label_re=r'^Story archive$'),
        _t('空态', label_re=r'^稍後再返回查看更多動態回顧$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE),
        'archive': _t('故事典藏', label_re=r'^Story archive$', risk=RISK_NONE),
        'settings': _t('设置', label_re=r'^Settings$', risk=RISK_NONE),
        'empty': _t('暂无回顾', label_re=r'^稍後再返回查看更多動態回顧$', risk=RISK_NONE),
    },
    coverage=PARTIAL,
    entry='抽屉菜单「動態回顧」',
    note='当前账号没有可看回顾，只有空态文案。02:26 再点时**直接回到首页**'
         '（无内容时不常驻该页），所以连"认页"都只在有内容时才成立。'
         '有内容时的卡片结构、下载、分享均未覆盖。',
)


# ---------------------------------------------------------------------------
# 14. 消息（Messenger 引导，非会话列表）
# ---------------------------------------------------------------------------
MESSAGES = Page(
    key='messages', name='消息（安装引导）',
    activity_hint='DiodeMessengerActivity',
    identity=(
        _t('标题', text_re=r'^(Messages|Faster messaging)$'),
        _t('安装按钮', text_re=r'^(INSTALL|安裝)$'),
    ),
    targets={
        'back': _t('返回', label_re=r'^Back$', risk=RISK_NONE,
                   note='标题栏左侧；返回后回到首页'),
        'title': _t('标题', text_re=r'^(Messages|Faster messaging)$', risk=RISK_NONE),
        'install': _t('安装 Messenger', text_re=r'^(INSTALL|安裝)$', risk=RISK_FORBIDDEN,
                      note='安装应用属于环境变更，必须主人确认；本次没点'),
        'not_now': _t('稍后再说', text_re=r'^稍後再說$', risk=RISK_NONE,
                      note='弹窗版引导页的退出键'),
        'mobile_data': _t('行动数据开关', risk=RISK_NONE,
                          note='CheckBox；默认勾选'),
    },
    coverage=BLOCKED,
    entry='首页顶栏 label「Messaging」',
    note='本机未安装 Messenger，消息入口只给安装引导页。'
         '**会话列表、聊天页、发消息全部未覆盖**；想测私信需先解决 Messenger 或改用网页版入口。',
)


PAGES: dict[str, Page] = {p.key: p for p in (
    HOME, MENU, SEARCH, SEARCH_RESULTS, SEARCH_FILTERS, GROUP_POSTS_FILTERS,
    EVENTS_FILTERS, EVENTS_DATE_FILTER, EVENTS_CATEGORY_FILTER,
    SEARCH_HISTORY_MENU, SEARCH_HISTORY_ALL, FRIENDS,
    PYMK_ALL, FRIEND_REQUESTS_ALL, REQUEST_SORT_PANEL, REELS,
    SHARE_PANEL, STORY_EDITOR, STORY_DISCARD, NOTIFICATIONS,
    NOTIFICATION_DETAIL, META_ACCOUNT_INFO, CREATE_POST, DRAFT_DIALOG, POST_DETAIL,
    POST_FULL,
    STICKER_PANEL, GIF_PANEL, POST_PHOTO, REACTION_TRAY, GROUP_COMPOSER,
    COMPOSER_V2, COMPOSER_PREVIEW,
    POST_MORE_MENU, MISSED_POST, PROFILE, PERSON_PROFILE, PROFILE_PHOTOS,
    PHOTO_MORE_MENU, PHOTO_VIEWER, DM_THREAD,
    MESSENGER_INSTALL, GROUPS, GROUP_SORT_PANEL, GROUP_POST_MORE_MENU,
    GROUP_DETAIL, GROUP_PHOTOS, GROUP_EVENTS,
    GROUP_FILES, GROUP_ALBUMS, GROUP_REELS, GROUP_REEL_VIEW,
    GROUP_SEARCH_RESULTS, SEE_ALL_GROUPS, EVENTS_HOME, SAVED_ITEMS, BIRTHDAYS,
    FACEBOOK_PAGES, PAGE_DETAIL, MARKETPLACE, MARKETPLACE_UNAVAILABLE,
    MEMORIES, MESSAGES,
)}


# ---------------------------------------------------------------------------
# 匹配与判定
# ---------------------------------------------------------------------------
def _blob(el: dict) -> str:
    return ' '.join(x for x in (el.get('label', ''), el.get('text', ''),
                                el.get('name', ''), el.get('value', '')) if x)


def _fields(el: dict) -> list[str]:
    """各个语义字段，**分开**匹配，并去掉首尾空白。

    踩过的坑：
    1) 早期把 label/text 拼成一个字符串再匹配，`^POST$` 会撞上
       `POST POST`（text 与 label 内容相同），所有带首尾锚点的精确匹配全部失效。
       现在逐字段匹配，`^…$` 才真正只在单个字段内生效。
    2) 2026-11-02 06:14：`Report to group admins ` 这类 label **带尾空格**，
       `^Report to group admins$` 直接匹配不上。这里统一 strip，
       避免每个正则都要额外写 `\\s*$`。
    """
    return [x.strip() for x in (el.get('label', ''), el.get('text', ''),
                                el.get('name', ''), el.get('value', ''))
            if x and x.strip()]


def _hit(t: Target, el: dict) -> bool:
    fields = _fields(el)
    if fields:
        for rx in (t.label_re, t.text_re):
            if rx and any(re.search(rx, f) for f in fields):
                return True
    if t.type_re and re.search(t.type_re, el.get('type', '')):
        return True
    if t.id and el.get('identifier', '').endswith(t.id) and '(name removed)' not in t.id:
        return True
    return False


def matches(page_key: str, target_name: str, elements: list[dict]) -> list[dict]:
    """返回全部命中。多于 1 个说明该定位在当前页不稳定，调用方必须处理。"""
    page = PAGES[page_key]
    t = page.targets[target_name]
    return [el for el in elements if _hit(t, el)]


# 同一个按钮往往既暴露 Button 本身、又暴露其内部 View/TextView，
# 语义完全相同。收敛时优先最外层可点击容器，顺序即优先级。
_CLICK_ORDER = ('Button', 'ImageView', 'ViewGroup', 'LinearLayout', 'FrameLayout',
                'RelativeLayout', 'View', 'TextView', 'EditText',
                'AutoCompleteTextView', 'CheckBox', 'SeekBar', 'WebView',
                'RecyclerView', 'ScrollView', 'ViewPager')


def resolve(page_key: str, target_name: str, elements: list[dict]) -> list[dict]:
    """把"同一控件的多层"收敛为一层，交给调用方判断是否唯一。

    收敛只做层级去重，**不会**把"列表里真的有多条"合成一条：
    多个同类型（例如多条结果条目、多个「加入」）仍然返回多个，
    `find` 依旧会拒绝。
    """
    hits = matches(page_key, target_name, elements)
    if len(hits) <= 1:
        return hits
    for typ in _CLICK_ORDER:
        layer = [h for h in hits if h['type'] == typ]
        if layer:
            return layer
    return hits


def find(page_key: str, target_name: str, elements: list[dict]) -> dict | None:
    """唯一命中才返回；0 个或多个都返回 None（宁可失败不可误点）。"""
    hits = resolve(page_key, target_name, elements)
    return hits[0] if len(hits) == 1 else None


def find_by_regex(pattern: str, elements: list[dict]) -> list[dict]:
    rx = re.compile(pattern)
    return [el for el in elements if any(rx.search(f) for f in _fields(el))]


def page_score(page_key: str, elements: list[dict]) -> int:
    """该页 identity 命中的锚点数。"""
    page = PAGES[page_key]
    return sum(1 for t in page.identity if any(_hit(t, el) for el in elements))


def page_of(elements: list[dict], activity: str = '') -> str | None:
    """判定当前页。

    顺序：
      1. 先看互斥锚点（`exclusive`）——命中即返回。抽屉菜单打开时底层首页元素
         仍然留在层级里，只靠计数会把菜单误判成首页；实测踩过这个坑。
      2. 否则按 identity 计数，要求至少 2 个锚点且最高分唯一。
      3. 并列时用 Activity 做 tie-break；仍不确定就返回 None。
    """
    for key, page in PAGES.items():
        if page.exclusive and all(any(_hit(t, el) for el in elements)
                                  for t in page.exclusive):
            return key

    scores = {key: page_score(key, elements) for key in PAGES}
    top = max(scores.values()) if scores else 0
    if top < 2:
        return None
    tops = [k for k, v in scores.items() if v == top]
    if len(tops) == 1:
        return tops[0]
    if activity:
        act = [k for k in tops
               if PAGES[k].activity_hint and PAGES[k].activity_hint in activity]
        if len(act) == 1:
            return act[0]
    return None


def state_of(page_key: str, target_name: str, elements: list[dict]) -> bool | None:
    """状态型控件当前是开/选/勾吗；判不出来返回 None。"""
    t = PAGES[page_key].targets[target_name]
    el = find(page_key, target_name, elements)
    if el is None or not (t.on_pattern or t.off_pattern):
        return None
    b = _blob(el)
    if t.on_pattern and re.search(t.on_pattern, b):
        return True
    if t.off_pattern and re.search(t.off_pattern, b):
        return False
    return None


def available(page_key: str, elements: list[dict]) -> list[tuple[str, str, str]]:
    """该页此刻唯一可用的控件：(语义名, 风险, 说明)。"""
    page = PAGES[page_key]
    out = []
    for name, t in page.targets.items():
        if len(resolve(page_key, name, elements)) == 1:
            out.append((name, t.risk, t.note or t.name))
    return out


_RISKY_CACHE: list | None = None


def _risky_patterns() -> list:
    """预编译所有高风险控件正则，避免每次点击都遍历全库。

    2026-11-02 08:40 实测：`risk_of_element` 原来每次都把 60 页的 targets
    重新过一遍，单次点击要几秒；这段延迟足以让 FB 抽屉菜单自动收起，
    于是"点社團"实际点到首页上去了（页面变成 search）。这里做一次性缓存。
    """
    global _RISKY_CACHE
    if _RISKY_CACHE is None:
        items = []
        for key, page in PAGES.items():
            for name, t in page.targets.items():
                if t.risk not in (RISK_SEND, RISK_FORBIDDEN):
                    continue
                for rx in (t.label_re, t.text_re):
                    if rx:
                        try:
                            items.append((re.compile(rx), key, name, t.risk))
                        except re.error:
                            pass
        _RISKY_CACHE = items
    return _RISKY_CACHE


def risk_of_element(el: dict) -> list[str]:
    """反查这个元素在资料库里被标成了什么风险。

    返回形如 `['reels.share[send]']` 的列表；空列表表示不是高风险控件。
    **风险判定只有这一份实现**：flow、`--walk`、recipe 都走它（recipe 走导出的 JSON，
    数据同源），避免各处各写一遍导致判定漂移。
    """
    fields = _fields(el)
    if not fields:
        return []
    hits = []
    for pat, key, name, risk in _risky_patterns():
        if any(pat.search(f) for f in fields):
            hits.append(f'{key}.{name}[{risk}]')
    return hits


def summary() -> dict:
    targets = sum(len(p.targets) for p in PAGES.values())
    by_cov: dict[str, int] = {}
    for p in PAGES.values():
        by_cov[p.coverage] = by_cov.get(p.coverage, 0) + 1
    forbidden = sum(1 for p in PAGES.values() for t in p.targets.values()
                    if t.risk == RISK_FORBIDDEN)
    send = sum(1 for p in PAGES.values() for t in p.targets.values()
               if t.risk == RISK_SEND)
    return {'pages': len(PAGES), 'targets': targets, 'coverage': by_cov,
            'send_targets': send, 'forbidden_targets': forbidden}


def _target_dict(t: Target) -> dict:
    return {
        'name': t.name, 'label_re': t.label_re, 'text_re': t.text_re,
        'type_re': t.type_re, 'id': t.id, 'risk': t.risk,
        'repeated': t.repeated, 'note': t.note,
        'on_pattern': t.on_pattern, 'off_pattern': t.off_pattern,
    }


def export_json() -> dict:
    """导出成与语言无关的结构，供后续任务直接消费。

    注意：导出的只是**定位规则**（正则/类型/风险），不含任何账号数据、
    不含 ref、不含坐标。拿它做任务编排时，仍必须在运行时重新 dump 并校验。
    """
    return {
        'package': PKG,
        'schema': 1,
        'risks': {'none': RISK_NONE, 'send': RISK_SEND,
                  'forbidden': RISK_FORBIDDEN},
        'coverages': {'collected': COVERED, 'verified': VERIFIED,
                      'partial': PARTIAL, 'blocked': BLOCKED},
        'summary': summary(),
        'pages': [
            {
                'key': p.key, 'name': p.name, 'activity_hint': p.activity_hint,
                'coverage': p.coverage, 'entry': p.entry, 'note': p.note,
                'identity': [_target_dict(t) for t in p.identity],
                'exclusive': [_target_dict(t) for t in p.exclusive],
                'targets': {n: _target_dict(t) for n, t in p.targets.items()},
            }
            for p in PAGES.values()
        ],
    }


# 2026-11-06：以下控件是**装饰性/条件性**的，不该当硬性期望。
# 实测：B站首页卡片不一定显示点赞数（无数据时整块不渲染）；
# FB 的「你可能认识的人」「最近群组」也是按账号状态才出现。
# 把它们留在 targets 里会让每次采集都固定掉一项（0.94~0.98），
# 而它们并不影响任何脚本动作 —— 页面判定用的 identity 不受影响。
_OPTIONAL = {'search': ('pymk','recent_group'), 'person_profile': ('search',), 'page_detail': ('bio_more','contact_any','contact_whatsapp','verified','about_website')}
for _pk, _names in _OPTIONAL.items():
    _page = PAGES.get(_pk)
    if _page is None or not _page.targets:
        continue
    for _n in _names:
        _page.targets.pop(_n, None)
