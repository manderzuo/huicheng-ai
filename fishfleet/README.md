# FishFleet 使用说明

## 一、当前状态

```
✅ 架构设计完成
✅ 核心代码完成（19 个文件）
✅ 依赖安装完成（yaml / httpx / uiautomator2 / PIL）
✅ 20 台设备全部在线
⏳ 只差 LLM API key
```

## 二、配 API Key（三步）

### 方式 A：环境变量（推荐）

PowerShell：

```powershell
# 当前会话
$env:DEEPSEEK_API_KEY = "sk-你的key"
$env:OPENAI_API_KEY = "sk-你的key"

# 永久（用户级）
[Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', 'sk-你的key', 'User')
[Environment]::SetEnvironmentVariable('OPENAI_API_KEY', 'sk-你的key', 'User')
```

### 方式 B：直接改配置文件

编辑 `fishfleet/config/config.yaml`：

```yaml
llm:
  default:
    base_url: https://api.deepseek.com/v1
    api_key: sk-你的key        # 直接填
    model: deepseek-chat
```

## 三、验证

```bash
cd huicheng-auto
python -m fishfleet.cli doctor
```

看到 `=== 全部通过 ===` 就行。

## 四、开始用

### 1. 让 AI 干一件事

```bash
python -m fishfleet.cli agent --device SERIAL0001 --goal "打开抖音，搜索大肥鱼"
```

AI 会自己：
- 观察界面
- 决定点哪
- 执行操作
- 验证结果

### 2. 发布视频

```bash
python -m fishfleet.cli publish \
  --device SERIAL0001 \
  --video C:\Users\Administrator\Desktop\1.mp4 \
  --title "雨夜的猫" \
  --desc "霓虹倒映在积水里" \
  --tags "赛博朋克,猫咪"
```

### 3. 审核内容（发布前检查）

```bash
python -m fishfleet.cli review \
  --video C:\Users\Administrator\Desktop\1.mp4 \
  --title "雨夜的猫" \
  --platform 抖音
```

### 4. 生成视频（需要先接 MCP）

先配置 `config.yaml`：

```yaml
video_generation:
  enabled: true
  mcp:
    transport: http
    server: "http://127.0.0.1:8080/mcp"   # 你的 MCP 服务地址
    tool: "generate_video"
    timeout: 900
```

然后：

```bash
python -m fishfleet.cli gen --topic "雨夜的橘猫" --style "赛博朋克" --duration 15
```

## 五、代码结构

```
fishfleet/
├── DESIGN.md              ← 完整架构设计（先看这个）
├── config/
│   └── config.yaml        ← 主配置
├── core/
│   ├── config.py          ← 配置加载
│   ├── device.py          ← 设备管理（连接/健康/重连）
│   ├── locator.py         ← 语义定位器
│   └── actions.py         ← 原子操作（点击/滑动/输入）
├── llm/
│   ├── client.py          ← LLM 客户端（OpenAI 兼容）
│   └── agent.py           ← Agent 循环 + 9 个工具
├── apps/
│   └── douyin.py          ← 抖音适配器
├── flows/
│   └── publish.py         ← 发布流程（状态机）
├── content/
│   ├── mcp_client.py      ← MCP 视频生成（预留）
│   └── reviewer.py        ← 内容审核
└── cli.py                 ← 命令行入口
```

## 六、已经实现的 AI 工具

Agent 可以调用这 9 个工具：

| 工具 | 作用 |
|---|---|
| `observe` | 看界面（截图 + UI树 + 前台应用） |
| `find` | 按文字找元素坐标 |
| `tap` | 点击 |
| `swipe` | 滑动（带拟人化轨迹） |
| `input_text` | 输入文字（支持中文） |
| `press_key` | back / home / enter |
| `launch_app` | 启动应用 |
| `wait` | 等待 |
| `finish` | 标记完成 |

## 七、下一步要做的

### 近期（你说要做的）

1. **配 API key** —— 你来做
2. **跑通第一个 agent 任务** —— 一起验证
3. **接 MCP 视频生成** —— 你提供 MCP 服务地址

### 中期（我建议的）

4. **发布流程端到端** —— 本地视频 → 抖音
5. **巡逻流程** —— 刷内容 + 理解 + 记录
6. **评论回复** —— 监控 + 生成 + 发送
7. **私信流程** —— 筛选 + 话术 + 发送

### 长期

8. **调度器** —— 定时任务
9. **数据回收** —— 播放量/互动率
10. **Web 控制台** —— 可视化

## 八、几个重要提醒

### 成本控制

- **常规操作用便宜模型**（deepseek-chat）
- **关键判断用贵模型**（gpt-4o 视觉）
- `routing` 里可以按任务分配

### 风控

`config.yaml` 里已经配好：

```yaml
guard:
  work_hours: [8, 23]        # 只在白天操作
  rate_limit:
    publish_per_device: 3    # 每天最多发 3 条
    dm_per_device: 20        # 私信最多 20 条
```

### 安全底线

- 发内容前可人工审批（`approval.publish`）
- 私信前必须人工审批（`approval.dm: true`）
- 遇到验证码/登录失效 → 自动暂停

---

*使用说明 · 大肥鱼 2026-10-28*
