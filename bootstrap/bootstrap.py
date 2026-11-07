r"""FishFleet 引导器：首次运行自动装好全部运行环境，然后启动主程序。

设计目标（2026-11-06 主人要求）：
  · 发给**完全没有环境**的电脑也能跑；
  · 大件不走安装包，**联网自己下载**（首包只有几 MB）；
  · **国内源优先**（对方没有代理），失败自动回落官方源；
  · 已装好的跳过，**可重复运行**；
  · 装失败的项能单独重试，并给出**具体哪一步什么错**。

安装布局（与主程序同级）：
    FishFleet/
    ├── FishFleet.exe            主程序
    ├── bootstrap.exe            本引导器
    ├── runtime/
    │   ├── python/              嵌入式 Python（含 site-packages）
    │   ├── node/                Node.js 便携版
    │   ├── mobile-mcp/          mobile-mcp（npm 装）
    │   └── .state.json          各组件安装状态（断点续装）
    ├── bin/adb.exe              platform-tools
    └── data/                    运行时数据（首次启动自动建）

用法：
    python bootstrap.py           正常引导（有界面）
    python bootstrap.py --check   只检查环境并打印报告
    python bootstrap.py --repair  重装所有缺失/损坏的组件
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- 常量

#: 组件清单：(key, 展示名, 预估大小MB)
COMPONENTS = [
    ('python', 'Python 运行时', 12),
    ('pip', 'pip 与依赖包', 250),
    ('node', 'Node.js 运行时', 30),
    ('mobile_mcp', 'mobile-mcp 通信层', 70),
    ('adb', 'ADB 调试工具', 10),
]

#: 国内源（优先）。2026-11-06 实测：
#:   npmmirror 的 python / node / npm 包都可用；
#:   但 `-/binary/android-tools/` 和 `-/binary/python-tools/` 返回 404，
#:   所以 adb 与 get-pip.py **改为随包内置**（见 BUILTIN 目录），不依赖网络。
MIRRORS = {
    'python': [
        'https://registry.npmmirror.com/-/binary/python/3.12.7/python-3.12.7-embed-amd64.zip',
        'https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip',
    ],
    'node': [
        'https://registry.npmmirror.com/-/binary/node/v20.18.0/node-v20.18.0-win-x64.zip',
        'https://nodejs.org/dist/v20.18.0/node-v20.18.0-win-x64.zip',
    ],
    # adb / get-pip 优先用内置；这两个是网络兜底
    'adb': [
        'https://dl.google.com/android/repository/platform-tools-latest-windows.zip',
    ],
    'get_pip': [
        'https://bootstrap.pypa.io/get-pip.py',
    ],
}

#: 随包内置的文件（不打网络请求，直接拷）：(内置文件名, 目标相对路径)
#: 打包时把这两个文件放进 `bootstrap/resources/`。
BUILTIN = {
    'adb': ['adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll'],
    'get_pip': ['get-pip.py'],
}

PIP_MIRROR = 'https://pypi.tuna.tsinghua.edu.cn/simple'
NPM_MIRROR = 'https://registry.npmmirror.com'

#: 需要装的 pip 包（与主程序实际 import 一致）
#: 2026-11-06 修：**移除 pywebview**。
#:   主程序已经改用 Edge `--app` 开窗，不再需要 pywebview；
#:   而 pywebview 在 Windows 上会拉 pythonnet（.NET 互操作），
#:   在干净的嵌入式 Python 里经常装不上，导致整批依赖失败
#:   （主人实测：界面卡在「pip 安装中…」然后报「pip 与依赖包 失败」）。
PIP_PACKAGES = [
    'fastapi', 'uvicorn', 'starlette', 'pydantic',
    'pillow', 'numpy', 'av', 'opencv-python', 'httpx',
    'uiautomator2', 'imageio', 'pyyaml',
]

#: 逐个 import 检查（缺哪个报哪个，别只说一句"失败"）
PIP_IMPORTS = [
    ('fastapi', 'fastapi'),
    ('uvicorn', 'uvicorn'),
    ('starlette', 'starlette'),
    ('pydantic', 'pydantic'),
    ('Pillow', 'PIL'),
    ('numpy', 'numpy'),
    ('av', 'av'),
    ('opencv-python', 'cv2'),
    ('httpx', 'httpx'),
    ('uiautomator2', 'uiautomator2'),
    ('imageio', 'imageio'),
    ('PyYAML', 'yaml'),
]

#: mobile-mcp 的两个 npm 包（版本锁死，与开发机一致）
NPM_PACKAGES = [
    '@mobilenext/mobile-mcp@1.0.15',
    '@mobilenext/mobilecli-windows-amd64@1.0.21',
]


# ---------------------------------------------------------------- 基础工具

def app_base() -> Path:
    """引导器所在目录（打包后 = exe 所在；脚本态 = 脚本所在）。

    2026-11-06 修复中文路径：PyInstaller 打包后 sys.executable 里的中文
    会变成 ???（bootloader 用 ANSI 编码），导致安装到错误目录。
    这里优先用 Windows 宽字符 API 取真实路径。
    """
    if getattr(sys, 'frozen', False):
        if os.name == 'nt':
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(32768)
                n = ctypes.windll.kernel32.GetModuleFileNameW(None, buf, 32768)
                if n > 0 and buf.value:
                    p = Path(buf.value[:n])
                    if p.exists():
                        return p.parent
            except Exception:
                pass
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE = app_base()
# 2026-11-07 单 runtime 布局：运行环境只放在安装根（AI/runtime/），
# 引导器在 AI/汇城AI安装引导/。本目录优先（兼容旧包），否则用父目录的。
_DEF_RUNTIME = BASE / 'runtime'
RUNTIME = _DEF_RUNTIME if _DEF_RUNTIME.exists() else (BASE.parent / 'runtime')
_DEF_BIN = BASE / 'bin'
BIN = _DEF_BIN if _DEF_BIN.exists() else (BASE.parent / 'bin')
STATE_FILE = RUNTIME / '.state.json'
#: 随包内置资源目录（打包时把 adb / get-pip.py 放这里）
RESOURCES = BASE / 'resources'


def _meipass() -> Path | None:
    """PyInstaller onefile 解压出的临时目录。

    2026-11-06 关键修复：
        `bootstrap.spec` 把引导器打成了**单文件**（EXE 里带 a.datas，没有 COLLECT），
        所有 `datas`（包括 `resources/adb.exe`）都被解压到 `sys._MEIPASS` 下，
        **不是** exe 旁边。而原来的 `builtin_file()` 只找 `BASE/resources`，
        于是永远找不到 adb —— 客户机上 `bin\adb.exe` 压根没被安装，
        表现就是「运行 .\\bin\\adb.exe devices 无任何反应」。
    """
    p = getattr(sys, '_MEIPASS', None)
    return Path(p) if p else None


def builtin_file(name: str) -> Path | None:
    """在随包资源里找内置文件（onefile / onedir / 开发态都支持）。"""
    cands: list[Path] = []
    mp = _meipass()
    if mp is not None:
        cands.append(mp / 'resources')
    cands.append(RESOURCES)
    cands.append(BASE / 'bootstrap' / 'resources')
    for base in cands:
        try:
            p = base / name
            if p.exists():
                return p
        except Exception:
            continue
    return None


def log(msg: str) -> None:
    """安全打印：GBK 控制台下 `✓ / ✗` 等字符会抛 UnicodeEncodeError。

    2026-11-06 实测：
        打包后的引导器跑 `--check`，输出到 Windows 默认的 GBK 代码页时
        `UnicodeEncodeError: 'gbk' codec can't encode character '\\u2717'`，
        整个程序直接崩（客户机上表现为「点了没反应」或卡住）。
    处理：先试原样输出，失败就降级成纯 ASCII 替代符号。
    """
    try:
        print(msg, flush=True)
        return
    except (UnicodeEncodeError, OSError, ValueError):
        pass
    try:
        safe = (msg.replace('✓', '[OK]').replace('✗', '[X]')
                   .replace('→', '->').replace('…', '...')
                   .replace('（', '(').replace('）', ')'))
        print(safe.encode('ascii', 'replace').decode('ascii'), flush=True)
    except Exception:
        pass


def _fix_stdio_encoding() -> None:
    """把 stdout/stderr 强制成 UTF-8，避免 GBK 代码页下输出中文/符号时崩。

    PyInstaller 的 GUI 程序 stdout 可能是 None（另有 _SafeWriter 处理），
    这里只管「有 stdout 但编码是 GBK」的情况。
    """
    for stream in ('stdout', 'stderr'):
        s = getattr(sys, stream, None)
        if s is None:
            continue
        try:
            s.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
        except Exception:
            pass


_fix_stdio_encoding()


def _emit(line: str, full: list, label: str, progress, show_fn, tmod) -> None:
    """把一行输出收进完整日志，并把提炼后的状态推给界面。"""
    line = line.strip()
    if not line:
        return
    full.append(line)
    if not progress:
        return
    try:
        shown = show_fn(line)
        if shown:
            progress(shown)
    except Exception:
        pass


def run_stream(cmd, label='', progress=None, idle_timeout=600, cwd=None,
               env=None) -> tuple[int, str]:
    r"""跑外部命令并**实时**把输出喂给界面，而不是等它结束。

    2026-11-07 主人反馈「有一台电脑装到 PIP 就一直卡住」：
        原来是 `subprocess.run(capture_output=True, timeout=3600)`，
        pip 下载 250 MB 期间**一个字节都不输出**，界面看起来就是死了。
        实际在下载，但用户不知道，会以为卡住然后强杀。

    现在改成 Popen 逐行读取：
        · 每读到一行就回调 progress()，界面实时滚动
        · 用正则把 pip 的「Downloading / Collecting / 百分比」提炼成一行状态
        · 用 **idle_timeout（多久没有任何输出）** 判卡死，而不是总时长
          —— 慢速下载可能超过 1 小时，但只要还在动就不该被杀
        · 返回 (退出码, 完整输出文本)

    idle_timeout 默认 600 秒（10 分钟无任何输出才认定真卡死）。
    """
    import re as _re
    import time as _t

    full: list[str] = []
    last_output = _t.time()
    try:
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            bufsize=1, cwd=cwd, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except Exception as exc:                        # noqa: BLE001
        return -1, '启动失败: %s' % exc

    # 2026-11-07 改进：只显示**有信息量**的行。
    #   原来每个「Collecting xxx」都刷一行，12 个包能刷几十行噪音，
    #   反而把真正的「下载 xxx」淹没了（主人截图里全是「依赖 准备」）。
    #   现在：下载/进度/完成/错误 才显示，「准备」只在换包时提示一次。
    _dl = _re.compile(r'Downloading\s+(?:https?://\S+/)?([\w.\-+]+?\.(?:whl|tar\.gz|zip))'
                      r'(?:\s+\(([^)]+)\))?')
    _pct = _re.compile(r'(\d{1,3})%')
    _size = _re.compile(r'([\d.]+\s*[kMG]?B)\s*/\s*([\d.]+\s*[kMG]?B)')
    import time as _tm
    _state = {'last': '', 'start': _tm.time(), 'done_mb': 0.0}

    def _size_mb(text):
        # '15.9 MB' / '58 kB' / '1.2 GB' -> MB（浮点）
        if not text:
            return 0.0
        t = str(text).strip().upper().replace(' ', '')
        try:
            if t.endswith('GB'):
                return float(t[:-2]) * 1024.0
            if t.endswith('MB'):
                return float(t[:-2])
            if t.endswith('KB'):
                return float(t[:-2]) / 1024.0
            if t.endswith('B'):
                return float(t[:-1]) / 1048576.0
        except Exception:
            return 0.0
        return 0.0

    def _used() -> str:
        sec = int(_tm.time() - _state['start'])
        return '%d:%02d' % (sec // 60, sec % 60)

    def _show(line: str) -> str | None:
        m = _dl.search(line)
        if m:
            name = m.group(1)
            human = m.group(2) or ''
            _state['done_mb'] += _size_mb(human)
            out = ('%s下载 %s %s | 累计 %.1f MB / 约 250 MB | 已用 %s'
                   % (label, name, human, _state['done_mb'], _used()))
            if out != _state['last']:
                _state['last'] = out
                return out
            return None
        # 进度百分比（只在整 10% 时刷，减少噪音）
        pm = _pct.search(line)
        sm = _size.search(line)
        if pm and sm:
            pct = int(pm.group(1))
            if pct % 10 == 0:
                out = '%s%d%%  (%s / %s)' % (label, pct, sm.group(1), sm.group(2))
                if out != _state['last']:
                    _state['last'] = out
                    return out
            return None
        # 完成 / 开始安装
        if 'Successfully installed' in line:
            pkgs = line.split('Successfully installed', 1)[-1].strip()
            return '%s完成：%s' % (label, pkgs[:180])
        if 'Installing collected packages' in line:
            return '%s所有包已下载，开始安装…' % label
        # 错误
        if 'ERROR' in line.upper() or 'error:' in line.lower():
            return '%s%s' % (label, line[:160])
        # 重试 / 超时提示
        if 'Retrying' in line or 'Read timed out' in line or 'Connection' in line:
            return '%s%s' % (label, line[:140])
        return None

    # 2026-11-07 关键修复：pip 的进度条是用 \r 刷新的，不是 \n。
    #   原来用 readline()（按 \n 切），结果**整段进度条被当成一行**，
    #   在 GBK 控制台下变成 依赖 下载 。 这种乱码，界面上看不出在下什么。
    #   改为按 \r 和 \n 双分隔读取，只保留每段的最后一个片段用于显示。
    import io as _io

    try:
        buf = ''
        while True:
            ch = p.stdout.read(1)                   # type: ignore[union-attr]
            if not ch:
                if buf:
                    _emit(buf, full, label, progress, _show, _t)
                break
            if ch in '\r\n':
                if buf:
                    last_output = _t.time()
                    _emit(buf, full, label, progress, _show, _t)
                    buf = ''
                continue
            buf += ch
            if len(buf) > 4000:                     # 防御：超长行截断
                _emit(buf[:4000], full, label, progress, _show, _t)
                buf = ''
                last_output = _t.time()
            # 心跳：20 秒没有新输出就刷一行，明确告诉用户还在下
            if _t.time() - last_output > 20:
                last_output = _t.time()
                if progress:
                    try:
                        sec = int(_t.time() - _state['start'])
                        progress('%s仍在下载… 累计 %.1f MB / 约 250 MB | 已用 %d:%02d'
                                 % (label, _state['done_mb'], sec // 60, sec % 60))
                    except Exception:
                        pass
            if _t.time() - last_output > idle_timeout:
                try:
                    p.kill()
                except Exception:
                    pass
                msg = '[超时] %d 秒没有任何输出，已终止' % idle_timeout
                full.append(msg)
                if progress:
                    progress(label + msg)
                return -2, '\n'.join(full)
    finally:
        try:
            p.stdout.close()                       # type: ignore[union-attr]
        except Exception:
            pass

    try:
        p.wait(timeout=60)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    return p.returncode, '\n'.join(full)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding='utf-8')


# ---------------------------------------------------------------- 下载

def download(url_list, dest: Path, label: str = '', retries: int = 2,
             progress=None) -> bool:
    """按候选列表逐个尝试下载；支持简单重试。返回是否成功。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for url in url_list:
        for attempt in range(retries + 1):
            try:
                if progress:
                    progress('%s：连接 %s' % (label, url.split('/')[2]))
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'FishFleet-Bootstrap/1.0'})
                with urllib.request.urlopen(req, timeout=30) as r, \
                        open(dest, 'wb') as f:
                    total = int(r.headers.get('Content-Length') or 0)
                    done = 0
                    while True:
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress and total:
                            progress('%s：%d%%（%.1f/%.1f MB）' % (
                                label, done * 100 // total,
                                done / 1048576, total / 1048576))
                if dest.exists() and dest.stat().st_size > 0:
                    return True
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                if progress:
                    progress('%s：失败（%s），重试 %d/%d'
                             % (label, type(exc).__name__, attempt + 1, retries))
                time.sleep(1.5)
    return False


def unzip(src: Path, dst: Path, strip_root: bool = False) -> bool:
    """解压 zip。strip_root=True 时去掉压缩包里的顶层目录。"""
    try:
        with zipfile.ZipFile(src) as z:
            names = z.namelist()
            root = ''
            if strip_root and names:
                first = names[0].split('/')[0]
                if all(n.startswith(first + '/') or n == first for n in names):
                    root = first + '/'
            dst.mkdir(parents=True, exist_ok=True)
            for n in names:
                if root and n.startswith(root):
                    rel = n[len(root):]
                else:
                    rel = n
                if not rel:
                    continue
                target = dst / rel
                if n.endswith('/'):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as s, open(target, 'wb') as t:
                    shutil.copyfileobj(s, t)
        return True
    except (zipfile.BadZipFile, OSError) as exc:
        log('  解压失败：%s' % exc)
        return False


# ---------------------------------------------------------------- 检测

def python_exe() -> Path:
    return RUNTIME / 'python' / 'python.exe'


def node_exe() -> Path:
    return RUNTIME / 'node' / 'node.exe'


def adb_exe() -> Path:
    p = BIN / 'adb.exe'
    if p.exists():
        return p
    legacy = Path(r'C:\Program Files (x86)\huicheng_android\tools\adb.exe')
    return legacy if legacy.exists() else p


def has_python() -> bool:
    return python_exe().exists()


def has_pip_and_packages() -> bool:
    """装没装依赖：逐个 import 试，缺哪个能说清楚。"""
    missing = pip_missing()
    return not missing


def pip_missing() -> list[tuple[str, str]]:
    """返回 [(pip 包名, import 名), ...]，只含**导入失败**的项。

    2026-11-06 改为逐个检查：
        原来是一句 `import a,b,c,...`，任何一个失败整句就 False，
        界面上只能看到「pip 与依赖包 失败」，完全不知道是哪个包出的问题
        （主人截图里就是这个现象）。现在逐个跑，能精确报出缺哪个。
    """
    if not has_python():
        return list(PIP_IMPORTS)
    out: list[tuple[str, str]] = []
    py = str(python_exe())
    for pkg, mod in PIP_IMPORTS:
        try:
            r = subprocess.run([py, '-c', 'import %s' % mod],
                               capture_output=True, timeout=40)
            if r.returncode != 0:
                out.append((pkg, mod))
        except Exception:
            out.append((pkg, mod))
    return out


def has_node() -> bool:
    return node_exe().exists()


def has_mobile_mcp() -> bool:
    entry = RUNTIME / 'mobile-mcp' / 'start-mobile-mcp.cjs'
    return entry.exists() and (RUNTIME / 'mobile-mcp' / 'node_modules').exists()


def has_adb() -> bool:
    p = adb_exe()
    if not p.exists():
        return False
    try:
        r = subprocess.run([str(p), 'version'], capture_output=True, timeout=15)
        return b'Android Debug Bridge' in (r.stdout or b'')
    except Exception:
        return False


def has_seedance() -> bool:
    """视频生成 MCP 是否随主程序内置（零依赖，只看文件在不在）。

    vendor 打在主程序包里（AI/_internal/vendor/...），引导器在
    AI/汇城AI安装引导/，所以 BASE 的父目录也要找。
    """
    rel = Path('vendor/trae-maker-MCP/skills/aiwork-seedance/mcp/server.mjs')
    cands = [
        BASE / '_internal' / rel,
        BASE / rel,
        BASE.parent / '_internal' / rel,
        BASE.parent / rel,
    ]
    return any(p.exists() for p in cands)


def check_all() -> dict:
    return {
        'python': has_python(),
        'pip': has_pip_and_packages(),
        'node': has_node(),
        'mobile_mcp': has_mobile_mcp(),
        'adb': has_adb(),
        'seedance': has_seedance(),
    }


def check_all() -> dict:
    return {
        'python': has_python(),
        'pip': has_pip_and_packages(),
        'node': has_node(),
        'mobile_mcp': has_mobile_mcp(),
        'adb': has_adb(),
    }


# ---------------------------------------------------------------- 安装各组件

def install_python(progress=None) -> bool:
    archive = RUNTIME / '_python.zip'
    if not download(MIRRORS['python'], archive, 'Python 运行时', progress=progress):
        return False
    target = RUNTIME / 'python'
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    if not unzip(archive, target):
        return False
    archive.unlink(missing_ok=True)

    # 嵌入式包默认不加载 site-packages，要改 ._pth
    pth = list(target.glob('python*._pth'))
    if pth:
        text = pth[0].read_text(encoding='utf-8', errors='ignore')
        if 'Lib\\site-packages' not in text and 'Lib/site-packages' not in text:
            pth[0].write_text(text.rstrip() + '\nLib\\site-packages\nimport site\n',
                              encoding='utf-8')
    return python_exe().exists()


#: 候选 pip 镜像（按国内访问速度排序）
PIP_MIRRORS = [
    ('清华 TUNA', 'https://pypi.tuna.tsinghua.edu.cn/simple'),
    ('阿里云', 'https://mirrors.aliyun.com/pypi/simple'),
    ('腾讯云', 'https://mirrors.cloud.tencent.com/pypi/simple'),
    ('中科大', 'https://pypi.mirrors.ustc.edu.cn/simple'),
    ('华为云', 'https://repo.huaweicloud.com/repository/pypi/simple'),
]

#: 测速缓存（秒）；同一台机器不用每次都测
_MIRROR_CACHE = RUNTIME / '.mirror.txt'


def _probe_url(url: str, timeout: float = 6.0) -> float | None:
    """返回访问该 URL 的耗时（秒），失败返回 None。"""
    import time as _t
    import urllib.request as _ur
    try:
        req = _ur.Request(url.rstrip('/') + '/pip/',
                          headers={'User-Agent': 'FishFleet-Bootstrap/1.0'})
        t0 = _t.time()
        with _ur.urlopen(req, timeout=timeout) as r:
            r.read(256)
        return _t.time() - t0
    except Exception:
        return None


def pick_mirror(progress=None) -> str:
    """测速挑最快的 pip 镜像。

    2026-11-07 加：客户机网络千差万别，固定用清华源不一定最快
    （有的 ISP 到清华绕的国际线路，反而阿里云快）。
    这里依次测 5 个国内源，取延迟最低的；结果缓存到 runtime/.mirror.txt，
    下次启动直接用，不重复测速（省 3~5 秒）。
    """
    # 1) 缓存
    try:
        if _MIRROR_CACHE.exists():
            cached = _MIRROR_CACHE.read_text(encoding='utf-8').strip()
            if cached.startswith('http') and any(cached == u for _, u in PIP_MIRRORS):
                return cached
    except Exception:
        pass

    # 2) 测速
    best_url, best_cost = None, 1e9
    for name, url in PIP_MIRRORS:
        cost = _probe_url(url)
        if cost is None:
            continue
        if progress:
            try:
                progress('测速 %s: %.2f 秒' % (name, cost))
            except Exception:
                pass
        if cost < best_cost:
            best_cost, best_url = cost, url

    if best_url is None:
        # 全都不通：退回清华（至少配置是对的，让它自己报错）
        best_url = PIP_MIRRORS[0][1]
        if progress:
            try:
                progress('所有镜像都连不上，退回清华源')
            except Exception:
                pass
    else:
        if progress:
            try:
                n = next(nm for nm, u in PIP_MIRRORS if u == best_url)
                progress('选用镜像：%s（%.2f 秒）' % (n, best_cost))
            except Exception:
                pass
    try:
        _MIRROR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _MIRROR_CACHE.write_text(best_url, encoding='utf-8')
    except Exception:
        pass
    return best_url


def install_pip(progress=None) -> bool:
    """装 pip，再批量装依赖。"""
    if not has_python():
        return False
    py = str(python_exe())
    lib = RUNTIME / 'python' / 'Lib' / 'site-packages'
    lib.mkdir(parents=True, exist_ok=True)

    # get-pip.py：优先随包内置（2.3 MB），没有再走网络
    get_pip = RUNTIME / '_get-pip.py'
    builtin = builtin_file('get-pip.py')
    if builtin:
        shutil.copy2(builtin, get_pip)
    elif not download(MIRRORS['get_pip'], get_pip, 'pip 引导脚本', progress=progress):
        return False
    if progress:
        progress('pip：安装中（约 10 秒）…')
    # 2026-11-07：改流式，避免用户以为卡死
    rc, out = run_stream([py, str(get_pip), '--no-warn-script-location',
                          '-i', PIP_MIRROR],
                         label='pip ', progress=progress, idle_timeout=300)
    get_pip.unlink(missing_ok=True)
    if rc != 0:
        log('  pip 安装失败（退出码 %d）：' % rc)
        for line in out.splitlines()[-8:]:
            log('    ' + line[:200])
        return False

    # 依赖
    if progress:
        progress('依赖包：开始安装 %d 个（首次约 250 MB，逐个下载会持续显示）'
                 % len(PIP_PACKAGES))
    # 2026-11-07 复盘「卡在 PIP 一直不动」：
    #   总下载约 250 MB（opencv 38 / av 30 / numpy 16 MB）。
    #   原 --timeout 60 --retries 3 —— 单包最多耗 4 分钟且全程无输出，
    #   12 个包能拖 40+ 分钟，界面看起来就是死机。
    #   现在单包超时 120 秒、重试 5 次，并由 run_stream 每 20 秒发心跳。
    # 2026-11-07 关键修复：**去掉 --extra-index-url https://pypi.org/simple**
    #   pip 会向所有 index 并行探测，国内访问 pypi.org 常十几秒才超时。
    #   开发机有 HTTP_PROXY 所以感觉不到，客户机没代理就整个卡住
    #   （主人实测：界面停在「依赖 下载 。」不动）。
    #   清华源本身就是 PyPI 的完整镜像，根本不需要备用源。
    mirror = pick_mirror(progress=progress)
    cmd = [py, '-m', 'pip', 'install', '--no-warn-script-location',
           '--disable-pip-version-check', '-i', mirror,
           '--retries', '5', '--timeout', '120',
           '--progress-bar', 'off'] + PIP_PACKAGES
    # 2026-11-07 关键改动：
    #   · 原来 subprocess.run(capture_output=True, timeout=3600) —— 250 MB
    #     下载期间零输出，客户机表现为「卡在 PIP 一直不动」。
    #   · 现在流式读取，每下载一个包就刷一行；判卡死用**空闲超时**
    #     （15 分钟没有任何输出才放弃），慢速网络不会误杀。
    rc, out = run_stream(cmd, label='依赖 ', progress=progress,
                         idle_timeout=900)
    if rc != 0:
        lines = [l for l in out.splitlines() if l.strip()]
        errs = [l for l in lines if 'ERROR' in l.upper()]
        log('  依赖安装失败（退出码 %d）' % rc)
        for line in (errs or lines)[-12:]:
            log('    ' + line[:220])
        return False

    # 再逐个 import 验证一次，把缺的包名精确报出来
    missing = pip_missing()
    if missing:
        log('  依赖装完了，但以下模块仍无法导入：')
        for pkg, mod in missing:
            log('    %-20s (import %s)' % (pkg, mod))
        return False
    return True


def install_node(progress=None) -> bool:
    archive = RUNTIME / '_node.zip'
    if not download(MIRRORS['node'], archive, 'Node.js 运行时', progress=progress):
        return False
    target = RUNTIME / 'node'
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    # 压缩包里有顶层 node-vX-win-x64/，要去掉
    tmp = RUNTIME / '_node_tmp'
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    if not unzip(archive, tmp):
        return False
    inner = next((p for p in tmp.iterdir() if p.is_dir()), None)
    if inner:
        shutil.move(str(inner), str(target))
        shutil.rmtree(tmp, ignore_errors=True)
    archive.unlink(missing_ok=True)
    return node_exe().exists()


def install_mobile_mcp(progress=None) -> bool:
    """用 npm 装 mobile-mcp，并写入 start-mobile-mcp.cjs 入口。"""
    if not has_node():
        return False
    target = RUNTIME / 'mobile-mcp'
    target.mkdir(parents=True, exist_ok=True)

    npm = RUNTIME / 'node' / 'npm.cmd'
    if not npm.exists():
        log('  缺少 npm.cmd')
        return False

    if progress:
        progress('mobile-mcp：npm 安装中（约 70 MB，会显示下载进度）…')
    cmd = [str(npm), 'install', '--no-audit', '--no-fund',
           '--loglevel', 'http',
           '--registry', NPM_MIRROR] + NPM_PACKAGES
    # 2026-11-07：npm 也可能长时间无输出（同样让用户以为卡死），改流式。
    # --loglevel http 能让 npm 把每个 tarball 的下载动作打出来。
    rc, out = run_stream(cmd, label='npm ', progress=progress,
                         idle_timeout=900, cwd=str(target))
    if rc != 0:
        log('  npm 安装失败（退出码 %d）：' % rc)
        for line in out.splitlines()[-10:]:
            log('    ' + line[:200])
        return False

    # 入口脚本（与开发机一致：设置 MOBILECLI_PATH + 动态找 adb）
    entry = target / 'start-mobile-mcp.cjs'
    entry.write_text("""'use strict';
const path = require('node:path');
const fs = require('node:fs');

process.env.MOBILECLI_PATH = path.join(__dirname, 'node_modules', '@mobilenext', 'mobilecli-windows-amd64', 'mobilecli-windows-amd64.exe');
process.env.MOBILEMCP_DISABLE_TELEMETRY = '1';

// adb 目录：ADB_PATH → 程序 bin/ → 本目录 bin/ → 旧 huicheng 目录
const CANDIDATES = [];
if (process.env.ADB_PATH) CANDIDATES.push(path.dirname(process.env.ADB_PATH));
CANDIDATES.push(path.join(__dirname, '..', '..', 'bin'));
CANDIDATES.push(path.join(__dirname, 'bin'));
CANDIDATES.push('C:\\\\Program Files (x86)\\\\huicheng_android\\\\tools');
const ADB = process.platform === 'win32' ? 'adb.exe' : 'adb';
const dir = CANDIDATES.find(function (d) {
  try { return fs.existsSync(path.join(d, ADB)); } catch (e) { return false; }
});
if (dir) process.env.PATH = dir + path.delimiter + (process.env.PATH || '');

require('./node_modules/@mobilenext/mobile-mcp/lib/index.js');
""", encoding='utf-8')
    return has_mobile_mcp()


def install_adb(progress=None) -> bool:
    """装 adb。**优先用随包内置**（只有 5.7 MB，不依赖网络最稳）。"""
    BIN.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in BUILTIN['adb']:
        src = builtin_file(name)
        if src:
            try:
                shutil.copy2(src, BIN / name)
                copied += 1
            except OSError:
                pass
    if copied and (BIN / 'adb.exe').exists():
        if progress:
            progress('ADB：使用随包内置版本（%d 个文件）' % copied)
        return has_adb()

    # 内置没有才走网络
    archive = RUNTIME / '_adb.zip'
    if not download(MIRRORS['adb'], archive, 'ADB 工具', progress=progress):
        return False
    tmp = RUNTIME / '_adb_tmp'
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    if not unzip(archive, tmp):
        return False
    src = tmp / 'platform-tools'
    if not src.exists():
        inner = next((p for p in tmp.iterdir() if p.is_dir()), None)
        src = inner if inner else tmp
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, BIN / f.name)
    shutil.rmtree(tmp, ignore_errors=True)
    archive.unlink(missing_ok=True)
    return has_adb()


INSTALLERS = {
    'python': install_python,
    'pip': install_pip,
    'node': install_node,
    'mobile_mcp': install_mobile_mcp,
    'adb': install_adb,
}


# ---------------------------------------------------------------- 主流程

def check_path_length(base: Path | None = None, progress=None) -> int:
    r"""检查安装路径长度，太长的直接拦下来。

    2026-11-07 客户机实测（主人截图）：
        程序被放在 D:\xwechat_files\wxid_xxx\汇城AI安装引导\（微信接收文件夹），
        路径本身就有 70+ 字符。再往下 
untime\python\Lib\site-packages\numpy\core\...
        轻松超过 Windows 的 260 字符上限，pip 解压 numpy/opencv 时会失败，
        而且报错信息在旧版里被吞掉，用户只看到「pip 与依赖包 失败」。

    返回超出的字符数（0 表示没问题）。超过 200 就建议换目录。
    """
    b = base or BASE
    # 最深的典型路径：runtime\python\Lib\site-packages\<包>\<子模块>\<文件名>
    deepest = len(str(b)) + len(r'\runtime\python\Lib\site-packages' + r'\opencv_python.libs' + r'\abcdefghijklmnop.pyd')
    msg = ('安装目录：%s\n预计最深路径约 %d 字符（Windows 上限 260）'
           % (b, deepest))
    if deepest > 240:
        if progress:
            progress('【路径太长】' + msg)
            progress('请把整个文件夹移到更短的路径，例如 D:\\AI\\，然后重新运行。')
        else:
            log('【路径太长】' + msg)
            log('请把整个文件夹移到更短的路径，例如 D:\\AI\\，然后重新运行。')
    return max(0, deepest - 240)


def run(repair: bool = False, progress=None) -> int:
    say = progress or log
    RUNTIME.mkdir(parents=True, exist_ok=True)
    BIN.mkdir(parents=True, exist_ok=True)

    # 2026-11-07：装之前先检查路径长度（微信/QQ 接收文件夹特别容易超）
    _over = check_path_length(BASE, progress=say)
    if _over > 0:
        say('')
        say('=== 已中止：安装路径过长 ===')
        say('Windows 单文件路径上限 260 字符，超了 pip 解压会失败。')
        say('请把「汇城AI安装引导」整个文件夹移到 D:\\AI\\ 之类的短路径后再运行。')
        return 2

    status = check_all()
    todo = [k for k, _, _ in COMPONENTS if not status.get(k)]
    if repair:
        todo = [k for k, _, _ in COMPONENTS]

    if not todo:
        say('环境已就绪，无需安装')
        return 0

    say('需要安装：%s' % '、'.join(dict((k, n) for k, n, _ in COMPONENTS)[k]
                                   for k in todo))
    state = load_state()
    for key in todo:
        name = dict((k, n) for k, n, _ in COMPONENTS)[key]
        say('=== 安装 %s ===' % name)
        try:
            ok = INSTALLERS[key](progress=lambda m: say('  ' + m))
        except Exception as exc:                       # noqa: BLE001
            say('  ✗ %s 安装异常：%s: %s' % (name, type(exc).__name__, exc))
            ok = False
        state[key] = {'ok': bool(ok), 'at': time.time()}
        save_state(state)
        if not ok:
            say('✗ %s 安装失败；其余组件继续，稍后可重跑本程序补装' % name)
        else:
            say('✓ %s 完成' % name)

    final = check_all()
    # seedance 随主程序包发布，不由引导器安装，只提示不拦截。
    missing = [k for k, v in final.items() if not v and k != 'seedance']
    if not final.get('seedance'):
        say('提示：视频生成 MCP 未随包（旧主程序包），更新主程序后可用；不影响三链')
    if missing:
        say('仍有未完成项：%s' % '、'.join(missing))
        return 1
    say('全部就绪')
    return 0


def main() -> int:
    args = set(sys.argv[1:])
    if '--check' in args:
        st = check_all()
        log('环境检查：')
        for key, name, size in COMPONENTS:
            log('  %-12s %s' % (name, '✓ 已安装' if st.get(key) else '✗ 缺失'))
        log('  %-12s %s' % ('视频生成MCP', '✓ 随包' if st.get('seedance') else '- 未随包（旧主程序包，不影响三链）'))
        log('  程序根目录：%s' % BASE)
        return 0
    if '--repair' in args:
        return run(repair=True)
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
