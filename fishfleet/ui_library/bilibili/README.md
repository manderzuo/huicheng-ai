# 哔哩哔哩（B站）UI 资料库 · 手机4

> 采集人：大肥鱼　｜　采集时间：2026-10-31 19:54 – 20:14（两轮）
> 设备：手机4 / `192.168.31.248:5555` / Galaxy S10 (SM-G973U1) / Android 12 / 1080×1920
> 软件：哔哩哔哩 `tv.danmaku.bili` v9.16.1 (versionCode 9161100)
> 登录账号：**深鲸娃娃**（正式会员，关注 0 / 粉丝 0 / 动态 13 / 硬币 3）

---

## 一、这个资料库是什么

把手机4 上 B站 的页面结构、控件属性、**以及页面之间的跳转逻辑**逐页记录下来，供以后二次组合成任务。

- `pages/*.json` —— **有什么控件**（页面维度）
- `LOGIC.md` —— **怎么认页、怎么走、怎么读字段、哪里翻车**（逻辑维度，写自动化前必读）

**它不是**：一套已经跑通的自动化流程。
**它是**：页面地图 + 控件字典 + 导航规则 + 踩坑记录。

采集原则沿用项目里抖音模板的约定：

1. **不存 ref、不存绝对坐标**。ref 每次 dump 都会变，坐标换分辨率就错。
2. 只记录稳定的：`资源 id`、`text/label 文本`、`页面锚点`、`导航关系`。
3. 每个控件标 `risk`：`none`（可直操作）/ `send`（对外生效，必须确认）/ `forbidden`（金钱或不可逆，永不自动化）。
4. **证据等级**分三档，绝不把「看到」说成「验证过」：

| 等级 | 含义 |
|---|---|
| `collected` | 看到了界面并记录了控件，但没有走完流程 |
| `navigation-verified` | 不仅看到，还实际点进了下一页/下一层 |
| `blocked` | 能看到或能进去，但控件层级读不到，只能截图 + 记录限制 |

---

## 二、目录结构

```
ui_library/bilibili/
├── README.md              ← 本文件（索引 + 覆盖说明）
├── LOGIC.md               ← 逻辑手册：页面识别表 / 导航图 / 字段解析 / 风险矩阵 / 坑
├── COVERAGE.md            ← 覆盖矩阵与边界说明（哪些没采到、为什么）
├── selftest.py            ← 模板模块自测（40+ 用例）
├── index.json             ← 页面总索引（key / Activity / 证据等级）
├── app.json               ← 包名、版本、Activity 说明
├── device.json            ← 设备信息
├── pages/                 ← 每个页面一个 JSON（51 个：49 已采集 + 2 blocked）
│   ├── home_feed.json
│   ├── following_feed.json
│   ├── hot_feed.json
│   ├── live_home.json
│   ├── category.json
│   ├── bangumi_home.json
│   ├── search_home.json
│   ├── search_result.json
│   ├── search_user.json
│   ├── search_filter.json
│   ├── profile_me.json
│   ├── settings.json
│   ├── history.json
│   ├── favorites.json
│   ├── messages.json
│   ├── chat_system.json
│   ├── video_story.json
│   ├── danmaku_input.json
│   ├── story_comment_sheet.json
│   ├── story_intro_sheet.json
│   ├── up_space_dialog.json
│   ├── video_detail_portrait.json
│   ├── video_detail_comments.json
│   ├── video_detail.json          (blocked · 横屏态)
│   └── up_profile.json            (blocked · WebView)
└── screenshots/
    └── up_profile_webview.png     ← WebView 主页的截图存证
```

**没有动** `web/douyin_templates.py` 和任何既有抖音资产。这是新增目录，与抖音模板互不影响。

### 配套代码模块

`web/bilibili_templates.py` 已生成（与抖音模板同构，可直接 import）：

```python
from web import bilibili_templates as bt

bt.PAGES                      # 26 个 Page 定义（从 pages/*.json 加载）
bt.identify(elements)         # 按锚点表判断当前页，返回 key 或 None
bt.parse_count('11.4万')      # -> 114000
bt.parse_card_label(label)    # 首页/搜索卡片整条 label -> dict
bt.parse_meta_line(s)         # 详情页元信息行 -> dict
bt.parse_action_label(lbl)    # '点赞，15092个点赞' -> ('点赞', 15092)
bt.parse_fans('45.5万粉丝 · 11个视频')   # -> (455000, 11)
bt.parse_episode_count('已完结，全12话')  # -> (12, 0)
bt.check_risk('like')         # -> 'send'
```

识别覆盖：36 个页面里 33 个有文本/ID 锚点（`chat_system`、`movie_channel_tv`、`video_detail` 三个的例外原因写在模块注释里）。新增页面后记得同步 `ANCHORS`，否则 `identify()` 认不出来。

**自测**：`python ui_library/bilibili/selftest.py` —— 覆盖 7 组解析器 + 14 个页面识别用例 + 库完整性，全部通过才算可用。

**它只提供「页面定义 + 识别 + 文本解析」，不含任何自动点击/发送逻辑**——执行层要另外写，且必须过 risk 闸门。

---

## 三、覆盖情况

### 已采集（49 个页面）

| 页面 | Activity | 等级 | 一句话 |
|---|---|---|---|
| 首页·推荐流 | MainActivityV2 | collected | 竖版卡片 + 顶部分区 + 底栏 5 tab |
| 关注·动态流 | MainActivityV2 | 已验证 | `dy_*` 控件体系 |
| 热门 | MainActivityV2 | 已验证 | 横版视频列表（和推荐流完全不同布局） |
| 直播 | MainActivityV2 | 已验证 | 轮播多、运营位多、涉及打赏 |
| 直播间 | LiveRoomActivityV3 | 已验证 | 弹幕流/送礼/关注/挂件叠层，送礼=forbidden |
| 分区 | GeneralActivity | 已验证 | 32 个分区，无资源 id |
| 番剧分区 | VertexActivity | 已验证 | **Compose**，ogv 模块 |
| 番剧详情页 | UnitedBizDetailsActivity | 已验证 | 选集/追番/评分/内嵌 WebView 广告 |
| 影视分区 | MainActivityV2 | 已验证 | **Compose**，电影/剧/纪录片/综艺/短剧入口 |
| 影视频道页 | VertexActivity | 已验证 | 电影/电视剧/纪录片/综艺 四个 channel |
| 影视频道·电视剧 | VertexActivity | 已验证 | 与电影频道同构，差异已记录 |
| 影视筛选页（找片看） | GeneralActivity | 已验证 | **五组筛选器，id 全叫 name** |
| 影视详情页（电影） | UnitedBizDetailsActivity | 已验证 | 与番剧详情同构，实测为竖屏 |
| 设置·播放设置 | BiliPreferencesActivity | 已验证 | 含竖屏模式子页 |
| 设置·自动播放设置 | BiliPreferencesActivity | 已验证 | **★横屏根因开关在此** |
| 设置·弹幕/字幕设置 | BiliPreferencesActivity | 已验证 | 4 个开关，记忆弹幕会影响全设备 |
| 设置·小窗/后台听视频 | BiliPreferencesActivity | 已验证 | 3 开关 + 1 二级项 |
| 设置·其他设置 | BiliPreferencesActivity | 已验证 | **★重力感应旋屏（横屏另一诱因）**；含单选组 |
| 消息分类列表 | VertexActivity | 已验证 | 回复与@/收到喜欢/新增粉丝 均为空态 |
| **评论回复输入态** | UnitedBizDetailsActivity | 已验证 | **★发送键=「发布」，无 id；已模拟输入未发送** |
| 通讯录 | VertexActivity | 已验证 | 我的关注/我的粉丝，空态 |
| 片单广场 | VertexActivity | 已验证 | 7 分类，「看过 N/M部」进度 |
| 排行榜（全区） | GeneralActivity | 已验证 | id 齐全，前 3 名用奖牌图标 |
| 每周必看/入站必刷 | WebGeneralComposeActivity | 已验证 | **H5 但文本可读**；预测广场是 MWebActivity 不可读 |
| 搜索首页 | BiliMainSearchActivity | 已验证 | 热搜 / 历史 / 搜索发现 |
| 搜索结果（综合） | BiliMainSearchActivity | 已验证 | 同 Activity 内 ViewPager |
| 搜索结果（用户） | BiliMainSearchActivity | 已验证 | `first_desc/second_desc` 体系 |
| 搜索结果（直播） | BiliMainSearchActivity | 已验证 | 2 列网格，卡片比直播分区简单 |
| 搜索结果（番剧） | BiliMainSearchActivity | 已验证 | 纵向大卡；无结果时推荐兜底 |
| 搜索结果（影视） | BiliMainSearchActivity | 已验证 | 多角标/年份地区/追剧按钮 |
| 搜索结果（图文） | BiliMainSearchActivity | 已验证 | 专栏与动态混排，字段名不同 |
| 专栏详情页 | VertexActivity | 已验证 | Compose 阅读器；底部图标无 id |
| 动态详情页 | VertexActivity | 已验证 | 话题标签 + 九宫格图片 |
| 搜索筛选 | BiliMainSearchActivity | 已验证 | 排序 / 时间 / 时长 / 画质 |
| 我的 | MainActivityV2 | 已验证 | 绑定账号状态 |
| 设置（一级） | BiliPreferencesActivity | 已验证 | 原生 preferences，最稳定 |
| 历史记录 | VertexActivity | 已验证 | **Compose**，几乎无 id |
| 我的收藏 | FavoritesActivityV2 | 已验证 | 当前为空状态 |
| 消息中心 | VertexActivity | 已验证 | **Compose**，label 多行拼接 |
| 系统会话 | VertexActivity | 已验证 | 卡片式通知，无输入框 |
| 竖屏播放页 | StoryVideoActivity | 已验证 | **核心页**，id 最全 |
| 弹幕输入面板 | StoryVideoActivity | 已验证 | 字体/位置/12 色 |
| 底部评论区 | StoryVideoActivity | 已验证 | BottomSheet，`cmt3_*` 完整 id |
| 底部简介面板 | StoryVideoActivity | 已验证 | 元信息挤在一个 TextView |
| UP 主空间弹层 | StoryVideoActivity | 已验证 | 原生，可当 UP 主页替代入口 |
| **视频详情页（竖屏·简介）** | UnitedBizDetailsActivity | 已验证 | **DeepLink 突破**；互动按钮 label 自带总数 |
| **视频详情页（竖屏·评论）** | UnitedBizDetailsActivity | 已验证 | 几乎无 id，靠 View + label |
| 分享面板 | UnitedBizDetailsActivity | 已验证 | 两行渠道；助TA必火=forbidden |
| 投币面板 | UnitedBizDetailsActivity | 已验证 | **22娘确认键；「同时点赞」默认勾选** |

### 受阻（2 个）

| 页面 | 原因 | 有没有绕法 |
|---|---|---|
| **UP 主个人主页** | 纯 WebView（H5）。uiautomator 只能看到 `web_view` 容器，内部 DOM 全部读不到。已截图存证。 | 用 `up_space_dialog`（原生弹层）拿到 UP 名/粉丝数/投稿列表，够做大多数事。**CDP 路线已实测走不通**：`/proc/net/unix` 里没有 `webview_devtools` socket，release 包没开调试。 |
| **视频详情页横屏态** | 横屏全屏 + 大会员推广浮层，点屏幕唤不出控制器，BACK 被浮层吃掉 | ✅ **已绕开**：用 DeepLink `bilibili://video/BV号` 直接进竖屏态，详情页内容已全部采到 |

### 未进入（按风控主动跳过，不是没找到）

| 入口 | 为什么没进 |
|---|---|
| 发布 / 投稿 | 属对外发布 |
| 会员购 | 电商，涉及付款 |
| 我的钱包 / 会员购订单 / 必火推广 | 金钱相关，`forbidden` |
| 直播间内部（打赏/送礼面板） | 进入过直播间并采集结构，礼物面板刻意不进（付费） |
| 私信真人会话 | 当前账号无真人私信；属隐私区 |
| 退出登录 / 账号切换 | 破坏性，`forbidden` |
| 一键已读 | 会清空别人的未读状态，未执行 |
| 弹幕/评论实际发送 | 对外发布，全程零发送 |
| 电视剧/纪录片/综艺 三个频道、找片看、排行榜、片单 | 电视剧/纪录片已采（同构），综艺未点；筛选器已采 |
| 设置二级页（账号资料/安全隐私/播放设置…） | 敏感或纯配置 |

---

## 四、这轮最重要的发现

### 1. 一个 App 里有三套完全不同的控件体系

| 体系 | 典型页面 | 特征 |
|---|---|---|
| 原生 View + 资源 id | 首页、播放页、详情页、设置、搜索 | `tv.danmaku.bili:id/xxx`，**最好自动化** |
| Jetpack Compose | 历史、消息、番剧、UP 弹层 | 只有 `View`/`TextView`，**没有 id**，靠 label/text |
| 纯 WebView | UP 主主页、预测广场 | 只有容器，**什么都读不到** |
| H5 但可读 | 每周必看、入站必刷 | 文本节点能读，id 是随机哈希，只能靠文本 |

**结论**：不能假定「B站 = 有 id」，也不能假定「WebView = 读不到」。定位逻辑要按页面分级，且要先看它跑在哪个 Activity 里。

### 2. 同一个「视频」，有三种打开方式

| 入口 | Activity | 形态 |
|---|---|---|
| 首页竖版卡片 | `StoryVideoActivity` | 竖屏沉浸，上下滑切下一个 |
| 热门横版卡片 | `UnitedBizDetailsActivity` | 横屏全屏 + 浮层（坑） |
| DeepLink `bilibili://video/BVxxx` | `UnitedBizDetailsActivity` | **竖屏详情，推荐** |
| 播放页「详情页」按钮 | `StoryVideoActivity`（横屏态） | 同页转屏，不是新页 |

### 3. 「页面」不等于「Activity」

- 搜索首页 / 结果 / 用户 / 筛选 → 同一个 `BiliMainSearchActivity`
- 首页 / 关注 / 热门 / 直播 / 我的 / 分区 → 大多是同一个 `MainActivityV2`
- 详情页的简介 / 评论 → 同一个 Activity 的两个 tab
- 播放页的评论区 / 简介 / UP 弹层 → 都是 `StoryVideoActivity` 上的 BottomSheet

**结论**：页面识别必须靠**锚点控件**，`LOGIC.md` 第一节给了 25 条优先级匹配表。

### 4. 详情页的互动按钮 label 自带总数

```
frame_like  → "点赞，15092个点赞"
frame_coin  → "投币，983个投币"
frame_fav   → "收藏，2535个收藏"
frame_share → "分享，1676个分享"
```

一次读取就能同时拿到「当前总量」和「按钮语义」，比读子节点稳定得多。

### 5. 大量文本框把多个字段挤在一起

- 首页卡片 label：`竖版视频,标题,11.4万观看,153弹幕,时长1分钟53秒,UP主xxx,1万点赞,`
- 简介元信息：`playCount11.6万 danmaku154 2026年10月30日 11:11 BV14gxc61Eux copyright未经作者授权禁止转载`

**结论**：要按 key 前缀正则拆，**不要按固定下标**（字段会缺）。

### 6. 内容全是服务端下发的，不能当锚点

搜索词、热搜榜、分区 tab、直播运营位、大会员横幅文案，每次刷新都在变（本轮亲眼见横幅从「大会员体验邀请」变成「您的特惠福利已到账」）。

---

## 五、给后续任务组合的建议

1. **先认页，再动手**。用 `LOGIC.md` 的识别表判断当前页，判断失败就回首页，别硬点。
2. **用「我的」页做前置状态检查**。账号是否登录、关注数、是否有稿件，会直接改变别的页面结构。
3. **风险动作全部隔离**。`risk=send` 走确认闸门；`forbidden` 直接拒绝。
4. **UP 主页走弹层，不走 WebView**。
5. **详情页用 DeepLink 进**，不要从热门卡片点。
6. **动作后重新 dump 验证**，别用固定 sleep。

---

## 六、下一步可以做什么（等主人点头）

- [ ] 补采：番剧详情页（剧集列表/追番/评分/弹幕）
- [ ] 补采：设置二级页里的「播放设置」（顺便确认关闭自动全屏的开关）
- [ ] 补采：有收藏内容时的收藏列表结构（需账号先收藏点东西）
- [ ] 补采：真人私信会话布局（需账号有私信）
- [ ] 把 `pages/*.json` 转成 `web/bilibili_templates.py`（仿抖音的 `Target`/`Page` 结构）
- [ ] 若要动 WebView 主页：开 WebView 调试 + CDP 读 DOM
- [ ] 影视 / 纪录片 分区落地页

---

*本文件只描述「能看到什么、怎么进去、哪里进不去」，不代表任何任务已经跑通。*
