r"""统一日志：把「谁在什么时候做了什么、错在哪」完整记下来。

2026-11-06 主人要求：
    程序要发给不特定的电脑用，那边一定会有 bug。
    出问题时必须能查清楚**对方操作了什么**，而不是只拿到一句「打不开」。

设计：
    1. **文件日志**（滚动，单文件 5MB × 5 份），放 `%LOCALAPPDATA%\FishFleet\logs\`
       —— 不放在程序目录，避免 Program Files 无写权限。
    2. **错误单独一份** `error.log`，排查时只需要看这一个。
    3. **请求日志**：记录每个 API 调用（方法/路径/状态/耗时/客户端），
       这样能还原用户点了什么。
    4. **操作日志**：前端可以主动上报「我点了发布按钮」这类语义事件。
    5. **启动快照**：版本、路径、Python、系统、进程 CPU/内存，一并写入。
    6. **绝不因为日志失败影响主流程**（全部 try/except 包裹）。

用法：
    from fishfleet.logsetup import setup, get_logger, log_event
    setup()                       # 进程启动时调一次
    log = get_logger(__name__)
    log.info('...')
    log_event('publish.click', {'serial': 'xxx'})   # 用户操作事件
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import sys
import time
import traceback
from pathlib import Path

APP_NAME = 'FishFleet'
APP_TITLE = '汇城群控AI版'

_initialized = False
_log_dir: Path | None = None
_event_logger: logging.Logger | None = None


def log_dir() -> Path:
    """日志目录。

    2026-11-06 改：**便携版优先写程序目录** `data/logs/`。

    原实现优先 `%LOCALAPPDATA%\\FishFleet\\logs`，结果客户在安装文件夹里
    找不到 `app.log`（主人实测反馈「文件夹里没有 app.log」）。
    现在的顺序：
        1. 程序目录 / data / logs   （便携版，最直观，跟随程序走）
        2. %LOCALAPPDATA% / FishFleet / logs  （程序在 Program Files 无写权限时）
        3. 当前工作目录 / logs
    """
    global _log_dir
    if _log_dir is not None:
        return _log_dir
    cands: list[Path] = []
    # 1) 程序目录（便携）
    try:
        from fishfleet.paths import app_base
        cands.append(app_base() / 'data' / 'logs')
    except Exception:
        pass
    # 2) 用户目录
    la = os.environ.get('LOCALAPPDATA')
    if la:
        cands.append(Path(la) / APP_NAME / 'logs')
    # 3) 兜底
    cands.append(Path.cwd() / 'logs')

    for c in cands:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / '.write_test'
            probe.write_text('1', encoding='utf-8')
            probe.unlink(missing_ok=True)
            _log_dir = c
            return c
        except Exception:
            continue
    _log_dir = Path('.')
    return _log_dir


def _fmt() -> logging.Formatter:
    return logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )


def setup(level: int = logging.INFO, console: bool = True) -> None:
    """初始化日志。重复调用安全。"""
    global _initialized, _event_logger
    if _initialized:
        return
    _initialized = True

    d = log_dir()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 主日志：滚动 5MB × 5
    try:
        fh = logging.handlers.RotatingFileHandler(
            d / 'app.log', maxBytes=5 * 1024 * 1024, backupCount=5,
            encoding='utf-8', errors='replace')
        fh.setLevel(level)
        fh.setFormatter(_fmt())
        root.addHandler(fh)
    except Exception:
        pass

    # 错误单独一份：排查只看这个
    try:
        eh = logging.handlers.RotatingFileHandler(
            d / 'error.log', maxBytes=2 * 1024 * 1024, backupCount=3,
            encoding='utf-8', errors='replace')
        eh.setLevel(logging.WARNING)
        eh.setFormatter(_fmt())
        root.addHandler(eh)
    except Exception:
        pass

    # 控制台（打包成 GUI 时 stderr 可能不可用，忽略即可）
    if console:
        try:
            sh = logging.StreamHandler(stream=sys.stderr)
            sh.setLevel(level)
            sh.setFormatter(_fmt())
            root.addHandler(sh)
        except Exception:
            pass

    # 操作事件专用（JSON 行，方便程序解析）
    try:
        ev = logging.getLogger('fishfleet.event')
        ev.setLevel(logging.INFO)
        ev.propagate = False
        eh2 = logging.handlers.RotatingFileHandler(
            d / 'events.jsonl', maxBytes=4 * 1024 * 1024, backupCount=4,
            encoding='utf-8', errors='replace')
        eh2.setFormatter(logging.Formatter('%(message)s'))
        ev.addHandler(eh2)
        _event_logger = ev
    except Exception:
        pass

    # 未捕获异常兜底
    def _hook(exc_type, exc, tb):
        try:
            get_logger('uncaught').critical(
                '未捕获异常: %s', ''.join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    try:
        sys.excepthook = _hook
    except Exception:
        pass

    # 启动快照
    try:
        log_event('app.start', snapshot())
    except Exception:
        pass


def get_logger(name: str = 'fishfleet') -> logging.Logger:
    """取 logger；没初始化就顺手初始化（避免漏日志）。"""
    if not _initialized:
        try:
            setup()
        except Exception:
            pass
    return logging.getLogger(name)


def log_event(name: str, payload: dict | None = None) -> None:
    """记录一次用户/系统操作事件（JSON 行）。"""
    try:
        rec = {
            'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
            'ts_epoch': round(time.time(), 3),
            'event': name,
            'pid': os.getpid(),
        }
        if payload:
            rec.update(payload)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        if _event_logger is not None:
            _event_logger.info(line)
        else:
            logging.getLogger('fishfleet.event').info(line)
    except Exception:
        pass


def snapshot() -> dict:
    """运行环境快照：出 bug 时这几行往往就能定位问题。"""
    info: dict = {
        'app': APP_TITLE,
        'python': sys.version.split()[0],
        'executable': sys.executable,
        'frozen': bool(getattr(sys, 'frozen', False)),
        'cwd': os.getcwd(),
        'os': f'{platform.system()} {platform.release()} ({platform.version()})',
        'machine': platform.machine(),
        'log_dir': str(log_dir()),
    }
    try:
        from fishfleet.paths import describe
        info['paths'] = describe()
    except Exception:
        pass
    try:
        import psutil  # type: ignore
        info['mem_mb'] = round(psutil.Process().memory_info().rss / 1048576, 1)
    except Exception:
        pass
    return info


def tail(filename: str = 'error.log', lines: int = 200) -> str:
    """读日志尾部（给「导出诊断」功能用）。"""
    try:
        p = log_dir() / filename
        if not p.exists():
            return ''
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            data = f.readlines()
        return ''.join(data[-lines:])
    except Exception:
        return ''


def export_bundle(dest: Path | None = None) -> Path | None:
    """把诊断信息打包成一个文本文件，方便用户发回来。"""
    try:
        import zipfile
        d = log_dir()
        out = dest or (Path(os.environ.get('USERPROFILE', '.')) / 'Desktop'
                       / ('汇城群控诊断_%s.zip' % time.strftime('%Y%m%d_%H%M%S')))
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for name in ('app.log', 'error.log', 'events.jsonl',
                         'app.log.1', 'error.log.1'):
                p = d / name
                if p.exists():
                    z.write(p, name)
            z.writestr('snapshot.json',
                       json.dumps(snapshot(), ensure_ascii=False, indent=2))
        return out
    except Exception:
        return None
