/* ============ FishFleet 前端（完整版） ============ */

const API = '';

const S = {
  devices: [],
  tags: { all: {}, untagged: {}, tags: [] },
  settings: {},
  selected: null,
  expanded: null,          // 就地放大的设备
  multi: new Set(),
  activeTag: 'all',
  ws: null,
  taskPollTimer: null,
  currentScript: null,
};

/* ---------- 工具 ---------- */
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const esc = (s) => {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
};
const fmtT = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';

function sb(msg) {
  const m = $('sb-msg');
  if (m) m.textContent = msg;
  const t = $('sb-time');
  if (t) t.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

async function get(url) {
  const r = await fetch(API + url);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

async function post(url, body) {
  const r = await fetch(API + url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

/* ============================================================
   设备
   ============================================================ */

async function loadDevices() {
  try {
    const data = await get('/api/devices');
    S.devices = data.devices;
    $('stat-online').textContent = `${data.online}/${data.total}`;
    await loadTags();
    renderGrid();
  } catch (e) {
    sb('加载设备失败: ' + e.message);
  }
}

async function loadTags() {
  try {
    S.tags = await get('/api/tags');
    renderTagbar();
  } catch (e) { /* 忽略 */ }
}

function renderTagbar() {
  const all = S.tags.all || {};
  const unt = S.tags.untagged || {};
  $('tag-all-count').textContent = all.count ?? 0;
  $('tag-untagged-count').textContent = unt.count ?? 0;

  const box = $('tag-chips');
  box.innerHTML = '';
  (S.tags.tags || []).forEach((t) => {
    const b = el('button', 'tagchip' + (S.activeTag === t.id ? ' active' : ''));
    b.dataset.tag = t.id;
    b.innerHTML = `${esc(t.name)} <b>${t.count}/${t.total}</b>`;
    b.style.borderColor = t.color;
    b.addEventListener('click', () => setActiveTag(t.id));
    box.appendChild(b);
  });

  document.querySelectorAll('.tagchip[data-tag="all"], .tagchip[data-tag="untagged"]').forEach((b) => {
    b.classList.toggle('active', S.activeTag === b.dataset.tag);
    b.addEventListener('click', () => setActiveTag(b.dataset.tag));
  });
}

function setActiveTag(tagId) {
  S.activeTag = tagId;
  renderTagbar();
  renderGrid();
  sb(`筛选: ${tagId === 'all' ? '全部' : tagId === 'untagged' ? '未标签' : tagId}`);
}

function devicesInActiveTag() {
  if (S.activeTag === 'all') return S.devices;
  if (S.activeTag === 'untagged') {
    const tagged = new Set();
    (S.tags.tags || []).forEach((t) => (t.serials || []).forEach((s) => tagged.add(s)));
    return S.devices.filter((d) => !tagged.has(d.serial));
  }
  const t = (S.tags.tags || []).find((x) => x.id === S.activeTag);
  if (!t) return S.devices;
  const set = new Set(t.serials || []);
  return S.devices.filter((d) => set.has(d.serial));
}

function tagsOf(serial) {
  const out = [];
  (S.tags.tags || []).forEach((t) => {
    if ((t.serials || []).includes(serial)) out.push(t);
  });
  return out;
}

/* ============================================================
   网格
   ============================================================ */

function renderGrid() {
  const grid = $('grid');
  const visible = devicesInActiveTag();
  const existing = new Map();
  grid.querySelectorAll('.phone').forEach((n) => existing.set(n.dataset.serial, n));

  visible.forEach((d) => {
    let node = existing.get(d.serial);

    if (!node) {
      node = el('div', 'phone');
      node.dataset.serial = d.serial;
      node.innerHTML = `
        <div class="phone-head">
          <span class="phone-tag">USB</span>
          <span class="phone-state"></span>
          <span class="phone-num"></span>
          <span class="phone-serial"></span>
          <span class="phone-bat"></span>
          <span class="phone-close" title="关闭投屏">×</span>
        </div>
        <div class="phone-body">
          <img class="phone-img" alt="">
          <div class="phone-check">✓</div>
          <div class="phone-tags-row"></div>
        </div>
        <div class="bs-toolbar">
          <button data-bs="back">◁ 返回</button>
          <button data-bs="home">○ 主页</button>
          <button data-bs="recent">□ 多任务</button>
          <button data-bs="shot">↻ 刷新</button>
          <button data-bs="close">✕ 收起</button>
        </div>
        <div class="phone-keys">
          <button class="pk" data-k="back">◁</button>
          <button class="pk" data-k="home">○</button>
          <button class="pk" data-k="recent">□</button>
          <button class="pk" data-k="menu">≡</button>
        </div>`;

      node.addEventListener('click', (e) => {
        if (e.ctrlKey || e.metaKey) toggleMulti(d.serial);
        else selectDevice(d.serial);
      });
      node.addEventListener('dblclick', () => openBigscreen(d.serial, d.index));
      node.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        selectDevice(d.serial);
        showCtxMenu(e.clientX, e.clientY, d);
      });

      // ===== 拖拽排序 =====
      node.draggable = true;
      node.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', d.serial);
        e.dataTransfer.effectAllowed = 'move';
        node.classList.add('dragging');
      });
      node.addEventListener('dragend', () => {
        node.classList.remove('dragging');
        grid.querySelectorAll('.dragover').forEach((n) => n.classList.remove('dragover'));
      });
      node.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (!node.classList.contains('dragging')) node.classList.add('dragover');
      });
      node.addEventListener('dragleave', () => node.classList.remove('dragover'));
      node.addEventListener('drop', (e) => {
        e.preventDefault();
        node.classList.remove('dragover');
        const srcSerial = e.dataTransfer.getData('text/plain');
        if (srcSerial && srcSerial !== d.serial) {
          reorderDevices(srcSerial, d.serial);
        }
      });

      node.querySelectorAll('.pk[data-k]').forEach((btn) => {
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const k = btn.dataset.k;
          if (k === 'menu') {
            const r = node.getBoundingClientRect();
            showCtxMenu(r.left + 60, r.top + 50, d);
            return;
          }
          await post('/api/key', { serial: d.serial, key: k });
          setTimeout(() => refreshShot(d.serial), 500);
        });
      });

      // 大屏工具条
      node.querySelectorAll('.bs-toolbar [data-bs]').forEach((btn) => {
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const act = btn.dataset.bs;
          if (act === 'close') {
            openBigscreen(d.serial);   // toggle 收起
            return;
          }
          if (act === 'shot') {
            refreshShot(d.serial);
            return;
          }
          await post('/api/key', { serial: d.serial, key: act });
          setTimeout(() => refreshShot(d.serial), 500);
        });
      });

      node.querySelector('.phone-close').addEventListener('click', (e) => {
        e.stopPropagation();
        node.style.display = 'none';
        sb(`已关闭 #${d.index}`);
      });

      grid.appendChild(node);
    }

    // 更新
    node.classList.toggle('offline', d.state !== 'online');
    node.classList.toggle('active', S.selected === d.serial);
    node.classList.toggle('multi', S.multi.has(d.serial));
    node.classList.toggle('expanded', S.expanded === d.serial);

    // 展开的卡片用中尺寸图
    const useBig = S.expanded === d.serial;

    const st = node.querySelector('.phone-state');
    st.className = 'phone-state ' + (d.state === 'online' ? 'online' : d.state === 'busy' ? 'busy' : '');
    node.querySelector('.phone-num').textContent = `#${d.index}`;
    node.querySelector('.phone-serial').textContent = d.serial;
    node.querySelector('.phone-bat').textContent = d.battery != null ? `${d.battery}%` : '';
    node.querySelector('.phone-check').style.display = S.multi.has(d.serial) ? 'flex' : 'none';

    // 标签
    const row = node.querySelector('.phone-tags-row');
    const ts = tagsOf(d.serial);
    row.innerHTML = ts.map((t) =>
      `<span class="ptag" style="background:${t.color}cc">${esc(t.name)}</span>`
    ).join('');

    // 缩略图（展开时用中尺寸）
    const img = node.querySelector('.phone-img');
    const ts2 = String(d.shot_ts || 0);
    const sizeParam = useBig ? 'size=medium&' : '';
    const key = `${ts2}_${useBig ? 'big' : 'small'}`;
    if (img.dataset.ts !== key) {
      img.dataset.ts = key;
      img.src = `${API}/api/devices/${d.serial}/screenshot?${sizeParam}t=${ts2}`;
    }

    // 展开的卡片绑定操控
    if (useBig && !img.dataset.bound) {
      img.dataset.bound = '1';
      bindBigscreenControls(d.serial, img);
    } else if (!useBig && img.dataset.bound) {
      // 收起时解绑
      delete img.dataset.bound;
      img.onclick = null;
      img.onmousedown = null;
      img.onmousemove = null;
      img.onmouseup = null;
      img.onwheel = null;
      img.oncontextmenu = null;
      img.style.cursor = 'pointer';
    }
  });

  existing.forEach((node, serial) => {
    if (!visible.find((d) => d.serial === serial)) node.remove();
  });
}

function refreshShot(serial) {
  const img = document.querySelector(`[data-serial="${serial}"] .phone-img`);
  if (img) img.src = `${API}/api/devices/${serial}/screenshot?fresh=1&t=${Date.now()}`;
}

/* ---------- 拖拽排序 ---------- */
function reorderDevices(srcSerial, dstSerial) {
  const srcIdx = S.devices.findIndex((d) => d.serial === srcSerial);
  const dstIdx = S.devices.findIndex((d) => d.serial === dstSerial);
  if (srcIdx < 0 || dstIdx < 0) return;

  const [moved] = S.devices.splice(srcIdx, 1);
  S.devices.splice(dstIdx, 0, moved);

  // 重新编号
  S.devices.forEach((d, i) => { d.index = i + 1; });

  // 重建网格（强制）
  const grid = $('grid');
  grid.querySelectorAll('.phone').forEach((n) => n.remove());
  renderGrid();

  // 持久化
  saveOrder();

  sb(`已调整位置：${srcSerial.slice(-4)} → 第 ${dstIdx + 1} 位`);
}

async function saveOrder() {
  try {
    const order = S.devices.map((d) => ({ serial: d.serial, index: d.index }));
    await post('/api/settings', { section: 'device_order', values: { list: order } });
  } catch (e) { /* 忽略 */ }
}

async function loadOrder() {
  try {
    const s = await get('/api/settings');
    const saved = s.device_order?.list || [];
    if (!saved.length) return;

    const map = new Map(saved.map((x) => [x.serial, x.index]));
    S.devices.forEach((d) => {
      if (map.has(d.serial)) d.index = map.get(d.serial);
    });
    S.devices.sort((a, b) => a.index - b.index);
    renderGrid();
  } catch (e) { /* 忽略 */ }
}

/* ============================================================
   选中 / 多选
   ============================================================ */

async function selectDevice(serial) {
  S.selected = serial;
  document.querySelectorAll('.phone').forEach((n) => {
    n.classList.toggle('active', n.dataset.serial === serial);
  });

  const dev = S.devices.find((d) => d.serial === serial);
  if (!dev) return;

  $('ctrl-hint').classList.add('hidden');
  $('ctrl-content').classList.remove('hidden');

  $('ctrl-index').textContent = `#${dev.index}`;
  $('ctrl-serial').textContent = dev.serial;

  const badge = $('ctrl-state');
  badge.textContent = dev.state;
  badge.className = 'badge ' + (dev.state === 'online' ? 'online' : dev.state === 'busy' ? 'busy' : 'offline');

  $('ctrl-battery').textContent = dev.battery != null ? `${dev.battery}%` : '—';
  $('ai-target').textContent = `#${dev.index} ${dev.serial}`;

  const ts = tagsOf(serial);
  $('ctrl-tags').textContent = ts.length ? ts.map((t) => t.name).join(', ') : '无';

  try {
    const info = await get(`/api/devices/${serial}/info`);
    $('ctrl-app').textContent = info.foreground || '—';
    if (info.screen) $('ctrl-screen').textContent = `${info.screen.w}×${info.screen.h}`;
  } catch (e) { /* 忽略 */ }

  sb(`已选中 #${dev.index}`);
}

function toggleMulti(serial) {
  if (S.multi.has(serial)) S.multi.delete(serial);
  else S.multi.add(serial);
  renderGrid();
  updateMultiBar();
  sb(`已选 ${S.multi.size} 台`);
}

function updateMultiBar() {
  const bar = $('multi-bar');
  if (!bar) return;
  if (S.multi.size > 0) {
    bar.classList.remove('hidden');
    $('multi-count').textContent = S.multi.size;
  } else {
    bar.classList.add('hidden');
  }
}

function clearMulti() {
  S.multi.clear();
  renderGrid();
  updateMultiBar();
}

/* ============================================================
   右键菜单
   ============================================================ */

function showCtxMenu(x, y, dev) {
  const menu = $('ctx-menu');
  menu.innerHTML = '';

  const items = [
    { label: '手机功能' },
    { icon: '🔍', label: '打开大屏', fn: () => openBigscreen(dev.serial, dev.index) },
    { icon: '#', label: '修改编号', fn: () => editNumber(dev) },
    { icon: '✏️', label: '修改名称', fn: () => editName(dev) },
    { icon: '🏷️', label: '设置标签', fn: () => openTagDialog(dev) },
    { icon: '📊', label: '手机参数', fn: () => showProps(dev) },
    { sep: true },
    { icon: '📦', label: '安装APK', fn: () => batchInstall([dev.serial]) },
    { icon: 'A', label: '文字分发', fn: () => batchText([dev.serial]) },
    { icon: '📄', label: '文件分发', fn: () => batchPush([dev.serial]) },
    { sep: true },
    { icon: '⌨️', label: 'ADB命令', fn: () => batchAdb([dev.serial]) },
    { icon: '🔄', label: '自动滑屏', fn: () => autoSwipe([dev.serial]) },
    { icon: '⏺️', label: '录制动作', fn: () => startRecord(dev.serial) },
    { icon: '▶️', label: '执行动作', fn: () => gotoScripts() },
    { sep: true },
    { icon: '📶', label: '转为WIFI模式', fn: () => toWifi([dev.serial]) },
    { icon: '🚫', label: '关闭屏幕', fn: () => sleepScreen([dev.serial]) },
    { icon: '🔧', label: '重启ADB', fn: () => restartAdb([dev.serial]) },
    { icon: '📋', label: '导出粘贴板', fn: () => getClipboard(dev.serial) },
  ];

  items.forEach((it) => {
    if (it.label && !it.icon && !it.fn) {
      menu.appendChild(el('div', 'ctx-label', it.label));
      return;
    }
    if (it.sep) {
      menu.appendChild(el('div', 'ctx-sep'));
      return;
    }
    const item = el('div', 'ctx-item');
    item.innerHTML = `<span class="ctx-icon">${it.icon || ''}</span><span>${esc(it.label)}</span>`;
    item.addEventListener('click', () => {
      hideCtxMenu();
      it.fn();
    });
    menu.appendChild(item);
  });

  menu.style.left = Math.min(x, window.innerWidth - 200) + 'px';
  menu.style.top = Math.min(y, window.innerHeight - 460) + 'px';
  menu.classList.remove('hidden');
}

function hideCtxMenu() { $('ctx-menu').classList.add('hidden'); }

/* ---------- 右键功能 ---------- */

function editNumber(dev) {
  const n = prompt('输入新编号（1-99）：', dev.index);
  if (!n) return;
  const i = parseInt(n, 10);
  if (isNaN(i) || i < 1 || i > 99) return sb('编号无效');
  dev.index = i;
  renderGrid();
  sb(`编号改为 ${i}`);
}

function editName(dev) {
  const name = prompt('输入新名称：', dev.model || 'Samsung');
  if (name) {
    dev.model = name;
    sb(`名称改为 ${name}`);
  }
}

async function openTagDialog(dev) {
  const all = S.tags.tags || [];
  if (!all.length) return sb('还没有标签，先点"+ 标签"创建');

  const cur = tagsOf(dev.serial).map((t) => t.id);
  const opts = all.map((t) => `${t.id}: ${t.name}`).join('\n');
  const input = prompt(
    `为 #${dev.index} 选标签（逗号分隔的 id 或名称，留空=清除）\n\n可选：\n${opts}\n\n当前：${cur.join(',') || '无'}`,
    cur.join(',')
  );
  if (input === null) return;

  const ids = input.split(',').map((x) => x.trim()).filter(Boolean).map((x) => {
    const byId = all.find((t) => t.id === x);
    if (byId) return byId.id;
    const byName = all.find((t) => t.name === x);
    return byName ? byName.id : x;
  });

  await post(`/api/devices/${dev.serial}/tags`, { tag_ids: ids });
  await loadTags();
  renderGrid();
  sb('标签已更新');
}

async function showProps(dev) {
  sb('读取参数中...');
  try {
    const r = await get(`/api/devices/${dev.serial}/properties`);
    const p = r.props || {};
    const txt = Object.entries(p).map(([k, v]) => `${k}:\n  ${v}`).join('\n\n');
    $('props-box').textContent = txt;
    switchTab('files');
    sb('参数已读取');
  } catch (e) {
    sb('读取失败: ' + e.message);
  }
}

async function getClipboard(serial) {
  try {
    const r = await get(`/api/clipboard/${serial}`);
    if (r.ok) {
      await navigator.clipboard.writeText(r.text).catch(() => {});
      alert(`剪贴板内容：\n${r.text}`);
    } else {
      alert(r.error || '读取失败');
    }
  } catch (e) {
    alert('读取失败: ' + e.message);
  }
}

/* ============================================================
   批量操作
   ============================================================ */

async function batchText(serials) {
  const text = prompt(`分发到 ${serials.length} 台设备的文字：`);
  if (!text) return;
  sb('分发中...');
  const r = await post('/api/batch/text', { serials, text, concurrency: 4 });
  sb(`文字分发: ${r.ok}/${r.total}`);
  alert(`成功 ${r.ok} 台，失败 ${r.failed} 台`);
}

async function batchAdb(serials) {
  const cmd = prompt(`在 ${serials.length} 台执行 ADB shell：`, 'input keyevent KEYCODE_HOME');
  if (!cmd) return;
  sb('执行中...');
  const r = await post('/api/batch/adb', { serials, command: cmd, concurrency: 4 });
  sb(`ADB: ${r.ok}/${r.total}`);
  alert(`成功 ${r.ok} 台，失败 ${r.failed} 台`);
}

async function batchInstall(serials) {
  const path = prompt('APK 完整路径：');
  if (!path) return;
  sb(`安装到 ${serials.length} 台...`);
  const r = await post('/api/batch/install', { serials, apk_path: path, concurrency: 3 });
  sb(`安装: ${r.ok}/${r.total}`);
  alert(`成功 ${r.ok} 台，失败 ${r.failed} 台`);
}

async function batchPush(serials) {
  const path = prompt('本地文件完整路径：');
  if (!path) return;
  sb(`推送到 ${serials.length} 台...`);
  const r = await post('/api/media/push-batch', { local_path: path, serials, concurrency: 3 });
  sb(`推送: ${r.ok}/${r.total}`);
  alert(`成功 ${r.ok} 台，失败 ${r.failed} 台`);
}

async function autoSwipe(serials) {
  const sec = parseInt(prompt('自动滑屏时长（秒）：', '60'), 10);
  if (!sec || sec < 5) return;

  sb(`自动滑屏 ${sec} 秒...`);
  const r = await post('/api/autoswipe', {
    serials, duration: sec, interval: 3.0, direction: 'up',
  });
  sb(`已启动 ${r.count} 台自动滑屏任务`);
  pollTasks();
}

async function toWifi(serials) {
  if (!confirm(`将 ${serials.length} 台转为 WiFi 模式？`)) return;
  sb('切换中...');
  const r = await post('/api/batch/adb', {
    serials,
    command: 'setprop service.adb.tcp.port 5555; stop adbd; start adbd',
    concurrency: 2,
  });
  sb(`切换: ${r.ok}/${r.total}`);
}

async function sleepScreen(serials) {
  sb('熄屏中...');
  await post('/api/batch/adb', {
    serials, command: 'input keyevent KEYCODE_SLEEP', concurrency: 4,
  });
  sb('已熄屏');
}

async function restartAdb(serials) {
  if (!confirm(`重启 ${serials.length} 台 ADB？`)) return;
  sb('重启中...');
  await post('/api/batch/adb', {
    serials, command: 'killall adbd; start adbd', concurrency: 2,
  });
  sb('已发送重启命令');
}

/* ============================================================
   大屏（就地放大 + 可操控）
   ============================================================ */

// 大屏状态
const BIG = {
  serial: null,
  index: 0,
  devW: 1080,
  devH: 2280,
  dragStart: null,
  dragMoved: false,
  refreshTimer: null,
};

async function openBigscreen(serial, index) {
  const dev = S.devices.find((d) => d.serial === serial);

  // 已经展开同一台 → 收起
  if (S.expanded === serial) {
    S.expanded = null;
    renderGrid();
    sb('已收起');
    return;
  }

  // 展开
  S.expanded = serial;
  BIG.serial = serial;
  BIG.index = index ?? (dev?.index);

  // 获取屏幕尺寸（用于坐标映射）
  if (!BIG.devW || BIG.devW === 1080) {
    try {
      const info = await get(`/api/devices/${serial}/info`);
      if (info.screen) {
        BIG.devW = info.screen.w;
        BIG.devH = info.screen.h;
      }
    } catch (e) { /* 用默认值 */ }
  }

  renderGrid();
  selectDevice(serial);
  switchTab('control');

  // ★ 告诉后端：这台是活跃设备，跳过它的后台轮询（避免双重截图）
  post('/api/active', { serial }).catch(() => {});

  // ★ 启动实时流（WebSocket 推 JPEG）
  startStream(serial);

  // 展开的卡片需要重新绑定操控
  setTimeout(() => {
    const node = document.querySelector(`[data-serial="${serial}"]`);
    if (node && node.classList.contains('expanded')) {
      const img = node.querySelector('.phone-img');
      if (img) bindBigscreenControls(serial, img);
    }
  }, 100);

  sb(`#${BIG.index} 已展开（点击画面操控）`);
}

function closeBigscreen() {
  if (S.expanded) {
    S.expanded = null;
    renderGrid();
  }
  // 停止实时流
  stopStream();
  // 清除活跃标记
  post('/api/active', { serial: '' }).catch(() => {});
  BIG.serial = null;
  if (BIG.refreshTimer) {
    clearInterval(BIG.refreshTimer);
    BIG.refreshTimer = null;
  }
}

function updateBigscreenHint() {
  const hint = $('bigscreen-hint');
  if (!hint) return;
  hint.textContent = `点击 = 点屏幕 ｜ 拖动 = 滑动 ｜ 滚轮 = 上下滑 ｜ ${BIG.devW}×${BIG.devH}`;
}

/* ---------- 坐标映射与操控 ---------- */

function toDeviceCoords(e, img) {
  const rect = img.getBoundingClientRect();

  // object-fit: contain → 图片按比例缩放并居中，四周有留白
  const nw = img.naturalWidth || 1080;
  const nh = img.naturalHeight || 2280;
  const natural = nw / nh;
  const box = rect.width / rect.height;

  let drawW, drawH, padX, padY;
  if (natural > box) {
    // 图片更宽 → 上下留白
    drawW = rect.width;
    drawH = rect.width / natural;
    padX = 0;
    padY = (rect.height - drawH) / 2;
  } else {
    // 图片更高 → 左右留白
    drawH = rect.height;
    drawW = rect.height * natural;
    padX = (rect.width - drawW) / 2;
    padY = 0;
  }

  const px = e.clientX - rect.left - padX;
  const py = e.clientY - rect.top - padY;

  const rx = Math.max(0, Math.min(1, px / (drawW || 1)));
  const ry = Math.max(0, Math.min(1, py / (drawH || 1)));

  // 设备真实分辨率（adb 坐标系）
  const devW = BIG.realW || 1080;
  const devH = BIG.realH || 2280;

  return {
    x: Math.round(rx * devW),
    y: Math.round(ry * devH),
    rx, ry,
    _dbg: `pad=${Math.round(padX)},${Math.round(padY)} draw=${Math.round(drawW)}x${Math.round(drawH)}`,
  };
}

/* ---------- 点击位置可视化（校准用） ---------- */
function showClickMark(img, e) {
  const rect = img.getBoundingClientRect();
  const mark = document.createElement('div');
  mark.className = 'click-mark';
  mark.style.left = (e.clientX - rect.left) + 'px';
  mark.style.top = (e.clientY - rect.top) + 'px';
  img.parentElement.appendChild(mark);
  setTimeout(() => mark.remove(), 500);
}

function bindBigscreenControls(serial, img) {
  // 点击
  img.onclick = async (e) => {
    if (BIG.dragMoved) {
      BIG.dragMoved = false;
      return;
    }
    const p = toDeviceCoords(e, img);
    sb(`点击 (${p.x},${p.y}) ${p._dbg || ''}`);
    await post('/api/tap', { serial, x: p.x, y: p.y });
    setTimeout(() => {
      img.src = `${API}/api/devices/${serial}/screenshot?size=medium&t=${Date.now()}`;
    }, 400);
  };

  // 拖动（滑动）
  img.onmousedown = (e) => {
    BIG.dragStart = { x: e.clientX, y: e.clientY, t: Date.now() };
    BIG.dragMoved = false;
    img.style.cursor = 'grabbing';
  };

  img.onmousemove = (e) => {
    if (!BIG.dragStart) return;
    const dx = e.clientX - BIG.dragStart.x;
    const dy = e.clientY - BIG.dragStart.y;
    if (Math.hypot(dx, dy) > 8) BIG.dragMoved = true;
  };

  img.onmouseup = async (e) => {
    if (!BIG.dragStart) return;
    const start = BIG.dragStart;
    BIG.dragStart = null;
    img.style.cursor = 'pointer';

    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    const dist = Math.hypot(dx, dy);
    const duration = (Date.now() - start.t) / 1000;

    // 位移太小，交给 click
    if (dist < 20) return;

    // 用专业坐标换算（两个点都用同法）
    const p1 = toDeviceCoords({ clientX: start.x, clientY: start.y }, img);
    const p2 = toDeviceCoords(e, img);

    sb(`滑动 (${p1.x},${p1.y}) → (${p2.x},${p2.y})`);

    await post('/api/swipe-points', {
      serial, x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y,
      duration: Math.max(0.15, Math.min(1.2, duration)),
    });

    setTimeout(() => {
      img.src = `${API}/api/devices/${serial}/screenshot?size=medium&t=${Date.now()}`;
    }, 300);
  };

  img.onmouseleave = () => {
    BIG.dragStart = null;
    img.style.cursor = 'pointer';
  };

  // 滚轮 = 快速上下滑
  img.onwheel = async (e) => {
    e.preventDefault();
    const dir = e.deltaY > 0 ? 'up' : 'down';   // 滚轮下 = 内容上滑
    await post('/api/swipe', { serial, direction: dir });
    setTimeout(() => {
      img.src = `${API}/api/devices/${serial}/screenshot?size=medium&t=${Date.now()}`;
    }, 300);
  };

  // 右键 = 返回
  img.oncontextmenu = async (e) => {
    e.preventDefault();
    await post('/api/key', { serial, key: 'back' });
    setTimeout(() => {
      img.src = `${API}/api/devices/${serial}/screenshot?size=medium&t=${Date.now()}`;
    }, 400);
  };

  img.style.cursor = 'pointer';
}

/* ============================================================
   AI
   ============================================================ */

async function runAI() {
  if (!S.selected) return sb('请先选设备');
  const goal = $('ai-goal').value.trim();
  if (!goal) return sb('请填写任务目标');

  $('btn-run-ai').disabled = true;
  $('btn-run-ai').textContent = '执行中...';
  $('stat-ai').textContent = '运行中';
  sb('AI 开始执行');

  try {
    const r = await post('/api/agent', { serial: S.selected, goal, max_steps: 25 });
    pollAgentTask(r.task_id);
  } catch (e) {
    sb('失败: ' + e.message);
    $('btn-run-ai').disabled = false;
    $('btn-run-ai').textContent = '▶ 让 AI 执行';
  }
}

async function pollAgentTask(taskId) {
  const start = Date.now();
  const timer = setInterval(async () => {
    try {
      const data = await get('/api/tasks');
      const t = data.tasks[taskId];
      if (t && ['done', 'failed', 'error'].includes(t.status)) {
        clearInterval(timer);
        $('btn-run-ai').disabled = false;
        $('btn-run-ai').textContent = '▶ 让 AI 执行';
        $('stat-ai').textContent = '闲置';

        $('ai-result-section').classList.remove('hidden');
        $('ai-result').textContent = t.status === 'done'
          ? (t.result || '完成')
          : ('失败: ' + (t.error || t.result || '未知'));
        sb(t.status === 'done' ? 'AI 完成' : 'AI 失败');
        if (S.selected) refreshShot(S.selected);
      }
      if (Date.now() - start > 300000) {
        clearInterval(timer);
        $('btn-run-ai').disabled = false;
        $('btn-run-ai').textContent = '▶ 让 AI 执行';
        sb('AI 超时');
      }
    } catch (e) { /* 忽略 */ }
  }, 1500);
}

async function batchAI(serials) {
  const goal = $('ai-goal').value.trim() || prompt('输入 AI 任务：');
  if (!goal) return;
  let ok = 0;
  for (const s of serials) {
    try {
      await post('/api/agent', { serial: s, goal, max_steps: 20 });
      ok++;
    } catch (e) { /* 忽略 */ }
  }
  sb(`已派发 ${ok}/${serials.length} 个 AI 任务`);
}

/* ============================================================
   任务
   ============================================================ */

async function pollTasks() {
  try {
    const data = await get('/api/queue');
    const st = data.stats || {};
    $('stat-tasks').textContent = st.active ?? 0;

    const box = $('task-stats');
    if (box) {
      box.innerHTML = `
        <span>进行中 <b>${st.running ?? 0}</b></span>
        <span>等待 <b>${st.pending ?? 0}</b></span>
        <span>完成 <b>${st.done ?? 0}</b></span>
        <span>失败 <b>${st.failed ?? 0}</b></span>`;
    }

    const list = $('task-list');
    if (!list) return;
    list.innerHTML = '';
    (data.tasks || []).forEach((t) => {
      const item = el('div', 'task-item');
      item.innerHTML = `
        <div class="task-head">
          <span class="task-type">${esc(t.type)} #${esc(t.serial.slice(-4))}</span>
          <span class="task-state ${t.state}">${t.state}</span>
        </div>
        <div class="task-bar"><div class="task-bar-fill" style="width:${(t.progress || 0) * 100}%"></div></div>
        <div style="color:#8b949e;font-size:10px">${esc((t.result || t.error || '').slice(0, 80))}</div>`;
      list.appendChild(item);
    });
  } catch (e) { /* 忽略 */ }
}

/* ============================================================
   动作脚本
   ============================================================ */

async function loadScripts() {
  try {
    const data = await get('/api/actions');
    const list = $('script-list');
    list.innerHTML = '';
    (data.scripts || []).forEach((s) => {
      const item = el('div', 'script-item');
      item.innerHTML = `<span>${esc(s.name)} <small style="color:#8b949e">(${s.count} 步)</small></span><span class="del">×</span>`;
      item.addEventListener('click', (e) => {
        if (e.target.classList.contains('del')) {
          if (confirm(`删除脚本「${s.name}」？`)) {
            fetch(`${API}/api/actions/${s.id}`, { method: 'DELETE' }).then(() => loadScripts());
          }
          return;
        }
        openScript(s.id);
      });
      list.appendChild(item);
    });
  } catch (e) { /* 忽略 */ }
}

async function openScript(id) {
  try {
    const s = await get(`/api/actions/${id}`);
    S.currentScript = s;
    $('script-detail').style.display = 'block';
    $('script-detail-title').textContent = `${s.name}（${s.actions.length} 步）`;

    const list = $('action-list');
    list.innerHTML = '';
    s.actions.forEach((a, i) => {
      const item = el('div', 'action-item');
      const detail = a.type === 'tap' ? `(${a.params.x},${a.params.y})`
        : a.type === 'text' ? `"${(a.params.text || '').slice(0, 15)}"`
        : a.type === 'key' ? a.params.key
        : a.type === 'swipe' ? `${a.params.direction || ''}`
        : '';
      item.innerHTML = `<span>${i + 1}. ${a.type} ${esc(detail)} <small style="color:#8b949e">${a.delay}s</small></span><span class="del">×</span>`;
      item.querySelector('.del').addEventListener('click', async () => {
        await fetch(`${API}/api/actions/${id}/actions/${i}`, { method: 'DELETE' });
        openScript(id);
      });
      list.appendChild(item);
    });
  } catch (e) {
    sb('打开脚本失败');
  }
}

async function playScript() {
  if (!S.currentScript) return;
  if (!S.selected) return sb('请先选设备');
  sb('执行脚本...');
  try {
    const r = await post(`/api/actions/${S.currentScript.id}/play`, {
      serial: S.selected, speed: 1.0, repeat: 1,
    });
    sb(r.ok ? `脚本执行完成 ${r.done}/${r.total}` : `脚本失败: ${r.error}`);
  } catch (e) {
    sb('执行失败: ' + e.message);
  }
}

function startRecord(serial) {
  switchTab('actions');
  sb('录制功能：请在「动作」面板新建脚本后手动添加步骤');
}

/* ============================================================
   文件
   ============================================================ */

async function pushFile() {
  const path = $('push-local-path').value.trim();
  if (!path) return sb('请输入本地路径');
  if (!S.selected) return sb('请先选设备');

  sb('推送中...');
  try {
    const r = await post('/api/media/push', { local_path: path, serial: S.selected });
    if (r.ok) {
      sb(`推送成功 → ${r.content_uri || r.remote_path}`);
      alert(`推送成功\n\n路径: ${r.remote_path}\nURI: ${r.content_uri || '（未识别）'}`);
    } else {
      sb('推送失败: ' + r.error);
      alert('失败: ' + r.error);
    }
  } catch (e) {
    sb('推送失败: ' + e.message);
  }
}

async function pullFile() {
  const path = $('pull-remote-path').value.trim();
  if (!path) return sb('请输入手机路径');
  if (!S.selected) return sb('请先选设备');

  sb('拉取中...');
  try {
    const r = await post('/api/files/pull', {
      serial: S.selected, remote_path: path, local_dir: 'data/pulled',
    });
    sb(r.ok ? `已保存: ${r.local_path}` : '拉取失败');
    alert(r.ok ? `保存到:\n${r.local_path}` : ('失败: ' + r.output));
  } catch (e) {
    sb('拉取失败: ' + e.message);
  }
}

async function listFiles() {
  if (!S.selected) return sb('请先选设备');
  try {
    const r = await post('/api/files/list', {
      serial: S.selected, remote_dir: '/sdcard/Download/',
    });
    $('file-list').textContent = (r.files || []).join('\n');
    sb(`找到 ${(r.files || []).length} 项`);
  } catch (e) {
    sb('列目录失败');
  }
}

/* ============================================================
   设置
   ============================================================ */

async function openSettings() {
  try {
    S.settings = await get('/api/settings');
    renderSettings();
    $('settings-modal').classList.remove('hidden');
  } catch (e) {
    sb('读取设置失败');
  }
}

function renderSettings() {
  const body = $('settings-body');
  const s = S.settings;
  body.innerHTML = '';

  const addGroup = (title, rows) => {
    const g = el('div', 'set-group');
    g.appendChild(el('div', 'set-group-title', title));
    rows.forEach((r) => g.appendChild(r));
    body.appendChild(g);
  };

  const row = (label, ctrl) => {
    const d = el('div', 'set-row');
    d.appendChild(el('span', '', label));
    d.appendChild(ctrl);
    return d;
  };

  const select = (section, key, options, current) => {
    const sel = document.createElement('select');
    options.forEach(([v, n]) => {
      const o = document.createElement('option');
      o.value = v; o.textContent = n;
      if (String(current) === String(v)) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener('change', () => saveSetting(section, key, sel.value));
    return sel;
  };

  const range = (section, key, min, max, step, current, unit = '') => {
    const wrap = document.createElement('div');
    wrap.style.display = 'flex';
    wrap.style.alignItems = 'center';
    wrap.style.gap = '8px';
    const inp = document.createElement('input');
    inp.type = 'range';
    inp.min = min; inp.max = max; inp.step = step; inp.value = current;
    const val = el('span', 'set-val', current + unit);
    inp.addEventListener('input', () => { val.textContent = inp.value + unit; });
    inp.addEventListener('change', () => saveSetting(section, key, Number(inp.value)));
    wrap.appendChild(inp);
    wrap.appendChild(val);
    return wrap;
  };

  const toggle = (section, key, current) => {
    const sw = el('div', 'set-switch' + (current ? ' on' : ''));
    sw.addEventListener('click', () => {
      sw.classList.toggle('on');
      saveSetting(section, key, sw.classList.contains('on'));
    });
    return sw;
  };

  // 软件界面
  addGroup('软件界面', [
    row('系统语言', select('ui', 'language', [['zh-CN', '简体中文'], ['en', 'English']], s.ui?.language)),
    row('控制板位置', select('ui', 'panel_position', [['right', '居右'], ['left', '居左']], s.ui?.panel_position)),
    row('显示模式', select('ui', 'theme', [['dark', '深色'], ['light', '浅色']], s.ui?.theme)),
    row('排列方式', select('ui', 'sort_mode', [['index', '编号排序'], ['tag', '标签排序']], s.ui?.sort_mode)),
  ]);

  // 手机界面
  addGroup('手机界面', [
    row('编号透明度', range('phone', 'number_opacity', 0.2, 1, 0.1, s.phone?.number_opacity ?? 1)),
    row('小屏间距', range('phone', 'grid_gap', 4, 24, 1, s.phone?.grid_gap ?? 10, 'px')),
    row('单排数量', select('phone', 'per_row', [[0, '自动'], [3, '3'], [4, '4'], [5, '5'], [6, '6']], s.phone?.per_row ?? 0)),
    row('显示放大镜', toggle('phone', 'show_magnifier', s.phone?.show_magnifier)),
    row('显示编号', toggle('phone', 'show_number', s.phone?.show_number)),
    row('显示名称', toggle('phone', 'show_name', s.phone?.show_name)),
  ]);

  // 显示参数
  addGroup('显示参数', [
    row('小屏宽度', range('display', 'small_width', 160, 480, 20, s.display?.small_width ?? 320, 'px')),
    row('大屏宽度', range('display', 'big_width', 320, 900, 40, s.display?.big_width ?? 480, 'px')),
    row('画质', range('display', 'quality', 30, 90, 5, s.display?.quality ?? 55, '%')),
    row('刷新间隔', range('display', 'fps', 1, 10, 1, Math.round(1 / (s.display?.fps || 0.33)), 's')),
  ]);

  // 筛选
  addGroup('连接筛选', [
    row('连接方式', select('filter', 'connection', [
      ['all', '全部'], ['usb', 'USB'], ['wifi', 'WIFI'], ['otg', 'OTG'], ['accessibility', '无障碍'],
    ], s.filter?.connection)),
    row('设备类型', select('filter', 'type', [
      ['local', '本地手机'], ['cloud', '云真机'], ['virtual', '云手机'],
    ], s.filter?.type)),
  ]);

  // 行为
  addGroup('行为', [
    row('自动熄屏', toggle('behavior', 'auto_sleep', s.behavior?.auto_sleep)),
    row('截图间隔', range('behavior', 'screenshot_interval', 1, 10, 1, s.behavior?.screenshot_interval ?? 3, 's')),
  ]);
}

async function saveSetting(section, key, value) {
  try {
    await post('/api/settings', { section, values: { [key]: value } });
    sb(`已保存: ${section}.${key} = ${value}`);
    applySettings();
  } catch (e) {
    sb('保存失败');
  }
}

function applySettings() {
  const s = S.settings;
  // 面板位置
  const panel = $('panel');
  if (s.ui?.panel_position === 'left') panel.classList.add('left-pos');
  else panel.classList.remove('left-pos');

  // 小屏宽度
  const grid = $('grid');
  const w = s.display?.small_width || 320;
  grid.style.gridTemplateColumns = `repeat(auto-fill, minmax(${Math.round(w * 0.55)}px, 1fr))`;

  // 间距
  grid.style.gap = `${s.phone?.grid_gap || 10}px`;
}

/* ============================================================
   Tab 切换
   ============================================================ */

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-body').forEach((b) => b.classList.toggle('active', b.dataset.body === name));
  if (name === 'logs') loadLogs();
  if (name === 'tasks') pollTasks();
  if (name === 'actions') loadScripts();
}

function gotoScripts() {
  switchTab('actions');
}

/* ============================================================
   日志
   ============================================================ */

async function loadLogs() {
  try {
    const data = await get('/api/logs?limit=200');
    const list = $('log-list');
    list.innerHTML = '';
    (data.logs || []).slice().reverse().forEach((l) => {
      const item = el('div', 'log-item ' + l.level);
      item.innerHTML = `<span class="log-time">${fmtT(l.ts)}</span><span class="log-msg">${esc(l.msg)}</span>`;
      list.appendChild(item);
    });
  } catch (e) { /* 忽略 */ }
}

/* ============================================================
   WebSocket
   ============================================================ */

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => sb('已连接');

  ws.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === 'status') {
        (data.devices || []).forEach((d) => {
          const dev = S.devices.find((x) => x.serial === d.serial);
          if (dev) {
            dev.battery = d.battery;
            dev.state = d.state;
            dev.shot_ts = d.shot_ts;
          }
        });
        renderGrid();
      }
    } catch (e) { /* 忽略 */ }
  };

  ws.onclose = () => {
    sb('连接断开，重连中...');
    setTimeout(connectWS, 3000);
  };
}

/* ============================================================
   初始化
   ============================================================ */

function initEvents() {
  // 顶栏
  $('btn-refresh').onclick = () => { loadDevices(); sb('已刷新'); };
  $('btn-select-all').onclick = () => {
    devicesInActiveTag().forEach((d) => S.multi.add(d.serial));
    renderGrid(); updateMultiBar();
    sb(`已全选 ${S.multi.size} 台`);
  };
  $('btn-clear-sel').onclick = clearMulti;
  $('btn-settings').onclick = openSettings;

  // 标签
  $('btn-add-tag').onclick = async () => {
    const name = prompt('标签名称：');
    if (!name) return;
    const color = prompt('颜色（如 #ff2442）：', '#2f81f7') || '#2f81f7';
    await post('/api/tags', { name, color });
    await loadTags();
    sb(`标签「${name}」已创建`);
  };

  // 控制面板
  // 设备操作 / 批量分发 / 任务动作
  document.querySelectorAll('.op-btn[data-op]').forEach((b) => {
    b.onclick = () => {
      if (!S.selected) return sb('请先选设备');
      const dev = S.devices.find((x) => x.serial === S.selected);
      if (!dev) return;
      handleOp(b.dataset.op, dev);
    };
  });

  document.querySelectorAll('.app-btn').forEach((b) => {
    b.onclick = async () => {
      if (!S.selected) return sb('请先选设备');
      await post('/api/launch', { serial: S.selected, package: b.dataset.pkg });
      sb(`启动 ${b.textContent}`);
      setTimeout(() => refreshShot(S.selected), 1500);
    };
  });

  document.querySelectorAll('.key-btn[data-key]').forEach((b) => {
    b.onclick = async () => {
      if (!S.selected) return sb('请先选设备');
      await post('/api/key', { serial: S.selected, key: b.dataset.key });
      setTimeout(() => refreshShot(S.selected), 600);
    };
  });

  document.querySelectorAll('.key-btn[data-swipe]').forEach((b) => {
    b.onclick = async () => {
      if (!S.selected) return sb('请先选设备');
      await post('/api/swipe', { serial: S.selected, direction: b.dataset.swipe });
      setTimeout(() => refreshShot(S.selected), 600);
    };
  });

  $('btn-autoswipe').onclick = () => {
    if (!S.selected) return sb('请先选设备');
    autoSwipe([S.selected]);
  };

  $('btn-send-text').onclick = sendText;
  $('input-text').onkeydown = (e) => { if (e.key === 'Enter') sendText(); };

  $('btn-use-phrase').onclick = () => {
    const v = $('phrase-select').value;
    if (v) $('input-text').value = v;
  };

  $('btn-shot').onclick = () => {
    if (!S.selected) return sb('请先选设备');
    refreshShot(S.selected);
    sb('已截图');
  };

  // AI
  $('btn-run-ai').onclick = runAI;
  document.querySelectorAll('.quick').forEach((b) => {
    b.onclick = () => { $('ai-goal').value = b.dataset.goal; };
  });

  // Tab
  document.querySelectorAll('.tab').forEach((t) => {
    t.onclick = () => switchTab(t.dataset.tab);
  });

  // 任务
  $('btn-cancel-all').onclick = async () => {
    const r = await post('/api/queue/cancel-all', {});
    sb(`已取消 ${r.cancelled} 个任务`);
    pollTasks();
  };
  $('btn-clear-finished').onclick = async () => {
    const r = await post('/api/queue/clear', {});
    sb(`已清除 ${r.cleared} 个`);
    pollTasks();
  };

  // 动作
  $('btn-create-script').onclick = async () => {
    const name = $('new-script-name').value.trim();
    if (!name) return sb('请输入脚本名');
    await post('/api/actions', { name });
    $('new-script-name').value = '';
    loadScripts();
    sb(`脚本「${name}」已创建`);
  };

  $('btn-add-act-tap').onclick = () => addAct('tap');
  $('btn-add-act-swipe').onclick = () => addAct('swipe_up');
  $('btn-add-act-key').onclick = () => addAct('back');
  $('btn-add-act-wait').onclick = () => addAct('wait');
  $('btn-play-script').onclick = playScript;

  // 文件
  $('btn-push-file').onclick = pushFile;
  $('btn-pull-file').onclick = pullFile;
  $('btn-list-files').onclick = listFiles;
  $('btn-device-props').onclick = () => {
    const d = S.devices.find((x) => x.serial === S.selected);
    if (d) showProps(d); else sb('请先选设备');
  };

  // 设置
  $('settings-close').onclick = () => $('settings-modal').classList.add('hidden');

  // 大屏
  $('bs-close').onclick = closeBigscreen;

  $('bs-refresh').onclick = () => {
    if (!BIG.serial) return;
    $('bigscreen-img').src = `${API}/api/devices/${BIG.serial}/screenshot?size=medium&fresh=1&t=${Date.now()}`;
  };

  $('bs-key-back').onclick = async () => {
    if (!BIG.serial) return;
    await post('/api/key', { serial: BIG.serial, key: 'back' });
    setTimeout(() => {
      $('bigscreen-img').src = `${API}/api/devices/${BIG.serial}/screenshot?size=medium&t=${Date.now()}`;
    }, 400);
  };

  $('bs-key-home').onclick = async () => {
    if (!BIG.serial) return;
    await post('/api/key', { serial: BIG.serial, key: 'home' });
    setTimeout(() => {
      $('bigscreen-img').src = `${API}/api/devices/${BIG.serial}/screenshot?size=medium&t=${Date.now()}`;
    }, 600);
  };

  $('bs-key-recent').onclick = async () => {
    if (!BIG.serial) return;
    await post('/api/key', { serial: BIG.serial, key: 'recent' });
    setTimeout(() => {
      $('bigscreen-img').src = `${API}/api/devices/${BIG.serial}/screenshot?size=medium&t=${Date.now()}`;
    }, 600);
  };

  // 多选栏
  document.querySelectorAll('#multi-bar [data-mb]').forEach((b) => {
    b.onclick = async () => {
      const sel = Array.from(S.multi);
      const act = b.dataset.mb;

      if (act === 'clear') return clearMulti();
      if (!sel.length) return sb('请先选设备');

      if (act === 'text') return batchText(sel);
      if (act === 'back' || act === 'home') {
        const r = await post('/api/batch/key', { serials: sel, key: act, concurrency: 4 });
        return sb(`${act}: ${r.ok}/${r.total}`);
      }
      if (act === 'swipe') {
        const r = await post('/api/batch/swipe', { serials: sel, direction: 'up', concurrency: 4 });
        return sb(`上滑: ${r.ok}/${r.total}`);
      }
      if (act === 'launch') {
        const r = await post('/api/batch/launch', { serials: sel, package: 'com.ss.android.ugc.aweme', concurrency: 3 });
        return sb(`抖音: ${r.ok}/${r.total}`);
      }
      if (act === 'adb') return batchAdb(sel);
      if (act === 'install') return batchInstall(sel);
      if (act === 'push') return batchPush(sel);
      if (act === 'autoswipe') return autoSwipe(sel);
      if (act === 'ai') return batchAI(sel);
      if (act === 'tag') {
        for (const s of sel) {
          const d = S.devices.find((x) => x.serial === s);
          if (d) await openTagDialog(d);
        }
        return;
      }
    };
  });

  // 全局点击
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.ctx-menu')) hideCtxMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      hideCtxMenu();
      $('settings-modal').classList.add('hidden');
      closeBigscreen();
    }
  });
}

/* ---------- 实时流 ---------- */
function startStream(serial) {
  stopStream();
  BIG.serial = serial;

  // 先停掉后端的旧流（避免多流抢 CPU）
  post('/api/stream/stop', { serial: '' }).catch(() => {});

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}/ws/stream/${serial}`;

  try {
    const ws = new WebSocket(url);
    ws.binaryType = 'blob';
    BIG.ws = ws;

    ws.onopen = () => console.log('[stream] connected', serial);

    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'ready') {
          BIG.devW = msg.width || 1080;
          BIG.devH = msg.height || 2280;
          // 流的画面尺寸（scrcpy 输出）≠ 设备真实分辨率
          // 设备真实分辨率从设备信息取
          const dev = S.devices.find((d) => d.serial === BIG.serial);
          if (dev && dev.width && dev.height) {
            BIG.realW = dev.width;
            BIG.realH = dev.height;
          }
          console.log(`[stream] ready stream=${BIG.devW}x${BIG.devH} real=${BIG.realW}x${BIG.realH}`);
        } else if (msg.type === 'error') {
          console.error('[stream] error:', msg.message);
          sb('实时流失败: ' + msg.message);
        }
        return;
      }

      // 二进制 = JPEG
      const node = document.querySelector(`[data-serial="${serial}"]`);
      if (!node) return;
      const im = node.querySelector('.phone-img');
      if (!im) return;

      // 用 createImageBitmap 异步解码，避免 URL.createObjectURL 的 GC 压力
      if (BIG.decoding) return;   // 上一帧还没解完，丢弃这一帧
      BIG.decoding = true;

      createImageBitmap(ev.data).then((bmp) => {
        if (BIG.blobUrl) {
          try { URL.revokeObjectURL(BIG.blobUrl); } catch (e) {}
        }
        BIG.blobUrl = URL.createObjectURL(ev.data);
        im.src = BIG.blobUrl;
        im.classList.add('streaming');
        bmp.close();
        BIG.decoding = false;
      }).catch(() => { BIG.decoding = false; });
    };

    ws.onerror = () => console.error('[stream] ws error');
    ws.onclose = () => console.log('[stream] closed');
  } catch (e) {
    console.error('[stream] start failed:', e);
  }
}

function stopStream() {
  if (BIG.ws) {
    try { BIG.ws.close(); } catch (e) {}
    BIG.ws = null;
  }
  if (BIG.blobUrl) {
    URL.revokeObjectURL(BIG.blobUrl);
    BIG.blobUrl = null;
  }
  BIG.serial = null;
}

async function sendText() {
  if (!S.selected) return sb('请先选设备');
  const t = $('input-text').value.trim();
  if (!t) return;
  await post('/api/text', { serial: S.selected, text: t });
  $('input-text').value = '';
  sb('已发送');
  setTimeout(() => refreshShot(S.selected), 600);
}

/* ---------- 右侧面板操作分发 ---------- */
async function handleOp(op, dev) {
  const batchSels = S.multi.size > 0 ? Array.from(S.multi) : [dev.serial];

  switch (op) {
    case 'bigscreen':
      return openBigscreen(dev.serial, dev.index);

    case 'rename':
      return editName(dev);

    case 'tag':
      return openTagDialog(dev);

    case 'props':
      return showProps(dev);

    case 'wifi':
      return toWifi(batchSels);

    case 'sleep':
      return sleepScreen(batchSels);

    case 'reboot': {
      if (!confirm(`重启 ${batchSels.length} 台设备？`)) return;
      sb('重启中...');
      await post('/api/batch/adb', { serials: batchSels, command: 'reboot', concurrency: 2 });
      return sb('重启命令已发送');
    }

    case 'restartadbd':
      return restartAdb(batchSels);

    case 'clipboard':
      return getClipboard(dev.serial);

    case 'install':
      return batchInstall(batchSels);

    case 'text':
      return batchText(batchSels);

    case 'file':
      return batchPush(batchSels);

    case 'adb':
      return batchAdb(batchSels);

    case 'autoswipe':
      return autoSwipe(batchSels);

    case 'swipecount': {
      const n = parseInt(prompt('连续上滑次数：', '10'), 10);
      if (!n || n < 1) return;
      sb(`连续滑动 ${n} 次...`);
      const r = await post('/api/queue/batch', {
        type: 'swipe',
        serials: batchSels,
        params: { direction: 'up', count: n, interval: 2.5 },
      });
      return sb(`已创建 ${r.count} 个滑动任务`);
    }

    case 'record':
      switchTab('actions');
      return sb('请在「动作」面板新建脚本');

    case 'play':
      return gotoScripts();

    case 'task': {
      const type = prompt('任务类型（adb/launch/key/text/swipe/agent）：', 'adb');
      if (!type) return;
      let params = {};
      if (type === 'adb') {
        const cmd = prompt('shell 命令：', 'input keyevent KEYCODE_HOME');
        if (!cmd) return;
        params = { command: cmd };
      } else if (type === 'launch') {
        const pkg = prompt('包名：', 'com.ss.android.ugc.aweme');
        if (!pkg) return;
        params = { package: pkg };
      } else if (type === 'agent') {
        const goal = prompt('AI 目标：');
        if (!goal) return;
        params = { goal };
      }
      sb('创建任务中...');
      const r = await post('/api/queue/batch', { type, serials: batchSels, params });
      pollTasks();
      return sb(`已创建 ${r.count} 个任务`);
    }

    case 'stoptask': {
      const r = await post('/api/queue/cancel-all', {});
      pollTasks();
      return sb(`已取消 ${r.cancelled} 个任务`);
    }

    case 'tasklist':
      switchTab('tasks');
      return pollTasks();

    case 'ime': {
      sb('读取输入法列表...');
      try {
        const r = await post('/api/batch/adb', {
          serials: [dev.serial],
          command: 'ime list -s',
          concurrency: 1,
        });
        alert('输入法列表已输出到日志，切换功能待实现');
      } catch (e) {
        sb('失败');
      }
      return;
    }

    default:
      return sb(`未知操作: ${op}`);
  }
}

async function addAct(type) {
  if (!S.currentScript) return sb('请先打开一个脚本');
  const params = {};
  if (type === 'tap') {
    const x = prompt('X 坐标：', '540'); const y = prompt('Y 坐标：', '1140');
    if (!x || !y) return;
    params.x = parseInt(x, 10); params.y = parseInt(y, 10);
  } else if (type === 'wait') {
    const s = prompt('等待秒数：', '2');
    if (!s) return;
    params.seconds = parseFloat(s);
  }

  await post(`/api/actions/${S.currentScript.id}/actions`, { type, params, delay: 0.5 });
  openScript(S.currentScript.id);
}

async function loadPhrases() {
  try {
    const data = await get('/api/phrases');
    const sel = $('phrase-select');
    sel.innerHTML = '<option value="">快捷短语...</option>';
    (data.phrases || []).forEach((p) => {
      const o = document.createElement('option');
      o.value = p.text; o.textContent = p.text;
      sel.appendChild(o);
    });
  } catch (e) { /* 忽略 */ }
}

/* ---------- 启动 ---------- */
document.addEventListener('DOMContentLoaded', async () => {
  initEvents();
  await loadDevices();
  await loadPhrases();
  try { S.settings = await get('/api/settings'); applySettings(); } catch (e) { /* 忽略 */ }
  await loadOrder();

  connectWS();
  setInterval(pollTasks, 3000);

  // 缩略图定时刷新（大屏设备跳过 —— 它走 WebSocket 实时流）
  setInterval(() => {
    // 正在大屏操控的设备不用刷新
    const skip = S.expanded;

    S.devices.forEach((d) => {
      if (d.state !== 'online') return;
      if (d.serial === skip) return;          // ★ 关键：跳过大屏设备

      const img = document.querySelector(`[data-serial="${d.serial}"] .phone-img`);
      if (img) img.src = `${API}/api/devices/${d.serial}/screenshot?t=${Date.now()}`;
    });
  }, (S.settings?.behavior?.screenshot_interval || 10) * 1000);   // 3秒 → 10秒

  // 设备列表定时刷新
  setInterval(loadDevices, 20000);

  sb('就绪');
  if (S.devices.length) selectDevice(S.devices[0].serial);
});
