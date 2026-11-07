# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller 打包配置：FishFleet 主程序。

2026-11-06 主人要求打包成 EXE 分发（对方可完全无环境）。
配合 `bootstrap/bootstrap_gui.py` 的引导器使用：
    首包（引导器 8 MB）→ 首次运行自动下 Python/依赖 → 启动本程序

关键点：
  · 只打 `fishfleet/` 代码 + 前端 static，**排除全部数据目录**（data/logs/apk 等 4.4 GB）；
  · C 扩展（numpy / cv2 / av / pydantic_core / PIL）靠 PyInstaller 的 hook 收集；
  · uvicorn 的子模块是动态 import，必须显式列进 hiddenimports，否则运行时报缺模块；
  · pywebview 在 Windows 上走 edgechromium（WebView2），后端模块也要带上。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent    # noqa: F821 - PyInstaller 注入；spec 在 build/ 下，根在上一级

# ---------------------------------------------------------------- 收集数据文件
datas = [
    (str(ROOT / 'apk' / 'ADBKeyboard.apk'), 'apk'),   # 中文输入必需（17 KB）
    # 前端（必须打进去，否则窗口白屏）
    (str(ROOT / 'fishfleet' / 'web' / 'static'), 'fishfleet/web/static'),
    # 配置模板（不含用户 API Key；config.yaml 用环境变量占位）
    (str(ROOT / 'fishfleet' / 'config'), 'fishfleet/config'),
    # 2026-11-07 主人反馈「一键里的快捷功能全没了」的根因：
    #   `server.py:_onekey_actions()` 读 `fishfleet/ui_library/phone20/actions.json`，
    #   而 datas 里**从来没打过 ui_library**，打包后路径不存在 → 返回空字典
    #   → 手机一键那一栏一个动作都不显示。
    #   同目录还有 bilibili/tiktok 的页面定义、phone20 的控件模板与证据截图，
    #   合计 19.3 MB / 173 个文件，直接整包带上（开发态零影响）。
    (str(ROOT / 'fishfleet' / 'ui_library'), 'fishfleet/ui_library'),
    # 2026-11-06 修：creative_studio 是项目根下的**命名空间包**（没有 __init__.py），
    # 而 `fishfleet/web/server.py:250` 有
    #     from creative_studio.backend import app as studio_app, resume_known_tasks
    # 打包后这个模块缺失 → server 导入即崩 → 用户只看到「后端启动失败」。
    #
    # 尝试过 `collect_submodules('creative_studio')`：在普通 python 里能收到，
    # 但 PyInstaller 执行 spec 时 sys.path 不含项目根，结果还是没打进去。
    # 改为把 .py 当**数据文件**直接放到 _internal/creative_studio/——
    # `_internal` 本身就在 sys.path 上，运行时能正常 import 这些 .py。
    (str(ROOT / 'creative_studio' / 'backend.py'), 'creative_studio'),
    (str(ROOT / 'creative_studio' / 'static'), 'creative_studio/static'),
    # 2026-11-07 视频生成 MCP 内置（trae-maker / seedance，zero-dependency）：
    # server.mjs 只用 node 内建模块，实际干活的是 ../scripts/*.ps1（单实现），
    # 两边相对位置不能变，所以 mcp/ 与 scripts/ 成对打进去；install.cmd 留给
    # 用户首次配网关/API Key 用（密钥存用户目录，绝不进包）。
    (str(ROOT / 'vendor' / 'trae-maker-MCP' / 'skills' / 'aiwork-seedance' / 'mcp'), 'vendor/trae-maker-MCP/skills/aiwork-seedance/mcp'),
    (str(ROOT / 'vendor' / 'trae-maker-MCP' / 'skills' / 'aiwork-seedance' / 'scripts'), 'vendor/trae-maker-MCP/skills/aiwork-seedance/scripts'),
    (str(ROOT / 'vendor' / 'trae-maker-MCP' / 'skills' / 'aiwork-seedance' / 'install.cmd'), 'vendor/trae-maker-MCP/skills/aiwork-seedance'),
    (str(ROOT / 'vendor' / 'trae-maker-MCP' / 'skills' / 'aiwork-seedance' / 'SKILL.md'), 'vendor/trae-maker-MCP/skills/aiwork-seedance'),
]

# ---------------------------------------------------------------- 隐藏导入
hiddenimports = [
    # uvicorn 动态加载的子模块（不列会 ModuleNotFoundError）
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.loops.asyncio',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    # pywebview 的 Windows 后端：**只保留 edgechromium（WebView2）**。
    # 打包环境下 `Python.Runtime.dll` 加载失败，整个窗口起不来。
    # FastAPI / Starlette 的可选依赖
    'starlette.middleware.cors',
    'starlette.middleware.gzip',
    'email.mime.text',
    'multipart',
    # 图像/视频
    'PIL._tkinter_finder',
    'av',
    'cv2',
    'imageio',
    'yaml',
    # 本项目内动态导入的模块
    'fishfleet.web.server',
    'fishfleet.web.execution',
    'fishfleet.core.scrcpy',
    'fishfleet.core.media',
]

# 2026-11-06 关键修复：
#   代码里有大量 `__import__("fishfleet.paths", fromlist=[...])` 这种**动态导入**，
#   PyInstaller 的静态分析只认字面 `import xxx`，看不到字符串里的模块名，
#   结果打出来的 exe 运行时报 `ModuleNotFoundError: No module named 'fishfleet.paths'`。
#   这里直接把 fishfleet 整包的子模块全部收进来，一劳永逸。
hiddenimports += collect_submodules('fishfleet')

# 2026-11-06 第三个关键修复（客户机「后端启动失败」的真正原因）：
#   `fishfleet/web/server.py:250` 有一句
#       from creative_studio.backend import app as studio_app, resume_known_tasks
#   而 `creative_studio` 是**项目根目录**下的包，不在 `fishfleet/` 里面。
#   `collect_submodules('fishfleet')` 收不到它，`datas` 里也只放了它的 static，
#   结果打出来的 exe 里**没有 backend.py**，server 一导入就崩，后端直接起不来。
#   用户看到的只有「后端启动失败，详情见日志」，日志里还什么都没有。
#   这里把 creative_studio 整包的子模块一并收进来。
try:
    hiddenimports += collect_submodules('creative_studio')
except Exception:
    # 精简构建时这个包可能不在，不要让整个打包失败
    pass



# ---------------------------------------------------------------- 排除项
# 注意：**排掉 tkinter**，主程序不用它（引导器才用 tkinter，单独打包）。
# 再排掉体积大又用不到的科学计算包，能省 100+ MB。
excludes = [
    'tkinter',
    'matplotlib',
    'scipy',
    'pandas',
    'pytest',
    'notebook',
    'IPython',
    'jupyter',
    'PyQt5',
    'PySide2',
    'PySide6',
    'wx',
    'sphinx',
    # 2026-11-06：改用 Edge --app 开窗，pywebview 及其 pythonnet 依赖
    # 在打包环境里加载 Python.Runtime.dll 不可靠，整条链路移除。
    'webview',
    'pythonnet',
    'clr',
    'clr_loader',
]

a = Analysis(
    [str(ROOT / 'fishfleet' / 'desktop.py')],
    pathex=[str(ROOT)],
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
    exclude_binaries=True,
    # 2026-11-06 主人定名：汇城群控AI版
    name='HuichengFleetAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # 不压缩：UPX 会让杀软误报，也拖慢启动
    console=False,             # 无控制台窗口（GUI 程序）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 应用图标（柴犬）：任务栏 / 文件管理器 / 快捷方式都用它
    icon=str(ROOT / 'build' / 'assets' / 'app.ico'),
    # Windows 版本信息：任务栏悬停 / 文件属性里显示中文产品名
    version=str(ROOT / 'build' / 'assets' / 'version.txt'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='HuichengFleetAI',
)
