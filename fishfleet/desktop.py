"""
FishFleet 桌面版启动器

把 Web 版包装成本地窗口程序（不需要 Chrome）：
  - 内嵌 WebView2（Windows 系统自带，和汇城的 Tauri 同一个内核）
  - 后台跑 uvicorn
  - 无地址栏、无标签页，就是个原生窗口

用法:
    python -m fishfleet.desktop
    或双击 启动群控.bat
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
from pathlib import Path


HOST = "127.0.0.1"

# 调试端口：默认开启本地 CDP（仅监听本机），可用 FISHFLEET_DEBUG=0 关闭。
DEBUG = os.environ.get("FISHFLEET_DEBUG", "1").strip() != "0"
DEBUG_PORT = os.environ.get("FISHFLEET_DEBUG_PORT", "9223").strip()


def _free_port(start: int = 8765) -> int:
    """找一个可用端口。"""
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, p))
                return p
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


class ServerThread(threading.Thread):
    """后台跑 uvicorn。"""

    def __init__(self, port: int) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.error: str | None = None

    def run(self) -> None:
        try:
            import uvicorn

            from fishfleet.web.server import app

            # 2026-11-06 关键修复（客户机「后端启动失败」的最终原因）：
            #   PyInstaller 用 console=False 打包时，sys.stdout / sys.stderr 是 None。
            #   uvicorn 的日志格式化器（uvicorn/logging.py:42）会调
            #       self.use_colors = sys.stdout.isatty()
            #   于是直接 `AttributeError: 'NoneType' object has no attribute 'isatty'`，
            #   最终表现为 `ValueError: Unable to configure formatter 'default'`，
            #   整个 uvicorn.run 抛异常 → 后端永远起不来。
            #   开发态（有控制台）永远复现不了，只在打包后的 GUI 程序里出现。
            #
            # 双保险：
            #   1. 把 None 的 stdout/stderr 换成安全 writer（带 isatty/fileno）；
            #   2. 给 uvicorn 传 log_config=None，让它不去装自定义 formatter。
            _ensure_stdio()
            uvicorn.run(
                app,
                host=HOST,
                port=self.port,
                log_level="warning",
                access_log=False,
                log_config=None,          # 见上：避免 configure_formatter 崩
            )
        except Exception:  # noqa: BLE001
            self.error = traceback.format_exc()


class _SafeWriter:
    """替代 stdout/stderr 的安全 writer：GUI 程序里它们是 None。

    只需要满足 logging / uvicorn 会用到的那几个方法。
    """

    def __init__(self, path: "Path | None" = None) -> None:
        self._f = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._f = open(path, "a", encoding="utf-8", errors="replace",
                               buffering=1)
            except Exception:
                self._f = None

    def write(self, s):
        if self._f is not None:
            try:
                self._f.write(s)
                return len(s)
            except Exception:
                return 0
        return len(s) if s else 0

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if self._f is not None:
            try:
                self._f.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        if self._f is not None:
            try:
                return self._f.fileno()
            except Exception:
                pass
        raise OSError("fileno 不可用")

    def close(self):
        if self._f is not None:
            try:
                self._f.close()
            except Exception:
                pass

    @property
    def encoding(self) -> str:
        return "utf-8"


def _ensure_stdio() -> None:
    """保证 sys.stdout / sys.stderr 不是 None（打包成 GUI 程序时就是 None）。"""
    try:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
            else Path(__file__).resolve().parents[1]
        logdir = base / "data" / "logs"
    except Exception:
        logdir = Path(".")
    if sys.stdout is None:
        sys.stdout = _SafeWriter(logdir / "stdout.log")
    if sys.stderr is None:
        sys.stderr = _SafeWriter(logdir / "stderr.log")


def _wait_ready(port: int, timeout: float = 60.0) -> bool:
    """等后端起来。

    注意：必须用轻量接口（/api/control/config）探活，
    不能用 /api/devices——冷启动时 adb 枚举 20 台手机很慢，
    单次 2 秒超时永远等不到，窗口会直接起不来（2026-11-04 实测）。
    """
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://{HOST}:{port}/api/control/config"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def _find_browser() -> str | None:
    """找可用的 Chromium 内核浏览器（Edge 优先，其次 Chrome）。

    2026-11-06 为什么改用它：
        pywebview 在 Windows 上硬性依赖 pythonnet（winforms 后端），
        而 pythonnet 的 `Python.Runtime.dll` 在 PyInstaller 打包后
        无法被 .NET 正确解析（clr_loader.netfx 报
        `Failed to resolve Python.Runtime.Loader.Initialize`），
        试过 collect_all、复制 DLL 到根目录都不稳。

        改用 Chromium 的 `--app` 模式：窗口无地址栏/无标签页，
        观感与 pywebview 基本一致，但**零 Python 侧依赖**，
        Win10/11 自带 Edge 就能跑，打包也少 100+ MB。
    """
    import os as _os

    def _valid(p: str | None) -> str | None:
        r"""只认真正的 Edge / Chrome 主程序。

        踩坑 1：`shutil.which('chrome')` 在本机返回了
        `...\Google\Chrome\Bin\chromex.exe`（那是别的 Electron 外壳，
        不是浏览器），拿它 --app 会开不起来。所以按文件名严格校验。

        踩坑 2：新版 Edge 装在 `Program Files (x86)\Microsoft\EdgeCore\<版本>\msedge.exe`，
        不在常规的 `Edge\Application\msedge.exe`。本机实测只有 EdgeCore 有，
        所以必须把这个目录也纳入探测，否则会误判「没装浏览器」。
        """
        if not p or not _os.path.exists(p):
            return None
        base = _os.path.basename(p).lower()
        if base in ("msedge.exe", "chrome.exe"):
            return p
        return None

    def _ver_key(name: str) -> list[int]:
        """把 '156.0.4314.58' 变成可比较的数字列表。"""
        out = []
        for part in name.replace('-', '.').split('.'):
            out.append(int(part) if part.isdigit() else 0)
        return out

    cands = [
        _os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        _os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        _os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
        _os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        _os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        _os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in cands:
        got = _valid(c)
        if got:
            return got

    # 新版 Edge：EdgeCore\<版本>\msedge.exe（取版本号最大的）
    for base in (r"%ProgramFiles(x86)%\Microsoft\EdgeCore",
                 r"%ProgramFiles%\Microsoft\EdgeCore",
                 r"%LOCALAPPDATA%\Microsoft\EdgeCore"):
        root = _os.path.expandvars(base)
        if not _os.path.isdir(root):
            continue
        try:
            subs = [d for d in _os.listdir(root)
                    if _os.path.isdir(_os.path.join(root, d))]
        except OSError:
            continue
        subs.sort(key=_ver_key, reverse=True)
        for s in subs:
            got = _valid(_os.path.join(root, s, "msedge.exe"))
            if got:
                return got

    # 再试 PATH 和注册表 App Paths
    import shutil as _sh
    for name in ("msedge", "chrome"):
        got = _valid(_sh.which(name))
        if got:
            return got
    try:
        import winreg  # type: ignore
        for exe in ("msedge.exe", "chrome.exe"):
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    key = winreg.OpenKey(
                        hive,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe)
                    with key:
                        val, _ = winreg.QueryValueEx(key, "")
                        got = _valid(val)
                        if got:
                            return got
                except OSError:
                    continue
    except Exception:
        pass
    return None


def _open_app_window(url: str, profile_dir: str) -> bool:
    """用浏览器以「应用窗口」方式打开 URL。返回是否成功拉起。"""
    import subprocess as _sp

    exe = _find_browser()
    if not exe:
        return False
    args = [
        exe,
        "--app=" + url,
        "--window-size=1600,950",
        "--window-position=80,60",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,BackForwardCache",
        "--user-data-dir=" + profile_dir,
    ]
    try:
        _sp.Popen(args, close_fds=True)
        print(f"[FishFleet] 已用 {os.path.basename(exe)} 打开窗口")
        return True
    except Exception as exc:            # noqa: BLE001
        print(f"[FishFleet] 打开浏览器失败：{exc}", file=sys.stderr)
        return False


def main() -> int:
    # 2026-11-06 关键修复：隐藏子进程控制台窗口。
    # 之前 desktop.py 没装这个 patches，导致每启动一个 node（MCP 通道）/ adb
    # 就弹一个黑色控制台窗口；预热 8 个通道 = 8 个黑窗，
    # 主人实测「疯狂出弹窗」。standalone.py 里本来就有，这里对齐。
    try:
        from fishfleet.standalone import configure_hidden_children
        configure_hidden_children()
    except Exception:
        # 兜底：自己装一遍（不能因为导入失败就放任黑窗）
        if os.name == 'nt':
            try:
                import subprocess as _sp

                if not getattr(_sp.Popen, '_fishfleet_hidden', False):
                    _orig = _sp.Popen

                    class _Hidden(_orig):
                        _fishfleet_hidden = True

                        def __init__(self, *a, **kw):
                            fl = kw.get('creationflags', 0)
                            if not fl & (_sp.CREATE_NEW_CONSOLE | _sp.DETACHED_PROCESS):
                                kw['creationflags'] = fl | _sp.CREATE_NO_WINDOW
                            super().__init__(*a, **kw)

                    _sp.Popen = _Hidden
            except Exception:
                pass

    # 关掉 pywebview 的控制台输出噪音（仍保留：万一有人用老入口）
    os.environ.setdefault("PYWEBVIEW_LOG", "warning")

    # 2026-11-06：**必须最先做**。GUI 程序（console=False）里
    # sys.stdout / sys.stderr 都是 None，而 logsetup 的 StreamHandler、
    # uvicorn 的日志格式化器都会直接访问它们（isatty/write），不先补上就会崩。
    _ensure_stdio()

    # 2026-11-06：统一日志。放在所有子进程启动之前，
    # 这样后面任何异常都有记录可查（主人要求：出 bug 要知道对方做了什么）。
    try:
        from fishfleet.logsetup import setup as _log_setup, get_logger
        _log_setup()
        _log = get_logger('desktop')
        _log.info('启动 %s', '汇城群控AI版')
        from fishfleet.logsetup import snapshot as _snap
        _log.info('环境快照: %s', _snap())
    except Exception:
        pass

    port = _free_port()
    print(f"[FishFleet] 后端端口 {port}")

    srv = ServerThread(port)
    srv.start()

    print("[FishFleet] 等待后端就绪…")
    if not _wait_ready(port):
        print("[FishFleet] 后端启动失败", file=sys.stderr)
        if srv.error:
            print(srv.error, file=sys.stderr)

        # 2026-11-06 关键修复：
        #   原来这里的错误只 print 到 stderr，而打包后是 GUI 程序（console=False），
        #   stderr 无处可去 —— 用户只能看到「启动失败」四个字，日志里啥也没有。
        #   主人实测：新电脑报「后端启动失败」，弹窗指向 desktop.log，
        #   可那个文件是 standalone.py 留下的旧内容，跟本次失败无关。
        # 现在把**真实 traceback 写进日志**，并在弹窗里直接显示摘要。
        err_text = (srv.error or "").strip()
        log_paths: list[str] = []
        try:
            from fishfleet.logsetup import get_logger, log_dir, log_event
            _lg = get_logger('desktop')
            _lg.error('后端启动失败（端口 %d）\n%s', port,
                      err_text or '(无 traceback)')
            log_event('backend.start_failed',
                      {'port': port, 'error': err_text[:4000]})
            log_paths.append(str(log_dir() / 'error.log'))
        except Exception:
            pass
        # 兜底：直接写一份到程序目录，保证一定有文件可查
        try:
            base = Path(sys.executable).parent if getattr(sys, 'frozen', False) \
                else Path(__file__).resolve().parents[1]
            p = base / '启动失败.txt'
            p.write_text(
                '汇城群控AI版 · 后端启动失败\n'
                '时间: %s\n端口: %d\n\n%s\n'
                % (time.strftime('%Y-%m-%d %H:%M:%S'), port,
                   err_text or '(无 traceback)'),
                encoding='utf-8')
            log_paths.insert(0, str(p))
        except Exception:
            pass

        # 从 traceback 里挑最有价值的一行（最后的异常信息）
        brief = ''
        for line in reversed(err_text.splitlines()):
            if line.strip() and not line.startswith(' '):
                brief = line.strip()
                break

        if os.environ.get("FISHFLEET_NO_POPUP", "").strip() != "1":
            try:
                import ctypes as _ct
                title = "汇城群控AI版"
                text = "后端启动失败。\n\n"
                if brief:
                    text += "错误：%s\n\n" % brief[:300]
                text += "详细日志：\n" + "\n".join(log_paths[:2])
                # 自动关闭：起个后台线程按回车（等价点「确定」）
                try:
                    _secs = float(os.environ.get("FISHFLEET_AUTOCLOSE", "0") or 0)
                except ValueError:
                    _secs = 0.0
                if _secs > 0:
                    import threading as _th

                    def _auto_ok() -> None:
                        import time as _t
                        _t.sleep(_secs)
                        try:
                            _ct.windll.user32.PostMessageW(
                                _ct.windll.user32.FindWindowW(None, title),
                                0x0100, 0x0D, 0)      # WM_KEYDOWN / VK_RETURN
                            _ct.windll.user32.PostMessageW(
                                _ct.windll.user32.FindWindowW(None, title),
                                0x0101, 0x0D, 0)      # WM_KEYUP
                        except Exception:
                            pass

                    _th.Thread(target=_auto_ok, daemon=True).start()
                _ct.windll.user32.MessageBoxW(None, text, title, 0x10)
            except Exception:
                pass
        return 1
    print("[FishFleet] 后端就绪，打开窗口")

    url = f"http://{HOST}:{port}/"

    # 无窗口模式：只跑服务，不开浏览器窗口。
    # 用途：自动化测试（避免反复弹窗骚扰用户）、服务器/无桌面环境部署。
    if os.environ.get("FISHFLEET_HEADLESS", "").strip() == "1":
        print(f"[FishFleet] 无窗口模式，服务在 {url}")
        try:
            while True:
                time.sleep(1)
                if srv.error:
                    print(srv.error, file=sys.stderr)
                    break
        except KeyboardInterrupt:
            pass
        return 0

    # 2026-11-06：改用 Chromium 的 --app 应用窗口（无地址栏、无标签页）。
    # 原因见 _find_browser() 的注释：pywebview → pythonnet 在打包环境里不可靠。
    profile = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "FishFleet", "webprofile",
    )
    os.makedirs(profile, exist_ok=True)

    if _open_app_window(url, profile):
        print("[FishFleet] 窗口已打开（关闭窗口后请在本程序按 Ctrl+C 或点关闭退出）")
        # 保持后端存活：浏览器窗口是独立进程，主进程在这里守到被杀。
        try:
            while True:
                time.sleep(1)
                if srv.error:
                    print(srv.error, file=sys.stderr)
                    break
        except KeyboardInterrupt:
            pass
        return 0

    # 兜底：没找到 Edge/Chrome，退回默认浏览器
    print("[FishFleet] 未找到 Edge/Chrome，改用默认浏览器", file=sys.stderr)
    try:
        import webbrowser
        webbrowser.open(url)
        print("[FishFleet] 已在默认浏览器打开；按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0
    except Exception as exc:            # noqa: BLE001
        print(f"[FishFleet] 打开失败：{exc}", file=sys.stderr)
        return 1


def _diagnose_to_file() -> Path:
    r"""把环境诊断写成一个文本文件，放在 exe 旁边和桌面各一份。

    2026-11-06 为什么需要这个：
        主人反馈「新电脑装完，双击 exe 完全没反应」。GUI 程序（console=False）
        没有控制台，PyInstaller 的 bootloader 阶段如果失败（_internal 缺失、
        被杀软拦截、编码问题），`main()` 根本不会执行，连日志都写不出来，
        用户只能看到「点了没反应」。

        所以提供一个 `--diag` 入口：**不启动服务**，只把能查到的信息全写下来，
        包括 exe 真实路径（宽字符 API）、_internal 是否存在、runtime 是否装好、
        Python 版本、系统版本、环境变量里的可疑项、以及最近一次的错误日志。
    """
    import datetime as _dt

    lines: list[str] = []
    add = lines.append

    add('汇城群控AI版 · 环境诊断')
    add('生成时间: %s' % _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    add('=' * 60)

    # 1. 路径（这里最容易出中文问题）
    add('')
    add('[1] 程序路径')
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetModuleFileNameW(None, buf, 32768)
        add('  exe（宽字符API）: %s' % (buf.value[:n] if n > 0 else '(读取失败)'))
    except Exception as exc:                    # noqa: BLE001
        add('  exe（宽字符API）: 读取异常 %s' % exc)
    add('  sys.executable : %r' % sys.executable)
    add('  sys.argv       : %r' % (sys.argv,))
    add('  cwd            : %r' % os.getcwd())
    add('  frozen         : %s' % getattr(sys, 'frozen', False))
    try:
        from fishfleet.paths import app_base, describe
        add('  app_base()     : %s' % app_base())
        add('  paths.describe : %s' % describe())
    except Exception as exc:                    # noqa: BLE001
        add('  paths 模块异常  : %s: %s' % (type(exc).__name__, exc))

    # 2. 关键文件
    add('')
    add('[2] 关键文件检查')
    try:
        from fishfleet.paths import app_base
        base = app_base()
    except Exception:
        base = Path(sys.executable).parent
    checks = [
        ('_internal 目录', base / '_internal'),
        ('_internal/python312.dll', base / '_internal' / 'python312.dll'),
        ('_internal/base_library.zip', base / '_internal' / 'base_library.zip'),
        ('runtime/python/python.exe', base / 'runtime' / 'python' / 'python.exe'),
        ('runtime/node/node.exe', base / 'runtime' / 'node' / 'node.exe'),
        ('runtime/mobile-mcp/start-mobile-mcp.cjs',
         base / 'runtime' / 'mobile-mcp' / 'start-mobile-mcp.cjs'),
        ('bin/adb.exe', base / 'bin' / 'adb.exe'),
        ('data 目录', base / 'data'),
    ]
    for label, p in checks:
        try:
            add('  %-38s %s' % (label, 'OK  ' + str(p) if p.exists() else 'MISSING'))
        except Exception as exc:                # noqa: BLE001
            add('  %-38s 检查异常 %s' % (label, exc))

    # 3. 系统与 Python
    add('')
    add('[3] 系统与运行时')
    try:
        import platform as _pf
        add('  系统     : %s %s (%s)' % (_pf.system(), _pf.release(), _pf.version()))
        add('  机器     : %s' % _pf.machine())
        add('  用户名   : %s' % os.environ.get('USERNAME', '?'))
        add('  LOCALAPPDATA: %s' % os.environ.get('LOCALAPPDATA', '?'))
    except Exception as exc:                    # noqa: BLE001
        add('  系统信息读取异常: %s' % exc)
    add('  Python   : %s' % sys.version.replace('\n', ' '))
    try:
        import importlib.util as _iu
        for mod in ('fastapi', 'uvicorn', 'pydantic', 'PIL', 'numpy', 'av', 'cv2'):
            add('  %-10s: %s' % (mod, 'OK' if _iu.find_spec(mod) else 'MISSING'))
    except Exception as exc:                    # noqa: BLE001
        add('  依赖检查异常: %s' % exc)

    # 4. 浏览器（--app 开窗依赖它）
    add('')
    add('[4] 浏览器（用于开窗口）')
    try:
        b = _find_browser()
        add('  找到: %s' % (b or '✗ 没有 Edge / Chrome'))
    except Exception as exc:                    # noqa: BLE001
        add('  探测异常: %s' % exc)

    # 5. 最近日志
    add('')
    add('[5] 最近错误日志（error.log 尾部 60 行）')
    try:
        from fishfleet.logsetup import log_dir, tail
        add('  日志目录: %s' % log_dir())
        for line in (tail('error.log', 60) or '(空)').splitlines():
            add('  ' + line)
    except Exception as exc:                    # noqa: BLE001
        add('  读取日志异常: %s' % exc)

    add('')
    add('=' * 60)
    add('如果这里是 MISSING，说明环境没装全，请重新运行「汇城AI安装引导.exe」')
    add('如果 exe 路径显示正常但进程仍起不来，多半是杀毒软件拦截，请加入白名单')

    text = '\n'.join(lines)
    outs: list[Path] = []
    for dest in (Path(sys.executable).parent, Path(os.path.expanduser('~')) / 'Desktop'):
        try:
            dest.mkdir(parents=True, exist_ok=True)
            p = dest / '诊断结果.txt'
            p.write_text(text, encoding='utf-8')
            outs.append(p)
        except Exception:
            continue
    # 弹窗把最重要的几行直接显示出来（真正跑起来时才有）
    try:
        head = '\n'.join(lines[:40])
        if os.environ.get('FISHFLEET_NO_POPUP', '').strip() != '1':
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, head[:2000], '汇城群控AI版 · 诊断结果', 0x40)
    except Exception:
        pass
    return outs[0] if outs else Path('诊断结果.txt')


if __name__ == "__main__":
    # 2026-11-06：诊断入口必须在任何初始化之前处理。
    # 用法：`HuichengFleetAI.exe --diag`（或双击配套的 `诊断.bat`）
    if '--diag' in sys.argv or '--diagnose' in sys.argv:
        try:
            _p = _diagnose_to_file()
            print('诊断已写入: %s' % _p)
        except Exception:
            pass
        sys.exit(0)
    sys.exit(main())
