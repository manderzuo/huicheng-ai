# TikTok UI 资料库 · 手机7

> 设备：手机7 / `SERIAL0001` / Galaxy S10 (SM-G973U1) / Android 12 / 1080×1920 @420dpi
> 应用：TikTok 国际版 `com.zhiliaoapp.musically` v35.8.1（2025-08-12 安装）
> 采集：2026-11-01 20:26–20:37（Asia/Shanghai），第 1 轮，16 个页面
> 采集方式：Mobile MCP 逐页 dump UI 层级 + 必要处截图；所有页面都是**实际点进去**的

## 这是什么

把手机7 上 TikTok 的页面结构、控件属性、**以及页面之间的跳转逻辑**逐页记录下来，供以后二次组合成任务（尤其是截流/获客类任务）。

**重要边界**：本库只回答「页面长什么样、控件怎么定位、点哪里到哪」。它**不代表**任何完整任务已经实现或验证过。发布、评论、私信、关注这些链路，每一步都要在真实环境里逐步实测。

## 怎么用

```python
# 伪代码：任何动作前先确认「我在哪一页」
page = library.match(current_dump)      # 用 pages/*.json 的 identity 锚点
target = page.targets['comment_btn']    # 取控件定义
ref = resolve(current_dump, target)     # id → label → text，按优先级解析出当次 ref
if target.risk == 'send' and not confirmed:
    raise NeedConfirm                   # 发送类必须先问人
```

三条铁律：

1. **不缓存 ref，不缓存坐标**。ref 每次 dump 都变；坐标换机、换分辨率、换状态都会错位。
2. **先认页面，再动控件**。每页都有 `identity`，任一命中才认为在该页。
3. **risk 分级**：`none` 直接做 / `send` 必须人工确认 / `forbidden` 永不自动化。

## 目录

```
tiktok/
  README.md          本文件
  COVERAGE.md        覆盖矩阵：已采集 / 已验证 / 未覆盖 / 无法进入
  LOGIC.md           导航逻辑手册（截流视角可达路径）
  app.json           包名、版本、已知坑
  device.json        机型、分辨率、输入法注意事项
  index.json         页面索引 + 证据等级 + 关键结论
  pages/*.json       16 个页面，每页一个文件
```

## 单页 JSON 的字段约定

| 字段 | 含义 |
|---|---|
| `key` / `name` | 页面标识 / 中文名 |
| `entry` | 怎么进入这一页（可执行的描述） |
| `evidence_level` | `collected` 只看到 / `navigation-verified` 实际点进来过 / `blocked` 进不去 |
| `identity` | **页面锚点**，命中任一即判定在该页 |
| `targets` | 控件列表：`id` / `label_re` / `text` / `role` / `risk` / `note` |
| `navigation` | 从本页能去哪，`verified` 表示是否实测过 |
| `uncovered` | 这一页没采到的部分 |
| `notes` | 坑与限制 |

控件匹配优先级：**id > text 精确 > label 正则 > text 正则**。TikTok 的 id 是混淆短名且会重复，所以实际用时 label 语义往往比 id 更可靠。

## 这个 App 的五个大坑

1. **id 混淆且重复**：`d63` 同时是「点赞」和「分享」；`kjr` 同时是「关注数」和「获赞数」。必须靠同一 dump 内的 label/相邻文本消歧。
2. **同一页多套布局**：评论区未输入/已输入是两套容器（输入态把表情条上移、发送按钮才出现）。写死某一套布局的脚本会失效。
3. **自绘区域读不到**：空态引导卡的标题（「热门创作者」）、红色「关注」按钮，层级里没有文本，只能截图判断。
4. **搜索分栏顺序不固定**：同一账号两次搜索，分栏顺序不同（综合/用户/视频… vs 综合/视频/用户…）。必须按 label 文本找，不能按索引。
5. **单位混用**：主页「8761.3 万」、搜索「87.6M」、评论「618」。数字解析要同时支持中英文单位。

## 安全边界（采集时遵守，做任务时继续遵守）

- 只读浏览、打开页面、返回；**不发送、不点赞、不关注、不收藏、不发布**
- 发送类控件**测试到「差最后一步」**：输入框验证、发送按钮存在性验证，但不点
- 授权类弹窗（如 Facebook 好友/邮箱）→ 拒绝，不扩大权限
- 金钱相关（推广、余额）→ 标记 `forbidden`，永不自动化
- 备注：TikTok 的发送按钮**没有二次确认**，点下去立即生效，不可撤回
