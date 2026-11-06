"""无障碍投屏会话：电脑监听端口，手机端「投屏助手」主动回连。

与 ADB 通道的区别（对齐汇城）：
  · 不建 adb forward，不依赖 adb shell 起 scrcpy；
  · 由 App 通过广播拿到 ip/port 后主动连回来；
  · 视频流与控制流是两条 TCP 连接，先后顺序：video → control；
  · 建好后即可关闭 ADB，画面与控制仍走 TCP。

协议与 ADB 通道完全一致（scrcpy 2.4）：复用 ScrcpySession 的读帧 /
控制消息编码，只重写「如何拿到 socket」这一段。
"""
from __future__ import annotations

import re
import socket
import struct
import subprocess
import threading
import time

from .scrcpy import ScrcpySession, VideoFrame

ACCESSIBLE_PACKAGE = "com.xiaowei.assistant"
SCRCPY_ACTION = "com.xiaowei.assistant.MainActivity.scrcpy"
ASSISTANT_ACTIVITY = "com.xiaowei.assistant/.MainActivity"

# 对齐汇城口径：单次连接等待 15 秒（「无障碍连接断开或是超过15秒未连接」）；
# 断线后每台每 20 秒重试一次（client_center 节奏）。
ACCESSIBLE_CONNECT_TIMEOUT = 15.0
ACCESSIBLE_RETRY_SECONDS = 20

# 手机上需要自动点掉的按钮文案，按优先级从高到低匹配。
# 录屏授权页各 ROM 文案不同：
#   三星 = 「立即開始」；荣耀/Android 15 = 「整个屏幕」+「允许」；
# 必须先选「整个屏幕」，否则默认只投单个应用。
UI_ALLOW_WORDS = ("整个屏幕", "整個屏幕", "立即開始", "立即开始", "立即开启",
                  "允许", "允許", "开始录制", "开始录屏")
# 「连接电脑」= App 主界面的发起按钮（不点它 App 不连），放最后避免误点。
UI_CONNECT_WORDS = ("连接电脑",)
# 自动点击的总时长：覆盖「App 冷启动 → 授权 → 点连接 → 建流」全程。
UI_DRIVE_SECONDS = 60.0


class AccessibleScrcpySession(ScrcpySession):
    """电脑端监听、手机端回连的 scrcpy 会话。"""

    def __init__(self, serial: str, max_size: int = 720, max_fps: int = 20,
                 bit_rate: int = 12000000, use_soft_encoder: bool = False,
                 pc_ip: str = ""):
        super().__init__(serial, max_size=max_size, max_fps=max_fps,
                         bit_rate=bit_rate, scid=-1)
        self.use_soft_encoder = use_soft_encoder
        self.pc_ip = pc_ip
        self._listener = None
        self._accept_thread = None
        self.connected = False
        self.last_error = ""
        self._reverse_ports: list[int] = []
        # app_msg 通道（汇城 app_msg_read/app_msg_write）：App 可能先连一条
        # 握手连接发 TypeBindSerial(串号+地址)，PC 回端口消息/TypeStartUp。
        # 目前只做接线与原文记录，不断开、不伪造回复，等抓到真实报文再补。
        self.appmsg_raw = ""
        self._appmsg_sock = None
        self._ui_stop = False
        self._ui_thread = None
        self._last_tap = (0.0, 0, 0)

    # ---- App 冷启动与 UI 自动点击 ----

    def _shell(self, *args, timeout: float = 20.0):
        try:
            return subprocess.run([self.adb, "-s", self.serial, "shell", *args],
                                  capture_output=True, text=True, timeout=timeout)
        except Exception:
            return None

    def _restart_app(self) -> None:
        """把投屏助手拉到前台并确保无障碍服务已绑定。

        注意：不能 force-stop —— 杀进程会让系统的无障碍服务解绑，
        App 重启后会弹「无障碍未授权」直接拒绝连接（实测荣耀 ROM）。
        这里只做「唤醒 + 校验绑定」，不破坏已有授权。
        """
        self._shell("am", "start", "-n", ASSISTANT_ACTIVITY, "-e", "serial", self.serial, timeout=20)
        time.sleep(1.5)
        ok = self._a11y_bound()
        if not ok:
            # 只补写设置，不杀进程；部分 ROM 需要界面开关，这里尽力而为。
            self._shell("settings", "put", "secure", "enabled_accessibility_services",
                        f"{ACCESSIBLE_PACKAGE}/{ACCESSIBLE_PACKAGE}.scrcpy.PerformService", timeout=15)
            self._shell("settings", "put", "secure", "accessibility_enabled", "1", timeout=15)
            time.sleep(2.0)
            ok = self._a11y_bound()
        print(f"[accessible] 投屏助手就绪，无障碍绑定={'是' if ok else '否'}", flush=True)

    def _a11y_bound(self) -> bool:
        r = self._shell("dumpsys", "accessibility", timeout=20)
        txt = (r.stdout or "") if r is not None else ""
        return "投屏助手" in txt and "Bound services:{}" not in txt.replace(" ", "")

    def _dump_ui(self) -> str:
        self._shell("rm", "-f", "/sdcard/_ff_ui.xml", timeout=8)
        r = self._shell("uiautomator", "dump", "/sdcard/_ff_ui.xml", timeout=25)
        if r is not None and "dumped to" not in ((r.stdout or "") + (r.stderr or "")):
            pass
        r2 = self._shell("cat", "/sdcard/_ff_ui.xml", timeout=15)
        return (r2.stdout or "") if r2 is not None else ""

    @staticmethod
    def _find_hit(xml: str, words) -> tuple[str, int, int] | None:
        """按 words 的先后顺序找第一个命中，返回 (命中词, x, y)。"""
        nodes = [m.group(0) for m in re.finditer(r"<node[^>]*>", xml)]
        for w in words:
            for node in nodes:
                if w not in node:
                    continue
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
                if not b:
                    continue
                x1, y1, x2, y2 = (int(b.group(i)) for i in range(1, 5))
                if x2 > x1 and y2 > y1:
                    return w, (x1 + x2) // 2, (y1 + y2) // 2
        return None

    @staticmethod
    def _find_center(xml: str, words) -> tuple[int, int] | None:
        hit = AccessibleScrcpySession._find_hit(xml, words)
        return (hit[1], hit[2]) if hit else None

    def _tap(self, x: int, y: int) -> None:
        # 同一位置 2.5 秒内不重复点，避免把按钮点成连击。
        last_at, lx, ly = self._last_tap
        if lx == x and ly == y and time.time() - last_at < 2.5:
            return
        self._last_tap = (time.time(), x, y)
        self._shell("input", "tap", str(x), str(y), timeout=15)

    def _ui_driver(self, deadline: float) -> None:
        """自动完成「录屏授权」与「连接电脑」。

        两条铁律（实测得来）：
          1. 授权页（systemui / MediaProjection）优先处理，先「整个屏幕」再「允许」；
          2. App 重建后「连接电脑」必须重新点 —— 不能记成一次性的，
             否则第二次重试永远卡在等连接。
        """
        done: set[str] = set()
        last_connect = 0.0
        while not self._ui_stop and time.time() < deadline and not self.connected:
            xml = self._dump_ui()
            if not xml:
                time.sleep(0.6)
                continue
            is_projection = ("MediaProjection" in xml
                             or "录制" in xml or "錄製" in xml or "投射" in xml)
            if is_projection:
                hit = self._find_hit(xml, [w for w in UI_ALLOW_WORDS if w not in done])
                if hit:
                    word, x, y = hit
                    self._tap(x, y)
                    done.add(word)
                    print(f"[accessible] 自动点录屏授权「{word}」({x}, {y})", flush=True)
                    time.sleep(1.2)
                    if "允许" in done or "允許" in done or "立即開始" in done:
                        # 授权已给，把 App 拉回前台，准备点发起按钮。
                        self._shell("am", "start", "-n", ASSISTANT_ACTIVITY,
                                    "-e", "serial", self.serial, timeout=20)
                        time.sleep(1.5)
                    continue
                time.sleep(0.6)
                continue
            hit = self._find_hit(xml, UI_CONNECT_WORDS)
            if hit and time.time() - last_connect > 6.0:
                word, x, y = hit
                self._tap(x, y)
                last_connect = time.time()
                print(f"[accessible] 自动点「{word}」({x}, {y})", flush=True)
                time.sleep(2.0)
                continue
            time.sleep(0.6)

    def _start_ui_driver(self) -> None:
        self._ui_stop = False
        deadline = time.time() + UI_DRIVE_SECONDS
        self._ui_thread = threading.Thread(target=self._ui_driver, args=(deadline,),
                                           daemon=True, name=f"a11y-ui-{self.serial}")
        self._ui_thread.start()

    # ---- 启动：监听 → 重建 App → reverse → 广播 → 收视频/控制/app_msg ----

    def start(self, timeout: float = ACCESSIBLE_CONNECT_TIMEOUT) -> bool:
        self.stop()

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", 0))
        listener.listen(4)
        listener.settimeout(1.0)
        self._listener = listener
        self.port = int(listener.getsockname()[1])

        # adb 反向隧道（对齐汇城 kill_reverse_all_ports 的建立侧）。
        # 建不上也不致命：内网直连仍可工作，手机连 127.0.0.1 也能通。
        self._reverse_ports = []
        try:
            subprocess.run(
                [self.adb, "-s", self.serial, "reverse", "--remove", f"tcp:{self.port}"],
                capture_output=True, text=True, timeout=10)
            r = subprocess.run(
                [self.adb, "-s", self.serial, "reverse", f"tcp:{self.port}", f"tcp:{self.port}"],
                capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                self._reverse_ports.append(self.port)
                print(f"[accessible] reverse tcp:{self.port} ok", flush=True)
            else:
                print("[accessible] reverse 建失败，继续用内网直连："
                      f"{((r.stderr or r.stdout) or '').strip()[:120]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[accessible] reverse 异常，继续用内网直连：{type(exc).__name__}", flush=True)

        # 广播地址：实测投屏助手只认局域网 IP（曾改成 127.0.0.1 走
        # reverse 隧道，结果 App 一次都不拨号；局域网 IP 则多次连上）。
        # reverse 隧道照建不误（不断开，留作备用），但广播只发局域网 IP。
        ip = self.pc_ip or _local_ip()
        if not ip:
            self.last_error = "获取设备ip地址失败"
            self.stop()
            return False

        # 先完整重建 App：冷启动 → 无障碍重绑 → 录屏授权 → 等广播。
        self._restart_app()

        cmd = [
            self.adb, "-s", self.serial, "shell",
            f"am broadcast -a {SCRCPY_ACTION} -e serial {self.serial} "
            f"-e ip {ip} -e port {self.port} "
            f"-e useSoftEncoder {'true' if self.use_soft_encoder else 'false'} "
            f"--receiver-foreground",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if r.returncode != 0:
                self.last_error = (r.stderr or r.stdout or "下发投屏广播失败")[:200]
                self.stop()
                return False
        except subprocess.TimeoutExpired:
            self.last_error = "下发投屏广播超时"
            self.stop()
            return False
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"下发投屏广播异常: {type(exc).__name__}"
            self.stop()
            return False

        # 广播已发，App 会停在「连接电脑」；后台线程负责把按钮和授权点掉。
        self._start_ui_driver()

        deadline = time.time() + (timeout if timeout <= 2 else max(timeout, 30.0))
        video = None
        control = None
        pending: list = []
        for _ in range(12):
            if time.time() >= deadline:
                break
            if video is not None and control is not None:
                break
            item = self._accept_until(deadline)
            if item is None:
                break
            conn, addr = item
            # 先读最多 64 字节：凑不齐 64 的按 app_msg/探测处理，不断开；
            # 凑齐的走视频头判定。绝不按到达顺序猜身份。
            conn.settimeout(2.0)
            head = b""
            try:
                while len(head) < 64:
                    chunk = conn.recv(64 - len(head))
                    if not chunk:
                        break
                    head += chunk
            except socket.timeout:
                pass
            except OSError:
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            if len(head) < 64:
                if not head and control is None:
                    # scrcpy 控制连接不发言：连上后只等指令，一个字节都不发。
                    control = conn
                    print(f"[accessible] control 连接（0 字节日志）from {addr}", flush=True)
                else:
                    self._note_appmsg(conn, addr, head)
                continue
            name = head.rstrip(b"\x00").decode("utf-8", "replace")
            try:
                conn.settimeout(1.5)
                hdr = self._readn(conn, 12, 1.5)
            except Exception:
                if control is None:
                    control = conn
                    print(f"[accessible] control 连接（只发名字）: {name}", flush=True)
                else:
                    pending.append(conn)
                continue
            codec, width, height = struct.unpack(">III", hdr)
            if codec == 0x68323634 and 1 <= width <= 65535 and 1 <= height <= 65535:
                if video is None:
                    video = conn
                    self.device_name = name
                    self.codec, self.width, self.height = codec, width, height
                    print(f"[accessible] video 连接: {name} {width}x{height}", flush=True)
                else:
                    pending.append(conn)
            else:
                pending.append(conn)

        if video is None:
            self.last_error = self.last_error or "【投屏助手】未建立视频流（超时）"
            for conn in pending + ([control] if control else []):
                try:
                    conn.close()
                except OSError:
                    pass
            self.stop()
            return False
        # 视频有了但控制还没到：再等一会儿，避免 App 因缺控制通道自行断开。
        if control is None:
            item = self._accept_until(deadline + 5.0)
            if item is not None:
                control, _caddr = item
                # 控制连接不发任何数据，只需把 socket 留着。
                print("[accessible] control 连接（补收）", flush=True)

        for conn in pending:
            try:
                conn.close()
            except OSError:
                pass

        self._video_sock = video
        if control is not None:
            try:
                control.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            self._control_sock = control
            # 关键：App 冷启动时用自己很低的默认编码参数（实测只有 3.8fps、画质糊）。
            # 连上后立刻发一次 TYPE_RESET_OPTIONS（汇城私有消息 99）重置编码器，
            # 帧率实测可提到 11-15fps。失败不影响出图。
            try:
                ok = self.request_options(
                    max_size=self.max_size,
                    max_fps=self.max_fps,
                    bit_rate=self.bit_rate,
                )
                print(f"[accessible] 重置编码参数 ok={ok} "
                      f"{self.max_size}/{self.max_fps}/{self.bit_rate}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[accessible] 重置编码参数失败: {type(exc).__name__}", flush=True)

        self.connected = True
        self._running = True
        return True

    def _note_appmsg(self, conn, addr, raw: bytes) -> None:
        try:
            text = bytes(raw).decode("utf-8", "replace")
        except Exception:
            text = repr(raw)
        self.appmsg_raw = (text or "")[:512]
        print(f"[accessible] app_msg 疑似握手 from {addr}: {(text or '')[:200]!r}"
              "（不断开，等待后续协议）", flush=True)
        if self._appmsg_sock is None:
            self._appmsg_sock = conn
        else:
            try:
                conn.close()
            except OSError:
                pass

    def _accept_until(self, deadline: float):
        listener = self._listener
        if listener is None:
            return None
        while time.time() < deadline:
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return None
            conn.settimeout(10.0)
            return conn, addr
        return None

    # ---- 停止：只关自己的连接，不碰 adb forward ----

    def stop(self) -> None:
        self._ui_stop = True
        self._running = False
        self.connected = False
        with self._control_lock:
            sock, self._control_sock = self._control_sock, None
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        sock, self._video_sock = self._video_sock, None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        appmsg, self._appmsg_sock = self._appmsg_sock, None
        if appmsg is not None:
            try:
                appmsg.close()
            except OSError:
                pass
        # 清掉本会话建的 adb 反向隧道（对齐汇城 kill_reverse_all_ports 的单会话部分）。
        for port in list(getattr(self, "_reverse_ports", []) or []):
            try:
                subprocess.run(
                    [self.adb, "-s", self.serial, "reverse", "--remove", f"tcp:{int(port)}"],
                    capture_output=True, text=True, timeout=10)
            except Exception:
                pass
        self._reverse_ports = []
        listener, self._listener = self._listener, None
        if listener:
            try:
                listener.close()
            except OSError:
                pass

    def info(self) -> dict:
        base = super().info()
        base.update({
            "transport": "accessible",
            "pc_ip": self.pc_ip,
            "connected": self.connected,
            "last_error": self.last_error,
            "reverse_ports": list(getattr(self, "_reverse_ports", []) or []),
            "appmsg_preview": (getattr(self, "appmsg_raw", "") or "")[:200],
        })
        return base


def _local_ip() -> str:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            candidate = probe.getsockname()[0]
        finally:
            probe.close()
        if candidate and not candidate.startswith("127."):
            return candidate
    except Exception:
        pass
    try:
        candidate = socket.gethostbyname(socket.gethostname())
        if candidate and not candidate.startswith("127."):
            return candidate
    except Exception:
        pass
    return ""
