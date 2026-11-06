"""进程内 H.264 → JPEG 解码器（PyAV / libavcodec）。

═══ 为什么需要这个模块 ═══

旧方案：每台设备起一个 `ffmpeg.exe` 子进程
    20 台 = 20 个进程 = 20 套内存空间 + 20 个线程池 + 20 组管道
    实测只能跑到 2fps，再高就卡死，因为进程间 IO 和上下文切换吃光了 CPU。

新方案：所有解码器跑在同一个 Python 进程里，直接调 libavcodec
    这正是汇城 touping.exe 的做法（它静态导入 avcodec-60.dll 的
    avcodec_find_decoder / avcodec_send_packet / avcodec_receive_frame，
    以及 avcodec_find_encoder / avcodec_send_frame / avcodec_receive_packet）。

    20 路只是同一个进程里的 20 个 CodecContext，没有进程开销。

═══ 关键细节 ═══

1. H.264 是流式编码，必须用 `CodecContext.parse()` 按 Annex-B 起始码切包，
   不能把任意字节块直接丢给 decode()。
2. SPS/PPS（config 包）必须先喂，否则解码器无法初始化。
3. MJPEG 编码器只接受 `yuvj420p`（full-range YUV），
   而 H.264 解出来通常是 `yuv420p`（limited-range），必须 reformat。
4. `-q:v` 对应 PyAV 的 `ctx.options = {"q": ...}` 或 `ctx.qmin/qmax`。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

try:
    import av
    from av.codec import CodecContext
    from av.video.reformatter import ColorRange, Colorspace, VideoReformatter

    _HAS_AV = True
except Exception:  # noqa: BLE001
    av = None  # type: ignore
    CodecContext = None  # type: ignore
    ColorRange = None  # type: ignore
    Colorspace = None  # type: ignore
    _HAS_AV = False


def av_available() -> bool:
    return _HAS_AV


class AvJpegDecoder:
    """一台设备一个实例：吃 H.264，吐 JPEG。

    线程安全：feed() 由读线程调用，read_jpeg() 由消费线程调用，
    内部用锁保护编解码上下文（libavcodec 的 CodecContext 不是线程安全的）。
    """

    def __init__(
        self,
        width: int = 360,
        height: int = 720,
        quality: int = 8,
        fps: int = 15,
        on_jpeg: Optional[Callable[[bytes], None]] = None,
    ) -> None:
        self.width = width
        self.height = height
        self.quality = quality      # 2=最好 31=最差，和 ffmpeg -q:v 一致
        self.target_fps = fps
        self.on_jpeg = on_jpeg

        self._lock = threading.Lock()
        self._dec = None
        self._enc = None
        self._reformatter = VideoReformatter() if _HAS_AV else None
        self._last_emit = 0.0
        self._min_interval = 1.0 / max(1, fps)
        self._count = 0
        self._decoded_count = 0
        self._decode_seconds = 0.0
        self._encode_seconds = 0.0
        self._decode_errors = 0
        self._encode_errors = 0
        self._errors = 0
        self._started_at = 0.0
        self._ready = False

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> bool:
        """创建解码器 / 编码器上下文。"""
        if not _HAS_AV:
            return False
        dec = enc = None
        try:
            dec = CodecContext.create("h264", "r")
            dec.flags |= 0x00000100   # AV_CODEC_FLAG_LOW_DELAY
            # 低延迟：不缓冲 B 帧，来一帧解一帧
            dec.options = {"flags": "low_delay"}

            enc = CodecContext.create("mjpeg", "w")
            enc.width = self.width
            enc.height = self.height
            enc.pix_fmt = "yuvj420p"     # JPEG 必须用 full-range
            enc.options = {
                "q": str(self.quality),
                "huffman": "optimal",
            }
            enc.qmin = self.quality
            enc.qmax = self.quality
            enc.open()

            self._dec = dec
            self._enc = enc
            self._ready = True
            self._started_at = time.time()
            return True
        except Exception:  # noqa: BLE001
            self._ready = False
            # 创建途中失败时上下文尚未挂到 self，必须释放局部资源。
            for codec in (enc, dec):
                try:
                    if codec is not None:
                        codec.close()
                except Exception:
                    pass
            return False

    def stop(self) -> None:
        with self._lock:
            self._ready = False
            for c in (self._dec, self._enc):
                try:
                    if c is not None:
                        c.close()
                except Exception:
                    pass
            self._dec = None
            self._enc = None

    # ------------------------------------------------------------------ 输入

    def feed(self, data: bytes) -> None:
        """喂一段 H.264 数据（可以是一个或多个 NAL 单元，含起始码）。"""
        if not self._ready or not data:
            return

        # 限流：只在需要输出时才解码（省 CPU）
        now = time.monotonic()
        if self.target_fps > 0 and (now - self._last_emit) < self._min_interval:
            # 仍然要喂给解码器，否则参考帧会断
            # 但跳过 decode 会破坏状态，所以这里只跳过后面的编码
            pass

        with self._lock:
            if self._dec is None:
                return
            try:
                # parse() 会把字节流切成完整 AVPacket（处理起始码 / 分片 NAL）
                for packet in self._dec.parse(data):
                    self._decode_packet(packet, now)
            except Exception:  # noqa: BLE001
                self._errors += 1
                if self._errors <= 3:
                    pass

    def feed_access_unit(self, data: bytes) -> None:
        """scrcpy已给出完整编码包，直接解包，避免parse等下一包才输出尾帧。

        仅供保持完整AU边界的调用者；任意分片仍应使用feed()。
        """
        if not self._ready or not data:
            return
        with self._lock:
            if self._dec is None:
                return
            try:
                self._decode_packet(av.Packet(data), time.monotonic())
            except Exception:
                self._errors += 1

    def _decode_packet(self, packet, now: float) -> None:
        """解一个包，按需编码成 JPEG。"""
        began = time.perf_counter()
        try:
            frames = self._dec.decode(packet)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            self._decode_errors += 1
            return
        finally:
            self._decode_seconds += time.perf_counter() - began

        self._decoded_count += len(frames)
        for frame in frames:
            # 限流：距上次输出不够 min_interval 就丢掉这一帧
            if self.target_fps > 0 and (now - self._last_emit) < self._min_interval:
                continue
            began = time.perf_counter()
            try:
                jpeg = self._encode_frame(frame)
            except Exception:  # noqa: BLE001
                self._encode_errors += 1
                continue
            finally:
                self._encode_seconds += time.perf_counter() - began
            if not jpeg:
                self._encode_errors += 1
                continue
            self._last_emit = now
            self._count += 1
            if self.on_jpeg is not None:
                try:
                    self.on_jpeg(jpeg)
                except Exception:  # noqa: BLE001
                    pass

    def _encode_frame(self, frame) -> bytes:
        """把解码出的 AVFrame 转成 JPEG 字节。"""
        if self._enc is None:
            return b""

        # 尺寸不一致时（流的真实尺寸和声明不同）调整编码器
        if self._enc.width != frame.width or self._enc.height != frame.height:
            old_encoder = self._enc
            enc = CodecContext.create("mjpeg", "w")
            enc.width = frame.width
            enc.height = frame.height
            enc.pix_fmt = "yuvj420p"
            enc.options = {"q": str(self.quality), "huffman": "optimal"}
            enc.qmin = self.quality
            enc.qmax = self.quality
            enc.open()
            self._enc = enc
            self.width, self.height = frame.width, frame.height
            try:
                old_encoder.close()
            except Exception:
                pass

        # 直接YUV范围转换；每路复用转换器并限制单线程，避免20路
        # 各自自动创建线程池及YUV→RGB→YUV的重复转换。
        # 未声明范围的H264 YUV按limited处理；明确full-range则保持full。
        src_range = ColorRange.JPEG if frame.color_range == 2 else ColorRange.MPEG
        out = self._reformatter.reformat(
            frame, width=frame.width, height=frame.height, format="yuvj420p",
            src_color_range=src_range, dst_color_range=ColorRange.JPEG,
            threads=1,
        )

        out.pts = None

        try:
            packets = self._enc.encode(out)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return b""

        if not packets:
            return b""
        # MJPEG 每帧就是一个完整的 JPEG，通常只有一个 packet
        return bytes(packets[0])

    # ------------------------------------------------------------------ 统计

    def stats(self) -> dict:
        return {
            "frames": self._count,
            "decoded_frames": self._decoded_count,
            "decode_seconds": self._decode_seconds,
            "encode_seconds": self._encode_seconds,
            "decode_errors": self._decode_errors,
            "encode_errors": self._encode_errors,
            "errors": self._errors,
            "fps_target": self.target_fps,
            "quality": self.quality,
            "size": f"{self.width}x{self.height}",
            "ready": self._ready,
            "uptime": round(time.time() - self._started_at, 1) if self._started_at else 0,
        }


class PullAvDecoder(AvJpegDecoder):
    """带队列的版本：解码线程直接把 JPEG 塞进队列，消费者取最新的。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._latest: Optional[bytes] = None
        self._seq = 0
        self._cond = threading.Condition()

    def _on_jpeg_internal(self, jpeg: bytes) -> None:
        with self._cond:
            self._latest = jpeg
            self._seq += 1
            self._cond.notify_all()

    def start(self) -> bool:
        ok = super().start()
        if ok:
            self.on_jpeg = self._on_jpeg_internal
        return ok

    def read_jpeg(self, timeout: float = 0.3) -> Optional[bytes]:
        """取最新一帧（没有新帧就等）。"""
        with self._cond:
            if self._seq == 0:
                self._cond.wait(timeout)
            if self._latest is None:
                return None
            j = self._latest
            self._latest = None
            return j

    def latest(self) -> Optional[bytes]:
        with self._cond:
            return self._latest
