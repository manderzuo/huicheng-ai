# 鱼群（FishFleet）— AI 驱动的多机内容运营系统

> 版本：v0.1 设计稿
> 日期：2026-10-28
> 目标：**完全替代汇城群控，让 LLM 接管所有操作，实现无人化自动运营**

---

## 一、核心设计原则

### 1. 人只做两件事：给目标、看结果

```
人："这周给 20 台手机各发 3 条视频，主题是萌宠"
系统：自己找素材 → 自己生成 → 自己审核 → 自己发布 → 自己巡逻 → 自己回复 → 汇报
```

### 2. 三层分离：想、编、做

| 层 | 职责 | 变化频率 |
|---|---|---|
| **想**（LLM） | 决策、生成内容、理解界面 | 随时可换模型 |
| **编**（流程） | 任务编排、状态机、重试 | 业务变才改 |
| **做**（执行） | ADB 操作、定位、截图 | 最底层，最稳定 |

### 3. 一切可插拔

- **LLM 可换**：OpenAI / Claude / DeepSeek / Qwen / 本地 Ollama
- **视频生成可换**：通过 MCP 接入，任何 MCP 服务都行
- **平台可加**：抖音、小红书、B站、快手……
- **设备可扩**：20 台 → 200 台

---

## 二、系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                        用户界面层                              │
│  CLI / Web 控制台 / 微信通知                                   │
│  · 下达目标（自然语言）                                        │
│  · 查看进度、审批关键节点                                       │
│  · 查看数据报表                                                │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│                      编排层（Orchestrator）                    │
│  · 任务队列（Task Queue）                                      │
│  · 状态机（State Machine）                                     │
│  · 调度器（Scheduler）：定时 / 事件驱动                         │
│  · 并发控制（每台设备独立 worker）                              │
│  · 断点续跑（SQLite 持久化）                                    │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│                     能力层（Capabilities）                     │
│                                                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐            │
│  │ 内容生产 │ │ 内容分发 │ │ 互动运营 │ │ 数据回收 │            │
│  ├─────────┤ ├─────────┤ ├─────────┤ ├─────────┤            │
│  │选题     │ │上传     │ │搜索     │ │播放量   │            │
│  │生成     │ │发布     │ │巡逻     │ │互动率   │            │
│  │剪辑     │ │定时     │ │私信     │ │涨粉     │            │
│  │审核     │ │多平台   │ │回复评论 │ │爆款识别 │            │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘            │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│                    执行层（Executor）                          │
│  · locator（语义定位 + 降级链）                                │
│  · actions（tap/swipe/text/screenshot）                       │
│  · apps/（抖音、小红书、B站适配器）                             │
│  · 视觉理解（多模态 LLM）                                      │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│                     设备层（Devices）                          │
│  · ADB（USB / WiFi）                                          │
│  · AOA/HID（预留，解决输入稳定性）                              │
│  · 健康检查 / 自动重连 / 并发控制                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、目录结构

```
fishfleet/
├── config/
│   ├── config.yaml              # 主配置
│   ├── llm.yaml                 # LLM 配置（可多套）
│   ├── devices.yaml             # 设备清单
│   └── accounts.yaml            # 账号（平台/设备/登录态）
│
├── core/
│   ├── __init__.py
│   ├── device.py                # 设备管理（连接/健康/重连）
│   ├── locator.py               # 语义定位器（已写）
│   ├── actions.py               # 原子操作
│   ├── vision.py                # 视觉理解（多模态）
│   └── errors.py                # 异常定义
│
├── llm/
│   ├── __init__.py
│   ├── client.py                # OpenAI 兼容客户端
│   ├── tools.py                 # 工具定义（function calling）
│   ├── agent.py                 # Agent 循环
│   └── prompts.py               # 提示词
│
├── apps/
│   ├── __init__.py
│   ├── base.py                  # App 基类
│   ├── douyin.py                # 抖音
│   ├── xhs.py                   # 小红书
│   └── bilibili.py              # B站
│
├── flows/
│   ├── __init__.py
│   ├── base.py                  # Flow 基类（状态机）
│   ├── publish.py               # 发布流程
│   ├── search.py                # 搜索流程
│   ├── patrol.py                # 巡逻流程
│   ├── dm.py                    # 私信流程
│   ├── reply.py                 # 评论回复流程
│   └── engage.py                # 互动（点赞/关注）
│
├── content/
│   ├── __init__.py
│   ├── generator.py             # 内容生成（选题/文案）
│   ├── mcp_client.py            # MCP 客户端（视频生成）
│   ├── editor.py                # AI 剪辑
│   ├── reviewer.py              # 自动审核
│   └── assets.py                # 素材管理
│
├── analytics/
│   ├── __init__.py
│   ├── collector.py             # 数据采集
│   ├── analyzer.py              # 数据分析
│   └── reporter.py              # 报表
│
├── scheduler/
│   ├── __init__.py
│   ├── queue.py                 # 任务队列
│   ├── store.py                 # SQLite 持久化
│   └── worker.py                # 设备 worker
│
├── guard/
│   ├── __init__.py
│   ├── ratelimit.py             # 频率限制
│   ├── risk.py                  # 风控检测
│   └── approve.py               # 人工审批
│
├── cli.py                       # 命令行入口
└── web/                         # Web 控制台（后续）
```

---

## 四、核心数据模型

### 4.1 设备

```python
@dataclass
class Device:
    serial: str              # ADB 序列号
    index: int               # 编号 1-20
    model: str               # SM-G973U1
    state: str               # online / offline / busy / error
    account: dict            # 平台账号 {douyin: "...", xhs: "..."}
    health: dict             # 电量/温度/存储/网络
    current_task: str | None
```

### 4.2 任务

```python
@dataclass
class Task:
    id: str
    type: str                # publish / patrol / reply / dm / ...
    device: str
    params: dict
    state: str               # pending / running / done / failed / paused
    steps: list[Step]        # 步骤记录
    result: dict | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    retries: int = 0
```

### 4.3 内容

```python
@dataclass
class Content:
    id: str
    type: str                # video / image / text
    source: str              # generated / uploaded / downloaded
    path: str
    title: str
    description: str
    tags: list[str]
    review: dict             # 审核结果
    platforms: list[str]     # 目标平台
    published: dict          # {platform: {url, time, views}}
```

---

## 五、业务流程设计

### 5.1 发布流程（AI 全自动）

```
[触发] 定时 / 手动 / 上游内容生成完成
  ↓
1. 选题（LLM）
   - 分析热点、对标账号、历史数据
   - 输出：主题 + 角度 + 目标平台
  ↓
2. 生成（MCP 视频生成）
   - 调用视频生成 MCP
   - 得到原始视频
  ↓
3. 剪辑（AI）
   - 裁切、加字幕、配 BGM
   - 适配平台比例（9:16 / 16:9）
  ↓
4. 审核（AI + 规则）
   - 内容合规（敏感词、画面）
   - 质量（清晰度、时长、完整性）
   - 平台适配（尺寸、码率、封面）
   - 通过 → 下一步；不通过 → 回炉或丢弃
  ↓
5. 文案（LLM）
   - 标题（钩子）
   - 描述（关键词 + 情绪）
   - 标签（话题）
  ↓
6. 分发（多设备）
   - 选择设备（按账号、健康度、负载）
   - 指派人审（可选）
  ↓
7. 发布（App 自动化）
   - 打开 App → 上传 → 填文案 → 发布
   - 验证成功
  ↓
8. 记录
   - 存 DB，关联内容 ID
```

### 5.2 巡逻流程（AI 浏览 + 学习）

```
1. 打开 App → 搜索关键词 / 推荐流
2. 浏览 N 条内容
   - 每条：截图 → LLM 理解 → 记录
   - 判断：值得互动？值得收藏？值得关注？
3. 输出：
   - 内容理解报告
   - 潜在客户/互动目标列表
   - 爆款特征分析
4. 可选：自动点赞/关注/评论
```

### 5.3 私信流程（AI 主动触达）

```
1. 选目标
   - 从巡逻结果 / 粉丝列表 / 关键词搜索 筛选
   - 过滤：已私信过、黑名单、不活跃
2. 生成话术（LLM）
   - 结合对方内容（个性化）
   - 自然、不生硬、不像广告
3. 发送
   - 打开私信 → 输入 → 发送
4. 跟进
   - 等回复 → 判断意图 → 继续对话或结束
5. 转化追踪
   - 记录：已读、回复、转化
```

### 5.4 评论回复流程（AI 客服）

```
1. 监控
   - 定时检查自己作品的评论
   - 或实时通知
2. 分类（LLM）
   - 咨询 / 夸奖 / 质疑 / 恶意 / 广告
3. 生成回复
   - 咨询：给准确答案
   - 夸奖：谢 + 互动
   - 质疑：解释
   - 恶意：忽略或拉黑
4. 发送
5. 记录
```

---

## 六、LLM 接入设计

### 6.1 配置（可多套，按任务路由）

```yaml
llm:
  # 主模型：决策 + 内容生成
  default:
    provider: openai
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    model: deepseek-chat
    temperature: 0.8

  # 视觉模型：界面理解
  vision:
    provider: openai
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o

  # 廉价模型：简单分类
  cheap:
    provider: openai
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    model: deepseek-chat
    temperature: 0.3

routing:
  decision: default
  content_gen: default
  vision: vision
  classify: cheap
```

### 6.2 工具集（LLM 可调用）

```python
TOOLS = [
    # 感知
    {"name": "device_list", "desc": "列出所有设备及状态"},
    {"name": "screenshot", "desc": "截图指定设备"},
    {"name": "ui_dump", "desc": "获取 UI 层级"},
    {"name": "current_app", "desc": "当前前台应用"},
    {"name": "ocr_screen", "desc": "识别屏幕文字"},

    # 定位
    {"name": "find", "desc": "按文字/描述/id 查找元素坐标"},
    {"name": "find_near", "desc": "相对定位"},

    # 操作
    {"name": "tap", "desc": "点击坐标或元素"},
    {"name": "swipe", "desc": "滑动"},
    {"name": "input_text", "desc": "输入文字"},
    {"name": "press_key", "desc": "按键"},
    {"name": "launch_app", "desc": "启动应用"},
    {"name": "wait", "desc": "等待"},

    # 业务流程
    {"name": "publish", "desc": "发布内容到指定平台"},
    {"name": "search", "desc": "搜索关键词"},
    {"name": "patrol", "desc": "巡逻浏览"},
    {"name": "send_dm", "desc": "发送私信"},
    {"name": "reply_comment", "desc": "回复评论"},

    # 内容
    {"name": "generate_video", "desc": "调用 MCP 生成视频"},
    {"name": "edit_video", "desc": "AI 剪辑"},
    {"name": "review_content", "desc": "审核内容"},

    # 数据
    {"name": "get_stats", "desc": "获取数据统计"},
]
```

### 6.3 Agent 循环

```python
async def run_agent(goal: str, device: str | None = None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": goal}]

    for step in range(MAX_STEPS):
        # 1. 感知（自动注入当前状态）
        state = await perceive(device)
        messages.append({"role": "user", "content": format_state(state)})

        # 2. 决策
        resp = await llm.chat(messages, tools=TOOLS)
        messages.append(resp)

        if resp.finish_reason == "stop":
            return resp.content

        # 3. 执行工具
        for call in resp.tool_calls:
            result = await execute_tool(call.name, call.args)
            messages.append({"role": "tool", "content": str(result)})

        # 4. 安全闸门
        if needs_approval(resp):
            await request_human_approval(resp)

    return "超过最大步数"
```

---

## 七、视频生成 MCP 接入（预留）

### 7.1 MCP 客户端设计

```python
class MCPVideoClient:
    """通过 MCP 调用视频生成服务"""

    def __init__(self, config):
        self.server = config["server"]      # MCP 服务器地址
        self.tool = config.get("tool", "generate_video")
        self.timeout = config.get("timeout", 600)

    async def generate(self, prompt: str, **kwargs) -> dict:
        """
        调用 MCP 工具生成视频

        返回:
          {
            "status": "success",
            "video_path": "...",
            "duration": 15.0,
            "resolution": "1080x1920",
            "cost": 0.05
          }
        """
        ...
```

### 7.2 配置

```yaml
video_generation:
  enabled: true
  mcp:
    server: "http://localhost:8080/mcp"   # 或 stdio
    tool: "generate_video"
    timeout: 600
  prompt_template: |
    生成一段{topic}的短视频。
    风格：{style}
    时长：{duration}秒
    画面：{scene}
  defaults:
    style: "写实，治愈系"
    duration: 15
    resolution: "1080x1920"
```

### 7.3 生成 → 审核 → 发布 流水线

```python
async def content_pipeline(topic: str):
    # 1. 生成
    video = await mcp_client.generate(prompt_from(topic))

    # 2. 剪辑
    edited = await editor.auto_edit(video["video_path"])

    # 3. 审核
    review = await reviewer.review(edited)
    if not review["passed"]:
        return {"status": "rejected", "reason": review["reason"]}

    # 4. 文案
    meta = await generator.make_caption(topic, edited)

    # 5. 发布
    result = await publish(edited, meta)
    return result
```

---

## 八、风控设计（必须有）

### 8.1 频率限制

```yaml
rate_limit:
  per_device:
    publish: 3/day          # 每台每天最多发 3 条
    dm: 20/day              # 私信最多 20 条
    comment: 50/day         # 评论最多 50 条
    like: 200/day
  per_account:
    publish: 5/day
  global:
    concurrent_devices: 8   # 同时在线的设备数
```

### 8.2 行为拟人化

- **随机延迟**：每次操作间隔 0.5-3 秒随机
- **随机顺序**：不总是从左到右
- **滚动浏览**：模拟真人滑动曲线
- **活跃时段**：只在 8:00-23:00 操作
- **设备差异**：不同设备不同节奏

### 8.3 异常检测

- **登录失效** → 告警，暂停该设备
- **验证码** → 告警，转人工
- **限流提示** → 降低频率
- **封号** → 立即停止，报告

---

## 九、监控与告警

### 9.1 指标

| 类别 | 指标 |
|---|---|
| 设备 | 在线率、故障率、平均负载 |
| 任务 | 成功率、平均耗时、失败原因分布 |
| 内容 | 发布量、审核通过率、播放量 |
| 账号 | 粉丝增长、互动率、违规次数 |
| 系统 | LLM 调用量、token 消耗、成本 |

### 9.2 告警

- 设备掉线 > 5 分钟
- 任务失败率 > 20%
- 账号异常
- 成本超预算

---

## 十、开发计划

### 阶段 1：骨架（1-2 天）
- [x] 架构设计
- [ ] `core/` 设备 + 定位 + 操作
- [ ] `llm/` 客户端 + Agent 循环
- [ ] 跑通：单机单任务（打开抖音 → 截图 → LLM 描述）

### 阶段 2：发布流程（2-3 天）
- [ ] `content/` 素材 + 文案
- [ ] `flows/publish.py` 发布状态机
- [ ] `apps/douyin.py` 抖音适配
- [ ] 跑通：本地视频 → 发布到抖音

### 阶段 3：巡逻与互动（2-3 天）
- [ ] `flows/patrol.py`
- [ ] `flows/reply.py`
- [ ] `flows/dm.py`
- [ ] 跑通：巡逻 10 条 → 生成互动建议

### 阶段 4：内容生产（3-5 天）
- [ ] `content/mcp_client.py` MCP 接入
- [ ] `content/reviewer.py` 审核
- [ ] `content/editor.py` 剪辑
- [ ] 跑通：主题 → 生成 → 审核 → 发布

### 阶段 5：生产化（持续）
- [ ] 调度器
- [ ] 监控
- [ ] 风控
- [ ] Web 控制台

---

## 十一、我替你想的几件事（补充需求）

### 11.1 内容策略引擎

不只是"发内容"，而是"发什么内容"：
- **热点追踪**：爬热点榜，结合账号定位选选题
- **对标分析**：分析同类账号爆款，提炼可复用结构
- **A/B 测试**：同一内容不同标题/封面，看哪个数据好
- **发布时间优化**：根据历史数据找最佳发布时段

### 11.2 账号养号

- **新号期**：模拟真人浏览，每天 30-60 分钟
- **成长期**：稳定发布 + 互动
- **成熟期**：维护 + 变现

### 11.3 私域引流

- **评论区**：主动评论同类内容，吸引注意
- **私信**：轻触达，不硬广
- **引导**：引导到微信/群（注意平台规则）

### 11.4 数据闭环

```
发布 → 采集数据 → 分析 → 优化 → 再发布
```

- 哪类内容数据好？
- 什么时间发最好？
- 什么标题点击率高？
- 哪种封面完播率高？

### 11.5 人工协作点

**不是全自动就最好**。保留几个审批点：
- **发布前**：可选人工审核（高风险内容）
- **私信前**：可选人工确认（避免骚扰）
- **异常时**：自动转人工

### 11.6 多平台差异化

不同平台规则不同：
- **抖音**：重钩子、快节奏、强互动
- **小红书**：重图文、重种草、重标签
- **B站**：重内容深度、重弹幕文化

**同一内容要"平台化改造"**，不是直接搬运。

### 11.7 成本控制

- **LLM 调用**：简单任务用便宜模型，复杂任务用贵的
- **视频生成**：批量生成，摊薄成本
- **设备**：20 台够用就别扩，先跑通再扩

---

## 十二、一句话总结

> **这套系统不是"群控"，是"AI 内容运营团队"。**
>
> 它有：选题编辑、内容创作者、视频剪辑师、审核员、发布员、运营专员、客服、数据分析师。
>
> 这些"人"，全是一个大脑在不同岗位上。

---

*设计稿 v0.1 · 大肥鱼 2026-10-28 00:55*
