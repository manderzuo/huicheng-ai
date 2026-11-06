/* ===========================================================================
 * MSE 播放器 —— 用浏览器原生视频管线播放 scrcpy 的 fMP4 流
 *
 * 为什么用 MSE 而不是 WebCodecs / MJPEG：
 *   - WebCodecs：20 路软件解码，CPU 必爆，会花屏
 *   - MJPEG：每帧独立 JPEG，2fps 才不卡，看起来像 PPT
 *   - MSE：交给 <video> 元素，走系统 GPU 解码器，1080p/30fps 都不卡
 *
 * 服务端做的是「零拷贝换容器」（ffmpeg -c copy），不重新编码，
 * 所以服务端 CPU 也几乎为 0。这是唯一能同时满足
 * 「20 路 + 高清 + 流畅 + 低 CPU」的方案。
 * =========================================================================== */

(function () {
  'use strict';

  const CODEC = 'video/mp4; codecs="avc1.42E01E"';   // Baseline，兼容性最好
  const CODEC_HI = 'video/mp4; codecs="avc1.64001F"'; // High profile

  class MsePlayer {
    constructor(videoEl, serial) {
      this.video = videoEl;
      this.serial = serial;
      this.ws = null;
      this.ms = null;
      this.sb = null;
      this.queue = [];          // 待 append 的分片
      this.busy = false;        // sourceBuffer 正忙
      this.running = false;
      this.frames = 0;
      this.bytes = 0;
      this.reconnects = 0;
      this._t0 = 0;
      this.onFps = null;
      this.onError = null;
    }

    start() {
      if (this.running) return;
      this.running = true;
      this._t0 = performance.now();
      this._connect();
      this._startFpsCounter();
    }

    _connect() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${proto}//${location.host}/ws/fmp4/${this.serial}`;

      let ws;
      try {
        ws = new WebSocket(url);
      } catch (e) {
        this._err('ws create: ' + e.message);
        return;
      }
      ws.binaryType = 'arraybuffer';
      this.ws = ws;

      ws.onopen = () => {
        console.log(`[mse ${this.serial}] WS 已连接`);
        // 视频元素静音 + 自动播放（浏览器策略要求 muted 才能 autoplay）
        this.video.muted = true;
        this.video.playsInline = true;
        this.video.autoplay = true;
        this._initMse();
      };

      ws.onmessage = (ev) => {
        if (typeof ev.data === 'string') {
          try {
            const msg = JSON.parse(ev.data);
            if (msg.type === 'error') this._err(msg.message || '服务端错误');
            if (msg.type === 'ready') {
              console.log(`[mse ${this.serial}] 就绪 ${msg.width}x${msg.height}`);
            }
          } catch (e) {}
          return;
        }
        // 二进制 = fMP4 分片
        this._push(ev.data);
      };

      ws.onerror = () => {
        this._err('ws error');
      };

      ws.onclose = () => {
        console.warn(`[mse ${this.serial}] WS 关闭`);
        if (!this.running) return;
        this.reconnects++;
        // 指数退避，最多 5 秒
        const delay = Math.min(1000 * this.reconnects, 5000);
        setTimeout(() => {
          if (this.running) this._connect();
        }, delay);
      };
    }

    _initMse() {
      // 清理旧的
      if (this.ms) {
        try {
          if (this.ms.readyState === 'open' && this.sb && this.sb.updating) {
            // 让它在后台结束
          }
        } catch (e) {}
      }
      this.ms = null;
      this.sb = null;
      this.queue = [];
      this.busy = false;

      const ms = new MediaSource();
      this.ms = ms;
      this.video.src = URL.createObjectURL(ms);

      ms.addEventListener('sourceopen', () => {
        try {
          URL.revokeObjectURL(this.video.src);
        } catch (e) {}

        let sb = null;
        // 优先试 High profile，失败退回 Baseline
        for (const c of [CODEC_HI, CODEC]) {
          if (MediaSource.isTypeSupported(c)) {
            try {
              sb = ms.addSourceBuffer(c);
              console.log(`[mse ${this.serial}] 用 codec: ${c}`);
              break;
            } catch (e) {}
          }
        }
        if (!sb) {
          this._err('浏览器不支持 MSE/H264');
          return;
        }
        this.sb = sb;
        sb.mode = 'segments';

        sb.addEventListener('updateend', () => {
          this.busy = false;
          this._drain();
          // 控制缓冲：落后太多就丢弃
          this._trimBuffer();
        });
        sb.addEventListener('error', () => {
          this._err('sourceBuffer error');
        });

        this._drain();
        this.video.play().catch(() => {});
      }, { once: true });
    }

    _push(data) {
      if (!data || !data.byteLength) return;
      this.bytes += data.byteLength;
      this.frames++;
      this.queue.push(data);
      // 队列过长（解码跟不上）→ 丢弃最老的，保证低延迟
      if (this.queue.length > 40) {
        const drop = this.queue.length - 40;
        this.queue.splice(0, drop);
      }
      this._drain();
    }

    _drain() {
      if (!this.sb || this.busy || !this.queue.length) return;
      if (this.ms && this.ms.readyState !== 'open') return;
      const seg = this.queue.shift();
      try {
        this.busy = true;
        this.sb.appendBuffer(seg);
      } catch (e) {
        this.busy = false;
        // QuotaExceeded：清掉老缓冲再试
        if (e && e.name === 'QuotaExceededError') {
          this._trimBuffer(true);
          this.queue.unshift(seg);
        } else {
          console.warn(`[mse ${this.serial}] append 失败: ${e && e.message}`);
        }
      }
    }

    _trimBuffer(force) {
      if (!this.sb || this.sb.updating || this.busy) return;
      try {
        const v = this.video;
        if (!v || !isFinite(v.currentTime)) return;
        // 保留最近 3 秒，避免内存无限增长
        const keep = 3;
        if (v.buffered.length) {
          const end = v.buffered.end(v.buffered.length - 1);
          const start = v.buffered.start(0);
          if (end - start > keep + 2) {
            const cut = end - keep;
            if (cut > start && (!this.sb.updating)) {
              this.sb.remove(start, cut);
            }
          }
        }
        // 落后太多就跳到最新
        if (v.buffered.length && !force) {
          const end = v.buffered.end(v.buffered.length - 1);
          if (end - v.currentTime > 3) {
            v.currentTime = end - 0.5;
          }
        }
      } catch (e) {}
    }

    _startFpsCounter() {
      if (this._fpsTimer) clearInterval(this._fpsTimer);
      let last = 0;
      this._fpsTimer = setInterval(() => {
        if (!this.running) return;
        const d = this.frames - last;
        last = this.frames;
        const fps = Math.round(d * 2);   // 500ms 采样 → 每秒
        if (this.onFps) this.onFps(fps);
      }, 500);
    }

    _err(msg) {
      console.warn(`[mse ${this.serial}] ${msg}`);
      if (this.onError) this.onError(msg);
    }

    stop() {
      this.running = false;
      if (this._fpsTimer) {
        clearInterval(this._fpsTimer);
        this._fpsTimer = null;
      }
      if (this.ws) {
        try { this.ws.close(); } catch (e) {}
        this.ws = null;
      }
      if (this.ms && this.ms.readyState === 'open') {
        try { this.ms.endOfStream(); } catch (e) {}
      }
      this.ms = null;
      this.sb = null;
      this.queue = [];
    }

    stats() {
      return {
        serial: this.serial,
        frames: this.frames,
        bytes: this.bytes,
        queue: this.queue.length,
        reconnects: this.reconnects,
        readyState: this.video ? this.video.readyState : -1,
        buffered: (() => {
          try {
            if (this.video && this.video.buffered.length) {
              return +(this.video.buffered.end(this.video.buffered.length - 1) -
                       this.video.buffered.start(0)).toFixed(2);
            }
          } catch (e) {}
          return 0;
        })(),
      };
    }
  }

  window.MsePlayer = MsePlayer;

  /* ---------------------------------------------------------------------
   * 卡片播放器池：每张卡片一个 MsePlayer，懒启动（进入视口才连）
   * ------------------------------------------------------------------- */
  const pool = new Map();

  window.attachMse = function (serial, videoEl) {
    if (pool.has(serial)) {
      const old = pool.get(serial);
      if (old.video === videoEl) return old;
      try { old.stop(); } catch (e) {}
      pool.delete(serial);
    }
    const p = new MsePlayer(videoEl, serial);
    p.onFps = (f) => {
      const el = videoEl.parentElement && videoEl.parentElement.querySelector('.card-fps');
      if (el) el.textContent = f > 0 ? f + ' fps' : '';
    };
    p.start();
    pool.set(serial, p);
    return p;
  };

  window.detachMse = function (serial) {
    const p = pool.get(serial);
    if (p) {
      try { p.stop(); } catch (e) {}
      pool.delete(serial);
    }
  };

  window.mseStats = function () {
    const out = [];
    pool.forEach((p, s) => out.push(p.stats()));
    return out;
  };

  window.msePool = pool;

  console.log('[mse] 播放器已就绪');
})();
