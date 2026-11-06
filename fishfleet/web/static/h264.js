/**
 * FishFleet H.264 / WebCodecs 播放器。
 * 汇城仅取得部分方法名与协议证据，不能视为已复刻其函数实现。
 * 性能及实际解码后端必须通过独立运行观测确定。
 */

// ★ 版本标记：用于确认浏览器加载的是最新脚本（排查缓存）
window.__H264_BUILD = '2026-10-28-recovery-v2';

class H264Player {
  constructor(canvas) {
    this.canvas = canvas;
    this.gl = null;
    this.program = null;
    this.texture = null;
    this.vbo = null;

    this.decoder = null;
    this.ws = null;
    this.running = false;
    this.width = 0;
    this.height = 0;

    this.sps = null;
    this.pps = null;

    this.frameCount = 0;
    this.fps = 0;
    this._fpsT0 = 0;
    this._fpsN = 0;
    this._vlog = 0;
    this._ts = 0;
    this._outN = 0;
    this._useAnnexB = false;

    this.onFps = null;
    this.onError = null;

    // ★ 对齐汇城的事件系统（从 WebView2 Code Cache 挖出的真实实现）：
    //   ON_WCS_DECODE_ERROR → onWcsDecodeError → reloadDecoder
    this.onDecodeError = null;     // WebCodecs 解码错误回调
    this.onFirstFrame = null;      // ON_RENERED_FIRST_FRAME
    this.onReloadDecoder = null;   // reloadDecoder 回调
    this._gotKey = false;          // ★ 关键帧门闸：收到 IDR 前丢弃所有帧
    // ★★ 对齐汇城：播放器实例长期存活，切换设备只切可见性（initVisable/cancelVisable）
    //    不销毁、不重连，所以切回来瞬间就有画面（解码器一直在跑）
    this.active = false;
    this._reloadCount = 0;         // 重载解码器次数
    this._firstFrameFired = false;

    // ★ 主用 canvas 2D：drawImage(VideoFrame) 会自动做 YUV→RGB 转换，
    //   而且 Chrome 内部同样走 GPU，性能和 WebGL 相当但不会颜色错乱。
    //   （WebGL 直传 VideoFrame 需要 YUV 三平面着色器，实现复杂易错）
    this.ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });
    this._useGL = false;
  }

  // ==================== WebGL 初始化 ====================

  _initGL() {
    const opts = {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      desynchronized: true,
      preserveDrawingBuffer: false,
      powerPreference: 'high-performance',
    };
    const gl = this.canvas.getContext('webgl', opts)
      || this.canvas.getContext('experimental-webgl', opts);
    if (!gl) {
      this._err('WebGL 不可用');
      return;
    }
    this.gl = gl;

    const vs = `
      attribute vec2 a_pos;
      attribute vec2 a_uv;
      varying vec2 v_uv;
      void main() {
        gl_Position = vec4(a_pos, 0.0, 1.0);
        v_uv = a_uv;
      }`;

    const fs = `
      precision mediump float;
      uniform sampler2D u_tex;
      varying vec2 v_uv;
      void main() {
        gl_FragColor = texture2D(u_tex, v_uv);
      }`;

    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        this._err('shader: ' + gl.getShaderInfoLog(s));
        return null;
      }
      return s;
    };

    const v = compile(gl.VERTEX_SHADER, vs);
    const f = compile(gl.FRAGMENT_SHADER, fs);
    if (!v || !f) return;

    const p = gl.createProgram();
    gl.attachShader(p, v);
    gl.attachShader(p, f);
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      this._err('link: ' + gl.getProgramInfoLog(p));
      return;
    }
    this.program = p;
    gl.useProgram(p);

    // 全屏四边形（UV 的 Y 翻转：视频纹理原点在左上）
    this.vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -1, -1, 0, 1,
       1, -1, 1, 1,
      -1,  1, 0, 0,
       1,  1, 1, 0,
    ]), gl.STATIC_DRAW);

    const aPos = gl.getAttribLocation(p, 'a_pos');
    const aUV = gl.getAttribLocation(p, 'a_uv');
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 16, 0);
    gl.enableVertexAttribArray(aUV);
    gl.vertexAttribPointer(aUV, 2, gl.FLOAT, false, 16, 8);

    this.texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);

    console.log('[h264] WebGL 渲染器已就绪', gl.getParameter(gl.VERSION));
  }

  // ==================== 渲染一帧 ====================

  _render(frame) {
    // ★ 非当前显示的设备：解码继续（保持解码器状态），但不画到画布
    if (!this.active) return false;

    // canvas 2D 路径：drawImage 自动处理 YUV→RGB，GPU 加速
    const ctx = this.ctx;
    if (!ctx) return false;

    const w = frame.displayWidth || frame.codedWidth;
    const h = frame.displayHeight || frame.codedHeight;

    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
      if (window.fitStageCanvas) window.fitStageCanvas();
    }
    ctx.drawImage(frame, 0, 0);
    this.renderCount = (this.renderCount || 0) + 1;
    this._lastRender = performance.now();
    return true;
  }

  /** 取 Annex-B 数据第一个 NAL 类型（跳过 3/4 字节起始码） */
  static nalType(data) {
    const n = data.length;
    if (n < 4) return -1;
    if (data[0] === 0 && data[1] === 0 && data[2] === 0 && data[3] === 1) {
      return n > 4 ? (data[4] & 0x1f) : -1;
    }
    if (data[0] === 0 && data[1] === 0 && data[2] === 1) {
      return n > 3 ? (data[3] & 0x1f) : -1;
    }
    return -1;
  }

  /** 是否 IDR 关键帧 */
  static isKeyframe(data) {
    // 一个 access unit 可由 AUD/SEI/SPS/PPS 开头，必须扫描所有 NAL。
    for (let i = 0; i + 3 < data.length; i++) {
      if (data[i] !== 0 || data[i + 1] !== 0) continue;
      let h = -1;
      if (data[i + 2] === 1) h = i + 3;
      else if (data[i + 2] === 0 && data[i + 3] === 1) h = i + 4;
      if (h >= 0 && h < data.length && (data[h] & 31) === 5) return true;
    }
    return false;
  }

  // ==================== Annex-B → AVCC ====================

  static annexBToAVCC(data) {
    const out = [];
    let i = 0;
    const n = data.length;
    const isSC3 = (p) => p + 2 < n && data[p] === 0 && data[p + 1] === 0 && data[p + 2] === 1;
    const isSC4 = (p) => p + 3 < n && data[p] === 0 && data[p + 1] === 0 && data[p + 2] === 0 && data[p + 3] === 1;

    while (i < n) {
      let scLen = 0;
      if (isSC4(i)) scLen = 4;
      else if (isSC3(i)) scLen = 3;
      if (!scLen) { i++; continue; }

      const start = i + scLen;
      let j = start;
      while (j < n && !isSC4(j) && !isSC3(j)) j++;

      const nal = data.subarray(start, j);
      if (nal.length > 0) {
        const len = nal.length;
        out.push((len >>> 24) & 0xff, (len >>> 16) & 0xff, (len >>> 8) & 0xff, len & 0xff);
        out.push(nal);
      }
      i = j;
    }

    let total = 0;
    for (const p of out) total += (typeof p === 'number') ? 1 : p.length;
    const buf = new Uint8Array(total);
    let off = 0;
    for (const p of out) {
      if (typeof p === 'number') buf[off++] = p;
      else { buf.set(p, off); off += p.length; }
    }
    return buf;
  }

  static splitConfig(cfg) {
    const nals = [];
    let i = 0;
    const n = cfg.length;
    const isSC3 = (p) => p + 2 < n && cfg[p] === 0 && cfg[p + 1] === 0 && cfg[p + 2] === 1;
    const isSC4 = (p) => p + 3 < n && cfg[p] === 0 && cfg[p + 1] === 0 && cfg[p + 2] === 0 && cfg[p + 3] === 1;

    while (i < n) {
      let scLen = 0;
      if (isSC4(i)) scLen = 4;
      else if (isSC3(i)) scLen = 3;
      if (!scLen) { i++; continue; }
      const start = i + scLen;
      let j = start;
      while (j < n && !isSC4(j) && !isSC3(j)) j++;
      nals.push(cfg.subarray(start, j));
      i = j;
    }

    let sps = null, pps = null;
    for (const nal of nals) {
      if (!nal.length) continue;
      const t = nal[0] & 0x1f;
      if (t === 7) sps = nal;
      else if (t === 8) pps = nal;
    }
    return { sps, pps };
  }

  static buildAvcC(sps, pps) {
    const parts = [];
    parts.push(0x01);
    parts.push(sps[1], sps[2], sps[3]);
    parts.push(0xff);
    parts.push(0xe1);
    parts.push((sps.length >> 8) & 0xff, sps.length & 0xff);
    for (const b of sps) parts.push(b);
    parts.push(0x01);
    parts.push((pps.length >> 8) & 0xff, pps.length & 0xff);
    for (const b of pps) parts.push(b);
    return new Uint8Array(parts);
  }

  // ==================== 启动 ====================

  start(serial) {
    // ★ 先停旧的（stop 会清掉旧定时器），再重建，否则新定时器会被自己删掉
    this.stop();

    this.running = true;
    this.serial = serial;
    this._vlog = 0;
    this._outN = 0;
    this._ts = 0;
    this.frameCount = 0;
    this._gotKey = false;          // ★ 新连接必须等 IDR
    this._firstFrameFired = false;
    this._lastReload = 0;
    this.active = true;      // 新建即激活
    this._fpsT0 = performance.now();

    // 分别观察输入/输出；静态页面无输入不视为故障。
    // 持续有输入但无输出才尝试恢复。汇城同名函数体尚未取得。
    this._lastF = 0;
    this._lastFT = Date.now();
    this._iframeLast = 0;
    this._iframeReqs = 0;
    this._iframeAcks = 0;
    this._received = 0;
    this._submitted = 0;
    this._lastReceive = 0;
    if (this._stuckTimer) { clearInterval(this._stuckTimer); }
    this._stuckTimer = setInterval(() => {
      if (!this.running) return;
      const now = Date.now();
      const f = this.frameCount || 0;
      if (f > this._lastF) {
        this._lastF = f;
        this._lastFT = now;
        return;
      }
      const idle = now - this._lastFT;
      // 静态页面没有输入属正常，不能仅因无输出每3秒重置手机编码器。
      // 仅在持续收到新数据、却迟迟没有输出时尝试恢复。
      const receiving = this._lastReceive && performance.now() - this._lastReceive < 2000;
      if (receiving && idle > 5000 && now - this._iframeLast > 10000) {
        this._iframeLast = now;
        this.reloadDecoder();
        this.requestIframe();
      }
    }, 1500);
    this._fpsN = 0;

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/h264/${serial}`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'ready') {
          this.width = msg.width;
          this.height = msg.height;
          this._initDecoder();
        } else if (msg.type === 'error') {
          this._err(msg.message);
        } else if (msg.type === 'iframe_ack') {
          this._iframeAcks++;
          this._iframeAck = msg;
        }
        return;
      }

      const buf = new Uint8Array(ev.data);
      if (buf.length < 2) return;
      const kind = buf[0];
      const payload = buf.subarray(1);

      if (kind === 0x43) {          // 'C' = SPS/PPS
        const { sps, pps } = H264Player.splitConfig(payload);
        if (sps && pps) {
          const equal = (a, b) => a && b && a.length === b.length && a.every((v, i) => v === b[i]);
          const changed = !equal(this.sps, sps) || !equal(this.pps, pps);
          this.sps = sps;
          this.pps = pps;
          if (changed || !this.decoder || this.decoder.state !== 'configured') this._initDecoder();
        }
        return;
      }

      if (kind !== 0x56) return;
      this._received++;
      this._lastReceive = performance.now();
      if (this.decoder && this.decoder.state === 'configured') {
        // ★ 解码队列保护：20 路并发时，某一路积压会拖慢所有路。
        if (this.decoder.decodeQueueSize > 8) {
          if (!H264Player.isKeyframe(payload)) {
            this._dropped = (this._dropped || 0) + 1;
            // ★★ 关键修复（参考汇城 reloadDecoder 语义）：
            //    丢掉的是 delta 帧 → 后续帧参考链可能已断。
            //    WebCodecs 对"参考帧缺失"不报 error，只静默输出花屏，
            //    所以必须立刻回到"等 IDR"，并主动向服务端要关键帧。
            if (this._gotKey) {
              this.reloadDecoder();
              this._breakCount = (this._breakCount || 0) + 1;
              if (this._breakCount <= 5 || this._breakCount % 50 === 0) {
                console.warn(`[h264] ${this.serial} 丢参考帧，等 IDR（第 ${this._breakCount} 次）`);
              }
              this.requestIframe();
            }
            return false;
          }
          // 是 IDR → 放行，让它重置解码状态
          this._dropped = 0;
        }

        const isKey = H264Player.isKeyframe(payload);

        // ★★ 关键帧门闸（对齐汇城 reloadDecoder 语义）：
        //    解码器刚重建 / 刚连接时，必须先收到 IDR 才能开始解码，
        //    否则 delta 帧缺参考数据 → 花屏。
        if (!this._gotKey) {
          if (!isKey) return;          // 丢弃，等 IDR
          this._gotKey = true;
        }

        try {
          const data = this._useAnnexB ? payload : H264Player.annexBToAVCC(payload);
          this._submitted++;
          this.decoder.decode(new EncodedVideoChunk({
            type: isKey ? 'key' : 'delta',
            timestamp: (this._ts += 33333),
            data,
          }));
        } catch (e) {
          if (this._vlog < 3) {
            this._vlog++;
            console.error('[h264] decode:', e.message, 'key=', isKey, 'len=', payload.length);
          }
          // 送不进去也算解码错误
          this._onWcsDecodeError('decode-throw: ' + e.message);
        }
      }
    };

    ws.onerror = () => this._err('WebSocket 错误');
    ws.onclose = () => {
      if (this.running && this.ws === ws) this._reconnect();
    };
  }

  // ==================== 汇城式错误恢复 ====================

  /**
   * ON_WCS_DECODE_ERROR 处理器（对齐汇城 onWcsDecodeError）
   *
   * 解码器一旦出错就不可用，必须重建。重建后要等新的 IDR 才能继续，
   * 否则 delta 帧缺参考数据会一直花屏。
   */
  _onWcsDecodeError(msg) {
    this._lastDecodeError = String(msg);
    if (!this.running) return;
    const now = performance.now();

    // 防抖：2 秒内最多重载一次，避免疯狂重建
    if (this._lastReload && now - this._lastReload < 2000) return;
    this._lastReload = now;
    this._reloadCount++;

    console.warn(`[h264] ON_WCS_DECODE_ERROR (${this._reloadCount}): ${msg}`);
    if (this.onDecodeError) this.onDecodeError(msg);

    this.reloadDecoder();
  }

  /**
   * reloadDecoder —— 对齐汇城的同名函数
   *
   * 1. 关闭坏掉的解码器
   * 2. 清空关键帧门闸（等下一个 IDR）
   * 3. 用缓存的 SPS/PPS 重新 configure
   * 4. 在收到 IDR 之前，所有 delta 帧一律丢弃
   */
  reloadDecoder() {
    if (this.onReloadDecoder) this.onReloadDecoder(this._reloadCount);

    if (this.decoder) {
      try { this.decoder.close(); } catch (e) {}
      this.decoder = null;
    }
    this._gotKey = false;      // ★ 重新等 IDR
    this._ts = 0;
    this.requestIframe();

    // SPS/PPS 还在就用它重建；否则等新的 config 包
    if (this.sps && this.pps) {
      this._initDecoder();
    }
  }

  _initDecoder() {
    if (this.decoder) {
      try { this.decoder.close(); } catch (e) {}
      this.decoder = null;
    }
    if (!this.sps || !this.pps) return;

    const avcC = H264Player.buildAvcC(this.sps, this.pps);
    const hex = (n) => n.toString(16).padStart(2, '0');
    const codec = `avc1.${hex(this.sps[1])}${hex(this.sps[2])}${hex(this.sps[3])}`;

    this.decoder = new VideoDecoder({
      output: (frame) => {
        // ★ player 已停止 → 丢弃排队中的旧帧，避免画到新设备的画布上
        if (!this.running) {
          try { frame.close(); } catch (e) {}
          return;
        }
        this._outN++;
        try {
          this._render(frame);
        } catch (e) {
          if (this._outN < 3) console.error('[h264] render:', e.message);
        }
        try { frame.close(); } catch (e) {}

        if (!this._firstFrameFired) {
          this._firstFrameFired = true;
          if (this.onFirstFrame) this.onFirstFrame();   // ON_RENERED_FIRST_FRAME
        }

        this.frameCount++;
        this._fpsN++;
        const now = performance.now();
        if (now - this._fpsT0 >= 1000) {
          this.fps = Math.round(this._fpsN * 1000 / (now - this._fpsT0));
          this._fpsN = 0;
          this._fpsT0 = now;
          if (this.onFps) this.onFps(this.fps);
        }
      },
      // ★ 对齐汇城 ON_WCS_DECODE_ERROR → onWcsDecodeError → reloadDecoder
      error: (e) => this._onWcsDecodeError(e.message),
    });

    try {
      this.decoder.configure({
        codec,
        codedWidth: this.width || 408,
        codedHeight: this.height || 720,
        description: avcC,
        // 实时投屏优先及时输出，避免切到静态页面后末尾帧滞留。
        // 此选项不等于应用层丢参考帧；是否改善由逐台回归验证。
        optimizeForLatency: true,
      });
      this._gotKey = false;    // ★ configure 之后必须等 IDR 才能解码
    } catch (e) {
      this._err('configure: ' + e.message);
    }
  }

  _err(msg) {
    console.error('[h264]', msg);
    if (this.onError) this.onError(msg);
  }

  _reconnect() {
    const s = this.serial;
    this.stop();
    setTimeout(() => { try { this.start(s); } catch (e) {} }, 600);
  }

  /**
   * 主动请求关键帧（IDR）。
   *
   * 参考汇城：画面静止或丢参考帧后，不能干等下一个 GOP ——
   * 服务端收到这条消息会调用 scrcpy 的 request_iframe，
   * 让手机立刻产出一个 IDR，前端拿到后即可干净恢复。
   */
  requestIframe() {
    try {
      const now = performance.now();
      if (this._requestSentAt && now - this._requestSentAt < 2500) return;
      if (this.ws && this.ws.readyState === 1) {
        this._requestSentAt = now;
        this.ws.send(JSON.stringify({ type: 'iframe' }));
        this._iframeReqs = (this._iframeReqs || 0) + 1;
      }
    } catch (e) {}
  }

  stop() {
    if (this._stuckTimer) { clearInterval(this._stuckTimer); this._stuckTimer = null; }
    this.running = false;
    if (this.ws) { try { this.ws.close(); } catch (e) {} this.ws = null; }
    if (this.decoder) { try { this.decoder.close(); } catch (e) {} this.decoder = null; }
    this.sps = this.pps = null;
  }
}

window.H264Player = H264Player;
