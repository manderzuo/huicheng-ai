# Facebook 页面／控件采集资料

> 增量观察记录，**不是完整任务实现**，也不表示已覆盖所有页面。
> 页面受账号、邮箱验证状态、权限、地区、语言、内容与操作状态影响。
> 任何动作运行前都必须重新 dump 层级、重新确认页面与控件唯一性。
> **不要持久化 ref，也不要持久化坐标。**

## 环境与范围

- 采集时间：2026-11-01 20:42–20:53（Asia/Shanghai；手机状态栏时间与主机一致）。
- 指定手机：主人指定「手机8」，按 `huicheng-auto/data/device_labels.json` 解析为
  `SERIAL0001`；Mobile MCP 报告 Galaxy S10、SM-G973U1、Android 12（真机）。
- 应用：Facebook，包名 `com.facebook.katana`；`dumpsys package` 读到
  `versionName=530.0.0.48.74`（主包）。
- 账号：Mander Zuo（个人档案）。**首页出现「確認電子郵件地址」提示**，说明邮箱未验证；
  该状态会影响部分功能入口，属于环境限制而不是控件缺失。
- 界面语言：系统为繁体中文，Facebook 文案中英繁混排（`Friends`、`Friend requests`
  与 `建議`、`你的朋友` 同屏）。所有匹配都按「中英双写正则」处理。
- 本轮全程只做浏览、打开、返回；**未发布任何内容、未点赞、未关注、未评论、未发私信、
  未安装应用、未改动任何账号设置**。

## 关键结构结论（决定了以后怎么定位）

1. **资源 ID 不可用**：`com.facebook.katana:id/(name removed)` 大面积出现，
   同一字符串在不同页重复。定位必须靠无障碍 label / text。
2. **底栏 tab 自带语义**：`Home, tab 1 of 4`、`Friends, tab 2 of 4`、
   `Reels, tab 3 of 4`、`Notifications, tab 4 of 4`（未读时追加 `, 2 new`）。
   角标数字是动态的，正则必须容忍缺省。
3. **抽屉菜单文字是异步渲染的**：第一次 dump 只看到无文字的 ViewGroup，
   稍后同一位置就能读到 `動態回顧`、`社團` 等 text。**先等待再 dump**，
   否则会误判成「控件没有文字」。
4. **Activity 粒度很粗**：搜索、社团、通知详情都落在
   `immersiveactivity.ImmersiveActivity`；个人主页是 `ImmersivePortraitActivity`；
   消息是 `orca.diode.DiodeMessengerActivity`。**单靠 Activity 无法区分页面**，
   必须叠加页面锚点。
5. **覆盖层分两种，判定方式完全不同**：
   - **半覆盖**（抽屉菜单、草稿对话框、分享面板）：底层页面元素仍在层级里，
     靠计数会判错，必须用 `Page.exclusive` 互斥锚点压过计数。
   - **全屏覆盖**（贴纸面板、GIF 面板）：底层元素被回收，锚点全部消失，
     必须**独立成页**，不能挂在父页下面当子面板。
   判断办法很简单：打开面板后 dump 一次，看父页的关键锚点还在不在。

## 已采集页面

| 页面 | 实测进入方式 | 前台包名／Activity | 覆盖状态 |
|---|---|---|---|
| 首页动态流 | App 启动默认页 | `com.facebook.katana` / `FbMainTabActivity` | 已采 + 跳转实测 |
| 左侧抽屉菜单 | 首页 `Facebook menu` | 同上（抽屉覆盖，底层首页仍在层级） | 已采 + 跳转实测 |
| 搜索页（空态） | 首页 `Search Facebook` | `immersiveactivity.ImmersiveActivity` | 部分（未输入关键词） |
| 好友页 | 底栏 `Friends, tab 2 of 4`；或菜单 `尋找朋友` | 同上 | 已采 |
| Reels | 底栏 `Reels, tab 3 of 4` | 同上 | 已采（仅一条视频） |
| 通知列表 | 底栏 `Notifications, tab 4 of 4` | 同上 | 已采 + 跳转实测 |
| 通知详情 | 通知列表点条目 | 同上 | 部分（WebView） |
| 发帖编辑器 | 首页 `Make a post on Facebook` | 同上 | 已采（只打开未发布） |
| 动态详情／评论区 | 首页动态点 `N comments` | 同上 | 已采（未互动） |
| 个人主页 | 首页 `Go to profile` | `immersiveactivity.ImmersivePortraitActivity` | 已采 + 跳转实测 |
| 社团（探索） | 菜单 `社團` | `immersiveactivity.ImmersiveActivity` | 已采 |
| 好物特蒐 Finds | 菜单 See more → `好物特蒐` | 同上 | 部分（内容骨架屏） |
| 动态回顾 | 菜单 `動態回顧` | `FbMainTabActivity`（覆盖层） | 部分（空态） |
| 消息 | 首页 `Messaging` | `orca.diode.DiodeMessengerActivity` | **阻塞**（未装 Messenger） |

---

## 1. 首页动态流

- Activity：`com.facebook.katana.activity.FbMainTabActivity`。
- 页面锚点组合：底栏 `Home, tab 1 of 4` + `Make a post on Facebook` + `Stories`。
  任一单独命中不足以证明在该页（Stories 可能整块不存在）。

| 控件／用途 | 类型 | 文本或无障碍标签 | 状态／证据 |
|---|---|---|---|
| 抽屉菜单 | Button | `Facebook menu` | 已观察；点击进入菜单，实测 |
| 新建 | Button | `Create, Double tap to create a new post, story, or reel` | 已观察，未点击 |
| 搜索 | Button | `Search Facebook`（部分版本为 `Search`） | 已观察；进入搜索页，实测 |
| 消息 | Button | `Messaging` | 已观察；进入安装引导页，实测 |
| 底栏 tab | View | `Home, tab 1 of 4` / `Friends, tab 2 of 4` / `Reels, tab 3 of 4` / `Notifications, tab 4 of 4[, N new]` | 首页 selected；其余三个均已实测切换 |
| 发帖入口 | ViewGroup | `Make a post on Facebook` | 已观察；打开 Create post，实测 |
| 发帖带图 | ViewGroup | `Select photos or videos for your post` | 已观察，未点击 |
| 限时动态区 | ViewGroup | `Stories` | 已观察；内容随账号变化 |
| 创建限时动态 | Button | `Create story` | 已观察，未点击（对外发布） |
| 个人主页入口 | Button | `Go to profile` | 已观察；进入个人主页，实测 |
| 好友推荐卡 | ViewGroup + Button | 作者名 + `Add friend` | 已观察；`Add friend` 改变账号状态，未点击 |
| 动态卡 | ViewGroup／Button | 作者名、`Sponsored•Shared with: 所有人`、正文、`N`／`N comments`／`N shares`、`Like button…`／`Comment`／`Share` | 已观察；计数与正文都是动态内容 |
| 广告操作 | Button | `See more options`、`Hide ad` | 已观察，未点击 |
| 邮箱验证提示 | Button | `確認電子郵件地址`、`新增其他電子郵件地址` | **账号级状态**；出现即说明未验证，未点击 |
| **邮箱确认横幅**（04:24 补采） | TextView＋Button | `再點擊一下就能確認 <邮箱>` + 说明文案 + `Close` | 横幅文案里带**账号真实邮箱**，模板只按前缀 `再點擊一下就能確認` 匹配、**不记录具体地址**；确认/新增都是 forbidden，`Close` 只是关横幅 |

- 不可当锚点：任何作者名、正文、赞数、广告名。它们全部是内容。
- **重要**：往下滚动后，**顶栏和底栏 tab 会一起自动隐藏**（沉浸式），
  `Facebook menu`／`Facebook logo`／`Home, tab 1 of 4` 全部从层级里消失。
  此时只能靠信息流自身特征兜底（`Sponsored•Shared with: ` + `Hide ad`）。
  自动化如果想点顶栏，必须先把列表滚回顶部。
- 未覆盖：上滑无限加载后的结构、Stories 全屏播放、广告详情、`Create` 的二级菜单。

## 2. 左侧抽屉菜单

- 进入：首页 `Facebook menu`（左上角）。实测抽屉从左侧推出，宽度约 964/1080，
  右侧保留半透明遮罩 `Close`，底层首页元素仍在层级里。
- 页面锚点组合：`Close` + `See more`/`See less` + 任意网格项的 text。
  **不要用底层首页的锚点判断菜单是否打开。**

| 网格项（11-01 20:51 首次采集） | 展开后（当时） |
|---|---|
| 動態回顧、我的珍藏 | + 好物特蒐、生日 |
| 社團、Reel | + 讚、粉絲專頁 |
| 動態消息、尋找朋友 | |
| 遊戲、活動 | |

#### 菜单项的内容和顺序**会变**（03:30 重要修正）

| 时间 | 菜单项（未展开） |
|---|---|
| 11-01 20:51 | 動態回顧 / 我的珍藏 / 社團 / Reel / 動態消息 / 尋找朋友 / **遊戲** / 活動 |
| 11-02 03:30 | 社團 / 動態回顧 / 尋找朋友 / **好物特蒐** / 我的珍藏 / Reel / 活動 / 動態消息 |

- `遊戲` **消失了**：等了 4 秒仍不出现，说明该账号当前没这个入口
- `好物特蒐` 从"See more 展开后才有"变成**默认就在**
- **顺序完全变了**

所以：**只能按文本定位，绝不能用"第 N 个格子"或坐标**；
等几秒仍找不到的项，应判断为"该账号当前没有这个入口"，而不是"渲染慢"。

**并且 exclusive 会失效**（04:29 实测）：菜单滚动后 `Settings & privacy` 节头滚出屏幕，
原来靠它做的互斥判定失效、页面被判成 `home`（危险）。已给菜单补 6 个菜单项作为计数锚点，
任何滚动位置都够分。

**`讚` 和 `遊戲` 都已不在菜单里**（05:19 复核）：展开后只剩
`好物特蒐` / `活動` / `粉絲專頁` / `生日` 四项。这两个入口**未进入**，
判断为"该账号当前没有"，而不是"没找到"。


| 其它控件 | 类型 | 文本或标签 | 说明 |
|---|---|---|---|
| 当前账号 | Button | `<昵称>，請查看你的個人檔案` | 昵称是动态值，用后缀锚定 |
| 个人档案切换 | Button | `開啟個人檔案切換工具` | 未点击 |
| 建立粉丝专页 | Button | `建立 Facebook 粉絲專頁` | 未点击 |
| 展开更多 | Button | `See more` → `See less` | **实测展开**，新增 4 项 |
| 帮助与支持 | Button | `Help & support, header. Section is collapsed…` | 折叠节，未展开 |
| 设置与隐私 | Button | `Settings & privacy, header. Section is collapsed…` | 折叠节，未展开 |
| 升级 | ViewGroup | `升級` + `公開據點` / `Meta 驗證` | 涉及付费验证，未点击 |
| Meta 旗下产品 | ViewGroup | `Meta 旗下產品` + `Edits`/`Threads`/`Instagram`/`Messenger` | 外链，未点击 |

#### `Settings & privacy` 展开项（23:06 实测，只展开未进入）

展开后头部 label 从 `Section is collapsed` 变成 `Section is expanded`，新增 6 项：

| 入口 | 文本 | 风险 |
|---|---|---|
| 设置 | `Settings` | **forbidden**（内含改密码/停用账号/退出登录） |
| 隐私中心 | `Privacy Center` | none（未进入） |
| 装置要求 | `裝置要求` | **forbidden**（设备授权） |
| 近期广告动态 | `近期廣告動態` | none（未进入） |
| 订单和付款 | `訂單和付款` | **forbidden**（交易链路） |
| 链接历史 | `Link history` | none（未进入） |

- `Help & support` 展开（23:10 实测）出 4 项：`Support`、`Report a problem`、
  `Safety`、`Terms & Policies`。均只采集入口、未进入；`Report a problem` 会提交反馈，
  按 send 级处理。
- 上面的两个折叠节可以同时展开，展开后菜单变长，`Help & support` 会被挤出屏幕，
  需要先向上滚动才能再次定位——这是常见的"元素在层级里消失"的诱因。


- 未覆盖：`Settings & privacy` 展开后的全部账号/隐私设置项、`Help & support` 子项、
  `讚`、`粉絲專頁`、`遊戲`、`活動`、`生日` 的目标页。

## 3. 搜索页（空态）

- 进入：首页 `Search Facebook`，实测。Activity `immersiveactivity.ImmersiveActivity`。
- 锚点组合：`Back` + 一个 focused 的 EditText + `沒有近期搜尋紀錄`。
- 控件：`Back`、搜索输入框（EditText，无文本无 label，进入即 focused）、
  `沒有近期搜尋紀錄`、`你可能認識的朋友`、`查看所有你可能認識的朋友`。
- 初始采集（20:45）**未输入任何关键词**，只采空态：输入会写入账号搜索历史。
- **两种状态必须分开看**（00:08 补测）：

| 状态 | 特征控件 | 说明 |
|---|---|---|
| ① 无历史 | `沒有近期搜尋紀錄` + `你可能認識的朋友` | 首采形态 |
| ② 有历史 | `近期` 分组 + `查看所有近期搜尋紀錄` + 关键词条目 + `此搜尋字詞的更多選項` | 搜过词之后再进来就是这套 |
| ③ 刚搜完返回 | 只剩 `EditText` + `Clear text` + `Back` | 既没历史也没空态文案 |
| ④ **输入中（02:15 补采）** | 弹出建议词列表，如 `marketplace-inbox request-now`、`marketplace items for sale` | 建议项是 Button，**label 就是建议词本身，动态、无稳定标签** |

- **条目右侧「此搜尋字詞的更多選項」会弹底部菜单**（03:49 补采）：
  关键词 + `刪除, 從搜尋紀錄移除此項目。` + `釘選此搜尋條件, 一次只能釘選 3 個搜尋條件。`
  —— 两个都是**本地行为**（删历史/钉选），不改账号对外状态，所以标 `none` 而不是 send。
  只打开看过，**没删也没钉选**。

- **推荐好友卡片**（04:13 补采）：搜索空态下方有 `你可能認識的朋友`，
  每条含姓名/头衔（动态）、`傳送交友邀請給 <名字>`（**send**，未点）、
  `移除`（**none**，只是把推荐从列表去掉，属本地行为）。

#### 搜索历史全量页（03:54 补采）

- 入口：搜索页有历史时点 `查看所有近期搜尋紀錄`。
- 结构：三个排序 tab（`全部` / `搜尋` / `瀏覽數`）+ `歷史紀錄` 标题 +
  `Clear all search history` + Back。
- **根因查明（04:00）**：切到「搜尋」tab 后，`Clear all search history` 的 label 变成
  **`Clear all search history\n已停用`**，页面显示 `沒有內容可查看！`。
  说明**该账号的搜索历史功能被关闭了**——所以列表恒为空、搜完回头也没有记录。
  这不是"历史被莫名清空"。
- 由此推出：搜索页的 ②有历史状态在这个账号上**基本不会出现**，
  那条状态是**在别的账号/开启记录时**才成立，不能拿本次账号的表现当"没有这个状态"。
- **tab 选中态会改 label**：选中时变成 `全部 Selected` / `搜尋 Selected`，
  切走后变回 `全部` / `搜尋`。正则必须写成 `^全部( Selected)?$` 这种兼容形式，
  否则切个 tab 就判不出来了（04:10 实测踩到）。


- 只按"无历史"建模会导致**有历史时判定失败**（实测踩过），现已两套锚点都纳入。




输入关键词的过程中还会出现第 ④ 种（建议词列表），建议项标签是内容本身，不可当锚点。- **交互特征**：搜索页第一次按返回往往只收起焦点/键盘，**第二次才真正退回首页**；
  端到端回归里必须用"多次返回直到落回目标页"的策略。


### 搜索结果页（21:00 补充，关键词 `marketplace`）

- 进入：搜索框输入关键词并回车，实测。Activity 仍是 `immersiveactivity.ImmersiveActivity`，
  **与搜索空态同一 Activity，只能靠锚点区分**。
- 锚点组合：`All search results, 1 of 7` + `Clear text` + `這些搜尋結果是否實用？`。
- 控件：

| 控件 | 类型 | 文本或标签 | 状态／证据 |
|---|---|---|---|
| 返回 | ImageView | `Back` | 可逆 |
| 搜索框 | EditText | text 就是关键词（动态） | 已观察 |
| 清空 | ImageView | `Clear text` | 未点击 |
| 分类标签 | Button | `All search results, 1 of 7`、`Reels …2 of 7`、`People …3 of 7`、`Groups …4 of 7`、`Events …5 of 7`、`Pages …6 of 7` | **数量与顺序随关键词变化**（见下） |
| 分组标题 | View | `社團` | 已观察 |
| 查看全部 | Button | `查看全部` | 未点击 |
| 结果条目 | Button | `<名称>, 公開 · 51 萬位成員 · 一天 90 則以上貼文` | 名称与人数是动态内容 |
| 加入 | Button | `加入` | **send 级**；与条目本体重叠，曾误触（见下节事故） |
| 已加入 | Button | `前往` | 加入成功后按钮变为「前往」，可作结果判据 |
| 反馈 | View + Button | `這些搜尋結果是否實用？` / `是` / `否` | Button 与内部 View 都会命中，属结构性重复 |
| 相关搜索 | Button | 关键词本身（如 `marketplace facebook`） | 动态内容，不可当锚点 |
| 可见范围提示 | ViewGroup | `搜尋結果只會包含你能查看的內容\n瞭解詳情\n按鈕` | 已观察 |

#### 分类标签的数量与顺序是**动态的**（00:35 重要修正）

| 关键词 | 分类顺序 |
|---|---|
| `marketplace` | All(1) → **Reels(2)** → People(3) → Groups(4) → Events(5) → Pages(6) → **Marketplace(7)** |
| `漲知識` | All(1) → **People(2)** → Groups(3) → Events(4) → Pages(5) → **Reels(6)** → … |

- **原来的模板把序号写死**（`Reels search results, 2 of \d+`），换关键词就全部失效。
  已改为 `Reels search results, \d+ of \d+` 这种只按名称匹配的写法。
- 有的关键词根本没有 `Marketplace` 分类，所以不能假设"一定有 7 个"。

#### 话题标签（`#xxx`）：这个版本没有独立话题页（01:17 定论）

- 帖子正文里的 `#太行山` / `#地理` / `#科普` / `#reels` 是**独立 Button**，可以点。
- 但点下去**不会进话题聚合页，而是直接变成一次搜索**：落到搜索结果页
  （`All search results, 1 of 7` 等分类标签齐全），内容是该词的帖子/Reels 结果。
- 之前搜 `#marketplace` 只返回帖子，也是同一机制。
- 结论：**不需要为"话题页"建模**，把它当"以 `#词` 为关键词的搜索"处理即可。
  结果条目用 `post_item`（`作者, 2 週. 正文…`）识别。

**筛选入口不是每个分类都有**（03:10–03:18 逐个核对完毕）：
| 分类 | 有 `Filters`？ |
|---|---|
| `All` | 无 |
| `Reels` | 无 |
| `People` | 无 |
| `Groups` | **有** |
| `Events` | **有** |
| `Pages` | 无 |

所以"某个分类一定有筛选"是错误假设，用前必须先 dump 确认。

**结果条目没有逐项菜单**（05:40 复核）：All 视图和 Groups 分类里，每个条目只有
`加入` 一个动作，**没有 `...` / `See more options`**。想隐藏或举报某条搜索结果，
只能先进它的详情页再说。

#### 筛选面板内容会随分类变化（01:21 实测）

| 入口 | 面板内容 |
|---|---|
| 搜索结果页 Groups 分类 → `Filters` | `Reset`、`城巿, 任何地方`、`在我附近`、`公開社團`、`我的社團`、`Show results` |
| 搜索结果页 **Events 分类 → `Filters`**（02:28 补采） | `Reset`、`日期, 任何時間`、`地點, 任何地方`、`類別, 所有類別`、`獲朋友好評`、`Show results` |
| 社团搜索 → `Group Posts` → `N applied filters` | `Reset`、`最新貼文`、`你看過的貼文`、`發佈日期, 任何日期`、`貼文來源, 你的社團`、`標註的地點, 任何地方`、`Show results` |

- 三者都有 `Reset` + `Show results`，**只能靠各自的专属项区分**，已分别建模。
- **但不是每个入口都会生成新面板**：`查看全部` 列表页右上也有 `Filters`，
  点开的内容与 Groups 分类**完全一致**（城巿/在我附近/公開社團/我的社團），
  判定就是同一个 `search_filters`，不需要新建页面。03:16 已实测确认。
- **下拉会展开成单选列表**（02:31–02:33 补采）：
  - `日期` → 6 项：`任何時間`（默认）／`今天`／`明天`／`本週`／`本週末`／`下週`
  - `類別` → **可滚动长列表**：`所有類別`（默认）+ `藝術`／`慈善活動`／`喜劇`／`手工藝`／`舞蹈`／`飲料`…
    只采了首屏 7 项，**后续类别未采集**
  - `地點` → **只有 `任何地方` 一项**。可能是没授予定位权限、没有其它可选地点，
    也可能是需要先搜索地点。**未覆盖"有多个地点可选"的形态**
  文案随语言变化，别把中文当唯一锚点。三个下拉都只打开看过，**没有改选任何值**。
- 开关均未点击。

#### 分类标签差异（22:49–22:50 实测 Groups / People）

| 分类 | 结构 | 条目形态 | 差异 |
|---|---|---|---|
| `All` | 分组标题 + 结果 + 反馈 + 相关搜索 | `名称, 公開 · N 萬位成員 · 一天 90 則以上貼文` + `加入` | 有 `社團` 分组标题、`查看全部`、`這些搜尋結果是否實用？` |
| `Groups` | **纯列表**，无分组/反馈/相关搜索 | 同上 | 搜索框变窄（664），右上出现 `Filters` |
| `People` | 纯列表 | `名字` / `名字, 現居地點` / `名字, 公司 員工 · 現居地點` / `名字（Facebook marketplace account）` | 每行按钮是 `加朋友`；条目 label 格式**不统一**，无法用一个正则覆盖；**没有 `Filters` 按钮**（03:10 复核） |
| `Events` | 纯列表 + 结束标记 | `名称, N 人將參加` + 时间 `11月3日週二上午3:00（CST）` | 每行按钮 `有興趣, <活动名>`；列表底部有 `End of results` |
| `Pages` | 纯列表 | `名称, 已驗證, 类别 · N 萬位追蹤者` | 每行按钮 `追蹤`；`已驗證` 是可选片段 |
| `Reels` | **2 列视频网格** | `作者, N 週. 标题` | 有 `Video sound toggle`；搜索框变宽（764），无 `Filters` |
| `Marketplace`（第 7 个） | **跳到另一个 Activity** | — | 见下方"Marketplace 不可用" |

#### Marketplace 不可用（23:00 实测，重要）

- 点第 7 个分类 `Marketplace search results, 7 of 7` 后，Activity 变成
  `com.facebook.katana.activity.react.ImmersiveReactActivity`（**React Native 页**，
  但控件仍是原生层级、可正常读取）。
- 页面内容：`你目前無法使用 Marketplace` +
  `如果你最近加入了 Facebook、Marketplace 尚未在你的國家／地區推出或你的帳號不符合我們的資格要求，就可能看到這則訊息。如需` +
  `瞭解詳情`。
- **这解释了首轮「好物特蒐 Finds」为什么一直是骨架屏**：不是加载慢，是这个账号/地区
  没有 Marketplace 资格。以后不要再把骨架屏当加载问题排查。
- 第 7 个分类默认**在屏幕外**，需要横向滑动分类条才会出现（`All` 会滚出屏幕）。

#### 筛选面板 `Filters`（23:02 实测，Groups 分类）

- 入口：分类视图右上 `Filters`（`All` 视图没有这个按钮）。
- 形态：**底部弹层**，占屏幕下半，底层搜索结果仍在层级中。
- 控件：`Reset`（无筛选时 disabled）、`城巿, 任何地方`、`在我附近`（CompoundButton 开关）、
  `公開社團`（开关）、`我的社團`（开关）、`Show results`。
- 只采集了 Groups 分类的面板；People/Events/Pages/Reels 的筛选项**未验证**是否相同。
  开关与城市选择都未点击（`在我附近` 可能触发定位权限）。



## 4. 好友页

- 进入：底栏 `Friends, tab 2 of 4`；或抽屉 `尋找朋友`（带 `Back`，结构与前者一致），
  两种入口均实测。
- 锚点组合：`Friends, Heading` + `Friend requests` / `建議` / `你的朋友`。

| 控件 | 类型 | 文本或标签 | 状态 |
|---|---|---|---|
| 返回 | Button | `Back` | 仅「尋找朋友」入口有 |
| 标题 | TextView | `Friends`（label `Friends, Heading`） | 英文 |
| 搜索 | Button | `Search` | 未点击 |
| 分段 | Button | `建議`、`你的朋友` | 繁体；未切换 |
| 好友请求区 | ViewGroup | `Friend requests` | 已观察 |
| 查看全部 | Button | `See all` | 未点击 |
| 请求者主页 | Button | `View <名字>'s profile` | 未点击 |
| 确认请求 | Button | `Confirm <名字>'s friend request` | **会建立好友关系**，未点击 |
| 删除请求 | Button | `Delete <名字>'s friend request` | 会移除他人请求，未点击 |
| 你可能认识 | ViewGroup | `People you may know` | 已观察 |
| 联络人上传 | ViewGroup | `開啟聯絡人上傳功能以找到朋友` | 涉及通讯录授权，未点击 |
| 加好友 | Button | `Add friend` | 改变账号状态，未点击 |

- 未覆盖：`你的朋友` 列表、`See all` 全量请求页、好友个人主页、搜索好友结果。

**「你的朋友」分段是空态（05:39 实测）**：这个账号**好友数为 0**，
切过去只有 `When you become friends with people on Facebook, they'll appear here.`。
所以"好友列表长什么样"仍未覆盖——有好友时的分组/搜索/排序都未知。

**并且这暴露了锚点缺口**：空态页既没有 `Friends, Heading` 也没有 `Friend requests`，
判定直接变 `None`。已把 `你的朋友` 与空态文案纳入 identity，现在两种状态都能判。

## 4b. 「你可能认识的朋友」全量页（02:40 实测）


- 入口：搜索页 `查看所有你可能認識的朋友`。
- **和「好友页」不是同一页**：没有 `Friends, Heading` 和分段按钮，是纯列表
  （好友请求 `View/Confirm/Delete` + `People you may know` + `開啟聯絡人上傳功能以找到朋友`）。
- 判定锚点：`People you may know` + `開啟聯絡人上傳功能以找到朋友` + `Confirm …friend request`。

**踩到的坑**：这两页都有 Confirm/Delete 请求和 `People you may know`，靠计数会**各 3 分打平**，
页面直接判 `None`。解决方式是给好友页加 exclusive（`Friends, Heading` 是它独有的）。
这已经是第 N 次遇到"两页锚点数量相同"，**新建页面时一定要回头验证老页面还判不判得出来**。

- 只采集结构，**没有确认/删除任何请求、没有加好友、没有开启联络人上传**。

## 4c. 好友请求全量页（03:09 实测）

- 入口：好友页 `Friend requests` 区的 `See all`。
- **三个页面长得很像，靠"多出来的那一个控件"区分**：

| 页面 | 独有特征 | 判定方式 |
|---|---|---|
| 好友页 `friends` | `Friends, Heading` + 分段 `建議`/`你的朋友` | **exclusive**（Friends, Heading） |
| 好友请求全量 `friend_requests_all` | **`Sort`** 按钮 | 计数（Friend requests + Sort + Confirm） |
| 你可能认识全量 `pymk_all` | `People you may know` + 联络人上传提示 | 计数 |

- 只采集结构，**没有确认/删除任何请求、没有改排序**。
- 顺带补采了排序面板：点 `Sort` 弹底部三选一——
  `Default` / `Newest request first` / `Oldest request first`（是 ViewGroup 不是 RadioButton）。
  改排序只影响本地视图顺序，不改账号状态。**只打开看过，没有改选**。

## 5. Reels



- 进入：底栏 `Reels, tab 3 of 4`，实测。
- 锚点组合：`Reels, tab 3 of 4`（selected）+ `Reels` 标题 + `Video tab details`。
- 控件：`Facebook menu`、`Reels`（文本标题）、`Create reel`、`Search`、
  `Navigate to your Reels profile`、视频卡 `Video tab details`（整屏点击区）、
  作者 `Author's profile`、`Follow <作者>`、`<N> reactions`、`<N> comments`、
  `Share, <N> shares`、`Save`、`More`、播放进度 SeekBar。
- **点视频卡不会跳新页面**（23:31 实测）：进入同页沉浸播放，浮出
  `Rewind 10 seconds`、`Play`（播放中变 `Pause`）、`Fast forward 10 seconds`，
  下方作者与互动按钮整体上移。判定仍是 `reels`。
- 只采集到一条视频卡。**下滑换视频、长按表情面板、评论面板、分享面板均未覆盖。**
- `Create reel`、`Follow`、`reactions`、`Share`、`Save` 全部未点击（对外状态）。

## 5b. 分享面板（23:34 实测，只打开未分享）

- 入口：Reels / 帖子详情点 `Share, N shares`。
- 形态：**底部弹层**，占屏幕下半；锚点 `Share now` + `Share to` + `Select the destination to share.`。

| 控件 | 文本或标签 | 风险 |
|---|---|---|
| 关闭 | `Close` | none（本次用它关闭） |
| 分享提示 | `你分享的連結為你專屬，且可能會用於改善你看到的建議和廣告。` | none |
| 了解详情 | `瞭解詳情` | none |
| 目标提示 | `Select the destination to share.` | none |
| 可见范围 | `所有人` | **send** |
| 感受/活动 | `Open feeling/activity screen` | none |
| 标注好友 | `Tag friends` | none |
| 立即分享 | `Share now` | **forbidden** |
| 分享到 | `Your story` / `Messenger` / `Copy link` / `Group` / `Text` / `More options for Share to` | 前四类多为 **send**；`Copy link` 只写剪贴板 |

- **`Share now` 标 forbidden 而不是 send**：它紧挨着一排目标按钮，误点即对外发布且不可撤销。
- 本轮为了采集结构，显式授权"只打开、不确认"，dump 完立刻 `Close`，
  **没有点 Share now、没有选任何目标**。
- 未覆盖：各目标点开后的二级页（限时动态编辑器、社团选择、短信面板）、可见范围选择器。

## 5c. 限时动态编辑器（00:31 实测，只打开不发布）

- 入口：分享面板 → `Your story`。
- Activity：`com.facebook.inspiration.activity.InspirationCameraActivity`。
- 锚点组合：`Story Settings Menu` + `Tap to mention people` + `Sticker.`。

| 控件 | 标签 | 风险 |
|---|---|---|
| 音乐 | `Music` | none |
| 特效 | `Effects` | none |
| 标注人物 | `Tap to mention people` | none |
| 贴纸 | `Sticker.` | none |
| 故事设置 | `Story Settings Menu` | none（可见范围、保存等，未点开） |
| **发布** | `Share` | **forbidden** |

- 这里的 `Share` 特意比普通分享 **更严**（forbidden 而不是 send）：它是**最终发布键**，
  不是"打开面板"，旁边还叠着一堆 send 级分享目标，误触代价最高。
- **打开后立刻退出，没有编辑、没有发布。** 退出时弹出「Discard photo?」确认框，
  选「Discard photo」后回到原帖，无残留。

## 5d. 放弃限时动态确认框（00:32 实测）

- 形态：覆盖层，编辑器元素仍在层级里 → 用 exclusive 互斥锚点判定。
- 控件：标题 `Discard photo? You'll lose this photo and any changes…`、`Discard photo`。
- 只采到「放弃」一个出口，**没看到"继续编辑/保存草稿"**；若后续遇到别的选项需补记。

## 6. 通知列表与详情



- 进入：底栏 `Notifications, tab 4 of 4`，实测。锚点：`Notifications, Heading` + 该 tab selected。
- 列表控件：`新通知` 分组标题；条目是 Button，label 形如
  `<正文>. 9h. Unread`，正文与时间都是账号相关内容，不可当锚点；
  用后缀 `. <时间>. Unread` 识别条目，用 `Unread` 区分已读。
- **分组会随时间推移增加**（05:02 实测，推翻 02:18 的结论）：
  当时只有 `新通知` 一组、下滑也没有更多，我记成了"该账号通知少、没有更早分组"；
  现在变成 **`新通知` / `今天` / `先前的通知` 三组**。
  所以"只有一组"只是那一刻的快照，**分组结构不能写死**。
- 分页加载（滑到底自动加载更早）**仍未观察到**。
- **进入后底栏角标 `, 2 new` 消失**：这是本地已读标记，不属于账号对外状态变化。
- 详情：点条目后**落点取决于通知类型**（00:29 修正结论）：

| 通知类型 | 落点 | 可采性 |
|---|---|---|
| 社团类（`歡迎加入 Marketplace…`） | **原生**社团详情页 | 控件可正常读取，判定为 `group_detail` |
| 账号安全类（`登入異常`） | 内嵌 **WebView** | 原生层级只剩一个 `WebView` 节点，**内容不可采** |

- 所以不能说"通知详情都不可采"——只有 WebView 那一类不行。
  如果后续任务要操作 WebView 内容，需另做 H5 方案，不能假设原生控件存在。

#### Meta 账号说明页（05:07 补采，修正旧结论）

- 入口：通知里「Meta 帳號即將推出」类通知。
- 结构：`關於 Meta 帳號` 标题 + 副标题 + 问答 `Meta 帳號是什麼？` + 说明正文 + `了解更多` 按钮。
- **修正 00:29 的结论**：当时记的是「账号安全类通知进内嵌 WebView，原生层级只剩一个 WebView 节点」。
  这次同类通知落点是**完全可读的原生页**。所以**"某类通知一定不可读"也站不住**——
  落点要按实际通知再看，不能按类型一概而论。
- 只读结构，未点「了解更多」。

## 7. 发帖编辑器（只打开、只输入、未发布）


- 进入：首页 `Make a post on Facebook`，实测。
- Activity：`com.facebook.composer.activity.ComposerActivity`。
- 锚点：`Create post` + `POST`。

| 控件 | 类型 | 文本或标签 | 状态／风险 |
|---|---|---|---|
| 返回 | ImageView | `Back` | 可逆 |
| 发布 | Button | `POST` | **空正文时 disabled**；输入任意文字后立刻变可用；未点击 |
| 可见范围 | Button | `所有人` | 改范围属对外状态，未点击 |
| AI 标签 | Button | `AI label off. Collapsed` | 未点击 |
| 添加相册 | Button | `Add album` | 未点击 |
| 同步 Instagram | Button | `Share to Instagram. Off.` | 未点击 |
| 正文输入 | AutoCompleteTextView | 无 label | 按类型 + 位置定位 |
| 相片／影片 | Button | `相片／影片` | 未进入 |
| 标注用户 | Button | `標註用戶` | 未进入 |
| 感受／活动 | Button | `感受／活動` | 未进入 |
| 打卡 | Button | `打卡` | 未进入 |
| 直播视讯 | Button | `直播視訊` | 未进入 |
| 背景色彩 | Button | `背景色彩` | 未进入 |

#### 输入正文后的变化（23:13 实测）

- `POST` 由 disabled 变为可用 —— **这是"是否已填写"的天然安全闸门**，
  自动化发帖前应先确认这一点，而不是盲点发布。
- 底部浮出**背景选择条**：`Hide all background options`、`No background`（默认选中）、
  若干纯色/渐层背景（`純紫色，背景`、`漸層，紅色到藍色，背景`）、
  AI 生成背景（label 是英文描述，如 `An abstract minimalist gradient background…`，
  **常被截断**）、`Show all background options`。
- 工具栏变为：`相片／影片`、`標註用戶`、`感受／活動`、`打卡`、`Add more to your post`。
- **本轮输入了 `UI probe - not published` 用于验证，随后放弃，未发布、未留草稿。**

#### 放弃草稿对话框（23:13 实测）

已有正文时按系统返回键 → 弹出覆盖层：

| 控件 | 文本 | 风险 |
|---|---|---|
| 标题 | `Want to finish your post later?` | — |
| 存为草稿 | `Save as draft, You'll receive a notification with your draft.` | **send**（留草稿 + 产生通知） |
| 放弃 | `Discard post` | none（本次选择它） |
| 继续编辑 | `Continue editing` | none |

- 对话框是**覆盖层**，编辑器元素仍在层级里，因此用 exclusive 互斥锚点判定；
  自动化中途退出必须显式处理它，否则会卡住。
- 本次选 `Discard post`，确认回到首页且**没有留下草稿**。

## 8. 动态详情／评论区

- 进入：信息流/动态详情点 `Comment` 或 `N comments`，实测。锚点：`Close` + `Write a comment…`。
- 控件：`Close`；表情计数按钮（纯数字）；`<N> shares`；评论条目（作者、正文、
  相对时间如 `3 days`/`48 minutes`、`你說` 标记）；每条评论的
  `Like <作者>'s comment button…`、`Reply to <作者>'s comment, button…`。

#### 底部输入工具条（23:25 实测）

| 控件 | 类型 | 文本或标签 | 风险 |
|---|---|---|---|
| 写评论 | AutoCompleteTextView | `Write a comment…` | none |
| 照片与视频 | Button | `Show photos and videos` | none |
| GIF 搜索 | Button | `Open GIF search` | none |
| 贴纸 | Button | `Show stickers` | none |
| 发送 | Button | `Send` | **send**；**未输入时 disabled**，和发帖的 `POST` 同一套安全闸门 |

#### 评论条目细节

- 每条评论：作者头像／名字、正文、相对时间、`Like <作者>'s comment button…`、
  `Reply to <作者>'s comment, button…`，部分带 `<N> reactions`。
- **展开回复实测可行**：点 `View 41 more replies to 刘刚…` 后，回复以缩进形式展开
  （出现被回复人的头像/名字/正文/时间），属于纯浏览、可逆。
- **作者名没有稳定特征**：早期想用 `^[^,]+$` 兜底定位作者，一次命中 8 个元素，
  已删除这个 target。评论只能靠「label 前缀 + 可视区域」关联，不要按作者名定位。

- **未点赞、未回复、未发送。** 评论排序、表情面板、作者主页跳转、GIF/贴纸面板
  仍未覆盖。

#### 从首页 feed 直接点 `Comment` 也进同一页（04:34 实测）

- 首页信息流里带互动栏的帖子，点 `Comment` 会打开同一个 `post_detail`（评论浮层），
  判定复用、不需要新建页面。
- **关闭要按两次返回**：第一次只是收起输入框/键盘，第二次才真正关掉浮层。
  只按一次会以为"没关掉"，其实还停在 `post_detail`。

## 8b. 单条动态详情页（23:19 实测，视频帖）


- 进入：在信息流里打开某条动态（本次是滑动手势误入的视频帖）。
- **与「动态详情／评论区」不是同一页**：那一页是评论浮层（有 `Close`、
  `Write a comment…`）；这一页是整屏动态，顶栏被 `Like / Comment / Share` 取代。
- 锚点组合：`Mute video: Off` + `Tap to mute` + `<N> views`。

| 控件 | 类型 | 文本或标签 | 风险 |
|---|---|---|---|
| 视频容器 | FrameLayout | `Video` | none（实测同一视频暴露 2 个，结构性重复） |
| 静音开关 | Button | `Mute video: Off` | none |
| 声音开关 | ImageView | `Toggle video sound` | none |
| 表情计数 | Button | `3.50K`（纯数字） | **send** |
| 评论数 | Button | `92 comments` | none |
| 分享数 | Button | `454 shares` | none |
| 观看数 | Button | `550K views` | none（详情页比信息流多出来的指标） |
| 赞 | Button | `Like button. Double tap and hold to react to the comment.` | **send** |
| 评论入口 | Button | `Comment` | none |
| 分享 | Button | `Share` | **send** |
| 作者限时动态 | ImageView | `Open <名字>'s story, unseen story` | none |
| 发布时间 | ViewGroup | `2d•Shared with: 所有人` | none |
| 更多操作 | Button | `See more options` | none |
| 隐藏帖子 | Button | `Hide post` | none |
| 正文 | ViewGroup | 动态内容 + `… more` | none |

- 未覆盖：评论区列表（需继续下滑）、图文帖详情（结构可能不同）、
  「更多操作」菜单、分享面板。

## 8c. 贴纸面板（23:40 实测）

- 入口：评论区底部 `Show stickers`。
- **整屏覆盖层**：打开后评论区的 `Close`、`Write a comment…` 全部从层级里消失，
  只剩贴纸面板 —— 所以必须独立成页，不能挂在评论区下面。
- 锚点组合：`Sticker search button` + `Sticker store` + 贴纸页签（`Happy`/`Sad`/`In love`/`Eating`）。

| 控件 | 标签 | 说明 |
|---|---|---|
| 贴纸搜索 | `Sticker search button` | 未点 |
| 贴纸商店 | `Sticker store` | 未点 |
| 分类 | `喜怒哀樂`／`94愛尷尬`／`哥倆好`／`一起來聊聊`／`美好日子`／`工作心情` | 6 个，横向排布；**名称随语言/地区变化，别当固定锚点** |
| 页签 | `Search`／`Happy`／`In love`／`Sad`／`Eating` | 5 个 |
| 贴纸 | 描述性 label，如 `Thumbs Up, girl giving thumbs up` | 点它只把贴纸放进评论框、**不会发送** |

- 本次只打开查看，**没有点任何贴纸**；看完按返回退出，回到评论区。
- 未覆盖：贴纸搜索、贴纸商店、其他分类下的内容。

## 8d. GIF 面板（23:44 实测）

- 入口：评论区底部 `Open GIF search`。
- **底部覆盖层**，与贴纸面板一样会吞掉评论区锚点，必须独立成页。
- 锚点组合：`Enter search terms to search for GIFs` + 任一 `…GIF` 结果。

| 控件 | 类型 | 标签 | 说明 |
|---|---|---|---|
| GIF 搜索框 | EditText | `Enter search terms to search for GIFs` | placeholder 就是 label |
| 搜索图标 | ImageButton | 无 label | 只能按类型定位 |
| GIF 结果 | Button | `Love You Reaction GIF by Bounce` / `Dance Dancing GIF` | 瀑布流；点它插入评论框、不发送 |

- 面板本身**没有可见关闭按钮**，退出靠系统返回键。
- 本次只打开查看、未点任何 GIF；未覆盖：搜索、滚动加载更多。

## 8e. 图文帖详情页（00:19 实测）

- 进入：信息流里点开图文帖子（这个号的信息流几乎全是视频帖，专门写了
  `python fishfleet/web/facebook_flow.py find_photo` 边滑边判定帖子类型才找到）。
- **与视频帖详情（8c）不同**：没有 `Mute video` / `views` / `Video` 容器。
- 锚点组合：`圖像` + `N comments` + `Shared Link: …`。

| 控件 | 标签 | 风险 |
|---|---|---|
| 图片 | `圖像` | none |
| **互动投票问题** | `你是否已經有相關產品？`（动态文案） | none |
| **投票选项** | `有` / `籌備中` / `沒有` | **send**（投票会记录在账号上） |
| 链接卡片 | `Shared Link: 表單, SHOPLINE - 全方位開網店平台` + `瞭解詳情` | none |
| 表情/评论/分享 | `126` / `3 comments` / `N shares` | 表情是 send |
| 互动栏 | `Like button…` / `Comment` / `Share` | 赞与分享是 send |
| 作者头像 | `<名字> Profile picture` | none |

- **帖子可以内嵌互动投票单元**——这是帖子级组件，不是页面级控件，
  所以没有按钮文案的稳定锚点，只能靠"问题句 + 三个选项"识别。
- 未投票、未点赞、未评论。未覆盖：投票后的结果态、链接跳转。

## 8f. 帖子更多操作菜单（00:22 实测）

> **注意：同一个 `See more options` 在不同上下文打开的是不同菜单。**
> 社团详情页里点它，出来的是 `Report to group admins` + `Save reel` 那一套，
> **不是**下面这个。两套必须分开建模（06:00 实测）。


- 入口：帖子详情右上 `See more options`。
- 形态：**底部弹层**（底层帖子仍在层级里），用 exclusive 互斥锚点判定：
  `Why am I seeing this post?` + `隱藏貼文` 同时命中。

| 选项 | 标签 | 风险 |
|---|---|---|
| 关闭 | `Close` | none（本次用它关闭） |
| 为什么看到这条 | `Why am I seeing this post?` | none |
| 感兴趣 | `有興趣 你會在動態消息看到更多這類建議貼文。` | **send**（改推荐偏好） |
| 不感兴趣 | `沒興趣 你會在動態消息看到更少這類建議貼文。` | **send** |
| 保存 | `Save reel Add this to your saved reels.` | **send** |
| 投屏 | `Cast reel Cast to a nearby device` | none（但会查找附近设备） |
| 隐藏贴文 | `隱藏貼文 看到更少這類貼文。` | **send**（改 feed） |
| 举报 | `檢舉 Reel 我們不會向 <作者> 透露檢舉人的身分。` | **forbidden**（单向不可撤销） |
| 开启此贴通知 | `Turn on notifications for this post` | **send** |

- 菜单里混着 Reel 专属项（`Save reel`/`Cast reel`/`檢舉 Reel`），说明这条素材被当作 Reel
  处理，但页面判定仍是图文帖详情——**同一素材可以同时具备两类身份，不要因此改判页面**。
- 只打开采集结构，随后关闭，**没有点任何一项**。

## 8h. 长按表情托盘（01:01 实测）

- 入口：**长按**帖子/Reels 的 `Like button…`（约 900ms）。
- 表情项是**自定义 View**，类型 `RopeStyleUFIDockView`，label 就是表情名：
  `Like` / `Love` / `Care` / `Haha` / `Wow` / `Sad` / `Angry`（7 个横向排开）+ `Cancel`。
- **选择任一表情都会改变账号对外状态，全部按 send 处理。**
- `Cancel` 实测是 **1x1 的隐藏节点**，点它不一定有效；关闭托盘更可靠的是按系统返回键。
- 只长按弹出看了一眼结构，随后按返回关闭，**没有选任何表情**。
- **副作用提醒**：长按期间 `Mute video` 这类控件会暂时隐藏，页面判定会短暂失效
  （实测从 3 个锚点掉到 1 个）。已给视频帖详情补了 `Video` 容器和 `Comment` 两个更稳的锚点。

## 8g. 社团发帖框（00:36 实测）



- 入口：社团详情页 `留個言吧……`。
- **与个人发帖框（第 7 节）不是同一套**，但 Activity 相同（`ComposerActivity`）：

| 差异点 | 个人发帖 | 社团发帖 |
|---|---|---|
| 标题位 | `Create post` | **社团名**（如 `漲知識`） |
| 可见范围 | `所有人` | **`公開社團` / `私密社團`** |
| 特有入口 | `Add album`、`Share to Instagram`、`背景色彩` | **`相機`、`貼文標題`** |
| 发帖键 | `POST`（空正文 disabled） | `POST`（同样 disabled） |

- 只打开看结构、**未输入、未发布**，返回后自动退出（无内容所以没弹草稿框）。

## 8i. 「你可能错过的帖子」详情（04:17 实测）

- 入口：通知列表里「你可能錯過的貼文」类通知。
- **和图文帖详情（post_photo）长得像但不是同一页**：多了顶部标题
  `你可能錯過的貼文, Heading`；这次 dump 里**没有点赞数/评论数/互动栏**
  （可能还没加载，或该页本就不显示）。
- 用标题做 **exclusive** 判定，避免和其它单帖页打平。
- 右上 `See more options` 打开的是**同一个** `post_more_menu`（复用，04:38 实测）。
  但这个入口下菜单**多了两项**：`Turn on notifications for this post`、
  `Add <专页> to Favorites`（加入收藏，会改信息流优先级 → **send**，未点）。
- 未点赞、未评论、未分享。


## 9. 个人主页






- 进入：首页 `Go to profile`，实测。Activity `immersiveactivity.ImmersivePortraitActivity`。
- 锚点组合：`Cover Photo` + `編輯個人檔案` + 顶栏 `Back`。
- 控件：`Cover Photo`、`編輯封面相片`、`大頭貼照`、`編輯大頭貼照`、
  `建立便利貼：在想些什麼？`、`開啟個人檔案切換工具`、`新增到限時動態`、
  `編輯個人檔案`、`People you may know`、分页 `全部, 1 of 3` / `相片, 2 of 3` / `Reel, 3 of 3`。
- 未覆盖：时间线内容、编辑档案表单、成果夹。
- 除 `Back`/搜索外均未点击（编辑类会改对外资料）。

#### 分页是**页内切换**（00:13–00:15 实测）

- 点 `相片, 2 of 3` / `Reel, 3 of 3` **不跳新页面**，仍判为 `profile`，
  只是内容区和选中态变化。**这与社团分页（会跳到独立全屏页）完全相反**，
  不要因为社团那边跳页就假设这里也跳页。
- 切到「相片」后，页内多出两个子分类：`你被標註在內的相片`、`相片`。
  注意 `相片` 子分类与 `相片, 2 of 3` 分页按钮**是两个不同控件**，
  用 `^相片$` 和 `^相片, 2 of 3$` 区分，不要写成一个正则。
- 切到「Reel」后页面基本为空（该账号没有 Reel 内容），只采到选中态。
- **但子分类会跳独立页**（03:37 实测）：分页内的 `相片` 子分类点下去会进入
  **独立全屏的照片页**——封面/编辑按钮全都消失，只剩 Back + 三个 tab：
  `PHOTOS OF YOU, tab 1 of 3` / `UPLOADS, tab 2 of 3`（默认选中）/ `ALBUMS, tab 3 of 3`，
  下面是照片网格。注意这三个 tab 名是**英文**，和界面其它中文混排。
  **所有照片格 label 都是 `Photo`**，没有描述，只能按位置关联。
  所以「分页是页内切换」成立，**但"分页里的子分类"照样会跳页**，别一概而论。
- **Activity 修正（03:55）**：这页其实是
  `com.facebook.photos.pandora.ui.PandoraTabPagerActivity`，
  **不是**主页那个 `ImmersivePortraitActivity`（原来按主页推测错了）。
- **三个 tab 是横向 ViewPager 分页**：切到 `ALBUMS` 后，未显示页里的元素坐标会变成
  **负数**（如 `Photo` 出现在 `x=-1080`）。
  已加 `facebook_live.is_visible(el, w, h)`：**用中心点判断 x/y 是否都在屏内**，
  flow 与 recipe 都改用它（之前只查 y，会把屏幕外元素当成可点）。
  单测验证：`x=-1080` 判不可见、`x=362` 判可见。





## 9b. 他人个人主页（22:51 实测）

- 进入：People 搜索结果点某个条目，实测。
- Activity：`immersiveactivity.ImmersivePortraitActivity` —— **与自己主页同一个 Activity**，
  只能靠按钮区分（自己有「編輯個人檔案」，别人有「加朋友」「發送訊息」）。
- 锚点组合：`發送訊息` + `加朋友`。

| 控件 | 类型 | 文本或标签 | 状态／风险 |
|---|---|---|---|
| 返回 | ImageView | `Back` | 可逆 |
| 搜索 | ImageView | `搜尋` | 未点击 |
| 封面 | ImageView | `Cover Photo` | 已观察 |
| 大头贴 | Button | `大頭貼照` | 已观察 |
| 对方名字 | View／Button | 动态（顶栏 + 正文各一处） | 不可当锚点 |
| 加朋友 | Button | `加朋友` | **send**，未点击 |
| 发送讯息 | Button | `發送訊息` | **send**；点了会进安装引导（见 9c） |
| 分页 | Button | `全部, 1 of 3`／`相片, 2 of 3`／`Reel, 3 of 3` | 只采集了首个分页 |
| 帖子区 | ViewGroup | `<名字>'s posts` | 名字动态 |
| 帖子时间 | ViewGroup | `2025年3月10日•Shared with: 所有人` | 时间动态 |
| 更多操作 | Button | `See more options` | 未点击 |
| 照片 | ImageView | `Photo` | 已观察 |

- 未覆盖：对方相片／Reel 分页、更多操作菜单、帖子详情。

## 9c. 私信链路：结论是「被 Messenger 硬拦截」（22:50–22:52 实测）

主人说不装 Messenger 也能测私信，我把 app 内三条可能入口都走了一遍：

| 入口 | 结果 |
|---|---|
| 首页顶栏 `Messaging` | 先弹 `com.facebook.appmanager` 安装引导，选「稍後再說」后落到 `katana.orca.diode.DiodeMessengerActivity`，内容只有 `Messages` / `Faster messaging` / `INSTALL` |
| 他人主页 `發送訊息` | 直接跳到 `com.facebook.appmanager/com.facebook.oxygen.appmanager.ui.landing.StubInstallActivity` |
| 通知条目 | 走 WebView，不涉及会话 |

- 新版安装引导（`StubInstallActivity`）控件：`返回`、`Messenger標誌`、`安裝Messenger`、
  `與你最愛的對象建立聯繫。`、`預估大小：178MB`、`查看詳情`、
  行动数据 CheckBox、`安裝`。
- **结论**：这套 Facebook（katana 530.0.0.48.74）在未安装 Messenger 时，
  私信入口一律导向安装引导，app 内**没有**可用的纯网页版会话界面。
  这不是控件缺失，是产品层面的功能门禁。
- 因此私信测试的可行路径只剩：
  1. 安装 Messenger（环境变更，需主人同意）；
  2. 用浏览器打开 `m.facebook.com` 网页版消息（需要 Facebook 登录，属登录授权，需主人同意）；
  3. 换一个已装 Messenger 的账号/设备。
- 本轮**没有**安装、没有登录、没有发送任何消息。

#### 三个 tab 各自不同的控件（04:17 补采「你的社團」）

| tab | 独有控件 |
|---|---|
| `為你推薦` | `Your groups` / `See all` / `來自你的社團` |
| **`你的社團`** | **`Create a group`（英文）/ `Search` / `Sort by: Most visited` / `Sort groups`** |
| `探索` | `Suggested for you` / `Create group`（英文） |

- 同一功能在不同 tab 下**文案语言都不一样**（`Create group` vs `Create a group`），
  所以锚点不能只写一个。
- `Sort by: …` 是动态文案，按前缀匹配。
- **点 `Sort groups` 会弹排序面板**（04:37 补采）：`Most visited` / `Groups you manage` /
  `Groups A-Z`，三个 RadioButton，**文案同样是英文**。只改本地排序、不改账号状态（全 none），
  **没有改选**。


#### 默认 tab 会随"有没有加入社团"变化（01:24 重要修正）


| 账号状态 | 默认 tab | 页面特征 |
|---|---|---|
| 没加入任何社团 | `探索` | `Suggested for you` + `Create group` + 推荐卡 |
| **加入过社团** | **`為你推薦`** | `為你推薦` + `Your groups` + `See all` + `來自你的社團` |

- 原模板只按「探索」建模，账号加入社团后**页面直接判不出来**（实测 `None`）。
  现在三个 tab 的锚点都保留。
- 顺带一个命名冲突：社团页的 tab 叫 `你的社團`，社团搜索结果的筛选 chip 也叫 `你的社團`，
  两者会互相干扰 —— 已把 `你的社團` 从搜索结果的锚点里移除，只用 `applied filters` 那组。

## 10. 社团（探索）




- 进入：抽屉 `社團`，实测。锚点：`Suggested for you` + `Create group` + `Search groups`。
- 控件：`Back`、`Create group`、`Settings`、`Search groups`、`探索`（选中）、
  推荐卡（label `Suggested group: <名称>. 公開社團 · <人数> 位成員`）、
  `Hide suggested group: <名称>`、`Join group: <名称>`。
- `Join group` 会改变账号对外状态，未点击。
- 未覆盖：`你的社团`、社团详情、社团内发帖、搜索社团结果。

## 10c. 社团搜索（00:44 实测）

- 入口：社团页右上 `Search groups`。
- **关键限制：搜索输入框完全不在无障碍层级里**。截图里明明有 `Search groups` 输入框、
  键盘也弹出来了，但 dump 只有 `Back`。这类输入框**不能按元素定位**，只能靠
  "键盘已弹出、焦点在那个框"直接打字。flow 脚本已加这条兜底路径。
- 输入关键词回车后进入**社团搜索结果页**（新页面），有两个视图：

| 视图 | 特征 | 锚点 |
|---|---|---|
| `Groups`（默认） | `Groups` / `Group Posts` 两个页签 + 社团条目 | `Groups` + `Group Posts` + 社团条目 |
| `Group Posts`（01:02 补采） | 换成筛选 chip 布局：`1 applied filters`、`Posts`、`最新貼文`、`你看過的貼文`、`發佈日期`、`你的社團`；下面是 2 列帖子格 | `applied filters` + 各 chip |

- 两个视图的页签会互相替换，所以锚点要覆盖两套，靠"任一命中"累加。
- **锚点权重要平衡**：一开始给搜索页第三态同时用了「输入框 + Clear」两个锚点，
  结果把同样带搜索框的社团搜索页压了过去（判成 `search`）。
  收窄成只用 `Clear text`，再给社团搜索页多列几个 chip 才稳定。
- 未覆盖：`Group Posts` 的筛选面板、排序切换后的结果。


## 10b. 社团详情页（23:52 实测）


- 进入：搜索结果点社团条目（用**安全点**避开「加入」按钮，见下）。
- Activity 仍是共用的 `immersiveactivity.ImmersiveActivity`，只能靠社团专属锚点判定。
- 锚点组合：`Cover photo of group <名字>` + `<名字>, 公開社團 · 830K members` + `留個言吧……`。

| 控件 | 标签 | 风险 |
|---|---|---|
| 返回 | `Back` | none |
| 社团内搜索 | `Search in Marketplace`（含社团名，动态） | none |
| 封面 | `Cover photo of group Marketplace. Description is unavailable.` | none |
| 名称与人数 | `Marketplace, 公開社團 · 830K members` | none |
| 加入社团 | `Join Marketplace group` | **send**（会改变对外状态） |
| 已加入 | `已加入「 Marketplace 」社團` | **send**；加入/未加入是**两种按钮**，点它会打开成员菜单（可退出），未点击 |
| 分组页签 | `Reel`／`討論區`／`相片`／`相簿`／`活動`／`檔案` | none |
| 社团内发言 | `留個言吧……` | **send**（最终对外发布） |
| 照片/视频 | `Photo/Video` | none |
| 排序 | `最相關` | none |
| 帖子时间 | `16m•Shared with: 公開社團` | none |

- 只采集了「討論區」默认视图；Reel/相片/相簿/活動/檔案 五个分页**未进入**。
- 未覆盖：社团内搜索、发帖、分类页签内容、加入申请流程。

#### 社团分页会跳到独立页（23:55–23:59 实测）

点社团的六个分页页签**不是切换内容，而是离开社团页**进入独立全屏页——
社团封面、成员数、六个页签全部消失，所以每个分页都要独立建模：

| 分页 | Activity | 标题锚点 | 主要控件 | 覆盖 |
|---|---|---|---|---|
| `相片` | 同 ImmersiveActivity | `Photos` + `Back` | 照片格子，**全部 label 都是 `View post`**（无描述，只能按顺序关联） | 已采 |
| `活動` | 同 | `近期活動` | 活动条目、`N 人會參加`、`對 X 有興趣`（send）、`分享 X`（send） | 已采 |
| `檔案` | 同 | `Files` + `Add file button` | 该社团为空：只有空态 `This group doesn't have any files yet.` | 部分 |
| `相簿` | 同 | `Create album button` + `<名>, N photos` | 相簿列表（`Guitar, 2 photos`）；有 `Go to profile`、`Back`，**无可见标题文本** | 已采 |
| `Reel` | 同 | `Reel,N plays` + `Back` | 3 列网格，label 形如 `Reel,167 plays` / `Muted Reel,68 plays`，**连标题都没有**；01:56 已点开一条验证跳转 → 升级为已采 |
| `討論區` | 同 | 在社团页内 | 社团内发言、排序、帖子列表 | 已采 |

- 六个分页**全部点过**看结构，没有点开任何照片/相簿/Reel、没有点「有興趣」、没有上传文件。


## 11. 好物特蒐（Finds）



- 进入：抽屉展开 `See more` 后点 `好物特蒐`，实测。锚点：`Finds, Heading` + `shopping cart`。
- 控件：`Back`、标题 `Finds`、`shopping cart`、`menu`、`為你推薦`（选中）。
- **内容区一直是骨架屏（灰色占位块），没有拉到任何商品卡。**
  商品列表、筛选、搜索、商品详情、下单全部未覆盖。不可据此认为该页没有控件。

## 11k. 社团 Reel 播放页（01:56 实测）

- 入口：社团详情页「Reel」分页 → 点任意 Reel 格。
- **极简页面**：整屏只有 4 个控件：

| 控件 | 标签 | 风险 |
|---|---|---|
| 返回 | `Back` | none |
| 选择观众内容 | `Pick viewer content to show` | none（未点开） |
| 搜索 | `Search` | none |
| 我的 Reels 主页 | `Navigate to your Reels profile` | none |

- **视频本体、作者、互动按钮都不进无障碍层级**，等了 6 秒也没有变化。
  所以这页只能做到"认出它"，播放控制与互动无法按元素操作。

**修正（07:08）**：01:56 那次记成"极简页 / 只有 4 个控件"，其实**是抓在异步加载中**。
这次等 3 秒后内容出来了：`Video details`、作者头像、`Marketplace · 原始音訊` 都在。
所以真实结论是——**内容会加载，只是慢**；播放控制仍不进层级。
另外**社团 Reel 与专页 Reel 落到的是同一页**（判定复用 `group_reel_view`）。

## 11j. 社团"查看全部"列表页（01:24 实测）



- 入口：搜索结果页社团分组的 `查看全部`。
- **和搜索结果页的 Groups 分类不是同一页**：顶部多一排筛选 chip
  （`Filters` / `Groups` / `城巿` / `在我附近` / `公開社團` / `我的社團`）。
- 一开始它被判成了 `search`（因为同样带 `Back` + `Clear text`），
  已按 chip 组合单独建模。
- 未覆盖：各 chip 点开后的筛选结果、`Filters` 面板。

## 11l. 活动主页（02:54 实测，抽屉「活動」）

- 顶部：城市按钮（本次 `Xinjin`，动态）+ 5 个筛选 tab：`為你推薦`（默认选中）/ `本地` / `本週` / `朋友` / `追蹤中`（最后一个默认在屏幕边缘）。
- 都是 Button + 内部 TextView 的**双层结构**（同一控件两层命中，属结构性重复）。
- **只采到筛选栏**：这次 dump 里列表区没有可见条目（可能仍在加载或无内容），
  所以**活动卡片结构、点开详情都未覆盖**，覆盖等级记 `partial`。
- tab 只观察，未切换。
- **列表区两次 dump 都是空的**（该账号/地区没有推荐活动，或仍在加载），
  所以活动卡片结构、点开详情都未覆盖，等级保持 `partial`。

**抽屉里的 `遊戲` 入口定位不到**（03:18 实测）：菜单项是异步渲染的，
滚动后能看到 `好物特蒐`/`活動`，但 `遊戲` 这次没出现在层级里。
所以「遊戲」页**未进入**——不是不存在，而是这次没抓到稳定入口。


## 11m. 我的珍藏（03:43 实测，抽屉「我的珍藏」）

- 6 个分类 tab：`全部，1／6`（默认选中）/ `Reel，2／6` / `貼文，3／6` / `Marketplace，4／6` /
  `珍藏分類，5／6` / 第 6 个默认在屏幕外。
- **编号格式和搜索结果页不一样**：这里是 `全部，1／6`（**全角逗号 + 全角斜杠**），
  搜索结果页是 `All search results, 1 of 7`（半角 + `of`）。
  **不能复用同一个正则**，否则一边永远匹配不上——这类"数字编号格式不一致"很容易踩。
- 列表是**空态**：该账号没有保存内容，只有 `珍藏分類` / `近期儲存` 两个分组标题、
  `建立新珍藏分類`（send，未点）和提示文案
  `在這裡重新探索你的珍藏內容，或將它們整理成珍藏分類。`。
  有内容时的卡片结构未覆盖。
- tab 只观察，未切换。





## 11n. 生日（03:07 实测，抽屉「生日」）

- **极简空态页**：整屏只有 `你朋友的生日會顯示在這裡。` + `Back`，
  没有任何生日条目、也没有月份/分组导航。
- 该账号当前没有可显示的好友生日。有生日时的列表结构**未覆盖**，
  所以只能做到"认出这页"，不能假设它长这样。

## 11o. 粉丝专页（05:00 实测，抽屉 See more 后「粉絲專頁」）

- 顶部三个 tab：`建立`（send）/ `已追蹤的粉絲專頁` / `邀請`（send）。
- 分组：`最近瀏覽過的粉絲專頁`，卡片含：专页名（动态）、类别（如 `媒體／新聞機構`）、
  `N 人在追蹤`、`發送訊息`（send，会进 Messenger 引导）。
- **只采到「最近浏览」一组**，已追踪列表、专页详情、创建流程都未覆盖，记 `partial`。
- tab 与卡片均未点击。

## 11p. 粉丝专页详情（05:00 实测）

- 入口：搜索结果 Pages 分类 → 点主页条目。（**抽屉那条路走不通**：菜单会被收起）
- 结构：封面图 + 头像（可能带限时动态环）+ 专页名/验证徽章/类别 +
  `25 萬位追蹤者` / `17 人追蹤中` + 简介 + `查看更多` + `追蹤` 按钮。
- **和「他人个人主页」（person_profile）很像**，区别是这里一定有专页专属的
  `位追蹤者` 计数与验证徽章位。
- 只采集首屏，帖子列表、相片、评价等分页均未覆盖；**未点追蹤**。
- **滚下去锚点会失效（05:24 实测）**：封面/查看更多滚出屏幕后判定变 `None`。
  已补两个滚动后仍在的锚点：`相片, N of 5` 与 **`提及, N of 5`**。
- **5 个分页是专页的关键特征**：`全部`/`相片`/`Reel`/`活動`/`提及`，
  而个人主页只有 3 个（`全部`/`相片`/`Reel`）——**「提及」「活動」两页是专页独有**，
  所以哪怕滚到只剩分页栏，也能靠它们把两者分开。
- **「提及」分页的实测内容（05:08）**：是一列**帖子卡**（作者 + 正文 + 链接预览图 +
  `Shared Link: 标题` + `OPEN` 按钮 + 点赞/评论）。
  `OPEN` 会跳浏览器/外部页面，**没点**。分页切换是页内滑动，Activity 仍是
  `ImmersivePortraitActivity`，判定还是 `page_detail`。
- **「活動」分页（05:22）**：`過去的活動` 分组 + 活动卡
  （label 形如 `<活动名> 的封面相片 未提供相片說明`，名字动态）。未点开任何活动。
- **「相片」分页（05:31）**：瀑布流网格——首图 717x594 大图、其余 355x352 小图；
  所有格子 label 都是 `Photo`，**无法按内容定位**。未点开任何照片。
- 到此**专页 5 个分页全部采过**（全部 / 相片 / Reel / 活動 / 提及）。
- **Activity 修正 + 锚点收紧（06:28）**：这页的 Activity 其实是
  `ImmersivePortraitActivity`（和主页同一个），改对之后立刻暴露出**判定打平**：
  `Cover Photo` 与 `相片, N of N` 在个人主页也有，三方各 2 分 → 判 `None`。
  已把 identity 换成**只属于专页**的四个锚点：
  `位追蹤者` / `人追蹤中` / `提及, N of 5` / `相片, N of 5`（专页 5 个分页，主页 3 个）。
  修好后 5 项主页断言恢复、`look_page` 配方仍能正确落到 `page_detail`。
- **联系方式按钮（06:52）**：首屏还有 `WhatsApp` 这类联系按钮（点了会跳外部 App）。
  具体出现哪些取决于专页填了哪些联系方式，所以模板里既记了 `WhatsApp`，
  也留了个通用 `contact_any`（WhatsApp/致電/電子郵件/網站）。**都没点**。
- **「全部」分页的纵向顺序（06:17）**：首屏（追踪数/简介/按钮）→ **「关于」信息区**
  （网站 `marketplace.com.py`、电话 `+595 …`）→ 最后才是**帖子流**（带 `•Shared with: 所有人`）。
  所以"往下滑就是帖子"不成立，中间隔着一层关于信息。电话/网站都是账号内容，**未点击**。
- **「Reel」分页（06:51）**：3 列网格，label 形如 `Reel,385 plays` / `Reel,1,212 plays`，
  元素类型是 **Button**（社团那边是 ViewGroup，格式一样、类型不同）。未点开任何 Reel。





## 11q. 照片查看器（07:25 实测）

- 入口：自己主页 → 相片 → 点任意照片（**之前只采到照片网格，没点开过**）。
- 全屏查看器，Activity 仍是 `PandoraTabPagerActivity`。
- 顶部：`Back` / **`1 of 6`** / `Tagging mode` / `Location` / `More`；底部：作者名 + `2023年4月8日`。
- **序号是单数 `1 of 6`**（无前缀），和搜索结果页的 `All search results, 1 of 7` 不是一种格式。
- **标记模式 / 地点 / 更多都未点**（标记模式会改账号内容，已标 send）；
  左右滑动切换照片、双击缩放手势未验证。
- **`More` 菜单（08:04）**：设为封面 / 编辑说明 / **删除照片** / 保存到手机 / 外部分享。
  **`Delete photo` 标 forbidden**——删除不可逆，永不自动点击；其余按对外影响分级。
  一项都没点。

## 12. 动态回顾（動態回顧）





- 进入：抽屉 `動態回顧`，实测。锚点：`Story archive` + `Settings`，空态 `稍後再返回查看更多動態回顧`。
- 当前账号无可用回顾，只有空态。有内容时的卡片、下载、分享结构未覆盖。

## 13. 消息（阻塞）

- 进入：首页 `Messaging`，实测。
- 首次点击弹出 `com.facebook.appmanager` 的「安裝Messenger」引导（含
  `如果無法使用 Wi-Fi，請使用行動數據。可能會收取資費。` 复选框与 `安裝`／`稍後再說`）。
  选 `稍後再說` 后落到 `com.facebook.katana.orca.diode.DiodeMessengerActivity`，
  内容是 `Messages` 标题 + `Faster messaging` / `Install Messenger…` + `INSTALL`。
- **没有安装 Messenger，因此会话列表、聊天页、发送消息全部未覆盖。**
  安装属于环境变更，需要主人明确同意后再做。

---

## 操作事故与纠正（2026-11-01 21:02，必须记住）

**事故**：为了验证"搜索结果条目能否点进社团"，我用**固定坐标** `(540,627)` 去点第一条结果。
该坐标正好落在条目内部「加入」按钮的下边缘内，结果把当前账号**真实加入了一个公开社团
`Marketplace`**——该条目的按钮从「加入」变成了「前往」。

**为什么算事故**：加入社团属于「改变账号对外状态」，本应事先取得主人确认；
而我用坐标而非元素定位，等于放弃了点击前的语义核对。

**已落地的纠正**：

1. `facebook_live.py` 新增 `tap_verified(el, expect_re)`：点击前必须用正则核对
   这个元素到底是什么，不匹配直接抛错、不发点击。
2. `facebook_templates.py` 给 `Target` 增加 `repeated` 字段，区分
   "列表里本来就重复"（结果条目、每条都有的加入/前往、是/否）与"意外重复"；
   自检的 live 体检只对**意外**多命中报警。
3. 搜索结果页把 `join`（加入）标为 **send** 级，并在 note 里写明
   "与条目本体重叠、曾误触"；新增 `joined`（前往）作为加入成功的结果判据。
4. 操作规范：**任何时候都不再用屏幕坐标点未知区域**；坐标只在已经通过
   `find()` 唯一命中、且核对过语义之后，由元素中心算出来用一次。

**未擅自补救**：没有退出该社团。退出同样是一次对外状态变更，而且会留下
"加入又退出"的痕迹。是否撤销，等主人决定。

**后续加固（23:51 落地）**：场景脚本新增 `_safe_point()` ——
点条目前先算出页面上所有 send/forbidden 元素的矩形，再从「中心 → 左侧 1/4 →
左侧 1/8 → 左侧 1/12」里挑第一个**不落在任何高风险矩形内**的点。
23:52 实测：点搜索结果条目时自动选到 `(270,627)`，而「加入」按钮在 x=494-576，
成功避开；顺带把 `tap` 命令也接上了模板风险反查（默认拒点 send/forbidden）。

## 判定层的四个坑（22:49–23:22 实测修复）

> **补一条（06:14）**：label 可能**带尾空格**（如 `Report to group admins `），
> `^X$` 会失配。已在 `_fields()` 里统一 `strip`，一处修、全库受益，
> 不用每个正则再写 `\s*$`。

这三处都是"页面对了、判定错了"的坑，自动化会因此在错误的页面上执行动作：

1. **搜索分类条滚动后判不出页面。** 原锚点依赖屏幕内的 `All search results, 1 of 7`，
   横向滑到第 7 个分类后它滚出屏幕，页面被判成 `None`。
   → 改为匹配任意 ` search results, N of 7`，不再依赖某一个分类可见。
2. **抽屉菜单打开时被判成首页。** 抽屉是覆盖层，底层首页元素仍留在层级里，
   按计数首页永远赢。这会让自动化在菜单上执行首页动作（例如去点"发帖"）。
   → 新增 `Page.exclusive`（互斥锚点）：`Close` + `Settings & privacy, header.`
   同时命中就直接判为菜单，压过一切计数。
   用这两个做互斥是因为评论浮层也有 `Close`，但它没有 Settings 节头，不会误伤。
3. **同一控件有 Button 和内部 View 两层**，导致 `find()` 认为"不唯一"、按钮永远定位不到。
   → 新增 `resolve()` 按可点击容器优先级收敛成一层；列表里真有多条（多条结果、
   多个「加入」）仍然返回多个，`find()` 继续拒绝。
4. **首页往下滚后顶栏和 tab 会一起隐藏**，所有顶栏锚点从层级里消失，页面判成 `None`。
   → 给首页补两个信息流自身的兜底锚点（`Sponsored•Shared with: ` + `Hide ad`），
   并在场景脚本里加 `top` 命令（先滚回顶部再操作顶栏）。





## 明确未覆盖 / 无法进入

- 消息与私信全链路：app 内三个入口全部被 Messenger 安装引导拦截（见 9c），
  **不是控件没采到，是这条路走不通**。
- 搜索的 `Events`／`Pages`／`Reels`／第 7 个分类，以及 `Filters` 筛选面板。
- 好物特蒐商品链路（内容未加载）。
- 通知详情内容（WebView 内部）。
- 设置与隐私、帮助与支持的**子页面内容**（条目已采，页面未进入）；
  其中 `Settings`、`裝置要求`、`訂單和付款` 已标为 forbidden。
- 赞／粉丝专页／游戏／活动／生日页。
- 发帖完整链路（选图、标注、打卡、直播、发布结果）。
- 任何需要点赞、评论、关注、加好友、加入社团、分享、付款、登录授权的实际生效操作。

## 资料库体检（01:15 加入自检）

每次跑离线自检都会顺带静态检查资料库本身，防止"定义写坏了但没人发现"：

| 检查项 | 结论 |
|---|---|
| 所有正则能否编译 | 通过 |
| 覆盖等级取值是否合法 | 通过 |
| forbidden 控件是否都单独写了"为什么不能点" | 通过（补了 `home.add_email`） |
| 内容类条目是否都有匹配条件 | **只提示、不失败** —— 见下 |
| 锚点偏少的页面 | 只提示 |

**关于"无匹配条件"的 12 个条目**：它们是**文档性占位**，不是可定位控件——
例如评论正文、投票问题文案、链接标题、账号名、活动标题、贴纸本身。
这些内容没有稳定语义特征，硬写正则只会造出过宽匹配（早期那次
`^[^,]+$` 一次命中 8 个元素就是教训）。所以它们留在 targets 里只作说明，
**不参与定位**，自检也只提示不报错。

顺手补了两个本该能定位的：
`notification_detail.webview` 加 `type_re=^WebView$`、
`post_detail.comment_list` 改成 `type_re=^RecyclerView$`。

## 不可见输入框的处理（01:00 抽成通用能力）

社团搜索页的 `Search groups` 输入框**不在无障碍层级里**（截图可见、键盘已弹，dump 只有 Back）。
这类框没法按元素点，只能靠"焦点已经在里面"直接打字。

已抽成 `DeviceSession.type_anywhere(text, submit=False)`：

1. 优先用 focused 的可编辑元素；
2. 其次任意 `EditText` / `AutoCompleteTextView`；
3. 最后：若检测到软键盘已抬起（`systemui:id/ime_switcher` 存在），直接向当前焦点打字；
4. 都不满足则返回 False，由调用方决定停手。

`facebook_flow.py type` 已改用它，实测在社团搜索页能直接打进去。
**注意**：这条兜底只适用于"已经确定焦点在目标输入框"的场景，
不要拿它往不明页面盲打。

## 复现方式

> 想不起来有啥命令时，先跑 `python fishfleet/web/facebook_selftest.py --help`——
> 会把所有模式、场景命令、关键文件列一遍。



- 结构化资料：`fishfleet/web/facebook_templates.py`
  （`PAGES` / `find` / `resolve` / `page_of` / `available`，共 60 页 559 个控件）
- 运行时封装：`fishfleet/web/facebook_live.py`
  （`DeviceSession`：只读 dump、前台应用、带语义核对的 `tap_verified`）
- 自测：`python fishfleet/web/facebook_selftest.py`（离线样本，约 110 项断言）
- 真机单页校验：`--live SERIAL0001`
- **端到端导航回归**：`--walk SERIAL0001` ——
  只走可逆路径，每步校验页面判定，当前覆盖 **15 步**：
  首页 → 抽屉 → 首页 → 搜索 → 首页 → 通知 → 首页 → 搜索 → 输入关键词 → 结果页 → 首页
  → 抽屉 → **社团页 → 社团搜索 → 输入 → Group Posts → 首页**。
  单页判定对不代表"点完会到预期页"，这一步专门抓转换错位。
  起始阶段会先滚到顶、再多次返回直到落在首页（单次返回在搜索页常常不够）。
  **失败会自动重试一次**（重试前清掉上次的失败记录、重新从首页走），
  避免把渲染时序造成的偶发失败当成真退步。
  **找搜索入口前会先向下滑回顶部**（06:30 实测：顶栏隐藏时搜索入口直接找不到，
  重试也救不回来，必须先滚回顶部）。
  02:46 做过**双向验证**（不碰设备、monkey-patch 单次执行）：
  ① 第一次失败 + 第二次成功 → 调用 2 次、失败记录清空、判定通过；
  ② 两次都失败 → 调用 2 次、只保留最后一次的失败、不会无限重试。
- **锚点稳定性采样**：`--stability SERIAL0001 [次数]` ——
  在同一页连续 dump 多次，统计每个 identity 锚点的命中率，区分两种情况：
  **"时有时无"（真不稳定，不能单独当锚点）** 与 **"全程未出现"（被交互状态隐藏）**。
  01:01 长按表情托盘时 `Mute video` 就是这样消失的，靠这个工具可以复现和发现。
  实测首页：顶部 4 个锚点 4/4 稳定，广告类锚点 0/4（只在滚动到广告位置时才出现）——
  **两组锚点互补**，正好覆盖"顶部"和"滚动后"两种状态。
- **浸泡测试**：`--soak SERIAL0001 [轮数]` —— 连续多轮跑端到端回归，抓间歇性失败。
  **只跑 walk 不跑配方**：配方那套带前置返回和重试，一轮十几分钟，塞进浸泡里没意义
  （实测跑 7 分钟还没完，已砍掉）。已累计跑过 **2 + 3 + 5 + 3 轮**，
  其中 **04:34 那次第 2 轮首次尝试失败、自动重试后通过**——
  说明间歇性失败真实存在，重试机制不是摆设（此前只做过逻辑层验证，这次是实战生效）。
  重试成功**不等于**那一次失败不存在，排查这类问题时要看日志里的"重试第 N 次"。

**那次偶发失败的根因（04:42 定位、04:48 修复）**：
日志里是 `拒绝：元素内部找不到避开高风险控件的点`，目标是**通知 tab**。
也就是说——通知 tab 内部所有候选点（中心/1/4/1/8/1/12）都落在某个高风险矩形里，
于是**整个正常导航被安全闸门拒掉**，后面两步连锁失败。
修法：**目标本身不是高风险控件时，退化为点元素中心并打印警告**，
只有目标自己是 send/forbidden 才拒绝。修完连跑 3 轮浸泡，全通过、未再触发重试。

**退化逻辑有双向验证（05:02，纯逻辑、不碰设备）**：
① 普通目标 + 全屏高风险遮挡 → 退化为点中心 `(200,150)`、返回 True；
② 目标自己就是 send → **拒绝且 0 次点击**。安全边界没被放宽，只是不再误伤正常导航。

**关键页稳定性实测（01:12–01:17，各采样 4 次）**：

| 页面 | 稳定锚点 | 未出现（依赖滚动/状态） | 判定一致性 |
|---|---|---|---|
| 首页 | 底栏首页tab、发帖输入框、动态区、Facebook logo | 广告归属标记、隐藏广告 | 4/4 |
| 搜索结果页 | 分类标签 ×2、清空输入 | `這些搜尋結果是否實用？`（在页面下方） | 4/4 |
| **搜索页（空态）** | 返回、无历史文案 | 近期分组、查看全部历史、清空输入（暂无历史所以不渲染） | 4/4 |
| 社团"查看全部"列表 | 筛选按钮、城市 chip、公开社团 chip | — | 4/4 |
| 社团详情 | 社团封面、成员数、发言入口 | — | 4/4 |

- 结论：**每页都至少保留 2–4 个与滚动位置无关的锚点**，即使某几个在特定状态下不渲染，
  判定依然成立。这正是之前补锚点的目的。
- 场景驱动：`python fishfleet/web/facebook_flow.py`
  （`search <词>` / `dump` / `open <页> <控件>` / `open_person` / `tap <语义>` /
  `type <文字>` / `top` / `find_photo` / `find_tag` / `export`；
  每次 dump 落盘到 `huicheng-auto/data/facebook_dumps/` 便于复盘）
- **导出可复用资料**：`python fishfleet/web/facebook_flow.py export`
  → `huicheng-auto/data/facebook_ui_library.json`（39 页 / 393 控件）。
  导出物**只含定位规则**（正则 / 控件类型 / 风险等级 / 结构性重复标记），
  **不含 ref、坐标或任何账号数据**，自检里有断言专门守这一点。
  后续任务可以直接读这个 JSON 做编排，不必导入 Python 模块。
- **只用 JSON 驱动任务的示例**：`python fishfleet/web/facebook_recipe.py`
  （`check` 校验导出物 / `list` 列配方 / `run <配方>` 真机执行）。
  这个脚本**刻意不导入 `facebook_templates`**，只吃导出的 JSON ——
  跑得通就说明资料库可以脱离原模块独立使用。
  已有只读配方：`look_home`（确认首页 + 列出可用控件）、
  `look_notifications`（开通知再回首页）、`look_search`（开搜索再回首页）。
  实测 `look_home` 用 JSON 定位到 16 个控件，`look_notifications` 跳转与返回都正确。
- **风险闸门**：脚本里点任何东西前都会先查 JSON 中该控件所属的 `risk`，
  `send`/`forbidden` 默认拒绝；同时用「安全点」避开页面上的高风险矩形
  （和 21:02 误触「加入」那次事故后加的机制同源）。
  跨页配方 `look_group`（搜索 → 结果 → 社团详情 → 返回首页）实测通过，
  点击社团条目时自动选到 `(270,627)`，避开了 `x=494-576` 的「加入」按钮。
- 现有只读配方（**5 个，已串联跑通、互不干扰**）：
  `look_home`、`look_search`、`look_notifications`、`look_group`、`look_pages`。
  每个配方都从首页出发、结束回到首页，可以顺序连续执行。
  其中 `look_group_files` 是**三级链路**：搜索 → 社团详情 → 檔案分页 → 返回，
  实测通过（会经过两次不同页面跳转，验证了深层导航的判定稳定性）。
  `look_comments` 是**内容型链路**：滚动找带评论的帖子 → 打开评论区 → 返回，
  实测滑 3 屏命中并正确进入 `post_detail`。
  `look_page` 是**专页链路**：搜索 → Pages 分类 → 点第一个专页 → 返回首页，
  实测通过（`search_results` → `page_detail` 判定正确）。
- **一个误导性报错的修复（05:18）**：首页顶栏会随滚动隐藏，搜索入口变 **0 个**，
  但代码写的是 `if len(el) != 1: 打印"搜索入口不唯一"`——**0 个被报成"不唯一"**，
  排查时容易往错方向找。已改成：先向下滚 3 次回顶部再找，
  并**把"找不到"和"命中多个"分开报**。10 个配方复跑全部通过。
- **同类问题在 `flow.py tap` 里也有（05:30 一并修）**：原来 0 命中会报
  「不是唯一命中；拒绝点击」，同样把"没找到"说成"找到太多"。
  现在分成：「没有命中任何元素」与「命中多个、无法确定点哪个」。
  实测 `tap "^这个肯定不存在xyz$"` 会给出前一句。
- **配方里也统一了（05:50）**：新增 `_one(el, what)` 辅助函数，
  0 个报「找不到 X（可能滚出屏幕或被隐藏）」、多个报「X 不唯一（命中 N 个）」，
  通知 tab / Pages 分类 / 檔案分页三处都改用它。受影响的 4 个配方复跑全部通过。
  注意信息流本身是沉浸式的：**滑动过程中页面会在 `home` 与 `post_full` 之间切换**，
  判定每屏都要重新做，不能沿用上一屏的结论。
  `look_replies` 在此基础上多一步「展开更多回复」：先**主动逐条试开评论**，
  找到带 `View N more replies` 的那条再展开。
  01:43 实测命中（354 条评论的帖子），展开后元素数 **105 → 107**，
  页面仍是 `post_detail` —— 这一步现已真实验证，不再是"只验证了跳过"。
  `look_reactions` 是**长按类**：找到帖子 → 长按「赞」→ 弹出 `reaction_tray`
  → 按返回关闭 → 回首页。实测弹出与关闭都正确，**全程没有选任何表情**。
- **内容依赖要单独处理**：`look_replies` 的第一步是"找带嵌套回复的帖子"，
  而这类帖子不是随时都有（02:52 那次试了 6 屏全都没有）。
  已改成：最多试 10 屏，仍找不到就**判定为「内容条件不满足，跳过」而不是失败**——
  把内容偶然性记成脚本失败会污染回归结果。03:27 复跑时正好遇到，
  展开后元素数 101 → 104，验证通过。
- **配方有前置条件**：执行前会先把设备带回首页（最多 6 次返回），
  所以从任意页面启动都能正常运行。实测从通知页启动时输出
  `前置：1 次返回后回到首页`，随后正常完成配方。
- **失败会重试一次**：这些交互依赖渲染时序（列表还在加载、弹层没全出来），
  一次失败常常只是"太早"。重试前强制回首页，避免在半路状态上瞎试。
  用临时造一个必失败配方验证过：第一次失败 → `回首页后重试` → 第二次仍失败 → 判定失败。
- **点击策略只有一份实现**：`facebook_live.tap_with_gate(session, el, els, risk_lookup)`。
  风险判断由调用方注入（flow 用模板查、recipe 用 JSON 查、`--walk` 也用模板查），
  但「查风险 → 选安全点 → 点击」的流程共用同一段代码，避免三处逻辑各自漂移。
- **风险定义也只有一份**：`facebook_templates.risk_of_element(el)`。
  之前 flow 和 `--walk` 各写了一模一样的反查逻辑，已合并。
  recipe 不导入模板（刻意如此），但它读的 JSON 是同一份数据派生的。
- **导出同步检查（页面级）**：自检会比对磁盘上的 `facebook_ui_library.json` 与模板的  ①页面集合 ②每页覆盖等级 ③每页控件名集合 ④风险定义，任一不一致就报错并提示重新 `export`。
  02:25 做过**反向验证**：故意从磁盘 JSON 里删掉 `home.menu` 并把 `home` 的覆盖等级改错，
  自检准确报出三项
  （`覆盖等级不一致` / `控件名集合不一致` / `风险定义不一致`），重新导出后恢复全绿。
- **资料库审计**：`python fishfleet/web/facebook_selftest.py --audit`
  （离线，不碰设备）输出每页的锚点数/互斥锚点/控件数/可定位数/send/forbidden，
  并写出 `reports/facebook_ui_audit.json`。
  02:10 审计结论：**39 页 / 393 控件，每页锚点都够**（≥2 个 identity 或互斥锚点）；
  覆盖分布 `verified=13, collected=16, partial=8, blocked=2`；
  高风险集中在评论区（send=5）、表情托盘（send=7）、分享面板（send=5）、更多菜单（send=5,forb=1）。
- **最新审计（03:34，50 页）**：462 控件、send 76 / forbidden 14，
  **每页仍有 ≥2 个 identity 或互斥锚点**；"内容类占位"共 13 个分布在 11 页
  （评论正文、投票问题、专页名等），它们不参与定位、只作说明。
- **交付报告**：`python fishfleet/web/facebook_selftest.py --report`  → `reports/facebook_ui_report.md`，含规模/覆盖/风险统计、逐页清单（锚点·控件·send·forbidden）、
  「明确受限」清单（每项一句话摘要 + 覆盖等级），以及**「怎么用」**一节
  （指向详细文档、`--help`、资料库文件、导出 JSON）。
  末尾明确写着「不把"已采集控件"当成"完整任务已实现"」。



## 重大变化：界面语言与底栏结构变了（2026-11-02 08:20 实测）

重启后发现 **FB 界面从「英文/繁体混排」变成了中文，并且底栏从 4 个 tab 变成 6 个**：

| 位置 | 旧 | 新 |
|---|---|---|
| 底栏 | Home / Friends / Reels / Notifications（4 个，英文） | 首頁 / Reels / 朋友 / 社團 / 通知 / 個人檔案（6 个，中文） |
| 抽屉 | Facebook menu | Facebook 功能表 |
| 搜索 | Search Facebook | 搜尋Facebook |
| 新建 | Create, Double tap… | 建立，點按兩下可建立新貼文、限時動態或 Reel |
| 消息 | Messaging | 發訊息 |
| logo | Facebook logo | acebook 標誌 |
| 通知分组 | 新通知 / 先前的通知 | 今天 / 先前的通知 |
| 已读标记 | 9h. Read | 10小時. 已讀 |

**「社團」「個人檔案」是新增的底部 tab**，这是之前完全没有的结构。

已把 home / search / notifications 的关键锚点改成**中英双语 + 容忍 tab 数量变化**的写法
（如 ^(Home, tab \d+ of \d+|首頁[，,])）。

**教训**：这批模板里凡是写死英文完整文案的地方，遇到界面语言或版本变化就会整体失效——
而且失效方式是页面判不出，不是报错。后续新增锚点应尽量用**名称前缀 + 双语**。



## 评论采集 + 中转站 + 模拟回复（2026-11-02 09:40 接入）

### 用的是项目里已有的中转站
ishfleet/web/lead_store.py（SQLite data/lead_pool.sqlite），
每个平台一张表。已新增 **leads_facebook**（14 个业务字段）与去重键
comment_author + comment_text + post_author。

**Facebook 的特殊限制（必须知道）**：UI 里**读不到稳定的用户 ID**，
不像抖音有 douyin_id、小红书有 
ed_id、B站有 uid。
联系对象只能靠「昵称 + 帖子上下文」定位，**昵称会重名、会改**。
表结构和备注里都如实写了这一点，没有套用别的平台的唯一号假设。

### 评论条目怎么解析
稳定锚点是**回复按钮的说明文字**：
對<作者>留言的「回覆」按鈕。點按兩下可回覆留言。
它把作者名和这条评论绑在一起，比按坐标猜可靠。

正文限定在「作者名」与「时间」之间取——直接取区间最长会把**回复内容**当成正文
（09:00 实测踩到，回复通常比原评论长）。
点赞数、时间同理要从作者名之后开始找，否则会取到上一条的（09:10 踩到）。

### 采集入口：只能用首页 feed，不能用搜索结果
搜索结果里点开的帖子是**精简页**，留言 按钮点不动（有时只是聚焦/滚动），
连续两次确认都进不去评论区（10:20–10:40 反复实测）。
首页 feed 上带 N則留言 的帖子能稳定点开。
所以 acebook_comments.py feed 模式可用，collect（搜索式）**暂时不通**。

用法：
`
python fishfleet/web/facebook_comments.py feed 关键词 2 10   # 采 2 个帖子、每帖 10 条
python fishfleet/web/facebook_comments.py pool                # 看中转站
python fishfleet/web/facebook_reply.py --dry                  # 列待回复任务（不碰设备）
python fishfleet/web/facebook_reply.py 谢谢分享～            # 只填草稿，**不发送**
python fishfleet/web/facebook_reply.py 谢谢分享～ --send      # 真发（需主人明确授权）
`

### 回复的安全设计
- **默认只填草稿、不发送**，状态记 drafted，停在发送键前等人工放行。
- 真正发送必须显式 --send，且每条会先打印将要发送的内容。
- 内容兜底过滤：命中政治敏感 / 违法风险 / 营销引流 特征就跳过并记 skipped。
  这只是兜底规则，**不是内容审核**。
- 已入库的 14 条样本里，3 条政治内容会被跳过、不自动回复。



### 单条闭环已验证通过（2026-11-02 12:30，连续 2 次）

验证脚本 `facebook_verify_one.py`，在**已打开的评论区**里走完整流程，
四项全中才算通过：

```
① 解析评论：2 条
② 目标评论：Andy Teo Yong Han / Thanks Love GIF
   点开回覆按钮：@e73
   输入框：@e93 (AutoCompleteTextView)
③ 草稿回读：出现
④ 识别到发送键：@e96 「傳送」（**未点击**）
判定：通过（四项全中）
```

**关键发现：发送键不在屏幕底部，而是在输入框同一行右侧。**
12:10 之前我在底部（y>1200）找，那里只有系统导航栏，所以一直找不到；
改成"按输入框位置就近找"后立刻命中。

**这一轮踩过并修掉的坑**：
- feed 互动栏只有「留言」两字、不带数字 → 只认带数字的会永远 0 命中
- `claim` 返回的单和 listing 顺序不同 → 比对 ID 会让每条都被 release
- back 太多会退出到系统桌面 → `ensure_home` 改成"不在 FB 就重新拉起"
- 新版 6 个 tab，靠 back 回不稳 → 优先点底栏「首頁」
- 正文误取回复内容 / 点赞数取到上一条 / `查看1則回覆` 当正文 → 已逐个修



### 采采回复流水线跑通（2026-11-02 13:04，20 条 100%）

`facebook_pipeline.py`：切 Reels → 打开某视频评论区 → **逐屏**「采这一屏 → 立刻回复这一屏 → 滚下一屏」。

20 条抽查结果：

```
采集条数        : 20
抽查条数        : 22
定位到评论      : 22
草稿回读成功    : 22
识别到发送键    : 22
四项全中占比    : 100.0%
```

**为什么要"逐屏采、立刻回"**（这一轮最关键的坑）：
一开始是"先滚到底采完、再回来逐条回复"，结果 100% 报"找不到回覆按钮"。
加诊断打印才发现——**目标是 `Fadhil Naji`，而当时屏幕上可见的是 `Mia Ling / 劉宜樂 / 徐建宏`**，
完全不是同一批人。因为采集时滚动了，回复时再滚回顶部看到的是顶部那批。
改成逐屏处理之后立刻正确。

**另外两个坑**：
- `collect_from_current_post(scrolls=1)` 里循环末尾**仍会 swipe 一次**，所以"只采当前屏"根本没生效
  → 改成最后一轮不滚动。
- 收起键盘不能点屏幕上方空白（那是评论区的关闭区，一点整个浮层就关），
  也不能用 `back`（同样会关掉评论区）→ 用一次返回键只收键盘。

**发送键位置**：不在屏幕底部，在输入框同一行右侧，`傳送`。只识别、不点击。

### 当前进度
| 步骤 | 状态 |
|---|---|
| 中转站建表 + 写入 | **通**（14 条真实评论已入库） |
| 评论解析（作者/正文/时间/赞） | **通**（实测 3 条全对） |
| 从 feed 打开评论区采评论 | **通** |
| 从搜索结果进评论区 | **不通**（已记为限制） |
| 回复时定位到那条评论 | **未跑通**（打不开评论区，需继续调） |
| 真正发送 | **从未执行** |



## 私信链路（2026-11-02 17:40 打通）

### 怎么"二次找到这个人"——这是最关键的一问

**正确路径**：评论 → 点评论作者的名字 → 他/她的主页 → `發送訊息` → 私信会话。

实测：
```
点: 林立偉 @e66
前台: ImmersivePortraitActivity
判定: person_profile
按钮: [Cover Photo, 大頭貼照, 林立偉, 756 位位朋友, 加朋友, 發送訊息, 全部, 相片, Reel, ...]
点「發送訊息」后:
前台: com.facebook.messaging.msys.thread.fragment.MsysThreadViewActivity
```

**为什么这条路可靠**：评论里作者名是一个**可点击的 Button**（`林立偉`），头像 label 是
`林立偉大頭貼照`，两者同一行同一 y，可以精确配对。点名字进去是确定性的关系，
**比按昵称搜索可靠得多**（搜索是模糊匹配，重名必错）。

### 私信不需要装 Messenger

进入的是 **FB 内置消息** `MsysThreadViewActivity`（Messaging in Blue）。
首次使用需要先做端对端加密的云备份设置（建立 PIN 码），**这一步由主人完成**。

### 私信会话页的 UI（全部已定位）

| 控件 | 元素 | 位置 |
|---|---|---|
| 输入框 | `EditText '輸入訊息'` | (318,818) 552x95（键盘弹出后） |
| **发送键** | `Button '發送👍'` / 空输入时 `傳送` | (964,793) 116x136 |
| 附件 | `Button '其他附件選項'` | (0,793) |
| 相机 | `Button '開啟相機。'` | — |
| 相册 | `Button '開啟相片圖庫。'` | — |
| 录音 | `Button '開啟錄音器。'` | — |
| 表情贴图 | `Button '開啟貼圖、表情符號和 GIF 鍵盤。'` | (870,818) |
| 取消回复 | `ImageView '取消回覆訊息。'` | (985,712) |
| 招呼提示 | `ViewGroup '打聲招呼。'` | (64,717) |

**两个坑**：
1. **发送键文案会变**：输入框为空时是 `傳送`，输入内容后变成 `發送👍`。
   只写死一种会漏判。正则要写成 `^(傳送|發送👍?)$`。
2. **这一页无障碍信息极贫瘠**：键盘收起时整屏**没有任何 FB 元素**（只有系统栏），
   输入框只在**键盘弹出后**才可读。所以必须先点屏幕下部（y≈1700）激活输入状态，
   才能判定页面、找到输入框和发送键。

### 输入框验证结果

```
输入框数: 1
   EditText 318,818 552x95 '輸入訊息'
输入: True
回读到草稿: 1
   EditText '测试草稿不发送'
```
草稿确实进了输入框，**没有点发送**，随后已清空。

### 重要更正

会话里显示的 `你好 👋` / `林立偉，你好！` 是 **Facebook 预制的打招呼用语**，
**不是真实历史消息**。采集时不要把它误当成对方说过的话，也不要据此判断"聊过"。



## 严重 bug 修正：评论正文取错了元素类型（2026-11-02 19:40）

**主人一眼看出来的问题**：采到的 `评论内容` 里有大量 `正在回覆X`、`頭號粉絲`、`作者說讚`，
而且昵称和内容对不上，明显错位。

**根因**：评论正文在层级里是 **`Button`，不是 `ViewGroup`**。
真实结构（实测）：

```
y=669 ImageView 林立偉大頭貼照
y=669 Button    林立偉            ← 作者名（可点击，是联系人的入口）
y=670 ViewGroup 6週               ← 时间
y=738 Button    be whi you are    ← 正文（**Button**）
y=797 Button    說林立偉的留言讚的按鈕…
y=801 Button    對林立偉留言的「回覆」按鈕…   ← 锚点
y=808 Button    4個心情
```

旧代码只按 `ViewGroup` 找正文，所以**一条真实正文都没采到**，
采到的全是时间/心情数/其他 ViewGroup，表现出来就是"错位"。

**修法**：正文取「作者名之后第一个合格的 `Button`」，并排除功能按钮与噪声
（`說…按鈕` / `對…按鈕` / `不推…` / `開啟…` / 时间 / 心情数 / `正在回覆X` 等）。

**修正后实测**（一次采 17 条，逐条正确）：
```
符麓受      | 26週 | 25個心情 | '台灣有這麼厲害修寶馬的師傅嗎？…'
Kokkeong Wong| 26週 | 5個心情  | '赖,,请问G20,,320能入手吗？'
林立偉      | 6週  | 1個心情  | 'be who you are'
Forhad Alam | 6週  | 1個心情  | '做你自己'
```

**教训**：不能靠"元素类型像不像"去猜正文，要用**一条真实评论把结构打印出来**核对。
这次的错误是靠主人肉眼发现才暴露的，说明之前的抽样验证不够——只验证了"流程通过"，
没验证"内容对不对"。



## 唯一身份标识：找到了（2026-11-02 17:20）

**主人问的核心问题**：FB 没有 UID，重名怎么办，多台手机怎么精确定位同一个人。

**答案：有 UID，藏在对方主页的「更多個人檔案設定」里。**

### 怎么取

对方主页 → `更多個人檔案設定`（需要往下滚才出现）→ 展开后页面上**直接显示明文链接**：

```
@e70 ViewGroup text="https://www.facebook.com/profile.php?id=61553716394951"
```

`61553716394951` 就是 **FB 的 UID**，和抖音号、小红书号等价。

**不需要点「複製連結」，也不用读剪贴板**——那一行是普通 ViewGroup，文本直接可读。

### 验证可用性（实测）

用 `open_url` 打开该链接：

```
open_url: https://www.facebook.com/profile.php?id=61553716394951
结果前台: com.facebook.katana.immersiveactivity.ImmersivePortraitActivity
判定: person_profile
按钮: [Cover Photo, 大頭貼照, Cherise Agolength, 加朋友, 發送訊息, ...]
```

**直接跳进 App 里那个人的主页**，不是网页、不是浏览器。

### 为什么这一条解决了重名

| 方式 | 可靠性 |
|---|---|
| 按昵称搜索 | 模糊匹配，重名必错，改昵称就失效 |
| 靠"帖子+评论上下文" | 帖子会沉、会删，跨手机不可复现 |
| **`profile.php?id=数字`** | **唯一、稳定、可跨手机复现** |

### 完整链路（现在闭环了）

**采集端**：
```
评论区 → 解析作者/正文/时间/点赞
       → 点作者名 → person_profile
       → 更多個人檔案設定 → 读 profile.php?id=数字
       → 连同评论一起写入中转站 leads_facebook
```

**发送端（任意一台手机）**：
```
从中转站领任务（含 commenter_profile_id）
→ open_url(https://www.facebook.com/profile.php?id=<ID>)   # 直达本人主页
→ 点「發送訊息」
→ 填草稿
→ 停在「發送👍 / 傳送」前等人工放行
```

### 表结构已更新

`leads_facebook` 新增两列：
- `commenter_profile_id` — `profile.php?id=` 后面的数字（**唯一身份**）
- `commenter_profile_url` — 完整链接

### 代码

`facebook_comments.fetch_profile_id(s, author)` — 从当前主页提取 ID 与链接，
取完自动点一次「返回」收回菜单。



### 唯一 ID 实测结果：18/18，100%（2026-11-02 17:27）

脚本 `facebook_collect_ids.py` 跑 18 条：

```
尝试条数      : 18
取到唯一 ID   : 18      ← 100%
进不了主页    : 0
取不到 ID     : 0
写入中转站    : 15（3 条同人重复已去重）
```

### 两种 profile 链接都要支持

FB 的链接有**两种形式**，都是唯一标识：

```
https://www.facebook.com/profile.php?id=61553716394951   （数字 ID）
https://www.facebook.com/chew.on.1                       （自定义用户名）
```

只认其中一种会漏掉一大半人。正则要写成：

```python
r'https://www\.facebook\.com/(?:profile\.php\?id=(\d+)|([A-Za-z0-9._]+)/?)\s*$'
```

**两种都实测可用**：`open_url` 打开后直接进 App 内的该人主页
（`ImmersivePortraitActivity`，页面上有 `發送訊息`）。

### 踩过的三个坑（都影响取到率）

1. **判定 homepage 太急**：点进主页后 4 秒常常只到过渡态，`page_of` 还返回 None。
   → 改成轮询最多 12 秒。
2. **只认 `page_of == person_profile` 也不行**：实测有些人主页判 None，
   但 Activity 已经是 `ImmersivePortraitActivity`、页面上也有 `發送訊息`。
   → 改成"page_of 或 Activity 或页面特征"三者任一成立。
   （这一条把取到率从 43% 直接拉到 100%）
3. **头像匹配太严**：原来要求头像 label 含"大頭貼照"且 y 差 ≤120；
   实际有"未讀限時動態"后缀、也有直接就是名字的。
   → 改成"以作者名开头 + y 差 ≤200"。

### 关于耗时

每条大约多花 10~15 秒（进主页 + 展开菜单 + 读链接 + 返回）。
主人明确说过不介意慢，只要稳——**慢换准，这个取舍是对的**。



### 发送端实测：5/5，100%（2026-11-02 18:10）

脚本 `facebook_dm_send.py`，用中转站里的 profile URL 逐个验证**只填不发**：

```
===== [3/5] Mohammad Ansar =====
  URL: https://www.facebook.com/muhammad.ansar.927
  ① 打开主页：True
  ② 进入私信：True
  ③ 输入框：True  填入：True
  ④ 发送键：True '傳送'（**未点击**）
  本次：通过

测试条数: 5
四项全中: 5
成功率  : 100.0%
**全程未点击发送**
```

四条判定（主人定的标准）：
1. **用 profile URL 能打开该人主页** ✔
2. **能点开「發送訊息」进入私信会话** ✔
3. **指定回复语真的填进了输入框**（回读确认，不是"以为填进去了"）✔
4. **能识别到发送键**（`傳送` / `發送👍`）✔
5. **不点击发送** ✔

### 关于发送键文案

这批实测识别到的都是 `傳送`（空输入框状态）。
之前某次输入内容后变成 `發送👍`，说明文案随状态变，
所以正则是 `^(傳送|發送👍?)$`，两种都收。

### 收尾动作

每条验证后都会：清空草稿（BACKSPACE ×60）→ 退出会话 → 退出主页，
**不留下任何草稿**。

### 现在整条链路是通的

```
采集端：评论区 → 点作者名 → 主页 → 读 profile ID → 入中转站
发送端：领任务 → open_url(profile) → 發送訊息 → 填草稿 → 识别发送键 → **停**
```

**唯一还缺的是"真的点发送"**——这一步按主人规矩必须人工确认，代码里始终停在它前面。

## 修订记录

- 2026-11-01 20:42–20:53：首轮采集 14 页（首页、菜单、搜索空态、好友、Reels、
  通知、发帖编辑器、评论、个人主页、社团、好物特蒐、动态回顾、消息、通知详情）。
- 2026-11-01 21:00：关键词 `marketplace` 搜索，新增「搜索结果页」。
- 2026-11-01 21:02：**误触「加入」事故**，加入一个公开社团；见「操作事故与纠正」。
- 2026-11-01 22:49–22:52：补采 Groups／People 分类、他人个人主页、
  Messenger 安装提示页；确认私信链路被拦截。
- 2026-11-01 22:56–23:07：补采 Events／Pages／Reels／Marketplace 四个分类、
  Marketplace 不可用页、`Filters` 面板、`Settings & privacy` 展开项；
  修复三处判定误判（见「判定层的三个坑」）。
- 2026-11-01 23:09–23:13：展开 `Help & support`（4 项）；实测发帖链路
  （输入正文 → POST 变可用 → 放弃草稿，未发布未留草稿）；
  新增「放弃草稿对话框」与背景选择条。
- 2026-11-01 23:18–23:22：发现**首页滚动后顶栏与 tab 一起隐藏**，补信息流兜底锚点；
  新增「单条动态详情页（视频帖）」。
- 2026-11-01 23:24–23:27：补采评论区底部工具条（`Show photos and videos`／
  `Open GIF search`／`Show stickers`／`Send`，未输入时 disabled）；
  实测回复展开；删除一个会一次命中 8 个元素的过宽正则（评论作者）。
- 2026-11-01 23:30–23:32：补采 Reels 播放态控件（`Rewind 10 seconds`／
  `Play`／`Fast forward 10 seconds`）；修复场景脚本里 `screen_size` 解包颠倒的 bug。
- 2026-11-01 23:34：新增「分享面板」；给场景脚本加**语义风控**——
  按模板反查风险等级，`tap` 默认拒绝 send/forbidden 控件，
  需要"只打开不确认"时显式加 `--allow-send`。
- 2026-11-01 23:40–23:44：实测贴纸面板是**整屏覆盖层**（评论区锚点全部消失），
  独立成页；GIF 面板同样是底部覆盖层，也独立成页。
- 2026-11-01 23:51–23:55：新增 `_safe_point()` 高风险区域避让点击，
  用它安全点开社团条目，补采「社团详情页」。
- 2026-11-01 23:55–00:01：实测社团三个分页（相片／活動／檔案）都是**独立全屏页**，
  分别建模。
- 2026-11-02 00:01–00:06：补完社团剩余分页（相簿／Reel），六个分页全部覆盖。
- 2026-11-02 00:08–00:12：发现搜索页**有历史时是另一套结构**（原建模直接判不出），
  两套锚点都纳入；新增 `--walk` 端到端导航回归，并据此发现
  **搜索页需要按两次返回才回首页**。
- 2026-11-02 00:13–00:16：确认**个人主页分页是页内切换**（与社团分页跳独立页相反），
  补「相片」分页下的两个子分类。
- 2026-11-02 00:17–00:21：写 `find_photo` 探测命令找到图文帖，新增
  **图文帖详情页**，并发现帖子可内嵌**互动投票单元**。
- 2026-11-02 00:22–00:26：新增**帖子更多操作菜单**（含举报／保存／推荐偏好），
  举报按 forbidden 处理。
- 2026-11-02 00:27–00:32：修正「通知详情一律是 WebView」的错误结论——
  **社团类通知会进原生页**；补「已加入社团」状态按钮。
- 2026-11-02 00:31–00:34：新增**限时动态编辑器**与**放弃确认框**；
  编辑器里的发布键按 forbidden 处理。
  另：本轮想在 feed 里找 `#话题` 标签，滑了 8 屏没遇到，**话题标签页仍未覆盖**。
- 2026-11-02 00:35–00:40：用关键词 `漲知識` 复测搜索，发现**分类标签的序号会随关键词变化**，
  原模板把序号写死会失效，已改为只按名称匹配；新增**社团发帖框**。
- 2026-11-02 00:41–00:58：确认**话题页在该版本不可达**（搜 `#marketplace` 只返回帖子/Reels）；
  补搜索页**第三种状态**（只有输入框+清空+返回）；发现**社团搜索输入框不进无障碍层级**，
  并补社团搜索结果页。
- 2026-11-02 01:01–01:12：补社团搜索的 `Group Posts` 视图（筛选 chip + 帖子格）；
  期间踩到**锚点权重失衡**（搜索页锚点压过社团搜索页），已收窄并记录。
- 2026-11-02 01:15–01:25：给自检加**资料库体检**（正则可编译、覆盖等级、forbidden 说明、
  内容类占位只提示），修掉 `home.add_email` 缺说明、补两个该可定位的 type 锚点。
- 2026-11-02 01:00：把「输入框不在层级里」的处理抽成
  `DeviceSession.type_anywhere()`，flow 的 `type` 命令已改用它。
- 2026-11-02 01:08：端到端回归扩到 10 步，新增「输入关键词 → 结果页 → 返回首页」；
  起始阶段改为"滚到顶 + 多次返回到首页"。
- 2026-11-02 01:17：**话题标签终于验证到底**——它不是独立话题页，点一下
  等于以 `#词` 搜索，落到搜索结果页；补 `post_item` 结果类型。
- 2026-11-02 01:01：**长按 Like 采到表情托盘**（7 种表情，自定义 View
  `RopeStyleUFIDockView`）；期间发现长按会让 `Mute video` 等控件隐藏、判定短暂失效，
  已给视频帖详情补 `Video`/`Comment` 锚点。
- 2026-11-02 01:08：新增 `--stability` 锚点稳定性采样工具，能区分"时有时无"和
  "全程未出现"两种失效；实测确认 `Mute video` 属于后者。
- 2026-11-02 01:20–01:32：首页稳定性采样通过（锚点互补）；全量回归（离线/live/walk）全绿；
  新增社团**「查看全部」列表页**（带筛选 chip，之前被误判成 search）。
- 2026-11-02 01:12–01:17：用 `--stability` 扫了首页、搜索结果页、"查看全部"列表、社团详情，
  四页判定均 4/4 一致，锚点足够冗余。
- 2026-11-02 01:24–01:37：发现**社团页默认 tab 会随"是否加入过社团"变化**
  （探索 → 為你推薦），原建模直接判不出，已补齐三个 tab 的锚点；
  同时消掉 `你的社團` 的命名冲突。
- 2026-11-02 01:20–01:22：走通「社团页 → Search groups → 输入 → Group Posts → 筛选面板」，
  发现**筛选面板内容随分类变化**，已分两套建模；社团搜索结果页补 exclusive
  （Groups + Group Posts 同时出现即判定，解决与 search 的并列）。
- 2026-11-02 01:25–01:28：端到端回归扩到 **15 步**，新增「社团页 → 社团搜索 → 输入 →
  Group Posts → 首页」整条链路。
- 2026-11-02 01:35–01:42：新增 **JSON 导出**（`export` 命令 →
  `data/facebook_ui_library.json`），导出物只含定位规则、不含 ref/坐标/账号数据，
  自检加了对应断言。
- 2026-11-02 01:45–01:59：新增 `facebook_recipe.py` —— **完全不导入模板模块**、
  只用导出的 JSON 驱动只读任务，实测 `look_home` / `look_notifications` 均通过，
  证明资料库可脱离原模块复用。
- 2026-11-02 02:02–02:12：给任务脚本加**风险闸门 + 安全点点击**，
  新增跨页配方 `look_group`（搜索→结果→社团详情→返回），实测通过并自动避开了
  「加入」按钮。
- 2026-11-02 02:15–02:20：新增 `look_pages` 配方（搜索 → Pages 分类 → 返回），
  用「分类标签变 selected」作为切换成功的判据；5 个配方串联执行全部通过。
- 2026-11-02 01:36–02:00：把「风险闸门 + 安全点」收敛成
  `facebook_live.tap_with_gate()` 一份实现，flow / recipe / `--walk` 三处共用，
  风险判断由调用方注入。期间踩到 PowerShell `Set-Content -Encoding UTF8` 会加 BOM
  的坑（Python 直接 `SyntaxError: U+FEFF`），改用 `utf-8-sig` 读取去 BOM 解决。
- 2026-11-02 02:05–02:12：新增三级链路配方 `look_group_files`
  （搜索 → 社团 → 檔案分页 → 返回），实测通过。
- 2026-11-02 02:15–02:20：新增内容型配方 `look_comments`
  （滚动找带评论的帖子 → 打开评论区 → 返回），滑 3 屏命中并进入 `post_detail`；
  记录"信息流滑动时页面会在 home/post_full 间切换"这一特性。
- 2026-11-02 02:23–02:30：新增 `look_replies`（评论区 → 展开更多回复）；
  配方逻辑通过，但本次样本帖**没有嵌套回复**，该步骤只验证了"识别并跳过"，
  真实展开仍待合适样本。
- 2026-11-02 01:43：把 `look_replies` 改成**主动寻找**带嵌套回复的评论
  （开评论→检查→没有就关掉继续滑），第 3 屏命中 354 条评论那帖，
  展开成功、元素 105→107，**该步已验证**。
- 2026-11-02 01:50–02:02：把风险判定收敛为唯一实现
  `facebook_templates.risk_of_element()`（flow 与 `--walk` 原来各写一份）；
  新增「磁盘 JSON 与模板是否同步」断言。
- 2026-11-02 02:05–02:12：新增 `--audit` 资料库审计，输出每页锚点/控件/风险分布
  并写 `reports/facebook_ui_audit.json`；结论：39 页 393 控件，**每页锚点都够**。
- 2026-11-02 02:15–02:22：给会话加 `long_press()`，新增长按类配方 `look_reactions`
  （长按赞 → 表情托盘 → 关闭 → 回首页），实测通过且没有选任何表情。
- 2026-11-02 02:25–02:30：配方加**前置条件**（先回首页再执行，最多 6 次返回），
  实测从通知页启动能自动归位并正常完成。
- 2026-11-02 02:34–02:40：配方加**失败重试一次**（重试前先回首页），
  用临时必失败配方验证触发路径确实生效。
- 2026-11-02 01:56–02:14：推 `group_reels` 到"点开单条"——发现社团 Reel 播放页
  **只有 4 个控件、视频内容完全不进层级**，已建模并标注为 partial；
  新增页面后「导出同步检查」立刻报出磁盘 JSON 落后，重新 export 后恢复。
- 2026-11-02 02:18–02:28：同步检查从"只比风险"扩展到页面级四项
  （页面集合 / 覆盖等级 / 控件名集合 / 风险），并做**反向验证**：
  故意破坏磁盘 JSON 后三项检查全部准确报警，恢复后全绿。
- 2026-11-02 02:07–02:20：新增 `--soak` 浸泡测试（连续多轮 walk）；
  首版把配方也塞进来，跑了 7 分钟没结束，已砍成只跑 walk，2 轮均 0 失败。
- 2026-11-02 02:24–02:34：`group_reels` 因已点开验证跳转，覆盖等级从 partial 升为
  collected；`memories` 再测时**点开直接回首页**（无内容不常驻），已如实记录。
- 2026-11-02 02:38–02:46：补采搜索页**第 ④ 种状态（输入中建议列表）**，
  建议项 label 是关键词本身、无稳定标签，只能按位置关联。
- 2026-11-02 02:17–02:22：复核通知列表——该账号**只有 3 条通知**（2 Read + 1 Unread），
  下滑无更多、无"更早"分组，长列表形态仍未覆盖，已如实记录。
- 2026-11-02 02:26–02:38：给 `--walk` 补**失败自动重试一次**
  （重试前清掉上次失败、从首页重走），并把单次逻辑抽成 `_walk_once`。
- 2026-11-02 02:42–02:46：给 walk 重试做**双向反向验证**（monkey-patch 单次执行）：
  "失败一次后成功"与"两次都失败"两条路径都符合预期，不会无限重试也不会吞掉失败。
- 2026-11-02 02:50–03:05：新增 `--report` 生成可交付的 Markdown 状态报告
  （规模/覆盖/风险 + 逐页清单 + 受限清单）；顺手修正模板里一处错别字。
- 2026-11-02 03:08–03:14：复核 `People` 分类——**没有 `Filters` 按钮**，
  说明筛选入口只在部分分类出现，已写进分类差异表。
- 2026-11-02 03:16–03:20：逐个核对其余分类——`Groups`/`Events` **有** `Filters`，
  `People`/`Reels`/`Pages` **没有**，差异表已补全。
- 2026-11-02 02:28–02:40：采 `Events` 分类的 `Filters`，发现是**第三套**筛选面板
  （`日期/地點/類別/獲朋友好評`），已独立建模。
- 2026-11-02 02:31–02:52：点开 Events 的「日期」下拉，采集到日期子面板
  （6 个 RadioButton，默认 `任何時間`），独立建模；未改选任何日期。
- 2026-11-02 02:33–03:07：补采 `類別` 子面板（可滚动长列表，首屏 7 项）与 `地點` 子面板
  （只有 `任何地方`，疑与定位权限有关）。
- 2026-11-02 03:12–03:18：核对「查看全部」列表的 `Filters`——内容与 Groups 分类**完全一致**，
  判定复用 `search_filters`，确认不需要为它新建页面。
- 2026-11-02 02:40–03:05：新增「你可能认识的朋友」全量页；新建后发现它与好友页
  锚点数量打平导致双方判不出，给好友页加 exclusive 修复。
- 2026-11-02 03:09–03:22：新增「好友请求全量页」（独有 `Sort` 按钮）；
  三页对比已写清各自区分点，本次新建后老页面判定未受影响。
- 2026-11-02 02:45：补采好友请求排序面板（`Default`/`Newest`/`Oldest` 三选一）。
- 2026-11-02 02:51–03:05：跑 3 轮浸泡测试（每轮 walk）**全部 0 失败**；
  补采抽屉「活動」入口，新增活动主页（只采到筛选栏，列表未加载，记 partial）。
- 2026-11-02 03:08–03:22：复核活动主页仍是空列表（该账号无推荐活动），补 `追蹤中` tab；
  尝试进 `遊戲` 未成功（菜单项异步渲染、这次没抓到稳定入口），已如实记录。
- 2026-11-02 03:26–03:38：进一步确认 `遊戲` **不是渲染慢，而是该账号菜单里已没有这一项**
  （等 4 秒四次 dump 都没有）；同时发现**菜单项的内容与顺序整体变了**
  （`好物特蒐` 提前、顺序全变），已写进抽屉菜单节。
- 2026-11-02 03:43–04:06：新增「我的珍藏」页，发现它的编号是 **`1／6`（全角）**，
  与搜索结果页的 `1 of 7` 格式不同，正则不能复用；顺手补回被吃掉的「动态回顾」小节标题。
- 2026-11-02 04:10–04:24：复核「我的珍藏」列表内容——是**空态**（该账号无保存内容），
  补 `珍藏分類`/`近期儲存`/`建立新珍藏分類`/空态文案四个目标。
- 2026-11-02 04:29–04:54：发现**菜单 exclusive 会因滚动失效**（被判成 home，危险），
  已补 6 个菜单项锚点兜底；新增「生日」空态页。
- 2026-11-02 05:00–05:14：新增「粉丝专页」页（最近浏览卡片 + 三个 tab），
  只采到最近浏览一组，其余记 partial。
- 2026-11-02 05:19：复核展开菜单，`讚` 也已不存在（与 `遊戲` 同）——
  当前展开后只剩 `好物特蒐`/`活動`/`粉絲專頁`/`生日` 四项。
- 2026-11-02 05:27：给自检加 `--help`，把离线/真机/场景三类命令和关键文件
  汇总成一份速查；文档「复现方式」也指向它。
- 2026-11-02 05:36–03:28：跑全部配方回归，发现 `look_replies` 会因"刷不到带嵌套回复的帖子"
  而失败；改为最多试 10 屏、找不到按「内容不满足，跳过」处理。
  重跑 9 个配方全部通过。
- 2026-11-02 03:34：重跑审计与报告（50 页规模）：**每页锚点仍充足**，
  内容类占位 13 个分布 11 页，风险 send 76 / forbidden 14。
- 2026-11-02 03:41–03:48：跑 **5 轮浸泡**（每轮 15 步 walk）全部 0 失败；
  累计已跑 10 轮浸泡零失败。
- 2026-11-02 03:37–03:50：发现自己主页「相片」分页里的 `相片` 子分类会**跳独立照片页**
  （之前只验证了分页本身是页内切换），已建模；照片格全部 label 为 `Photo`。
- 2026-11-02 03:55–04:05：修正照片页的 Activity 为 `PandoraTabPagerActivity`；
  发现三个 tab 是**横向 ViewPager**，未显示页的元素坐标会变成负数，
  按坐标点击前必须过滤不可见元素。
- 2026-11-02 04:08–04:20：把可见性判断抽成 `facebook_live.is_visible()`
  并接入 flow / recipe（原来只查 y 不查 x）；单测确认负坐标判不可见。
- 2026-11-02 04:24–04:38：首页弹出**邮箱确认横幅**，已补采；
  横幅含账号真实邮箱，模板只按前缀匹配、**不记录具体地址**；
  确认/新增仍标 forbidden，`Close` 只是关横幅（none）。
- 2026-11-02 04:41–04:52：在横幅存在的情况下跑 walk（15 步全通过，说明横幅不干扰导航）；
  稳定性采样确认搜索页空态由「返回 + 无历史」两点支撑、判定 4/4 一致。
- 2026-11-02 03:49–04:09：采到**搜索历史条目菜单**（删除/钉选，均为本地行为 → none），
  独立建模；没删也没钉选。
- 2026-11-02 04:13–04:24：补采搜索空态里的**推荐好友卡片**（`傳送交友邀請給 …` 是 send、
  `移除` 是 none）；同时观察到**搜索历史会莫名被清空**（04:13 还满的，04:15 就空了），
  所以"查看所有近期搜尋紀錄"这次没能进。
- 2026-11-02 03:54–04:44：趁历史还在快速重试，**进了「搜索历史全量页」**并建模；
  但里面列表已空，条目结构与两种排序差异仍未覆盖，记 partial。
- 2026-11-02 04:00–04:15：**查明搜索历史为空的原因**——`Clear all search history` 带
  **`已停用`**，该账号搜索历史功能是关闭的（不是莫名清空）；
  同时发现 tab 选中态会改 label（`全部` → `全部 Selected`），已改成兼容正则。
- 2026-11-02 04:18–04:28：53 页规模下做**完整回归**：离线断言、`--walk`（15 步）、
  9 个配方**全部通过**；审计确认每页锚点仍充足；报告与审计已刷新。
- 2026-11-02 04:34–04:12：验证首页 feed 的 `Comment` 也落到同一个 `post_detail`
  （复用、无需新页）；记录"关闭评论浮层需要按两次返回"。
- 2026-11-02 04:17–04:32：补采「你的社團」tab 的控件（`Create a group`/`Search`/
  `Sort by: Most visited`/`Sort groups`），并把 `你的社團` 纳入 groups 页锚点
  （之前只靠 `為你推薦` 一个点、数量不够）。
- 2026-11-02 04:37–04:52：补采社团排序面板（`Most visited`/`Groups you manage`/`Groups A-Z`，
  英文文案、只改本地排序）；未改选。
- 2026-11-02 05:02–05:16：**推翻 02:18 关于通知分组的结论**——现在出现了
  `今天`/`先前的通知` 两个新分组（之前只有 `新通知`），说明分组随时间增加；
  顺带修正 `item_unread` 会被多条命中（标 repeated）。
- 2026-11-02 04:17–04:32：点开一条「你可能錯過的貼文」通知，落点是**新的单帖页**
  （带 `你可能錯過的貼文, Heading` 标题、无互动栏），已用标题做 exclusive 建模。
- 2026-11-02 04:38–04:56：该页的「更多选项」复用 `post_more_menu`，
  但菜单里多出 `Turn on notifications for this post` 与 `Add … to Favorites`
  （后者已补、标 send）。
- 2026-11-02 04:59–05:02：55 页规模下刷新报告与审计（每页锚点仍充足）、
  `--walk` 15 步通过。
- 2026-11-02 05:07–05:22：点「Meta 帳號」通知，落点是**可读的原生说明页**，
  推翻 00:29「账号安全类通知进 WebView 不可读」的结论；已建模。
- 2026-11-02 05:26–05:35：给交付报告补「怎么用」一节（指向文档/速查/资料库/导出物），
  并在末尾写明"不把已采集控件当成完整任务已实现"。
- 2026-11-02 04:34：跑 3 轮浸泡，**第 2 轮首次尝试失败、自动重试后通过**——
  重试机制首次在实战中生效（此前只有逻辑层验证）。
- 2026-11-02 04:42–04:56：**定位并修复那次偶发失败的根因**——通知 tab 内部找不到
  无冲突安全点、被闸门整体拒绝；改为"目标非高风险则退化点中心"。
  修复后 3 轮浸泡全通过、未再触发重试。
- 2026-11-02 05:02：对退化逻辑做**双向纯逻辑验证**——普通目标被遮挡时退化点中心（True），
  高危目标仍拒绝且 0 次点击。
- 2026-11-02 04:45–04:52：56 页规模最终回归——离线断言、`--walk` 15 步、
  9 个配方**全部通过**；交付报告已刷新。
- 2026-11-02 04:55–05:18：上一轮走不通的专页详情，改从**搜索结果 Pages 分类**进入成功，
  已建模（新增 `page_detail`），未点追蹤。
- 2026-11-02 05:24–05:04：专页详情下滑后锚点失效（判 None），
  补 `相片, N of 5` 与专页独有的 `提及, N of 5` 作为滚动后锚点，修复。
- 2026-11-02 05:08–05:17：点开「提及」分页，采到帖子卡与分享链接预览/OPEN 外链按钮
  （OPEN 未点）；确认分页切换是页内滑动、判定不变。
- 2026-11-02 05:22–05:38：采「活動」分页（`過去的活動` 分组 + 活动卡，label 动态）。
- 2026-11-02 05:31–05:38：采「相片」分页（瀑布流，首图 717x594、其余 355x352，
  label 全为 `Photo`）；**专页 5 个分页至此全部采过**。
- 2026-11-02 05:41–05:48：新增专页链路配方 `look_page`
  （搜索 → Pages 分类 → 点专页 → 返回首页），实测通过。
- 2026-11-02 05:08–05:24：全量配方回归发现 `look_page` 失败，根因是**顶栏隐藏导致
  搜索入口 0 个、却被误报成"不唯一"**；改成先滚回顶部、并把两种情况分开报，
  10 个配方复跑全部通过。
- 2026-11-02 05:30–05:40：把同样的"0 个 vs 多个"分化也应用到 `flow.py tap`，
  避免"没找到"被误报成"找到太多"。
- 2026-11-02 05:50–05:22：配方脚本也用 `_one()` 统一了 0/多 两种失败（3 处），
  受影响配方复跑通过。
- 2026-11-02 05:24–05:32：完整回归（离线 + walk 15 步 + 10 个配方）**全部通过**；
  交付报告已刷新到 57 页 / 530 控件。
- 2026-11-02 05:39–05:32：切到「你的朋友」分段发现是**空态**——**该账号好友数为 0**；
  空态页原本判 `None`，已补 `你的朋友` + 空态文案两个锚点修复。
- 2026-11-02 05:40–05:49：确认**搜索结果条目没有逐项菜单**（只有 `加入`），
  想隐藏/举报只能进详情页；已写入分类差异小节。
- 2026-11-02 05:55–06:22：社团详情里点 `See more options`，出来的**不是**帖子菜单，
  而是 `Report to group admins` + `Save reel` 这一套——已独立建模
  （`group_post_more_menu`）；同时提醒"同名入口不同菜单"。
- 2026-11-02 06:14–05:42：把"label 带尾空格"这个坑一次性解决——
  `_fields()` 统一 strip；受影响的 `\s*$` 正则已简化回 `^…$`。
  离线、live、walk 全部通过。
- 2026-11-02 05:46–05:52：`strip` 改动后跑全部 **10 个配方**，全部通过——
  确认字段规范化没有破坏既有匹配。
- 2026-11-02 06:00–06:20：复核自己主页——**没有 `See more options`**，只有编辑类按钮
  （编辑封面/头像/个人档案，均 send）；同时确认自检里**已有** send 级未写说明的提示
  （26 个靠 Page.note 覆盖，不是缺失）。
- 2026-11-02 06:24–06:12：修正专页详情的 Activity（`ImmersivePortraitActivity`），
  结果暴露出与个人主页的判定打平；把 identity 换成 4 个专页独有锚点后修复，
  5 项主页断言与 `look_page` 配方均通过。
- 2026-11-02 06:18–06:31：新增「样本 Activity 与 `activity_hint` 一致性」检查
  （防的就是"靠错误 activity 做 tie-break、看起来能过"）；查出并修了 3 处：
  `messages` 改成短名 `DiodeMessengerActivity`、`sticker_panel`/`gif_panel` 的样本补上
  `FbMainTabActivity`。现在全部一致。
- 2026-11-02 06:00–06:02：Activity 改动后跑完整回归——离线、`--live`、`--walk`、
  **10 个配方全部通过**。
- 2026-11-02 06:05–06:12：交付报告刷新到 58 页 / 537 控件；2 轮浸泡（每轮 15 步）
  均 0 失败、未触发重试。
- 2026-11-02 06:17–06:29：社团详情页复核发现**加入按钮有三种文案格式**——
  中文 `加入`、英文 `Join <社团名> group`、推荐卡 `Join group: <名称>`；
  已补 `join_en`（send）。`最相關`（讨论区排序）模板里本来就有。
- 2026-11-02 06:31–06:40：抽查赞/分享/评论/关注/发送 五类高频动作的文案覆盖，
  **没有发现缺口**（帖子互动栏 `Like button…`/`Comment`/`Share`、
  Reel 的 `Follow …`/`Save`、专页的 `追蹤` 都已覆盖）。
- 2026-11-02 06:52–07:02：专页详情首屏补采联系方式按钮（`WhatsApp` 等，会跳外部）；
  新增 `contact_whatsapp` 与通用 `contact_any`，均未点。
- 2026-11-02 06:17–06:27：理清专页「全部」分页的纵向顺序
  （首屏 → **关于信息区** → 帖子流），补 `about_website` / `post_time`。
- 2026-11-02 06:31–06:40：完整回归——离线、`--walk`、**10 个配方全部通过**；
  交付报告刷新到 58 页 / 542 控件。
- 2026-11-02 06:30–06:41：专页「Reel」分页没采成（切分类后条目位置变了，流程断）；
  同时修掉 walk 的一个真问题——**顶栏隐藏时找不到搜索入口**，现在会先滚回顶部再找。
- 2026-11-02 06:45–07:02：换个走法重试，**专页「Reel」分页采到了**（3 列网格，
  元素类型是 Button、与社团的 ViewGroup 不同）；补 `reel_item`。
- 2026-11-02 07:08–07:19：点开专页 Reel，落点是同一个 `group_reel_view`（复用）；
  且发现**01:56 那次"极简页"是抓在异步加载中**——等一会儿会出现
  `Video details`/作者头像/`· 原始音訊`。已补 3 个目标并修正结论。
- 2026-11-02 07:26–07:34：把这条教训做成通用能力——`--live` 时若元素数 < 12，
  会提示"可能仍在加载"并等 3 秒重新 dump，避免再把加载中误判成"页面结构简单"。
  （触发路径逻辑简单，但**没有专门构造少元素场景验证**，如实记下。）
- 2026-11-02 06:31–07:05：**补上了这个验证**，并修了两个实现问题：
  ① 原来用 `len(els)` 判断，但那是**含系统栏在内**的总数（56 个），永远触发不到；
  ② 改成只数 `com.facebook.katana` 自己、且有文本的元素，阈值 3。
  实测：生日空态页（0 个）**会触发**等待、首页（107 元素）**不触发**。
  已知副作用：像生日页这种内容元素没有 katana id 的页面会**误触发多等 3 秒**（无害）。
- 2026-11-02 07:21–06:47：walk 失败排查发现**首页搜索按钮的文案变了**——
  从 `Search Facebook` 变成 `Search`（此前一直写死前者）。已改成
  `^Search( Facebook)?$` 兼容两种；改完 walk 通过（首轮仍偶发失败一次、重试通过）。
- 2026-11-02 06:51–07:20：**风险控件的正则统一放宽**（宁可多拦不能漏拦）：
  `^Create story$` → `^Create (a )?story`、`Create reel` 同理、`Report a problem` 去掉 `$`。
  另外查清 walk 首轮失败的真因：**点「社團」后只等 1.8 秒，页面还没渲染完就判定**，
  会判到上一页（search）——已加到 2.8 秒，walk 一次通过。
- 2026-11-02 07:24：**3 轮浸泡全部通过、一次重试都没触发**。
  对比修复前（每轮都要重试一次才过），说明"等 1.8 秒不够"确实是根因，
  重试机制回到"应急"状态、不再是常态兜底。
- 2026-11-02 07:15–07:30：切到社团「討論區」分页发现判定变 `None`
  （封面/成员数不在），补 `留個言吧` 与 `最相關` 两个锚点后恢复。
- 2026-11-02 07:33–07:45：想逐个核对社团各分页（相片/相簿/活動/檔案/Reel）是跳独立页
  还是留在详情页内，但**页签在滚动后不在层级里**（两种找法都是 0 命中），
  这次没核对完；期间 walk 仍通过。**未下结论，留待下次在社团首页顶部做。**
- 2026-11-02 07:51–08:00：再试一次（先滚到顶、用文本精确匹配），**仍然 0 命中**；
  诊断发现脚本里的社团条目其实没点进去，回退后停在搜索结果页。
  连续两次没做成，**不再硬试**，记为未完成项（社团分页行为仍需在能稳定进入详情页时核对）。
- 2026-11-02 08:03–08:15：**改用配方路径把这件事做成了**（新增 `look_group_tabs`：
  搜索 → 进社团 → 逐个点分页并记录落点 → 返回）。结论：
  **只有「討論區」是页内切换（判 `group_detail`），相片/相簿/活動/Reel 全部跳独立页**
  （`group_photos`/`group_albums`/`group_events`/`group_reels`）；
  `檔案` 这次页签滚出屏幕没点到，但之前已知是独立页 `group_files`。
  这和**个人主页"分页都在页内切换"正好相反**，不能互相类推。
- 2026-11-02 07:07：给配方加"每次找页签前先滚回顶部"再跑，**只稳定命中「相片」**，
  其余 4 个页签仍 0 命中。**不硬凑结论**：`檔案` 仍以早前单独进入时观察到的
  `group_files` 为准，本次没新增验证。
  当前：58 页 / 546 控件（collected 25 / verified 13 / partial 18 / blocked 2）。
- 2026-11-02 07:25–07:56：新增**照片查看器**（自己主页的相片点开后的全屏页）——
  之前只采到网格、没点进去过；顶部有 `1 of 6` / `Tagging mode` / `Location` / `More`，
  底部是作者名与拍摄日期；**序号是单数形式**。`Tagging mode` 标 send、未点。
  当前：59 页 / 554 控件（collected 25 / verified 13 / partial 19 / blocked 2）。
- 2026-11-02 08:04–08:30：点开照片查看器的 More，采到五项（设为封面/编辑说明/**删除照片**/保存到手机/外部分享）；
  **Delete photo 标 forbidden**（不可逆删除），其余按对外影响分级；一项都没点。
  当前：60 页 / 559 控件（collected 26 / verified 13 / partial 19 / blocked 2）。

