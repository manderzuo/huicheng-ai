# 手机20 系统级 UI 库（phone20）

> 目标：**一键完成系统级操作**。给每个开关同时提供「adb 命令」和「UI 控件」两条路。
> 采集时间：2026-11-01 16:52–17:04 · 设备：Galaxy S10 (SM-G973U1) / Android 12 / 1080x1920 / 繁体中文

## 一、先看结论

| 你要做的事 | 一条命令 |
|---|---|
| 看所有开关现状 | `powershell -File .\p20.ps1 status` |
| 开/关 Wi-Fi | `.\p20.ps1 wifi on` / `off` / `toggle` |
| 开/关蓝牙 | `.\p20.ps1 bt on` / `off` / `toggle` |
| 开/关自动旋转 | `.\p20.ps1 rotate on` / `off` |
| 强制竖屏 / 横屏 | `.\p20.ps1 rotate portrait` / `landscape` |
| 开/关自动时区 | `.\p20.ps1 tz on` / `off` |
| 开/关自动时间 | `.\p20.ps1 time on` / `off` |
| 亮度（0-255） | `.\p20.ps1 brightness 180` |
| 关自动亮度（固定亮度） | `.\p20.ps1 auto-bright off` |
| 深色模式 | `.\p20.ps1 dark on` / `off` |
| 护眼模式 | `.\p20.ps1 eye on` / `off` |
| 定位总开关 | `.\p20.ps1 location on` / `off` |
| 媒体音量（0-15） | `.\p20.ps1 volume 8` |
| 充电保持唤醒 | `.\p20.ps1 stayon on` |
| 永不熄屏 | `.\p20.ps1 timeout never` |
| 展开通知栏 / 快捷面板 / 收起 | `.\p20.ps1 qs` / `expand` / `collapse` |
| 回桌面 / 返回 / 最近任务 | `.\p20.ps1 home` / `back` / `recents` |
| 截图 | `.\p20.ps1 shot D:\shots\a.png` |
| 停止 / 停用 / 启用 / 卸载应用 | `.\p20.ps1 app kill|disable|enable|uninstall <包名>` |
| 任意 adb shell 命令 | `.\p20.ps1 raw "settings get global wifi_on"` |

换手机：任意命令后加 `-Serial SERIAL0001`。

## 二、目录结构

```
phone20/
├─ README.md                  ← 本文件
├─ index.json                 ← 页面索引 + 覆盖度 + 坑位清单
├─ device.json                ← 设备档案 + 采集时基线状态
├─ actions.json               ← ★ 一键操作命令表（46 个动作，带风险等级/验证级别/UI 对应）
├─ p20.ps1                    ← ★ 一键执行脚本（所有子命令）
├─ collect_system.ps1         ← 补采工具（把设置子页视图树批量落盘到 raw/）
├─ system/
│  ├─ launcher_home.json      ← 桌面布局、图标坐标、快捷入口
│  ├─ app_drawer.json         ← 抽屉 2 页 33 图标 + 2 文件夹 10 应用（含包名映射）
│  ├─ systemui_shade.json     ← 状态栏/通知栏/快捷设置 21 个 tile/电源菜单
│  ├─ systemui_recents_edge.json ← 最近任务 + 侧屏幕面板
│  └─ settings.json           ← 设置一级 22 项 + Wi-Fi/蓝牙/显示/日期时间/应用/开发者
├─ screenshots/               ← 9 张实拍截图
└─ raw/                       ← （补采后）每个设置子页的 dumpsys 视图树原文
```

## 三、关键坑（血泪）

1. **`uiautomator dump` 在本机被系统 KILL**，抓不到 XML。元素只能靠 `mobile-mcp`（accessibility）或 `dumpsys activity top` 视图树。
2. **多个 Switch 共用 `android:id/switch_widget`**：日期时间页 4 个开关 id 全一样，只能靠坐标或所在行文本区分。
3. **快捷设置 tile 容器没有 id**，靠 label（`WiFi,開啟。,Starlink,按鍵`）识别，状态就在 label 里。
4. **右侧 x>1060 是三星侧屏触控条**，点击/滑动会误开侧屏面板。
5. **设置一级页坐标随滚动变化**，别写死；用 `am start -a android.settings.XXX` 直达最稳。
6. **脚本必须 UTF-8 with BOM**，否则 PowerShell 5.1 按 GBK 读中文注释直接语法报错。
7. 关 Wi-Fi 会断 `192.168.31.93:5555` 的 WiFi adb；本机走 **USB adb**，不受影响。

## 四、风险分级（执行前看一眼）

- `danger`：关机/重启/卸载 —— 不要批量自动跑
- `high`：飞行模式、立即重启（设时区那页的按钮）、清空后台
- `medium`：Wi-Fi/蓝牙开关、深色模式、熄屏、卸载(user0)
- `low`：亮度/旋转/时区/音量/勿扰/定位
- `none`：查询/截图/展开面板/键事件

## 五、验证状态说明

`actions.json` 里每条 `verified` 字段：

- `read-verified`：已实际执行并回读（如自动旋转、自动时区、亮度）
- `help-only`：只确认命令存在和参数（如 `svc wifi`、`cmd location`）
- `unverified`：按 Android 12 通用语义登记，未在本机实跑

**实测已通过**：`status`、`rotate on/off`、`qs`、`collapse`。
