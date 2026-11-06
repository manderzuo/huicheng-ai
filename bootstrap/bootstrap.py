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
RUNTIME = BASE / 'runtime'
BIN = BASE / 'bin'
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
        progress('pip：安装中…')
    r = subprocess.run([py, str(get_pip), '--no-warn-script-location',
                        '-i', PIP_MIRROR],
                       capture_output=True, text=True, timeout=600)
    get_pip.unlink(missing_ok=True)
    if r.returncode != 0:
        log('  pip 安装失败：%s' % (r.stderr or r.stdout)[-300:])
        return False

    # 依赖
    if progress:
        progress('依赖包：开始安装 %d 个（首次约 250 MB，请耐心等）'
                 % len(PIP_PACKAGES))
    cmd = [py, '-m', 'pip', 'install', '--no-warn-script-location',
           '--disable-pip-version-check', '-i', PIP_MIRROR,
           '--extra-index-url', 'https://pypi.org/simple',
           '--retries', '3', '--timeout', '60'] + PIP_PACKAGES
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=3600)
    except subprocess.TimeoutExpired:
        log('  依赖安装超时（1 小时），请检查网络')
        return False
    if r.returncode != 0:
        # 2026-11-06：把 pip 的**最后几十行**打出来，并高亮 ERROR 行，
        # 原来只截 500 字符且不区分行，界面上一句「失败」看不出原因。
        out = ((r.stdout or '') + '\n' + (r.stderr or '')).strip()
        lines = [l for l in out.splitlines() if l.strip()]
        errs = [l for l in lines if 'ERROR' in l.upper()]
        log('  依赖安装失败（pip 返回码 %d）' % r.returncode)
        for l in (errs or lines)[-12:]:
            log('    ' + l[:220])
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
        progress('mobile-mcp：npm 安装中（约 70 MB）…')
    cmd = [str(npm), 'install', '--no-audit', '--no-fund',
           '--registry', NPM_MIRROR] + NPM_PACKAGES
    r = subprocess.run(cmd, cwd=str(target), capture_output=True,
                       text=True, timeout=1800, shell=False)
    if r.returncode != 0:
        log('  npm 安装失败：%s' % (r.stderr or r.stdout)[-400:])
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

def run(repair: bool = False, progress=None) -> int:
    say = progress or log
    RUNTIME.mkdir(parents=True, exist_ok=True)
    BIN.mkdir(parents=True, exist_ok=True)

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
    missing = [k for k, v in final.items() if not v]
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
        log('  程序根目录：%s' % BASE)
        return 0
    if '--repair' in args:
        return run(repair=True)
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
