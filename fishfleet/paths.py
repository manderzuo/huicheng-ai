r"""统一路径解析：开发态与打包态（PyInstaller）都能正确定位资源。

2026-11-06 背景：
    全项目有 23 个文件、38 处硬编码绝对路径（`C:\Program Files (x86)\huicheng_android\...`、
    `C:\Users\Administrator\.dsh\...`）。这些路径在别人电脑上都不存在，
    是打包分发的最大障碍。

设计原则：
    1. **开发态零影响**：现有机器上所有路径都还在，函数会照旧找到它们；
    2. **打包态可覆盖**：`sys.frozen` 为真时，优先找 exe 旁边的 `bin/`；
    3. **环境变量优先**：`ADB_PATH` / `FISHFLEET_NODE` 等仍可强制指定；
    4. **找不到不抛异常**：返回候选或裸命令名，让调用方自己决定怎么报错
       （很多调用点原本就允许 adb 走 PATH）。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在打包产物里（PyInstaller 等）。"""
    return bool(getattr(sys, 'frozen', False))


def app_base() -> Path:
    """程序根目录。

    - 打包态：exe 所在目录；
    - 开发态：项目根（即 `fishfleet/` 的上一级）。

    2026-11-06 重要修复（中文路径）：
        PyInstaller 打包后 `sys.executable` 里的中文会变成 `???`——
        实测 `C:\\...\\Desktop\\汇城AI\\xxx.exe` 取出来是 `C:\\...\\Desktop\\???AI\\xxx.exe`。
        根因是 bootloader 用 ANSI 编码读路径，多字节字符丢失。
        后果很隐蔽：程序还能启动（有 fallback），但 `bin/`、`data/`、`runtime/`
        全都指到不存在的目录，日志、adb、运行环境全部错位。

        解法：Windows 上改用宽字符 API `GetModuleFileNameW` 直接取真实路径，
        这是 PyInstaller 中文路径问题的标准做法。
    """
    if is_frozen():
        p = _exe_path_win32()
        if p is not None:
            return p.parent
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _exe_path_win32() -> Path | None:
    """用宽字符 API 取当前进程的 exe 真实路径（不受 ANSI 截断影响）。"""
    if os.name != 'nt':
        return None
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetModuleFileNameW(None, buf, 32768)
        if n <= 0:
            return None
        val = buf.value[:n]
        if not val:
            return None
        p = Path(val)
        return p if p.exists() else None
    except Exception:
        return None


def data_dir() -> Path:
    """用户数据目录（数据库、日志、配置）。打包后与程序同级，方便整体搬走。"""
    d = app_base() / 'data'
    d.mkdir(parents=True, exist_ok=True)
    return d


def bin_dir() -> Path:
    """外部二进制目录（adb / node / mobile-mcp / scrcpy 等）。"""
    return app_base() / 'bin'


def runtime_dir() -> Path:
    """运行环境目录 —— 由引导器（bootstrap）安装。

    2026-11-06 血泪教训：
        引导器把 Python / Node / mobile-mcp 装在 `runtime/` 下，
        但 `paths.py` 只在 `bin/` 里找，两边约定不一致。
        结果在客户机上是这样（真实日志）：
            'node':       'C:\\Users\\...\\hermes\\node\\node.EXE'   ← 撞上别人家的 node
            'mcp_exists': False                                      ← 找不到通信层
        后端因此起不来，用户只看到「后端启动失败」。

        现在统一：**bin/ 和 runtime/ 两处都找**，runtime/ 是引导器的标准位置。
    """
    return app_base() / 'runtime'


def _first_existing(*candidates) -> Path | None:
    """返回第一个存在的路径。"""
    for c in candidates:
        if not c:
            continue
        try:
            p = Path(c)
        except (TypeError, ValueError):
            continue
        if p.exists():
            return p
    return None


def _on_path(name: str) -> str | None:
    """在 PATH 里找可执行文件。"""
    return shutil.which(name)


# ---------------------------------------------------------------- adb
#: 开发机上的原始位置（保持兼容；别人的机器上没有）
_LEGACY_ADB = r'C:\Program Files (x86)\huicheng_android\tools\adb.exe'


def adb_path() -> str:
    """定位 adb。优先级：环境变量 → 程序 bin/ → 开发机旧路径 → PATH → 'adb'。"""
    env = os.environ.get('ADB_PATH')
    if env and Path(env).exists():
        return env
    found = _first_existing(
        bin_dir() / 'adb.exe',
        _LEGACY_ADB,
    )
    if found:
        return str(found)
    return _on_path('adb') or 'adb'


def adb_dir() -> Path | None:
    """adb 所在目录（部分流程要把这个目录加进 PATH）。"""
    p = Path(adb_path())
    return p.parent if p.name.lower() == 'adb.exe' else None


# ---------------------------------------------------------------- node
#: 开发环境下常见的 DSH 运行时路径（可选 fallback，客户机上不存在也没关系）
_LEGACY_NODE = r'%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'.replace('%USERPROFILE%', __import__('os').environ.get('USERPROFILE', ''))


def node_path() -> str:
    """定位 node。优先级：环境变量 → bin/ → runtime/ → 开发机旧路径 → PATH → 'node'。"""
    env = os.environ.get('FISHFLEET_NODE') or os.environ.get('NODE_BIN')
    if env and Path(env).exists():
        return env
    found = _first_existing(
        bin_dir() / 'node.exe',
        bin_dir() / 'node' / 'node.exe',
        runtime_dir() / 'node' / 'node.exe',
        runtime_dir() / 'node.exe',
        _LEGACY_NODE,
    )
    if found:
        return str(found)
    return _on_path('node') or 'node'


def python_path() -> str:
    """定位主程序要用的 Python（引导器装的嵌入式 Python）。

    注意：打包后的主程序**自带** Python 运行时，这个函数主要是给
    `runtime/` 里的脚本（以及将来的子进程调用）用。
    """
    env = os.environ.get('FISHFLEET_PYTHON')
    if env and Path(env).exists():
        return env
    found = _first_existing(
        runtime_dir() / 'python' / 'python.exe',
        bin_dir() / 'python.exe',
    )
    if found:
        return str(found)
    return sys.executable if not is_frozen() else 'python'


# ---------------------------------------------------------------- mobile-mcp
#: 开发环境下常见的 mobile-mcp 路径（可选 fallback）
_LEGACY_MCP = r'%USERPROFILE%\.dsh\mobile-mcp-runtime\start-mobile-mcp.cjs'.replace('%USERPROFILE%', __import__('os').environ.get('USERPROFILE', ''))


def mobile_mcp_entry() -> str:
    """定位 mobile-mcp 的启动脚本（start-mobile-mcp.cjs）。

    同样要同时看 `bin/mobile-mcp/` 和 `runtime/mobile-mcp/`——
    引导器默认装在 `runtime/` 下（见 runtime_dir 的注释）。
    """
    env = os.environ.get('FISHFLEET_MCP_ENTRY')
    if env and Path(env).exists():
        return env
    found = _first_existing(
        runtime_dir() / 'mobile-mcp' / 'start-mobile-mcp.cjs',
        bin_dir() / 'mobile-mcp' / 'start-mobile-mcp.cjs',
        _LEGACY_MCP,
    )
    if found:
        return str(found)
    # 找不到也返回引导器的标准位置，让上层报「缺 mobile-mcp」而不是 KeyError
    return str(runtime_dir() / 'mobile-mcp' / 'start-mobile-mcp.cjs')


def mobile_mcp_dir() -> Path:
    """mobile-mcp 运行时目录（含 node_modules）。"""
    return Path(mobile_mcp_entry()).parent


def mobilecli_path() -> str:
    """定位 mobilecli 可执行文件（mobile-mcp 依赖的原生 CLI）。"""
    env = os.environ.get('FISHFLEET_MOBILECLI')
    if env and Path(env).exists():
        return env
    rel = Path('node_modules') / '@mobilenext' / 'mobilecli-windows-amd64' \
        / 'mobilecli-windows-amd64.exe'
    found = _first_existing(
        runtime_dir() / 'mobile-mcp' / rel,
        bin_dir() / 'mobile-mcp' / rel,
        Path(r'C:\Users\Administrator\.dsh\mobile-mcp-runtime') / rel,
    )
    if found:
        return str(found)
    return str(runtime_dir() / 'mobile-mcp' / rel)


# ---------------------------------------------------------------- 视频生成 MCP（trae-maker / seedance）
def seedance_server() -> str:
    """定位 seedance MCP 的 server.mjs。

    优先级：环境变量 → 打包态 `_internal/vendor/...`（含 _MEIPASS）→
    程序根 `vendor/...` → 开发态项目内路径。
    找不到也返回打包态标准位置，让上层报「缺文件」而不是 KeyError。
    """
    import sys as _sys
    env = os.environ.get('FISHFLEET_SEEDANCE')
    if env and Path(env).exists():
        return env
    rel = Path('vendor') / 'trae-maker-MCP' / 'skills' / 'aiwork-seedance' / 'mcp' / 'server.mjs'
    cands: list[Path] = []
    try:
        meipass = getattr(_sys, '_MEIPASS', None)
        if meipass:
            cands.append(Path(meipass) / rel)
    except Exception:
        pass
    cands.append(app_base() / '_internal' / rel)
    cands.append(app_base() / rel)
    cands.append(Path(__file__).resolve().parents[1] / rel)
    found = _first_existing(*cands)
    if found:
        return str(found)
    return str(app_base() / '_internal' / rel)


def seedance_scripts_dir() -> Path:
    """seedance runner 脚本目录（server.mjs 的 ../scripts，与其保持同级）。"""
    return Path(seedance_server()).parent.parent / 'scripts'


# ---------------------------------------------------------------- scrcpy / apk
def scrcpy_jar() -> Path | None:
    """scrcpy-server.jar 位置。"""
    found = _first_existing(
        bin_dir() / 'scrcpy-server.jar',
        bin_dir() / 'scrcpy' / 'scrcpy-server.jar',
    )
    return found


def apk_path(name: str) -> Path | None:
    """定位随包的 APK（ADBKeyboard / assistant / XWKeyboard 等）。

    2026-11-07：加开发态 `apk/` 候选。ADBKeyboard.apk（17 KB）是中文输入
    的必需组件，`bin/apk/` 里没有它时，至少开发机能从项目根的 `apk/` 找到。
    """
    found = _first_existing(
        bin_dir() / 'apk' / name,
        bin_dir() / name,
        app_base() / 'apk' / name,
        # 2026-11-07：PyInstaller onedir 把 datas 放进 _internal\，
        # 所以打包后 apk 实际在 <程序>\_internal\apk\。
        # 不加上这一条，客户机上中文输入直接失效（ADBKeyboard 找不到）。
        app_base() / '_internal' / 'apk' / name,
        Path(__file__).resolve().parents[1] / 'apk' / name,
        Path(r'C:\Program Files (x86)\huicheng_android\tools') / name,
    )
    return found


# ---------------------------------------------------------------- ffmpeg
def ffmpeg_path() -> str | None:
    """定位 ffmpeg.exe（可选，av 库自带解码时不需要）。"""
    env = os.environ.get('FFMPEG_PATH')
    if env and Path(env).exists():
        return env
    found = _first_existing(
        bin_dir() / 'ffmpeg.exe',
        Path(r'C:\Program Files (x86)\huicheng_android\ffmpeg.exe'),
    )
    return str(found) if found else _on_path('ffmpeg')


def mobilecli_path() -> str:
    """定位 mobilecli 可执行文件（mobile-mcp 依赖的原生 CLI）。"""
    env = os.environ.get('FISHFLEET_MOBILECLI')
    if env and Path(env).exists():
        return env
    found = _first_existing(
        bin_dir() / 'mobile-mcp' / 'node_modules' / '@mobilenext'
        / 'mobilecli-windows-amd64' / 'mobilecli-windows-amd64.exe',
        Path(r'C:\Users\Administrator\.dsh\mobile-mcp-runtime\node_modules'
             r'\@mobilenext\mobilecli-windows-amd64\mobilecli-windows-amd64.exe'),
    )
    if found:
        return str(found)
    return str(bin_dir() / 'mobile-mcp' / 'node_modules' / '@mobilenext'
               / 'mobilecli-windows-amd64' / 'mobilecli-windows-amd64.exe')


def huicheng_config() -> Path | None:
    """原厂 sys_config.toml（品牌码等）。打包态可选，找不到不影响主流程。"""
    found = _first_existing(
        bin_dir() / 'sys_config.toml',
        Path(r'C:\Program Files (x86)\huicheng_android\sys_config.toml'),
    )
    return found


def describe() -> dict:
    """诊断用：一次性报告所有关键路径的解析结果。"""
    return {
        'frozen': is_frozen(),
        'base': str(app_base()),
        'adb': adb_path(),
        'node': node_path(),
        'mobile_mcp': mobile_mcp_entry(),
        'mcp_exists': Path(mobile_mcp_entry()).exists(),
        'ffmpeg': ffmpeg_path() or '(无)',
        'scrcpy_jar': str(scrcpy_jar()) if scrcpy_jar() else '(无)',
        'seedance': seedance_server(),
        'seedance_exists': Path(seedance_server()).exists(),
    }
