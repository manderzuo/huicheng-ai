# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller 打包配置：汇城AI 安装引导器。

产物：`汇城AI安装引导.exe`（约 10 MB），双击后：
  1. 检测本机缺哪些运行环境（Python / 依赖 / Node / mobile-mcp / adb）；
  2. 缺的自动从国内源下载安装（已装则跳过，可重复运行）；
  3. 装完自动启动主程序 `HuichengFleetAI.exe`。

设计要点：
  · 引导器**自己带 tkinter**（主程序反而排掉了 tkinter，体积省下来）；
  · 资源 `resources/`（adb 5.8MB + get-pip 2.3MB）随包，避免这两个源 404；
  · `bootstrap.py` 是数据文件而非模块（GUI 用 importlib 加载），
    直接 add-data 到根目录最简单可靠。
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent    # noqa: F821 - PyInstaller 注入

datas = [
    # 核心逻辑（GUI 里 import bootstrap）
    (str(ROOT / 'bootstrap' / 'bootstrap.py'), '.'),
    # 随包内置资源
    (str(ROOT / 'bootstrap' / 'resources'), 'resources'),
]

hiddenimports = [
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    'tkinter.filedialog',
    'bootstrap',
]

excludes = [
    # 引导器只需要标准库 + tkinter，把这些大件全排掉，产物才 10MB 级
    'numpy', 'cv2', 'av', 'PIL', 'fastapi', 'uvicorn', 'starlette',
    'pydantic', 'httpx', 'uiautomator2', 'webview', 'pythonnet',
    'clr', 'clr_loader', 'matplotlib', 'scipy', 'pandas',
    'fishfleet',
]

a = Analysis(
    [str(ROOT / 'bootstrap' / 'bootstrap_gui.py')],
    pathex=[str(ROOT / 'bootstrap')],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # ← 2026-11-06 改 onedir：不再塞进单文件
    name='汇城AI安装引导',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # GUI 程序，不要黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'build' / 'assets' / 'app.ico'),
    version=str(ROOT / 'build' / 'assets' / 'version_bootstrap.txt'),
)

# 2026-11-06 关键改动：从 **onefile 改成 onedir**。
#   onefile 每次启动都要把 16 MB（含 adb.exe 5.8 MB）解压到 %TEMP%\_MEIxxxx，
#   在客户机上会报 `Could not create temporary directory!`，
#   而且解压后的 resources/ 在 sys._MEIPASS 下，找起来绕。
#   onedir 直接把依赖和 resources 放在 exe 旁边的 _internal/ 里：
#     · 启动快（不解压）
#     · 不会再出临时目录错误
#     · resources 位置确定（_internal/resources/）
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='汇城AI安装引导',
)
