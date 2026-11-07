# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller 打包配置：网络与安装环境检测工具。

产物 `网络检测.exe`（约 10 MB，单文件）：
    客户机装不上时，双击它就能看出是网络问题还是别的。
    不需要 Python 环境。
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent    # noqa: F821 - PyInstaller 注入

a = Analysis(
    [str(ROOT / 'engine' / 'netcheck.py')],
    pathex=[str(ROOT / 'engine')],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 检测工具只用标准库，把项目依赖全排掉
    excludes=[
        'numpy', 'cv2', 'av', 'PIL', 'fastapi', 'uvicorn', 'starlette',
        'pydantic', 'httpx', 'uiautomator2', 'webview', 'pythonnet',
        'clr', 'clr_loader', 'matplotlib', 'scipy', 'pandas', 'fishfleet',
    ],
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
    name='网络检测',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,               # 这个是命令行工具，要显示窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'build' / 'assets' / 'app.ico'),
)
