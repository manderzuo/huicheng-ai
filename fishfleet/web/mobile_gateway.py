"""Mobile MCP 功能白名单、服务端风险网关与设备隔离通道池。

通道模型（诚实说明，不夸大）：
- 最多 5 个独立 MobileMCPAdapter 进程（与任务最大并行数一致）。
- 每个运行中的任务通过 device_session 独占租用一个槽位，同任务内所有
  MCP 调用走同一 adapter；任务结束立即归还。
- 同一线程已绑定某 serial 时，禁止再绑定另一台手机，防止串话；
  同 serial 嵌套进入复用同一绑定。
- 会话外的一次性调用（极少）同样从池中临时租用，不设无界全局共享锁。
- 调用失败一律抛 MobileCallError，不吞错、不自动重放写动作。
  outcome_unknown=True 表示请求可能已发出：调用方必须停下核验，
  禁止换通道重发（会造成重复执行）。
"""
from __future__ import annotations
import atexit
import math
import queue
import re
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from .mobile_mcp_adapter import MobileMCPAdapter

# 使用适配器自身的协议等待期限，不再把设备写动作放进可迟发的后台队列。
# 该期限不是操作系统进程启动/管道清理的端到端硬期限。
CALL_TIMEOUT = 30.0
ACQUIRE_TIMEOUT = 120.0
_DIAGNOSTICS = deque(maxlen=500)
_DIAGNOSTICS_LOCK = threading.Lock()

# ---------------------------------------------------------------- dump 自愈
# 实测故障链（2026-11-01）：uiautomator dump 返回空/"Killed" → MCP 报
# "未获取到 UI 层级"；每次失败还会留下一个 app_process 残留，越积越多形成
# 恶性循环；清理残留后即可恢复（必要时再重启抖音应用）。
# 这里只对**读操作** mobile_list_elements_on_screen 做一次自愈重试：
# 读操作幂等，重试安全；写操作绝不在此重试（可能已执行）。
ADB_CANDIDATES = (
    __import__('fishfleet.paths', fromlist=['adb_path']).adb_path(),
    'adb',
)


def _adb_binary():
    import os
    import shutil
    for candidate in ADB_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return ''


def heal_uiautomator(serial) -> bool:
    """清理 uiautomator 残留的 app_process。成功清掉返回 True。"""
    import subprocess
    adb = _adb_binary()
    if not adb or not serial:
        return False
    try:
        listing = subprocess.run([adb, '-s', serial, 'shell', 'ps', '-A'],
                                 capture_output=True, text=True, timeout=15)
        pids = []
        for line in (listing.stdout or '').splitlines():
            if 'app_process' in line:
                parts = line.split()
                if len(parts) > 1 and parts[1].isdigit():
                    pids.append(parts[1])
        if not pids:
            return False
        subprocess.run([adb, '-s', serial, 'shell', 'kill', '-9'] + pids,
                       capture_output=True, text=True, timeout=15)
        time.sleep(1.5)
        return True
    except Exception:
        return False


def diagnostics():
    """有界、无参数/正文/设备标识的调用计时快照，供受控诊断使用。"""
    with _DIAGNOSTICS_LOCK:
        return [dict(row) for row in _DIAGNOSTICS]

def _pool_size():
    """并行通道上限：默认 10（2026-11-04 主人指定），可用环境变量覆盖。
    原先是"最多5台"的硬上限——6 台手机时不该有 1 台永远闲置。"""
    import os
    try:
        n = int(os.environ.get('FISHFLEET_MAX_DEVICES', '') or 0)
    except ValueError:
        n = 0
    return n if n > 0 else 10


_POOL_SIZE = _pool_size()
# 批处理必须拆成独立动作审批，不暴露任意批处理以免跨越确认。
READ = {'mobile_get_foreground_app','mobile_get_screen_size','mobile_get_orientation','mobile_list_elements_on_screen','mobile_list_apps'}
WRITE = {'mobile_click_on_screen_at_coordinates','mobile_swipe_on_screen','mobile_type_keys','mobile_press_button','mobile_launch_app','mobile_terminate_app','mobile_install_app','mobile_uninstall_app','mobile_clear_app_data','mobile_clipboard','mobile_set_location','mobile_open_url'}
FIELDS = {
 'mobile_list_elements_on_screen': {'format'},
 'mobile_click_on_screen_at_coordinates': {'x','y','ref'},
 'mobile_swipe_on_screen': {'direction','x','y','distance'},
 'mobile_type_keys': {'text','submit'},
 'mobile_press_button': {'button'},
 'mobile_launch_app': {'packageName'}, 'mobile_terminate_app': {'packageName'},
 'mobile_uninstall_app': {'packageName'}, 'mobile_clear_app_data': {'packageName'},
 'mobile_install_app': {'path'}, 'mobile_clipboard': {'text','read'},
 'mobile_open_url': {'url'},
 'mobile_set_location': {'latitude','longitude'},
}
# 仅“请求未发出”类的失败才允许旧通道兜底；凡是请求已发出
# （outcome_unknown）或服务端已响应的（rpc_error / tool_error），一律停下。
# 仅能证明“请求未开始写入”的失败才允许旧通道兜底。
# slot_timeout = 独占锁都没拿到；其余一律停下核验。
PRE_SEND_ERRORS = frozenset({'slot_timeout', 'start_failed',
                             'unsupported_protocol'})


class MobileCallError(RuntimeError):
    """MCP 调用失败。error 为拨测码，outcome_unknown 表示请求可能已发出。"""
    def __init__(self, error, outcome_unknown=False, detail=''):
        message = 'Mobile MCP 调用失败（' + str(error) + '）'
        if detail:
            message += '：' + detail
        if outcome_unknown:
            message += '；执行结果未知，不能自动重试，请先核验'
        super().__init__(message)
        self.error = error
        self.outcome_unknown = bool(outcome_unknown)
        self.detail = detail


def _safe_tool_error(result):
    """仅映射已知诊断，不暴露设备返回的命令、输入或隐私内容。"""
    if not isinstance(result, dict):
        return ''
    content = result.get('content')
    if not isinstance(content, list):
        return ''
    text = ' '.join(str(item.get('text', '')) for item in content
                    if isinstance(item, dict) and item.get('type') == 'text').lower()
    if 'no xml content found in uiautomator dump' in text:
        return '手机界面识别失败：未获取到 UI 层级，请检查手机当前界面和无障碍服务'
    if 'device offline' in text or 'device not found' in text:
        return '手机连接不可用，请检查设备在线状态'
    return '手机工具返回错误；详细原因未能安全识别'


_slots: queue.Queue = queue.Queue()
_created = 0
_created_lock = threading.Lock()
_LOCAL = threading.local()
_fallback = None
_fallback_lock = threading.Lock()


def _new_adapter():
    return MobileMCPAdapter()


def close_all() -> int:
    """关闭池里所有 MCP 通道（每个通道 = 一个 node 子进程）。

    2026-11-07：桌面窗口关闭时调用。原来这些 Node 进程没有任何人管，
    关掉窗口后它们继续挂后台（预建 8 个就是 8 个进程），越积越多。
    返回实际关闭的数量；任何异常都吞掉，绝不阻塞退出流程。
    """
    global _created
    closed = 0
    # 1) 池里待命的
    while True:
        try:
            ad = _slots.get_nowait()
        except queue.Empty:
            break
        try:
            ad.close()
            closed += 1
        except Exception:
            pass
    # 2) 线程本地（正在被某个线程试绑的设备通道）
    try:
        binding = getattr(_LOCAL, 'binding', None)
        if binding:
            ad = binding[0]
            try:
                ad.close()
                closed += 1
            except Exception:
                pass
            _LOCAL.binding = None
    except Exception:
        pass
    # 3) 旧通道兜底
    global _fallback
    try:
        if _fallback is not None:
            try:
                _fallback.close()
                closed += 1
            except Exception:
                pass
            _fallback = None
    except Exception:
        pass
    with _created_lock:
        _created = 0
    return closed


def _acquire(stop_event=None):
    """等待空闲槽位；等待者不拥有槽，超时绝不扣减其他租户计数。"""
    global _created
    deadline = time.monotonic() + ACQUIRE_TIMEOUT
    while True:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError('任务已停止，放弃等待执行通道')
        try:
            return _slots.get_nowait()
        except queue.Empty:
            pass
        with _created_lock:
            if _created < _POOL_SIZE:
                _created += 1
                make = True
            else:
                make = False
        if make:
            try:
                return _new_adapter()
            except Exception:
                with _created_lock:
                    _created -= 1
                raise
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('等待手机通道超过 %.0f 秒仍未获得，已放弃；未改变现有槽位计数'
                               % ACQUIRE_TIMEOUT)
        try:
            return _slots.get(timeout=min(0.2, remaining))
        except queue.Empty:
            continue


def _release(ad):
    """归还通道；卡死过的通道直接丢弃，不把坏连接放回池里。"""
    if getattr(ad, '_broken', False) is True:
        global _created
        with _created_lock:
            if _created > 0:
                _created -= 1
        try:
            ad.close()
        except Exception:
            pass
        return
    _slots.put(ad)


def prewarm(count=None) -> int:
    """预先建好 N 个 MCP 通道（含 Node 进程 + 握手）放回池里。

    2026-11-06 主人要求：「dump 慢可以，但至少启动那一下要快」。
    实测慢点就在这里：每个通道 = 一个 Node 进程（mobile-mcp server），
    而且 `MobileMCPAdapter.__init__` **只是存参数、不启动进程**——
    真正启动发生在第一次调用时的 `_ensure_connected`（Popen + initialize 握手）。
    所以光"new 一个 adapter"没用，必须**真调一次工具**才能算预热成功。

    这里直接 `_new_adapter()`（不走 `_acquire`，否则会先拿到池里的旧通道，
    导致只建成一个）+ `list_tools()` 触发握手；成功才放回池里待命。
    通道不绑定设备（每次调用传 serial），预建几个就能服务任意手机。
    """
    global _PREWARM_DONE, _created
    if count is None:
        import os
        try:
            count = int(os.environ.get('FISHFLEET_PREWARM', '') or 0)
        except ValueError:
            count = 0
        if count <= 0:
            count = 8          # 默认预建 8 个：贴近 19 台分批下发的常见并发，
                               # 又不会把池（10）占满导致现场建通道
    made = 0
    for _ in range(max(0, int(count))):
        with _created_lock:
            if _created >= _POOL_SIZE:
                break
            _created += 1
        ad = None
        try:
            ad = _new_adapter()
            result = ad.list_tools()          # ← 真正启动 Node + MCP 握手
            if not getattr(result, 'ok', False):
                raise RuntimeError('handshake_failed')
        except Exception:
            with _created_lock:
                if _created > 0:
                    _created -= 1
            if ad is not None:
                try:
                    ad.close()
                except Exception:
                    pass
            break                              # 起不来就停，不硬撑
        _slots.put(ad)                         # 放回池里待命
        made += 1
    _PREWARM_DONE = made
    return made


_PREWARM_DONE = 0


@contextmanager
def _cross_process_lock(serial, timeout=20.0):
    """跨进程设备互斥：同一台手机同一时刻只允许一个进程操作。

    `device_session` 原本只是**进程内**锁（threading），两个脚本（例如一个在
    采集、一个在提取）同时操作同一台手机时，uiautomator 会互相踩，表现为
    偶发 tool_error——实测过：混合负载下同一台机的提取连续两次失败。

    这里用 OS 级文件锁补齐跨进程互斥。进程退出时系统自动释放，不留死锁。
    """
    import os
    from pathlib import Path
    lock_dir = Path(__file__).resolve().parents[2] / 'data' / '.device_locks'
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', serial) or 'device'
    handle = open(lock_dir / (safe + '.lock'), 'a+')
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (OSError, IOError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        '设备 %s 正被另一个进程占用（例如同时在采集/提取）。\n'
                        '同一台手机不要同时跑采集和提取——请等它空闲后重试，'
                        '否则 uiautomator 会互相踩。' % serial)
                time.sleep(0.25)
        yield
    finally:
        if locked:
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        handle.close()


@contextmanager
def device_session(serial, stop_event=None):
    """绑定 serial 的独占 MCP 会话；退出时归还槽位。

    互斥分两层：进程内（threading 绑定）+ 跨进程（OS 文件锁）。
    同一线程嵌套进入同一 serial 时复用已有绑定，不重复加锁。
    """
    if not isinstance(serial, str) or not serial:
        raise ValueError('serial 无效')
    bound = getattr(_LOCAL, 'binding', None)
    if bound is not None:
        if bound[1] != serial:
            raise RuntimeError('同一执行线程已绑定另一台手机，拒绝跨设备复用通道')
        yield bound[0]
        return
    # 先锁设备，再占槽位：避免拿着槽位空等，把并行能力锁死。
    with _cross_process_lock(serial):
        ad = _acquire(stop_event)
        _LOCAL.binding = (ad, serial)
        try:
            yield ad
        finally:
            _LOCAL.binding = None
            _release(ad)


def adapter(serial=None):
    """返回当前线程绑定的 adapter；会话外返回进程内唯一的诊断用实例。"""
    bound = getattr(_LOCAL, 'binding', None)
    if bound is not None:
        if serial is not None and serial != bound[1]:
            raise RuntimeError('通道已绑定另一台手机')
        return bound[0]
    global _fallback
    with _fallback_lock:
        if _fallback is None:
            _fallback = _new_adapter()
        return _fallback


def _reset_pool_for_tests():
    """仅测试使用：清空槽位与计数。"""
    global _created, _fallback
    while True:
        try:
            ad = _slots.get_nowait()
        except queue.Empty:
            break
        try:
            ad.close()
        except Exception:
            pass
    with _created_lock:
        _created = 0
    with _fallback_lock:
        if _fallback is not None:
            try:
                _fallback.close()
            except Exception:
                pass
            _fallback = None
    if getattr(_LOCAL, 'binding', None) is not None:
        _LOCAL.binding = None


def _close_all():
    while True:
        try:
            ad = _slots.get_nowait()
        except queue.Empty:
            break
        try:
            ad.close()
        except Exception:
            pass
    with _fallback_lock:
        global _fallback
        if _fallback is not None:
            try:
                _fallback.close()
            except Exception:
                pass


atexit.register(_close_all)

def validate(tool, args, serial):
 if tool not in READ | WRITE or not isinstance(args,dict): raise ValueError('不支持的 Mobile MCP 工具或参数')
 if set(args) - FIELDS.get(tool,set()): raise ValueError('工具包含不允许的参数')
 args=dict(args)
 for key in ('x','y','distance','latitude','longitude'):
  if key in args and (isinstance(args[key],bool) or not isinstance(args[key],(int,float)) or not math.isfinite(args[key])): raise ValueError('坐标或数值无效')
 if tool=='mobile_list_elements_on_screen' and 'format' in args and args['format'] not in ('text','json'): raise ValueError('元素格式无效')
 if tool=='mobile_click_on_screen_at_coordinates':
  if 'ref' in args:
   if set(args)!={'ref'} or not isinstance(args['ref'],str) or not re.fullmatch(r'@e[1-9]\d*',args['ref']): raise ValueError('元素引用无效或与坐标混用')
  elif set(args)!={'x','y'}: raise ValueError('点击需要坐标或元素引用')
 if tool=='mobile_swipe_on_screen' and args.get('direction') not in ('up','down','left','right'): raise ValueError('滑动方向无效')
 if tool=='mobile_type_keys' and (not isinstance(args.get('text'),str) or not 0<len(args['text'])<=2000 or ('submit' in args and not isinstance(args['submit'],bool))): raise ValueError('输入文字无效')
 if tool=='mobile_press_button' and args.get('button') not in ('HOME','BACK','ENTER','BACKSPACE','APP_SWITCH','DPAD_UP','DPAD_DOWN','DPAD_LEFT','DPAD_RIGHT','DPAD_CENTER','VOLUME_UP','VOLUME_DOWN'): raise ValueError('按键不允许')
 if tool in {'mobile_launch_app','mobile_terminate_app','mobile_clear_app_data','mobile_uninstall_app'} and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',str(args.get('packageName',''))): raise ValueError('应用包名无效')
 if tool=='mobile_install_app' and (not isinstance(args.get('path'),str) or not args['path'].lower().endswith('.apk')): raise ValueError('只能安装指定的 APK 文件')
 if tool=='mobile_clipboard':
    # 默认只写不读（保护用户剪贴板）。
    # 2026-11-05 起放开**显式读取**：采集抓真ID时必须读回自己刚「复制链接」得到的口令；
    # 调用方必须传 {'read': True} 表明意图，读到的内容由调用方写日志，便于审计。
    if args.get('read') is True:
        args = {'device': serial}
    elif ('text' not in args or not isinstance(args['text'],str)
          or len(args['text'])>2000):
        raise ValueError('只允许设置剪贴板，或显式 read=True 读取')
 if tool=='mobile_open_url':
     _u = str(args.get('url') or '')
     if not (_u.startswith('http://') or _u.startswith('https://')
             or _u.startswith('fb://')): raise ValueError('open_url 只允许 http/https/fb 链接')
     if len(_u) > 2000: raise ValueError('URL 过长')
 if tool=='mobile_set_location' and (set(args) not in (set(),{'latitude','longitude'}) or ('latitude' in args and (abs(args['latitude'])>90 or abs(args['longitude'])>180))): raise ValueError('定位参数无效')
 args['device']=serial
 return args

def _call_with_timeout(ad, tool, args, timeout=CALL_TIMEOUT):
    """同步执行一次，不创建排队写动作；超时由适配器按发送阶段分类。"""
    return ad.call_tool(tool, args, timeout=timeout)

def call_raw(tool, args, serial):
    args = validate(tool, args, serial)
    if tool in ('mobile_launch_app', 'mobile_open_url'):
        try:
            from .deeplink import note
            note('LAUNCH tool=%s target=%s' % (
                tool, args.get('packageName') or args.get('url') or ''))
        except Exception:
            pass
    started = time.monotonic()
    record = {'request_id': uuid.uuid4().hex, 'tool': tool,
              'kind': 'read' if tool in READ else 'write',
              'ok': False, 'error': 'interrupted', 'outcome_unknown': False}
    bound = getattr(_LOCAL, 'binding', None)
    acquired = None
    try:
        if bound is not None:
            if serial != bound[1]:
                raise RuntimeError('通道已绑定另一台手机')
            ad = bound[0]
        else:
            acquired = _acquire()
            ad = acquired
        record['queue_ms'] = round((time.monotonic() - started) * 1000, 1)
        call_started = time.monotonic()
        result = _call_with_timeout(ad, tool, args)
        # "界面识别失败"意味着**连 UI 层级都没拿到**，动作还没执行到，
        # 因此读操作和用 ref 的点击都可以清理残留后安全重试一次。
        # 判据要求 outcome_unknown 为 False（请求确实没发出去），
        # 结果未知的调用绝不重试。
        if (not result.ok and result.error == 'tool_error'
                and not result.outcome_unknown
                and '界面识别' in _safe_tool_error(result.result)):
            if heal_uiautomator(serial):
                record['healed'] = True
                result = _call_with_timeout(ad, tool, args)
        record['call_ms'] = round((time.monotonic() - call_started) * 1000, 1)
        record.update(ok=bool(result.ok), error=result.error,
                      outcome_unknown=bool(result.outcome_unknown))
        if not result.ok:
            detail = _safe_tool_error(result.result) if result.error == 'tool_error' else ''
            # 2026-11-05 可观测：报错必须带工具名，否则复盘不知道查谁
            # （41号曾被一条无名 tool_error 杀了整单）。
            if detail and not detail.startswith('['):
                detail = '[%s] %s' % (tool, detail)
            raise MobileCallError(result.error or 'tool_failed', bool(result.outcome_unknown), detail)
        return result.result
    finally:
        if acquired is not None:
            _release(acquired)
        record['total_ms'] = round((time.monotonic() - started) * 1000, 1)
        with _DIAGNOSTICS_LOCK:
            _DIAGNOSTICS.append(record)

def call(tool,args,serial):
 """统一出口。录制已下沉到 MobileMCPAdapter.call_tool（覆盖 FB 等直连 adapter 的路径），
 这里保持原样转调，行为与历史实现完全一致。"""
 # 2026-11-07 中文输入短接：Mobile MCP 的 type_keys 在 Android 上靠
 # com.mobilenext.devicekit 走"剪贴板 + 粘贴"，新机没装那个 agent 时
 # 会把**旧剪贴板内容**贴进搜索框（英文正常、中文错乱，极难排查）。
 # 这里改走 uiautomator2 的 AdbKeyboard 广播（base64，原生支持中文），
 # 不依赖任何额外安装；失败再原路退回 MCP，行为不倒退。
 if tool == 'mobile_type_keys' and not _is_ascii_text((args or {}).get('text')):
     if _adb_input_text(serial, str((args or {}).get('text') or '')):
         return {'ok': True, 'via': 'adbkeyboard'}
 return call_raw(tool,args,serial)


# ---------------------------------------------------------------- 中文输入（AdbKeyboard）
#: 两种 AdbKeyboard 实现，协议不同，按顺序探测：
#:   1) com.android.adbkeyboard/.AdbIME  —— 标准 ADBKeyboard（本项目 apk/ADBKeyboard.apk 装出来的）
#:      广播 ADB_INPUT_B64 + --es msg <base64>；返回 result=0 也算成功（实测字能进去）
#:   2) com.github.uiautomator/.AdbKeyboard —— uiautomator2 自带的那个
#:      广播 ADB_KEYBOARD_INPUT_TEXT + --es text <base64>；成功标志 result=-1
_ADB_IMES = [
    ('com.android.adbkeyboard/.AdbIME', 'ADB_INPUT_B64', 'msg'),
    ('com.github.uiautomator/.AdbKeyboard', 'ADB_KEYBOARD_INPUT_TEXT', 'text'),
]
_ADB_APK = 'ADBKeyboard.apk'
_IME_STATE = {}          # serial -> 当前默认输入法，避免每次都查
_IME_LOCK = threading.Lock()
_KB_READY = {}           # serial -> 可用的 (ime_id, action, extra_key)


def _is_ascii_text(text) -> bool:
    """纯 ASCII 沿用 MCP 的原生 input text（快且稳），其余走 AdbKeyboard。"""
    if not isinstance(text, str) or not text:
        return True
    try:
        text.encode('ascii')
        return True
    except (UnicodeEncodeError, AttributeError):
        return False


def _sh(serial, cmd, timeout=20):
    import subprocess as sp
    adb = _resolve_adb_path()
    return sp.run([adb, '-s', serial, 'shell', cmd], capture_output=True,
                  text=True, encoding='utf-8', errors='replace', timeout=timeout)


def _resolve_adb_path() -> str:
    try:
        from fishfleet.paths import adb_path as _ap
        return _ap()
    except Exception:
        return 'adb'


def _installed_imes(serial) -> set:
    try:
        out = _sh(serial, 'ime list -s', timeout=10).stdout or ''
    except Exception:
        return set()
    return {ln.strip() for ln in out.splitlines() if '/' in ln}


def _install_adb_keyboard(serial) -> bool:
    """把随包的 ADBKeyboard.apk 装到手机（17 KB，秒装）。

    这是"中文能输入"的关键组件：MCP 原生只支持 ASCII，
    非 ASCII 要靠输入法把文本 commit 进输入框。
    """
    try:
        import subprocess as sp
        from fishfleet.paths import apk_path
        apk = apk_path(_ADB_APK)
        if not apk or not os.path.exists(str(apk)):
            return False
        adb = _resolve_adb_path()
        r = sp.run([adb, '-s', serial, 'install', '-r', str(apk)],
                   capture_output=True, text=True, encoding='utf-8',
                   errors='replace', timeout=120)
        return 'Success' in ((r.stdout or '') + (r.stderr or ''))
    except Exception:
        return False


def _pick_keyboard(serial):
    """返回 (ime_id, action, extra_key)；没有可用的就装一个，再不行返回 None。"""
    with _IME_LOCK:
        cached = _KB_READY.get(serial)
    if cached:
        return cached
    imes = _installed_imes(serial)
    for ime_id, action, extra in _ADB_IMES:
        if ime_id in imes:
            with _IME_LOCK:
                _KB_READY[serial] = (ime_id, action, extra)
            return (ime_id, action, extra)
    # 都没装：装随包 apk 再探测一次
    if _install_adb_keyboard(serial):
        imes = _installed_imes(serial)
        for ime_id, action, extra in _ADB_IMES:
            if ime_id in imes:
                with _IME_LOCK:
                    _KB_READY[serial] = (ime_id, action, extra)
                return (ime_id, action, extra)
    return None


def _current_ime(serial) -> str:
    with _IME_LOCK:
        cached = _IME_STATE.get(serial)
    if cached:
        return cached
    try:
        out = (_sh(serial, 'settings get secure default_input_method', timeout=8).stdout or '').strip()
    except Exception:
        out = ''
    with _IME_LOCK:
        _IME_STATE[serial] = out
    return out


def _set_ime(serial, ime_id) -> bool:
    try:
        _sh(serial, 'ime enable %s' % ime_id, timeout=10)
        _sh(serial, 'ime set %s' % ime_id, timeout=10)
        with _IME_LOCK:
            _IME_STATE[serial] = ime_id
        time.sleep(0.5)
        return True
    except Exception:
        return False


def _adb_input_text(serial, text) -> bool:
    """用 AdbKeyboard 广播输入任意文本（含中文）。成功返回 True。

    2026-11-07 真机实测（Note9 / 抖音搜索页 et_search_kw）：
      · 输入法**已经是 AdbKeyboard** 时，点一下输入框再广播 → 文字正常写入；
      · 从别的输入法**切到** AdbKeyboard 会让输入框**丢焦点**，
        必须**重新点一次输入框**再广播，否则广播发出去了但字进不去
        （"看着成功实际没输入"，比直接报错更难查）。
    所以：需要切换时，先记下焦点框位置 → 切 → 点回去 → 广播。
    """
    if not text:
        return False
    try:
        import base64
    except Exception:
        return False
    picked = _pick_keyboard(serial)
    if not picked:
        return False          # 手机既没有 AdbKeyboard 也装不上：交回 MCP 报准确错误
    ime_id, action, extra_key = picked
    try:
        cur = _current_ime(serial)
        if ime_id not in (cur or ''):
            center = _focused_edit_center(serial)
            if not _set_ime(serial, ime_id):
                return False
            if center:
                _tap_point(serial, center)
                time.sleep(0.7)
        b64 = base64.b64encode(text.encode('utf-8')).decode('ascii')
        r = _sh(serial, "am broadcast -a %s --%s %s '%s'" % (action, 'es', extra_key, b64), timeout=20)
        out = (r.stdout or '') + (r.stderr or '')
        # 两个实现的成功标志不同：uiautomator 版是 result=-1，标准版是 result=0
        # （两者都实测能把字写进去）。真正的失败是广播根本没发出去。
        if 'Broadcast completed' not in out and 'result=' not in out:
            return False
        _sh(serial, 'am broadcast -a ADB_KEYBOARD_HIDE', timeout=8)
        return True
    except Exception:
        return False


def _tap_point(serial, center) -> None:
    try:
        x, y = int(center[0]), int(center[1])
        _sh(serial, 'input tap %d %d' % (x, y), timeout=15)
    except Exception:
        pass


def _focused_edit_center(serial):
    """dump 一次，找当前聚焦的 EditText 中心点；找不到返回 None。

    只在"需要切换输入法"时调用一次（每个设备第一次），
    后续 AdbKeyboard 已在位就不 dump，避免每输一次都花 2~4 秒。
    """
    try:
        import re as _re
        import subprocess as _sp
        r = _sh(serial, 'uiautomator dump /sdcard/_ff_input.xml', timeout=25)
        txt = ((r.stdout or '') + (r.stderr or '')).upper()
        if 'ERROR' in txt:
            # 视频页 dump 不出来的情况：退回不点击，直接广播（多数机型仍可写入）
            return None
        p = _sp.run([_resolve_adb_path(), '-s', serial, 'exec-out',
                     'cat', '/sdcard/_ff_input.xml'],
                    capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=25)
        xml = p.stdout or ''
        if len(xml) < 500:
            return None
        focused, first = None, None
        for m in _re.finditer(r'<node[^>]*EditText[^>]*>', xml):
            tag = m.group(0)
            b = _re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
            if not b:
                continue
            x1, y1, x2, y2 = map(int, b.groups())
            c = ((x1 + x2) // 2, (y1 + y2) // 2)
            if 'focused="true"' in tag:
                focused = c
                break
            if first is None:
                first = c
        return focused or first
    except Exception:
        return None
