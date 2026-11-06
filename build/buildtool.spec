# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller 打包配置：一键构建工具（BuildFishFleet.exe）。

产物是**工具**，不是产品：给主人自己在开发机上重新打包用。约 12 MB。
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent    # noqa: F821 - PyInstaller 注入

datas = []
hiddenimports = [
    'tkinter', 'tkinter.ttk', 'tkinter.messagebox', 'tkinter.filedialog',
]
# 构建工具只需要标准库 + tkinter；把项目依赖全排掉，才不会被误打包
excludes = [
    'numpy', 'cv2', 'av', 'PIL', 'fastapi', 'uvicorn', 'starlette',
    'pydantic', 'httpx', 'uiautomator2', 'webview', 'pythonnet',
    'clr', 'clr_loader', 'matplotlib', 'scipy', 'pandas', 'fishfleet',
]

a = Analysis(
    [str(ROOT / 'build' / 'build_gui.py')],
    pathex=[str(ROOT / 'build')],
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
    a.binaries,
    a.datas,
    [],
    name='汇城AI构建工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'build' / 'assets' / 'app.ico'),
)
