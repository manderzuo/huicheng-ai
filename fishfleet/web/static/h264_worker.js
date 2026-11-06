/**
 * FishFleet — H.264 解码 Worker
 *
 * ★ 对齐汇城的 initWebWorker / registerWorker / supportOffScreenCanvas：
 *   WebSocket + VideoDecoder + OffscreenCanvas 全部跑在 Worker 里，
 *   主线程只负责把 ImageBitmap 画到可见画布上，UI 永不卡顿。
 *
 * 主线程 → Worker:
 *   { type:'start', serial, host, width, height }
 *   { type:'stop' }
 *
 * Worker → 主线程:
 *   { type:'ready', width, height }
 *   { type:'frame', bmp }           ImageBitmap（transferable）
 *   { type:'error', message }
 *   { type:'stats', fps, frames, reloads }
 */

let ws = null;
let decoder = null;
let canvas = null;
let ctx = null;
let running = false;

let sps = null;
let pps = null;
let gotKey = false;
let ts = 0;
let frameCount = 0;
let reloadCount = 0;
let lastReload = 0;
let fpsN = 0;
let fpsT0 = 0;
let fps = 0;
let vlog = 0;

// ==================== 工具 ====================

function nalType(data) {
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

function annexBToAVCC(data) {
  const out = [];
  let i = 0;
  const n = data.length;
  const sc3 = (p) => p + 2 < n && data[p] === 0 && data[p + 1] === 0 && data[p + 2] === 1;
  const sc4 = (p) => p + 3 < n && data[p] === 0 && data[p + 1] === 0 && data[p + 2] === 0 && data[p + 3] === 1;

  while (i < n) {
    let len = 0;
    if (sc4(i)) len = 4;
    else if (sc3(i)) len = 3;
    if (!len) { i++; continue; }
    const start = i + len;
    let j = start;
    while (j < n && !sc4(j) && !sc3(j)) j++;
    const nal = data.subarray(start, j);
    if (nal.length > 0) {
      const L = nal.length;
      out.push((L >>> 24) & 0xff, (L >>> 16) & 0xff, (L >>> 8) & 0xff, L & 0xff);
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

function splitConfig(cfg) {
  const nals = [];
  let i = 0;
  const n = cfg.length;
  const sc3 = (p) => p + 2 < n && cfg[p] === 0 && cfg[p + 1] === 0 && cfg[p + 2] === 1;
  const sc4 = (p) => p + 3 < n && cfg[p] === 0 && cfg[p + 1] === 0 && cfg[p + 2] === 0 && cfg[p + 3] === 1;

  while (i < n) {
    let len = 0;
    if (sc4(i)) len = 4;
    else if (sc3(i)) len = 3;
    if (!len) { i++; continue; }
    const start = i + len;
    let j = start;
    while (j < n && !sc4(j) && !sc3(j)) j++;
    nals.push(cfg.subarray(start, j));
    i = j;
  }

  let s = null, p = null;
  for (const nal of nals) {
    if (!nal.length) continue;
    const t = nal[0] & 0x1f;
    if (t === 7) s = nal;
    else if (t === 8) p = nal;
  }
  return { sps: s, pps: p };
}

function buildAvcC(sps, pps) {
  const parts = [0x01, sps[1], sps[2], sps[3], 0xff, 0xe1,
    (sps.length >> 8) & 0xff, sps.length & 0xff];
  for (const b of sps) parts.push(b);
  parts.push(0x01, (pps.length >> 8) & 0xff, pps.length & 0xff);
  for (const b of pps) parts.push(b);
  return new Uint8Array(parts);
}

// ==================== 解码器 ====================

function buildDecoder() {
  if (decoder) {
    try { decoder.close(); } catch (e) {}
    decoder = null;
  }
  if (!sps || !pps) return;

  const avcC = buildAvcC(sps, pps);
  const hex = (n) => n.toString(16).padStart(2, '0');
  const codec = `avc1.${hex(sps[1])}${hex(sps[2])}${hex(sps[3])}`;

  decoder = new VideoDecoder({
    output: (frame) => {
      if (!running) { try { frame.close(); } catch (e) {} return; }
      try {
        if (canvas && ctx) {
          const w = frame.displayWidth || frame.codedWidth;
          const h = frame.displayHeight || frame.codedHeight;
          if (canvas.width !== w || canvas.height !== h) {
            canvas.width = w;
            canvas.height = h;
          }
          ctx.drawImage(frame, 0, 0);
          // ★ supportOffScreenCanvas：把画面 transfer 给主线程
          const bmp = canvas.transferToImageBitmap();
          self.postMessage({ type: 'frame', bmp }, [bmp]);
        }
      } catch (e) {
        if (vlog < 3) { vlog++; console.error('[worker] render:', e.message); }
      }
      try { frame.close(); } catch (e) {}

      frameCount++;
      fpsN++;
      const now = performance.now();
      if (now - fpsT0 >= 1000) {
        fps = Math.round(fpsN * 1000 / (now - fpsT0));
        fpsN = 0;
        fpsT0 = now;
        self.postMessage({ type: 'stats', fps, frames: frameCount, reloads: reloadCount });
      }
    },
    error: (e) => {
      if (!running) return;
      const now = performance.now();
      if (lastReload && now - lastReload < 2000) return;
      lastReload = now;
      reloadCount++;
      self.postMessage({ type: 'error', message: e.message });
      // reloadDecoder
      gotKey = false;
      ts = 0;
      if (sps && pps) buildDecoder();
    },
  });

  try {
    decoder.configure({
      codec,
      codedWidth: canvas ? canvas.width : 408,
      codedHeight: canvas ? canvas.height : 720,
      description: avcC,
      optimizeForLatency: true,
    });
    gotKey = false;
  } catch (e) {
    self.postMessage({ type: 'error', message: 'configure: ' + e.message });
  }
}

// ==================== WebSocket ====================

function initSocket(host, serial) {
  const proto = host.startsWith('https') ? 'wss' : 'ws';
  const url = `${proto}://${host}/ws/h264/${serial}`;
  ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => self.postMessage({ type: 'wsopen' });

  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      const m = JSON.parse(ev.data);
      if (m.type === 'ready') {
        if (canvas) { canvas.width = m.width || 408; canvas.height = m.height || 720; }
        self.postMessage({ type: 'ready', width: m.width, height: m.height });
      } else if (m.type === 'error') {
        self.postMessage({ type: 'error', message: m.message });
      }
      return;
    }

    const buf = new Uint8Array(ev.data);
    if (buf.length < 2) return;
    const kind = buf[0];
    const payload = buf.subarray(1);

    if (kind === 0x43) {   // 'C' SPS/PPS
      const { sps: s, pps: p } = splitConfig(payload);
      if (s && p) {
        sps = s;
        pps = p;
        buildDecoder();
      }
      return;
    }

    if (decoder && decoder.state === 'configured') {
      const isKey = nalType(payload) === 5;
      if (!gotKey) {
        if (!isKey) return;      // 关键帧门闸
        gotKey = true;
      }
      try {
        decoder.decode(new EncodedVideoChunk({
          type: isKey ? 'key' : 'delta',
          timestamp: (ts += 33333),
          data: annexBToAVCC(payload),
        }));
      } catch (e) {
        if (vlog < 3) { vlog++; console.error('[worker] decode:', e.message); }
      }
    }
  };

  ws.onerror = () => self.postMessage({ type: 'error', message: 'WebSocket 错误' });
  ws.onclose = () => self.postMessage({ type: 'wsclose' });
}

// ==================== 生命周期 ====================

self.onmessage = (e) => {
  const m = e.data;

  if (m.type === 'start') {
    running = true;
    gotKey = false;
    ts = 0;
    frameCount = 0;
    fpsN = 0;
    fpsT0 = performance.now();

    canvas = new OffscreenCanvas(m.width || 408, m.height || 720);
    ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });

    initSocket(m.host, m.serial);
  } else if (m.type === 'stop') {
    running = false;
    if (ws) { try { ws.close(); } catch (err) {} ws = null; }
    if (decoder) { try { decoder.close(); } catch (err) {} decoder = null; }
    sps = null;
    pps = null;
    canvas = null;
    ctx = null;
  }
};
