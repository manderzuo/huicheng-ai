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
 return call_raw(tool,args,serial)
