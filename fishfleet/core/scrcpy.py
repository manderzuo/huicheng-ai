"""
FishFleet — scrcpy 实时流客户端

逆向自汇城（XWCaptureScreen.jar = 改装版 scrcpy 2.4）

关键发现（踩了很多坑）：
  1. scid=-1 时 socket 名是 "scrcpy"（不带后缀）
     scid=N  时 socket 名是 "scrcpy_%08x"（8位十六进制补零）
  2. tunnel_forward=true → server listen，客户端连 adb forward 端口
  3. 连接顺序：video → audio(可选) → control
  4. send_dummy_byte=true（默认）时，每个 socket 先发 1 个 0 字节
  5. 协议：
       - 设备名：64 字节（UTF-8，补 \0）
       - codec metadata：12 字节（codec_id 4 + width 4 + height 4，大端）
       - 帧：pts(8) + size_flags(4) + data
         size_flags 高2位：bit63=config包，bit62=关键帧
"""

from __future__ import annotations

import struct
import os
import subprocess
import threading

_GESTURE_INIT_LOCK = threading.Lock()
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .config import get_config


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ADB = None          # 运行时从配置读

SCRCPY_SERVER_JAR = "/data/local/tmp/XWCaptureScreen.jar"   # 汇城的（已在手机上）
PHP_SCRCPY_SERVER = "C:/tools/scrcpy-win64-v2.4/scrcpy-server"


# ---------------------------------------------------------------------------
# 帧
# ---------------------------------------------------------------------------

@dataclass
class VideoFrame:
    data: bytes
    pts: int
    is_key: bool
    is_config: bool
    ts: float


# ---------------------------------------------------------------------------
# 单设备 scrcpy 会话
# ---------------------------------------------------------------------------

class ScrcpySession:
    """
    一台设备的 scrcpy 会话。

    用法：
        sess = ScrcpySession("SERIAL0001")
        sess.start()
        for frame in sess.frames():
            ...
        sess.stop()
    """

    def __init__(
        self,
        serial: str,
        max_size: int = 720,
        max_fps: int = 20,
        bit_rate: int = 12000000,
        scid: int = -1,
    ):
        # Android Options.parse() 使用 Integer.parseInt(value, 16)，只允许 -1 或非负 int。
        if type(scid) is not int or not -1 <= scid <= 0x7FFFFFFF:
            raise ValueError("scid must be an integer in [-1, 2147483647]")
        cfg = get_config()
        self.adb = cfg.devices.adb_path
        self.serial = serial
        self.max_size = max_size
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.scid = scid

        # socket 名
        if scid == -1:
            self.socket_name = "scrcpy"
        else:
            self.socket_name = f"scrcpy_{scid:08x}"

        self.port = 0  # 启动时由 adb 动态分配；0 表示尚未确认端口。
        self._forward_created = False
        self._forward_unknown = False
        self.proc: Optional[subprocess.Popen] = None
        self._video_sock = None
        self._control_sock = None
        # sendall() 可分多次写入，所有控制消息必须共享此锁以免字节交织。
        self._control_lock = threading.Lock()
        self._xw_ime_ok = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_cb: Optional[Callable[[VideoFrame], None]] = None

        # 信息
        self.device_name = ""
        self.width = 0
        self.height = 0
        self.real_width = 1080    # 设备真实分辨率（adb 坐标）
        self.real_height = 2280
        self.codec = 0

    # ---- 启动 ----

    def start(self, timeout: float = 15.0) -> bool:
        # 1. 清理旧的
        self.stop()

        print(f"[scrcpy] start {self.serial}, socket={self.socket_name}, 等待分配端口")

        # 2. 确定 server jar
        jar = SCRCPY_SERVER_JAR
        r = subprocess.run(
            [self.adb, "-s", self.serial, "shell", f"ls {jar}"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            # 回退到官方
            jar = "/data/local/tmp/scrcpy-server-24.jar"
            r2 = subprocess.run(
                [self.adb, "-s", self.serial, "shell", f"ls {jar}"],
                capture_output=True, text=True, timeout=10,
            )
            if r2.returncode != 0:
                return False

        # 3. 让 adb 分配本地端口，避免固定端口的哈希碰撞。
        try:
            r = subprocess.run(
                [self.adb, "-s", self.serial, "forward", "tcp:0",
                 f"localabstract:{self.socket_name}"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, UnicodeError) as exc:
            # 超时或输出解码失败都不能证明 ADB server 没有创建转发。
            self._forward_unknown = True
            raise RuntimeError('ADB 动态转发结果不可读，创建状态未知') from exc
        if r.returncode != 0:
            # 命令失败也不证明请求没有抵达 adb server。
            self._forward_unknown = True
            raise RuntimeError(f'ADB 动态转发失败，创建状态未知: {r.stderr[:100]}')
        port_text = r.stdout.strip()
        if not port_text.isascii() or not port_text.isdecimal() or not 1 <= int(port_text) <= 65535:
            # 成功响应异常时不猜测端口，也不能安全删除未知转发。
            self._forward_unknown = True
            raise RuntimeError('ADB 动态转发端口未知，无法安全清理')
        self.port = int(port_text)
        self._forward_created = True

        # 4. 启动 server
        args = [
            f"CLASSPATH={jar}",
            "app_process", "/", "com.genymobile.scrcpy.Server", "2.4",
        ]
        # scid 只在显式指定时传（-1 是默认值，不传）
        if self.scid != -1:
            args.append(f"scid={self.scid:x}")
        args += [
            "audio=false",
            "video_encoder=OMX.google.h264.encoder",
            f"max_size={self.max_size}",
            f"max_fps={self.max_fps}",
            f"video_bit_rate={self.bit_rate}",
            # ★★ 绝对不要传 video_codec_options！
            #   实测汇城这个改装版 scrcpy 收到任何 video_codec_options 都会
            #   静默停止出帧（frames=0，画面全黑）。IDR 间隔只能用它默认的。
            #   切设备靠「帧缓冲连续重放」+「前端关键帧门闸」解决。
            "tunnel_forward=true",
            "cleanup=false",
            "clipboard_autosync=false",
            "stay_awake=true",
            "send_dummy_byte=false",
        ]
        self.proc = subprocess.Popen(
            [self.adb, "-s", self.serial, "shell", " ".join(args)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


        # 6. 连接（轮询）
        import socket
        deadline = time.time() + timeout
        self._video_sock = None
        # ADB forward 本地连接成功不保证设备端 socket 已就绪。
        # 在能验证完整握手并安全重试之前保留既有等待。
        time.sleep(3.0)

        while time.time() < deadline:
            try:
                self._video_sock = socket.create_connection(
                    ("127.0.0.1", self.port), timeout=2
                )
                break
            except OSError:
                if self.proc.poll() is not None:
                    return False
                time.sleep(0.15)

        if self._video_sock is None:
            return False

        # 7. 连接 control
        try:
            self._control_sock = socket.create_connection(
                ("127.0.0.1", self.port), timeout=3
            )
            # ★ 实时触摸是几十字节小包：禁用 Nagle，避免内核攒包延迟。
            #   视频 socket 不动，只动控制 socket。
            try:
                self._control_sock.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        except OSError:
            self._control_sock = None

        # 8. 读 header
        try:
            name_raw = self._readn(self._video_sock, 64, timeout)
            self.device_name = name_raw.rstrip(b"\x00").decode("utf-8", "replace")
            print(f"[scrcpy] 设备名: '{self.device_name}'")

            hdr = self._readn(self._video_sock, 12, timeout)
            self.codec, self.width, self.height = struct.unpack(">III", hdr)
            if self.codec != 0x68323634 or not (1 <= self.width <= 65535 and 1 <= self.height <= 65535):
                raise ValueError('无效或不支持的视频头')
            print(f"[scrcpy] {self.width}x{self.height} codec={hex(self.codec)}")
        except Exception as e:  # noqa: BLE001
            print(f"[scrcpy] 读 header 失败: {type(e).__name__}: {e}")
            self.stop()
            return False

        self._running = True
        return True

    # ---- 读帧 ----

    @staticmethod
    def _readn(sock, n: int, timeout: float = 10.0) -> bytes:
        sock.settimeout(timeout)
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError(f"closed at {len(buf)}/{n}")
            buf += chunk
        return buf

    def read_frame(self, timeout: float = 10.0) -> Optional[VideoFrame]:
        if not self._running or self._video_sock is None:
            return None

        # 保留部分包：设置等静态页面的空闲超时不能破坏头/payload边界。
        if getattr(self, '_packet_sock', None) is not self._video_sock:
            self._packet_sock = self._video_sock
            self._packet_buffer = bytearray()
        buf = self._packet_buffer
        self._video_sock.settimeout(timeout)
        try:
            while True:
                if len(buf) >= 12:
                    pts_raw, size = struct.unpack_from('>QI', buf)
                    if size > 32 * 1024 * 1024:
                        raise ConnectionError(f'invalid video packet size: {size}')
                    if len(buf) >= 12 + size:
                        data = bytes(buf[12:12 + size])
                        del buf[:12 + size]
                        return VideoFrame(
                            data=data, pts=pts_raw & 0x3FFFFFFFFFFFFFFF,
                            is_key=bool(pts_raw & 0x4000000000000000),
                            is_config=bool(pts_raw & 0x8000000000000000),
                            ts=time.time(),
                        )
                chunk = self._video_sock.recv(65536)
                if not chunk:
                    raise ConnectionError('video socket closed')
                buf.extend(chunk)
        except TimeoutError:
            return None
        except (OSError, ConnectionError):
            self._running = False
            buf.clear()
            return None

    # ---- 控制 ----

    def _gesture_state(self):
        # __new__-constructed offline fixtures are supported as well as normal sessions.
        with _GESTURE_INIT_LOCK:
            if not hasattr(self, '_gesture_state_lock'):
                self._gesture_state_lock = threading.Lock()
                self._gesture_owner = None
                self._gesture_socket = None
        return self._gesture_state_lock

    def claim_gesture(self, owner) -> bool:
        """Reserve a whole touch sequence without waiting or preempting another owner."""
        with self._gesture_state():
            if self._gesture_owner is not None or self._control_sock is None:
                return False
            self._gesture_owner, self._gesture_socket = owner, self._control_sock
            return True

    def release_gesture(self, owner) -> None:
        with self._gesture_state():
            if self._gesture_owner == owner:
                self._gesture_owner = self._gesture_socket = None

    def owned_touch(self, owner, phase: str, x: int, y: int) -> bool:
        """Writer-thread API; fence every packet to the claimed socket identity."""
        with self._gesture_state():
            if self._gesture_owner != owner:
                return False
            sock = self._gesture_socket
        if not self.width or not self.height:
            return False
        vx, vy = self._video_coords(x, y)
        action = {'down': self.ACTION_DOWN, 'move': self.ACTION_MOVE,
                  'up': self.ACTION_UP}[phase]
        return self.send_control(self._touch_msg(action, vx, vy, self.width, self.height,
                                                 pressure=0 if phase == "up" else 0xFFFF),
                                 expected_sock=sock)

    def send_control(self, payload: bytes, *, expected_sock=None) -> bool:
        """发送控制消息（scrcpy ControlMessage 协议）。"""
        with self._control_lock:
            sock = self._control_sock
            if sock is None or (expected_sock is not None and sock is not expected_sock):
                return False
            try:
                sock.sendall(payload)
                return True
            except OSError:
                # sendall 可能只写入部分消息；继续使用会让消息边界失同步。
                self._control_sock = None
                try:
                    sock.close()
                except OSError:
                    pass
                return False

    # ---- scrcpy control 协议 ----
    # 参考汇城的实现：通过 control socket 直接注入触摸事件，延迟 < 50ms
    # 协议（scrcpy 2.4）：
    #   type(u8) + action(u8) + pointerId(u64) + x(i32) + y(i32)
    #   + screenWidth(u16) + screenHeight(u16) + pressure(u16) + buttons(u32)

    CTRL_INJECT_KEYCODE = 0
    CTRL_INJECT_TOUCH = 2
    CTRL_INJECT_SCROLL = 3      # ★ 滚轮滚动
    CTRL_RESET_OPTIONS = 99     # ★★ 重置编码器 → 立即产生新 IDR（汇城的 sendIframe）
    CTRL_INJECT_KEYCODE = 0
    CTRL_INJECT_SCROLL = 3

    ACTION_DOWN = 0
    ACTION_UP = 1
    ACTION_MOVE = 2

    def _touch_msg(self, action: int, x: int, y: int, w: int, h: int,
                   pressure: int = 0xFFFF, action_button: int = 0, buttons: int = 0) -> bytes:
        """构造 INJECT_TOUCH_EVENT 消息（scrcpy 2.4，共 32 字节）。

        协议（大端）：
          type           u8   (2)
          action         u8   (0=DOWN 1=UP 2=MOVE)
          pointerId      u64
          x              i32
          y              i32
          screenWidth    u16
          screenHeight   u16
          pressure       u16  (0xFFFF = 1.0)
          actionButton   i32
          buttons        i32
        """
        return struct.pack(
            ">BBQiiHHHii",
            self.CTRL_INJECT_TOUCH,
            action & 0xFF,
            0,                                 # pointerId（单点触摸用 0）
            int(x), int(y),
            int(w), int(h),
            int(pressure),
            int(action_button),
            int(buttons),
        )


    # ---- 文本输入（对齐汇城 XWKeyboard 方案）----

    # 汇城 Constants.java 里的广播协议：
    #   ACTION_KEYBOARD_MESSAGE_TEXT = "XW_INPUT_TEXT"
    #   EXTRA_KEYBOARD_MESSAGE       = "msg"
    # 还有 XW_INPUT_B64 / XW_INPUT_CHARS / XW_CLEAR_TEXT 等
    XW_IME_ID = "com.android.xwkeyboard/.XwIME"
    XW_ACTION_TEXT = "XW_INPUT_TEXT"
    XW_ACTION_B64 = "XW_INPUT_B64"
    XW_ACTION_CLEAR = "XW_CLEAR_TEXT"

    def ensure_xw_ime(self) -> bool:
        """确保 XWKeyboard 已安装并设为默认输入法（汇城方案）。

        汇城 Controller.injectText() 的逻辑：
            if (defaultIme 含 com.android.xwkeyboard)  → 广播发文字
            else if (setClipboardText)                 → 粘贴 KEYCODE 279
        前者对中文完全可靠，后者依赖剪贴板权限。
        """
        if self._xw_ime_ok:
            return True
        try:
            # 已装？
            out = subprocess.run(
                [self.adb] + ["-s", self.serial, "shell", "pm list packages com.android.xwkeyboard"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            if "com.android.xwkeyboard" not in out:
                apk = os.path.join(os.path.dirname(__file__), "..", "..", "apk", "XWKeyboard.apk")
                apk = os.path.abspath(apk)
                if not os.path.exists(apk):
                    # 汇城自带的那个
                    apk = str(__import__("fishfleet.paths", fromlist=["apk_path"]).apk_path("XWKeyboard.apk") or "")
                if not os.path.exists(apk):
                    return False
                subprocess.run(
                    [self.adb] + ["-s", self.serial, "install", "-r", apk],
                    capture_output=True, text=True, timeout=120,
                )

            # 启用 + 设为默认
            subprocess.run(
                [self.adb] + ["-s", self.serial, "shell", f"ime enable {self.XW_IME_ID}"],
                capture_output=True, text=True, timeout=15,
            )
            subprocess.run(
                [self.adb] + ["-s", self.serial, "shell", f"ime set {self.XW_IME_ID}"],
                capture_output=True, text=True, timeout=15,
            )
            out = subprocess.run(
                [self.adb] + ["-s", self.serial, "shell", "settings get secure default_input_method"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            self._xw_ime_ok = "xwkeyboard" in out
            return self._xw_ime_ok
        except Exception:
            return False

    def send_text(self, text: str) -> bool:
        """输入文字（支持中文），对齐汇城 injectText。

        优先 XWKeyboard 广播；不可用时退回剪贴板 + PASTE(279)。
        """
        if not text:
            return False

        if self.ensure_xw_ime():
            try:
                # am broadcast -a XW_INPUT_TEXT --es msg "..."
                # 用 base64 路径避免 shell 引号/特殊字符问题
                import base64 as _b64
                raw = text.encode("utf-8")
                b64 = _b64.b64encode(raw).decode("ascii")
                r = subprocess.run(
                    [self.adb] + [
                        "-s", self.serial, "shell",
                        f"am broadcast -a {self.XW_ACTION_B64} --es msg '{b64}'",
                    ],
                    capture_output=True, text=True, timeout=20,
                )
                if "Broadcast completed" in (r.stdout or "") + (r.stderr or ""):
                    return True
                # base64 路径不行就用明文路径
                safe = text.replace("'", "'\''")
                r = subprocess.run(
                    [self.adb] + [
                        "-s", self.serial, "shell",
                        f"am broadcast -a {self.XW_ACTION_TEXT} --es msg '{safe}'",
                    ],
                    capture_output=True, text=True, timeout=20,
                )
                if "Broadcast completed" in (r.stdout or "") + (r.stderr or ""):
                    return True
            except Exception:
                pass

        # 兜底：剪贴板 + PASTE
        try:
            subprocess.run(
                [self.adb] + ["-s", self.serial, "shell", "input", "keyevent", "279"],
                capture_output=True, text=True, timeout=15,
            )
            return True
        except Exception:
            return False

    def clear_text(self, times: int = 20) -> bool:
        """清空输入框（对齐汇城 XW_CLEAR_TEXT）。"""
        try:
            r = subprocess.run(
                [self.adb] + ["-s", self.serial, "shell", f"am broadcast -a {self.XW_ACTION_CLEAR}"],
                capture_output=True, text=True, timeout=15,
            )
            if "Broadcast completed" in (r.stdout or "") + (r.stderr or ""):
                return True
            # 兜底：删退格
            for _ in range(times):
                subprocess.run(
                    [self.adb] + ["-s", self.serial, "shell", "input", "keyevent", "67"],
                    capture_output=True, timeout=5,
                )
            return True
        except Exception:
            return False

    # ---- 按键（走 control socket，2ms 级，对齐汇城）----

    # Android KeyEvent 键码
    KEY = {
        "home": 3, "back": 4, "recent": 187, "app_switch": 187,
        "enter": 66, "delete": 67, "escape": 111, "tab": 61,
        "power": 26, "volume_up": 24, "volume_down": 25,
        "wakeup": 224, "sleep": 223,
        "menu": 82, "search": 84,
        "up": 19, "down": 20, "left": 21, "right": 22,
        "paste": 279, "copy": 278, "cut": 277,
        "select_all": 29,
    }

    def keycode(self, code: int) -> bool:
        token = object()
        if not self.claim_gesture(token):
            return False
        try:
            return self._keycode_reserved(code)
        finally:
            self.release_gesture(token)

    def owned_key(self, token, key):
        with self._gesture_state():
            if self._gesture_owner != token or self._gesture_socket is not self._control_sock:
                return False
        code = self.KEY.get(str(key).lower().lstrip('keycode_'))
        return False if code is None else self._keycode_reserved(code)

    def _keycode_reserved(self, code: int, meta: int = 0) -> bool:
        """注入一个按键（DOWN + UP），走 control socket。

        scrcpy 协议（type 0 INJECT_KEYCODE，共 14 字节）：
          type      u8   (0)
          action    u8   (0=DOWN 1=UP)
          keycode   u32
          repeat    u32
          metaState u32
        """
        if self._control_sock is None:
            return False
        try:
            down = struct.pack(">BBIII", self.CTRL_INJECT_KEYCODE, 0, int(code), 0, int(meta))
            up = struct.pack(">BBIII", self.CTRL_INJECT_KEYCODE, 1, int(code), 0, int(meta))
            return self.send_control(down + up)
        except OSError:
            return False

    def keycode_meta(self, code: int, meta: int = 0) -> bool:
        """按原生键码注入（直接键盘模式），meta：0 无 / 1 Shift（大写/上档符号）。"""
        try:
            code = int(code)
            meta = 1 if meta else 0
        except (TypeError, ValueError):
            return False
        if not 0 <= code <= 300:
            return False
        token = object()
        if not self.claim_gesture(token):
            return False
        try:
            return self._keycode_reserved(code, meta)
        finally:
            self.release_gesture(token)

    def press_key(self, key: str) -> bool:
        """按名字按键（home / back / recent / enter ...）。"""
        if isinstance(key, int):
            return self.keycode(key)
        code = self.KEY.get(str(key).lower().lstrip("keycode_"))
        if code is None:
            return False
        return self.keycode(code)

    def request_options(self, max_size: int, max_fps: int, bit_rate: int) -> bool:
        """发送汇城 99 号编码选项请求，不代表服务端已应用或尺寸已变化。

        max_size/max_fps 为 0 表示不限；正 max_size 至少为 8，服务端会
        向下对齐到 8 的倍数。只接受 Java 有符号 int 范围内的整数，避免
        类型隐式转换、文本选项注入及服务端 parseInt 溢出。

        不改写会话配置或 width/height：实际尺寸仍需由视频流观测确认。
        无控制连接或写入失败返回 False；参数不合法抛 ValueError。
        """
        for name, value, minimum in (
            ("max_size", max_size, 0),
            ("max_fps", max_fps, 0),
            ("bit_rate", bit_rate, 1),
        ):
            if type(value) is not int or not minimum <= value <= 0x7FFFFFFF:
                raise ValueError(f"{name} must be an integer in [{minimum}, 2147483647]")
        if 0 < max_size < 8:
            raise ValueError("max_size must be 0 (unlimited) or at least 8")

        # Options.parse() 的首项必须为版本号，后面才是空格分隔的选项。
        text = f"2.4 max_size={max_size} video_bit_rate={bit_rate} max_fps={max_fps}"
        raw = text.encode("utf-8")
        msg = bytes([self.CTRL_RESET_OPTIONS]) + struct.pack(">I", len(raw)) + raw
        return self.send_control(msg)

    def request_iframe(self) -> bool:
        """请求立即产生一个 IDR 关键帧（对齐汇城的 sendIframe）。

        ★ 汇城的真实做法（从 XWCaptureScreen 反编译 + WebView2 代码缓存挖出）：
          浏览器 sendIframe → Rust 后端 → 发 TYPE_RESET_OPTIONS(99) 到 scrcpy

        server 侧链路（Controller.java）：
          type == 99 → parseOption(text)
                     → Server.resetScreenEncoder(TextUtils.split(text, " "))
                     → SurfaceEncoder.reset(...)
                     → screenCapture.requestReset()
                     → 编码器重建 → 立即输出新 IDR

        消息格式（ControlMessageReader.parseInjectText）：
          type    u8   = 99
          length  u32  大端，UTF-8 文本字节数
          text    utf8 空格分隔的 key=value 选项

        返回 True 表示命令已发出（不代表 IDR 已到）。
        """
        if self._control_sock is None:
            return False
        try:
            # 参数必须和当前一致，避免 reset 时改分辨率/码率
            text = f"video_bit_rate={self.bit_rate} max_fps={self.max_fps}"
            raw = text.encode("utf-8")
            msg = bytes([self.CTRL_RESET_OPTIONS]) + struct.pack(">I", len(raw)) + raw
            return self.send_control(msg)
        except OSError:
            return False

    def scroll(self, x: int, y: int, h: float = 0.0, v: float = 0.0) -> bool:
        token = object()
        if not self.claim_gesture(token):
            return False
        try:
            return self._scroll_reserved(x, y, h, v)
        finally:
            self.release_gesture(token)

    def owned_scroll(self, token, x, y, h, v):
        with self._gesture_state():
            if self._gesture_owner != token or self._gesture_socket is not self._control_sock:
                return False
        return self._scroll_reserved(x, y, h, v)

    def _scroll_reserved(self, x: int, y: int, h: float = 0.0, v: float = 0.0) -> bool:
        """鼠标滚轮（INJECT_SCROLL_EVENT，payload 20 字节）。

        ★ 协议（对齐 scrcpy 2.4 ControlMessageReader.parseInjectScrollEvent）：
          x        i32
          y        i32
          w        u16
          h        u16
          hScroll  i16   水平滚动，fixed-point = value * 32768
          vScroll  i16   垂直滚动，fixed-point = value * 32768
          buttons  i32
        总 payload 20 字节 + type 1 字节 = 21 字节

        v > 0 = 向下滚，v < 0 = 向上滚（值约 ±1.0 一屏）
        """
        if self._control_sock is None:
            return False

        w, h_px = self.width, self.height
        rw, rh = self.real_width, self.real_height
        vx = int(x * w / rw) if rw else x
        vy = int(y * h_px / rh) if rh else y

        # fixed-point 转换（1.0 = 32768）
        hs = max(-32768, min(32767, int(h * 32768)))
        vs = max(-32768, min(32767, int(v * 32768)))

        try:
            msg = struct.pack(
                ">BiiHHhhi",
                self.CTRL_INJECT_SCROLL,
                vx, vy,
                w, h_px,
                hs, vs,
                0,                    # buttons
            )
            return self.send_control(msg)
        except OSError:
            return False

    def tap(self, x: int, y: int) -> bool:
        owner = object()
        if not self.claim_gesture(owner):
            return False
        try:
            return self._tap_reserved(x, y)
        finally:
            self.release_gesture(owner)

    def _tap_reserved(self, x, y):
        if self._control_sock is None:
            return False
        if self.width == 0 or self.height == 0:
            return False

        # 设备坐标 → scrcpy 编码器坐标
        # 注意：scrcpy 的 video 尺寸就是编码尺寸，直接用即可
        # ★ 关键：x,y 和 screenSize 必须都在「画面坐标系」里
        #   scrcpy 的 injectTouch 会校验 position.screenSize == 编码画面尺寸，
        #   不匹配直接丢弃事件（"different device size"）
        w, h = self.width, self.height          # 408 x 720
        rw, rh = self.real_width, self.real_height   # 1080 x 1920

        vx = int(x * w / rw) if rw else x
        vy = int(y * h / rh) if rh else y

        try:
            down = self._touch_msg(self.ACTION_DOWN, vx, vy, w, h)
            up = self._touch_msg(self.ACTION_UP, vx, vy, w, h, pressure=0)
            return self.send_control(down + up)
        except OSError:
            return False

    # ---- 实时触摸（对齐汇城：按下立刻发，移动节流发，松手发） ----
    # 与 swipe() 的区别：swipe() 是"松手后重播整段"；这里是"手指在哪就发到哪"。
    # 调用方（前端）负责 20ms 节流；这里收到什么就发什么，不 sleep、不缩短、不攒包。

    def _video_coords(self, x: int, y: int) -> tuple[int, int]:
        """设备坐标 → scrcpy 编码器坐标（与 tap/swipe 同一换算）。"""
        w, h = self.width, self.height
        rw, rh = self.real_width, self.real_height
        return (int(x * w / rw) if rw else x, int(y * h / rh) if rh else y)

    def _legacy_touch(self, phase, x, y):
        token = object()
        if not self.claim_gesture(token):
            return False
        try:
            return self.owned_touch(token, phase, x, y)
        finally:
            self.release_gesture(token)

    def touch_down(self, x, y):
        return self._legacy_touch('down', x, y)

    def touch_move(self, x, y):
        return self._legacy_touch('move', x, y)

    def touch_up(self, x, y):
        return self._legacy_touch('up', x, y)

    def swipe(self, x1, y1, x2, y2, duration=0.3, steps=12):
        owner = object()
        if not self.claim_gesture(owner):
            return False
        try:
            return self._swipe_reserved(x1, y1, x2, y2, duration, steps)
        finally:
            self.release_gesture(owner)

    def _swipe_reserved(self, x1: int, y1: int, x2: int, y2: int,
              duration: float = 0.3, steps: int = 12) -> bool:
        """通过 control socket 滑动。

        必须按真实时间铺开坐标事件：scrcpy 2.4 的 touch 消息不带时间戳，
        设备按收到的时刻计算速度。若一次性发出 DOWN/MOVE/UP，
        Android 会认为这段位移发生在 0 秒内，速度无穷大 → 触发惯性甩动，
        松手后页面还在继续滚动。因此这里用 sleep 均匀铺开 duration。
        """
        if self._control_sock is None or self.width == 0:
            return False

        w, h = self.width, self.height
        rw, rh = self.real_width, self.real_height

        def _vx(x):
            return int(x * w / rw) if rw else x

        def _vy(y):
            return int(y * h / rh) if rh else y

        # 时长下限避免“瞬间完成”的甩动；上限保证不会长时间占住手势。
        duration = max(0.08, min(3.0, float(duration or 0.3)))
        steps = max(2, min(60, int(steps)))
        interval = duration / steps

        # 手势期间独占，防止两次滑动的事件交错成一条乱七八糟的轨迹。
        # 用独立锁而非 _control_lock：sleep 期间不应阻塞按键等其它控制消息。
        if getattr(self, "_gesture_lock", None) is None:
            self._gesture_lock = threading.Lock()

        with self._gesture_lock:
            sock = self._control_sock
            if sock is None:
                return False
            try:
                if not self.send_control(self._touch_msg(self.ACTION_DOWN, _vx(x1), _vy(y1), w, h), expected_sock=sock):
                    return False
                for i in range(1, steps):
                    t = i / steps
                    xi = int(x1 + (x2 - x1) * t)
                    yi = int(y1 + (y2 - y1) * t)
                    time.sleep(interval)
                    # 会话可能在手势中途被替换或关闭，此时不要再写入旧 socket。
                    if self._control_sock is not sock:
                        return False
                    if not self.send_control(self._touch_msg(self.ACTION_MOVE, _vx(xi), _vy(yi), w, h), expected_sock=sock):
                        return False
                time.sleep(interval)
                if self._control_sock is not sock:
                    return False
                if not self.send_control(self._touch_msg(self.ACTION_UP, _vx(x2), _vy(y2), w, h, pressure=0), expected_sock=sock):
                    return False
                return True
            except OSError:
                # 已经发出部分事件，结果未知，交给调用方停止而不是重发。
                return False

    # ---- 停止 ----

    def stop(self) -> None:
        self._running = False

        # 控制写入和关闭共享锁，避免关闭时与 sendall 交叉。
        with self._control_lock:
            control_sock, self._control_sock = self._control_sock, None
            if control_sock:
                try:
                    control_sock.close()
                except OSError:
                    pass
        video_sock, self._video_sock = self._video_sock, None
        if video_sock:
            try:
                video_sock.close()
            except OSError:
                pass

        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None

        if self._forward_unknown:
            raise RuntimeError('ADB 动态转发端口未知，无法安全清理')
        if self._forward_created:
            try:
                listing = subprocess.run(
                    [self.adb, 'forward', '--list'], capture_output=True, text=True, timeout=5,
                )
                expected = (self.serial, f'tcp:{self.port}', f'localabstract:{self.socket_name}')
                rows = [tuple(line.split()) for line in listing.stdout.splitlines()]
                matches = [row for row in rows if len(row) == 3 and row[1] == expected[1]]
                if listing.returncode != 0:
                    raise RuntimeError('无法查询转发归属')
                if not matches:
                    # USB 断开会使 ADB 自动移除转发。成功查询确认不存在时，
                    # 无需再次删除；不能把这一正常状态永久当成清理失败。
                    self._forward_created = False
                    self.port = 0
                    return
                if matches != [expected]:
                    raise RuntimeError('转发已变化或归属无法确认')
                # 仅降低误删风险；列表检查和移除之间不是原子操作。
                result = subprocess.run(
                    [self.adb, "-s", self.serial, "forward", "--remove", f"tcp:{self.port}"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode != 0:
                    raise RuntimeError(f'ADB 转发清理失败: {result.stderr[:200]}')
            except Exception as exc:
                # 不丢失清理状态，也不能冒险重启并覆盖同一端口。
                raise RuntimeError('ADB 转发清理未确认') from exc
            self._forward_created = False
            self.port = 0
        # 不按进程名 pkill；远端进程是否退出仍未知。

    @property
    def running(self) -> bool:
        return self._running and self._video_sock is not None

    def info(self) -> dict:
        return {
            "serial": self.serial,
            "device_name": self.device_name,
            "width": self.width,
            "height": self.height,
            "codec": "h264" if self.codec == 0x68323634 else hex(self.codec),
            "running": self.running,
            "port": self.port,
        }


# ---------------------------------------------------------------------------
# H.264 → JPEG 解码器（用 ffmpeg）
# ---------------------------------------------------------------------------

class H264Decoder:
    """
    把 H.264 帧喂给 ffmpeg，从 stdout 读 MJPEG 帧。

    ffmpeg 命令：
      ffmpeg -f h264 -i pipe:0 -f mjpeg -q:v 5 pipe:1
    """

    def __init__(self, width: int, height: int, quality: int = 10, fps: int = 2):
        self.width = width
        self.height = height
        self.quality = quality
        self.fps = fps
        self.proc: Optional[subprocess.Popen] = None
        self._buf = bytearray()
        self._reader: Optional[threading.Thread] = None
        self._jpeg_event = threading.Event()

    def start(self) -> bool:
        import shutil

        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        try:
            self.proc = subprocess.Popen(
                [
                    ffmpeg,
                    "-hide_banner", "-loglevel", "error",
                    "-f", "h264",
                    "-i", "pipe:0",
                    "-f", "mjpeg",
                    "-q:v", str(self.quality),
                    "-vf", f"fps={self.fps}",
                    "pipe:1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            # 后台线程持续读 stdout（Windows select 不支持 pipe）
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            return True
        except FileNotFoundError:
            return False

    def _read_loop(self) -> None:
        """后台读 ffmpeg stdout，写入 self._buf。

        必须用 os.read（立即返回可用字节），
        file.read(n) 会阻塞直到读满 n 字节，导致流卡死。
        """
        assert self.proc is not None and self.proc.stdout is not None
        import os
        fd = self.proc.stdout.fileno()
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                self._buf.extend(chunk)
                self._jpeg_event.set()
        except (OSError, ValueError):
            pass

    def feed(self, frame: VideoFrame) -> None:
        """喂一帧 H.264（config 包 SPS/PPS 必须喂，否则解码器无法初始化）。"""
        if self.proc is None or self.proc.stdin is None:
            return
        try:
            self.proc.stdin.write(frame.data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def read_jpeg(self, timeout: float = 0.5) -> Optional[bytes]:
        """从 ffmpeg stdout 读一个完整 JPEG（SOI...EOI）。

        Windows 的 select() 不支持管道，所以用后台线程持续读入缓冲区。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            end = self._buf.find(b"\xff\xd9")
            if end >= 0:
                jpeg = bytes(self._buf[:end + 2])
                del self._buf[:end + 2]
                return jpeg
            # 用 Event 等待而不是忙轮询，省 CPU
            self._jpeg_event.wait(0.02)
            self._jpeg_event.clear()

        return None

    def stop(self) -> None:
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except OSError:
                    pass
            self.proc = None


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    serial = sys.argv[1] if len(sys.argv) > 1 else "SERIAL0001"

    print(f"测试 {serial}")
    sess = ScrcpySession(serial, max_size=720, max_fps=30, bit_rate=4000000)
    if not sess.start():
        print("启动失败")
        sys.exit(1)

    print(f"已连接: {sess.info()}")

    dec = H264Decoder(sess.width, sess.height, fps=15)
    if not dec.start():
        print("ffmpeg 启动失败")
        sess.stop()
        sys.exit(1)
    print("ffmpeg 已启动")

    t0 = time.time()
    n = 0
    jpegs = 0
    while time.time() - t0 < 5:
        f = sess.read_frame(timeout=2)
        if f is None:
            break
        n += 1
        dec.feed(f)
        j = dec.read_jpeg(timeout=0.05)
        if j:
            jpegs += 1
            if jpegs == 1:
                print(f"首个 JPEG: {len(j)} 字节")

    print(f"\n结果: {n} 个 H.264 帧, {jpegs} 个 JPEG")
    dec.stop()
    sess.stop()
