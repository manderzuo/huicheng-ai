# 汇城群控AI版

> 面向 Android 手机群控的 Web 应用：批量管理手机、自动发布内容、采集线索、AI 辅助操作。
> 前后端一体，打包成单个 exe，首次运行自动安装运行环境。

---

## 这是什么

用一台 Windows 电脑管理多台安卓手机（USB 连接），提供：

| 能力 | 说明 |
|---|---|
| **设备管理** | 多台手机同屏投屏、批量操作、编号 / 名称 / 标签 |
| **内容发布** | 抖音、小红书、B站、TikTok、Facebook 自动发布（到预览页停住，人工确认后才发） |
| **线索采集** | 按关键词批量采集评论 / 私信线索，存 SQLite 线索池 |
| **自动回复** | 从线索池生成私信草稿（**只填进输入框，不点发送**） |
| **AI 辅助** | 视觉判断当前界面、生成文案、审核内容 |

**设计原则**：所有「不可逆」动作（发布、发送、关注、付款）**都停在最后一步等人工确认**，不自动执行。

---

## 技术架构

```
┌──────────────────────────────────────────────┐
│  HuichengFleetAI.exe（PyInstaller，约 11 MB） │
│  ├─ uvicorn + FastAPI（127.0.0.1:8765）       │
│  ├─ 前端：原生 JS，无框架                      │
│  └─ 用 Edge --app 打开独立窗口（无地址栏）     │
└──────────────────────────────────────────────┘
                    │
      ┌─────────────┼─────────────┐
      ▼             ▼             ▼
   adb.exe      scrcpy        mobile-mcp
  （设备通讯）  （屏幕流）    （UI 自动化，Node）
```

**关键设计**：

- **投屏**：手机 H.264 → 服务端转 JPEG → 大小屏共享解码；触控走 scrcpy control socket
- **UI 自动化**：`uiautomator dump` 取控件树（**不需要无障碍服务**），坐标点击优先于 ref 点击
- **通信层**：mobile-mcp（Node.js MCP server），每通道一个进程，启动时预热 8 个
- **打包**：主程序 PyInstaller onedir；引导器负责首次安装 Python / Node / 依赖

---

## 目录结构

```
fishfleet/
├── desktop.py            桌面启动器（起 uvicorn + 开窗口）
├── paths.py              统一路径解析（打包态 / 开发态）
├── logsetup.py           日志（app.log / error.log / events.jsonl）
├── web/
│   ├── server.py         FastAPI 主服务
│   ├── static/           前端（index.html + app.js + chat_panel.js）
│   ├── execution.py      任务调度
│   ├── mobile_gateway.py MCP 通道池
│   ├── douyin_*.py       抖音发布 / 采集 / 私信
│   ├── xiaohongshu_*.py  小红书
│   ├── bilibili_*.py     B站
│   ├── tiktok_*.py       TikTok
│   ├── facebook_*.py     Facebook
│   └── lead_store.py     线索池（SQLite）
└── core/
    ├── device.py         设备枚举与管理
    ├── scrcpy.py         投屏会话
    └── device_ext.py     设备扩展操作

bootstrap/                 引导器（首次装环境）
build/                     PyInstaller 配置
creative_studio/           素材创作模块
```

---

## 快速开始（开发）

```bash
# 1. 装 Python 依赖
pip install fastapi uvicorn pydantic pillow numpy av opencv-python \
            httpx uiautomator2 imageio pyyaml

# 2. 装 mobile-mcp（需要 Node.js）
cd mobile-mcp-runtime
npm install @mobilenext/mobile-mcp@1.0.15 \
            @mobilenext/mobilecli-windows-amd64@1.0.21

# 3. 启动
python -m fishfleet.desktop
```

**依赖的外部工具**：

| 工具 | 用途 | 放哪 |
|---|---|---|
| `adb.exe` | 设备通讯 | `bin/adb.exe`（或系统 PATH） |
| `node.exe` | MCP server | `runtime/node/node.exe`（或 PATH） |
| Edge / Chrome | 显示界面（`--app` 模式） | 系统自带 |

---

## 打包发布

```bash
# 主程序
python -m PyInstaller build/fishfleet.spec --noconfirm

# 引导器
python -m PyInstaller build/bootstrap.spec --noconfirm

# 或者用图形化构建工具
python build/build_gui.py
```

**产物**：

```
dist/
├── HuichengFleetAI/          主程序（onedir）
└── 汇城AI安装引导/            引导器（onedir）
```

---

## 日志与排查

日志位置（程序目录）：`data/logs/`

| 文件 | 内容 |
|---|---|
| `app.log` | 全部日志（滚动 5MB × 5） |
| `error.log` | 只有警告和错误（排查先看这个） |
| `events.jsonl` | 操作事件（JSON 行，能还原点了什么） |

**诊断接口**：

```
GET  /api/log/diagnose    返回环境快照 + 最近错误
POST /api/log/export      打包日志 zip 到桌面
POST /api/log/event       前端上报操作事件
```

**命令行诊断**：

```bash
HuichengFleetAI.exe --diag     # 生成 诊断结果.txt
```

---

## 已知限制

- **仅支持 Windows**（依赖 adb、Edge、PyInstaller）
- **USB 授权**：每台新手机第一次连电脑都要在手机上点「允许 USB 调试」
- **端口 8765**：固定，被占时自动往后找
- **不自动发布**：所有发布流程停在预览页，需人工点确认
- **UI 自动化**依赖 `uiautomator dump`，每次约 2.3 秒（比 accessibility 慢但更准）

---

## 数据与隐私

- **不收集任何遥测**（`MOBILEMCP_DISABLE_TELEMETRY=1`）
- **所有数据本地存储**：SQLite 在 `data/` 目录
- **API Key** 存在 `_internal/data/llm.json`，不上传任何服务器
- 源码中**不含任何真实手机串号、用户数据或密钥**

---

## 许可

内部工具，未开源授权。如需使用请联系作者。

---

## 致谢

- [mobile-mcp](https://www.npmjs.com/package/@mobilenext/mobile-mcp) — 手机 UI 自动化
- [scrcpy](https://github.com/Genymobile/scrcpy) — 投屏协议
- [FastAPI](https://fastapi.tiangolo.com/) — Web 框架
