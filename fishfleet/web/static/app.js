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

// 安全版 $：找不到元素时返回"吸收一切操作"的代理，避免旧代码崩掉新布局
const $raw = (id) => document.getElementById(id);
const _noop = () => {};
const _safeNode = new Proxy({}, {
  get(t, k) {
    if (k === 'style') return _safeNode;
    if (k === 'classList') return _safeNode;
    if (k === 'dataset') return _safeNode;
    if (k === 'value') return '';
    if (k === 'textContent' || k === 'innerHTML' || k === 'src') return '';
    if (k === 'checked') return false;
    return typeof k === 'symbol' ? undefined : _noop;
  },
  set() { return true; },
  has() { return true; },
});
const $ = (id) => {
  const e = $raw(id);
  if (e) return e;
  return _safeNode;
};

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

    // 周期刷新只更新设备信息，不得把用户当前排列重置为 ADB 枚举顺序。
    const previousOrder = new Map(S.devices.map((d, i) => [d.serial, {rank:i, index:d.index}]));
    S.devices = data.devices.map(d => {
      const previous = previousOrder.get(d.serial);
      return d; // 编号以服务端为准，仅保留前端排列次序。
    });
    if (previousOrder.size) S.devices.sort((a,b) =>
      (previousOrder.get(a.serial)?.rank ?? Number.MAX_SAFE_INTEGER) -
      (previousOrder.get(b.serial)?.rank ?? Number.MAX_SAFE_INTEGER));

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



function refreshShot(serial) {

  const img = document.querySelector(`[data-serial="${serial}"] .phone-img`);

  if (img) img.src = `${API}/api/devices/${serial}/screenshot?fresh=1&t=${Date.now()}`;

}



function renderGrid() {
  const grid = $('grid');
  const filter = window.__deviceFilter || 'all';
  // 2026-11-06 新增：界面显示范围（对应左侧「全显 / 只显已选」两个按钮）
  //   'all'      —— 全显：忽略标签筛选，所有手机都在界面里出现
  //   'selected' —— 只显已选：只显示下方号码格子里已勾选的手机
  const scope = window.__gridScope || 'all';
  let base;
  if (scope === 'selected') {
    const sel = window.S?.multi || new Set();
    base = (window.S?.devices || []).filter(d => sel.has(d.serial));
  } else if (scope === 'all') {
    base = (window.S?.devices || []);      // 全显：不受标签限制
  } else {
    base = devicesInActiveTag();           // 'tag'：按当前标签（原行为）
  }
  const visible = base.filter(d => {
    if (filter === 'all') return true;
    if (filter === 'access') return d.mode === 3 || d.is_accessible === true;
    return d.connection === filter;
  });
  const existing = new Map();
  grid.querySelectorAll('.phone').forEach((n) => existing.set(n.dataset.serial, n));

  visible.forEach((d) => {
    // 单台卡片渲染保护：任何一台出错都不能影响其他台
    try {
    let node = existing.get(d.serial);

    if (!node) {
      node = el('div', 'phone');
      node.dataset.serial = d.serial;
      node.innerHTML = `
        <span class="usb-tag">USB</span>
        <span class="close-x" title="关闭">×</span>
        <div class="overlay">
          <div class="big-num"></div>
          <div class="model"></div>
          <div class="small-info"></div>
        </div>
        <canvas class="phone-live"></canvas>
        <div class="sel-mark"></div>`;

      node.addEventListener('click', (e) => {
        const serial = node.dataset.serial;
        if (e.target.classList.contains('close-x')) {
          e.stopPropagation();
          node.style.display = 'none';
          if (window.unregisterLiveCanvas) window.unregisterLiveCanvas(serial, node.querySelector('.phone-live'));
          return;
        }
        // 导航键区域的点击只走按钮自己的处理，不触发选中/多选，避免横屏时误触。
        if (e.target.closest('.card-keys')) return;
        if (e.ctrlKey || e.metaKey) toggleMulti(serial);
        else selectDevice(serial);
      });
      node.addEventListener('dblclick', () => { const dev = S.devices.find(x => x.serial === node.dataset.serial); if (dev) window.openBigscreen?.(dev.serial, dev.index); });
      node.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        const dev = S.devices.find(x => x.serial === node.dataset.serial);
        if (!dev) return;
        selectDevice(dev.serial);
        showCtxMenu(e.clientX, e.clientY, dev);
      });

      node.draggable = true;
      node.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('text/plain', node.dataset.serial);
        e.dataTransfer.effectAllowed = 'move';
      });
      node.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
      });
      node.addEventListener('drop', (e) => {
        e.preventDefault();
        const src = e.dataTransfer.getData('text/plain');
        if (src && src !== node.dataset.serial) reorderDevices(src, node.dataset.serial);
      });

      grid.appendChild(node);
    }

    // 复用画布节点调整 DOM 顺序，避免拖拽排序中断投屏。
    grid.appendChild(node);
    node.classList.toggle('offline', d.state !== 'online');
    node.classList.toggle('selected', S.multi.has(d.serial));
    node.classList.toggle('active', S.expanded === d.serial);
    node.classList.toggle('wifi', d.connection !== 'usb');
    node.querySelector('.usb-tag').textContent = (d.mode === 3 || d.is_accessible === true) ? '无障碍' : (({otg:'OTG', wifi:'WIFI', network:'TCP'})[d.connection] || 'USB');

    node.querySelector('.big-num').textContent = String(d.index || '').padStart(2, '0');
    // ★ 显示序列号短码（末 6 位）。型号全是 Samsung，看不出区别，
    //   而 AI / 人工排障都必须能一眼确认「这个编号对应哪台机器」。
    const shortId = String(d.serial || '').slice(-6);
    node.querySelector('.model').textContent = d.name || shortId;
    node.querySelector('.model').title = d.serial;   // 悬停看完整序列号
    node.querySelector('.small-info').textContent =
      (d.width && d.height ? `${d.width}x${d.height}` : '') +
      (d.battery != null ? ` · ${d.battery}%` : '');

    // 小屏：单WS JPEG + Worker解码，单台只保留最新待处理图片。
    const live = node.querySelector('.phone-live');
    if (live && d.state === 'online' && node.style.display !== 'none') {
      live.dataset.serial = d.serial;
      if (window.registerLiveCanvas) window.registerLiveCanvas(d.serial, live);
    } else if (live && window.unregisterLiveCanvas) {
      window.unregisterLiveCanvas(d.serial, live);
    }

    } catch (e) {
      console.error('[card] 渲染失败', d.serial, e);
    }
  });

  existing.forEach((node, serial) => {
    if (!visible.find((d) => d.serial === serial)) {
      if (window.unregisterLiveCanvas) window.unregisterLiveCanvas(serial, node.querySelector('.phone-live'));
      node.remove();
    }
  });
  window.renderNumGrid?.();
}

/* ---------- 拖拽排序 ---------- */

let reorderSaveQueue = Promise.resolve();
function reorderDevices(srcSerial, dstSerial) {

  const srcIdx = S.devices.findIndex((d) => d.serial === srcSerial);

  const dstIdx = S.devices.findIndex((d) => d.serial === dstSerial);

  if (srcIdx < 0 || dstIdx < 0) return;



  const [moved] = S.devices.splice(srcIdx, 1);

  S.devices.splice(dstIdx, 0, moved);



  // 拖拽只改变显示顺序，不改变设备身份编号。

  // ★ 同步到后端，否则刷新后又跳回原顺序
  const order = S.devices.map((d) => ({ serial: d.serial, index: d.index }));
  // 保存按操作顺序串行，避免较早请求迟到覆盖较新的拖拽结果。
  reorderSaveQueue = reorderSaveQueue.then(async () => {
    const r = await fetch('/api/devices/reorder', {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order}),
    });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || j.detail || `HTTP ${r.status}`);
  }).catch((error) => {
    console.warn('[reorder] 同步失败', error);
    sb('排序已在界面调整，但后端同步失败；刷新后可能恢复');
  });

  // 只移动已有卡片，保留 canvas、解码状态和实时订阅。
  renderGrid();



  // 顺序由 /api/devices/reorder 持久化，避免并发双写。



  sb(`已调整位置：${srcSerial.slice(-4)} → 第 ${dstIdx + 1} 位`);

}



async function saveOrder() {

  try {

    const order = S.devices.map((d) => ({ serial: d.serial, index: d.index }));

    const r = await fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({section:'device_order', values:{list:order}})});
    const result = await r.json();
    if (!r.ok || !result.ok) throw new Error(result.error || result.detail || `HTTP ${r.status}`);
  } catch (e) {
    console.warn('[reorder] 保存设置失败', e);
    sb('排序设置保存失败；刷新后可能恢复');
  }

}



async function loadOrder() {

  try {

    const s = await get('/api/settings');

    const saved = s.device_order?.list || [];

    if (!saved.length) return;



    const map = new Map(saved.map((x, rank) => [x.serial, rank]));
    S.devices.sort((a, b) => (map.get(a.serial) ?? Number.MAX_SAFE_INTEGER) - (map.get(b.serial) ?? Number.MAX_SAFE_INTEGER));

    renderGrid();

  } catch (e) { /* 忽略 */ }

}



/* ============================================================

   选中 / 多选

   ============================================================ */



async function selectDevice(serial) {
  const dev = S.devices.find((d) => d.serial === serial);
  if (!dev) return;

  S.selected = serial;

  // 单击只选择；双击/明确的大屏动作才打开，避免选择即重建大屏。

  // 更新卡片高亮
  document.querySelectorAll('.phone').forEach((n) => {
    n.classList.toggle('active', n.dataset.serial === serial);
  });

  sb(`已选择 #${dev.index} ${serial}`);
}



function toggleMulti(serial) {

  if (S.multi.has(serial)) S.multi.delete(serial);

  else S.multi.add(serial);

  renderGrid();

  // 2026-11-06：号码格子自身的选中高亮也要跟着刷新，
  // 否则「只显已选」模式下点了格子，格子还是旧的亮/灭状态。
  window.renderNumGrid?.();

  sb(`已选 ${S.multi.size} 台`);

}



function clearMulti() {

  S.multi.clear();

  renderGrid();

  window.renderNumGrid?.();

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

    if (!r.ok) throw new Error(r.error || r.message || '设备未返回参数');
    const dialog = document.createElement('dialog');
    dialog.className = 'device-props-dialog';
    const title = document.createElement('h3'); title.textContent = `设备信息 · ${dev.serial}`;
    const body = document.createElement('pre'); body.textContent = txt || '没有返回参数';
    const close = document.createElement('button'); close.textContent = '关闭';
    close.onclick = () => dialog.close(); dialog.onclose = () => dialog.remove();
    dialog.append(title, body, close); document.body.append(dialog); dialog.showModal();
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



  // 获取屏幕尺寸（用于坐标映射）。注意：这里绝不能阻塞推流——
  // 无障碍模式关掉 ADB 后该接口可能等 10 秒，会把大屏卡成黑屏。
  if (!BIG.devW || BIG.devW === 1080) {

    get(`/api/devices/${serial}/info`).then((info) => {

      if (info && info.screen) {

        BIG.devW = info.screen.w;

        BIG.devH = info.screen.h;

      }

    }).catch(() => { /* 用默认值 */ });

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



    // 用专业坐标换算（两个点都用同法）；距离缩短只在后端做一次

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

  }, 3000);

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

  grid.style.removeProperty('grid-template-columns');
  if (!document.documentElement.style.getPropertyValue('--card-w')) {
    document.documentElement.style.setProperty('--card-w', `${Math.round(w * 0.55)}px`);
  }



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

  try {

  // 顶栏

  $('btn-refresh').onclick = () => { loadDevices(); sb('已刷新'); };

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
} catch (e) { console.warn('[initEvents]', e.message); }

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



  // ★★ 大屏设备缩略图：3 秒刷新（1.5 秒太频繁，会占主线程导致流延迟）
  //    之前这里把大屏设备 skip 掉了，导致「大屏画面变了，缩略图不更新」
  setInterval(() => {
    const s = S.expanded;
    if (!s) return;
    const img = document.querySelector(`[data-serial="${s}"] .phone-img`);
    if (img) img.src = `${API}/api/devices/${s}/screenshot?t=${Date.now()}`;
  }, 1500);

  // 其他设备：按设置的间隔刷新（含大屏，双保险；不再 skip）
  setInterval(() => {
    S.devices.forEach((d) => {
      if (d.state !== 'online') return;
      const img = document.querySelector(`[data-serial="${d.serial}"] .phone-img`);
      if (img) img.src = `${API}/api/devices/${d.serial}/screenshot?t=${Date.now()}`;
    });
  }, (S.settings?.behavior?.screenshot_interval || 10) * 1000);



  // 设备列表定时刷新

  setInterval(loadDevices, 20000);



  sb('就绪');

  if (S.devices.length) selectDevice(S.devices[0].serial);

});



/* ============================================================

   暴露给 ui.js（新布局层）

   ============================================================ */

window.S = S;

window.API = API;

window.tagsOf = tagsOf;

window.devicesInActiveTag = devicesInActiveTag;

window.renderGrid = renderGrid;

window.loadDevices = loadDevices;

window.loadTags = loadTags;

window.clearMulti = clearMulti;

window.toggleMulti = toggleMulti;

window.selectDevice = selectDevice;

window.showCtxMenu = showCtxMenu;

window.showProps = showProps;

window.editNumber = editNumber;

window.editName = editName;

window.batchInstall = batchInstall;

window.batchPush = batchPush;

window.batchAdb = batchAdb;

window.autoSwipe = autoSwipe;

window.getClipboard = getClipboard;

window.refreshShot = refreshShot;

/* ===========================================================================
 * 单 WebSocket 推所有设备的帧（突破浏览器 6 并发连接限制）
 * 20 台只占 1 个 WS 连接，服务端按需推 JPEG，前端画到各自 canvas
 * =========================================================================== */
(function initAllFramesWS() {
  const devices=new Map(), workers=[]; let ws=null,reconnect=null,subTimer=null,disposed=false,lastSub='',nextId=0,cursor=0;
  let stageTarget=null;
  const visibility=new Map();
  const observer=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{
    let changed=false;
    for(const entry of entries){const serial=entry.target.dataset.serial;if(serial&&visibility.get(serial)!==entry.isIntersecting){visibility.set(serial,entry.isIntersecting);changed=true;}}
    if(changed)subscribe();
  }):null;
  function drawStage(d, frame) {
    const target=stageTarget;
    if(!target||target.serial!==d.serial||!target.canvas.isConnected)return;
    const cv=target.canvas,w=frame.displayWidth||frame.width,h=frame.displayHeight||frame.height;
    if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;if(window.__setStageSize)window.__setStageSize(w,h);else window.fitStageCanvas?.();}
    cv.getContext('2d',{alpha:false}).drawImage(frame,0,0,w,h);
    target.rendered++;target.lastRender=performance.now();
  }
  function error(d,e){if(d){d.errors++;d.lastError=String(e);}console.warn('[jpeg-live]',e);}
  function schedule(){
    if(disposed)return;
    const list=[...devices.values()];if(!list.length)return;
    for(const slot of workers){
      if(slot.job||slot.dead)continue;
      for(let n=0;n<list.length;n++){
        const d=list[cursor++%list.length];if(!d.pending||d.inFlight)continue;
        const packet=d.pending;d.pending=null;d.inFlight=true;
        const job={id:++nextId,d,canvas:d.canvas};slot.job=job;
        try{slot.worker.postMessage({id:job.id,serial:d.serial,...packet},[packet.buffer]);}
        catch(e){d.inFlight=false;slot.job=null;error(d,e);}break;
      }
    }
  }
  for(let i=0;i<4;i++){
    const slot={worker:new Worker('/static/jpeg_decode_worker.js?v=20261029'),job:null,dead:false};workers.push(slot);
    slot.worker.onmessage=({data:m})=>{
      const job=slot.job;slot.job=null;
      try{
        if(!job||job.id!==m.id){if(m.frame)m.frame.close();return;}
        const d=job.d;d.inFlight=false;
        if(m.type!=='decoded'){error(d,m.message||'decode failed');return;}
        d.decoded++;
        for(const warning of m.warnings||[])error(d,warning.message);
        if(devices.get(d.serial)!==d||d.canvas!==job.canvas||!d.canvas.isConnected)return;
        const frame=m.frame,cv=d.canvas,width=frame.displayWidth||frame.width,height=frame.displayHeight||frame.height;
        if(cv.width!==width||cv.height!==height){cv.width=width;cv.height=height;}
        const card=cv.closest('.phone');
        if(card){
          card.style.setProperty('--frame-ratio',`${width} / ${height}`);
          card.style.setProperty('--frame-ratio-rotated',`${height} / ${width}`);
        }
        if(!d.ctx)d.ctx=cv.getContext('2d',{alpha:false});
        d.ctx.drawImage(frame,0,0,cv.width,cv.height);d.rendered++;d.lastRender=performance.now();d.backend=m.backend;
        drawStage(d,frame);
      }catch(e){error(job&&job.d,e);}
      finally{if(m.frame)try{m.frame.close();}catch(e){}schedule();}
    };
    slot.worker.onerror=e=>{slot.dead=true;if(slot.job){slot.job.d.inFlight=false;error(slot.job.d,e.message);slot.job=null;}slot.worker.terminate();schedule();};
  }
  function subscribe(){
    clearTimeout(subTimer);subTimer=setTimeout(()=>{
      if(!ws||ws.readyState!==1)return;
      const q0=(window.__sliderQuality||[360,480,540,720,1080])[(window.__qLevel||2)-1]||480;
      const fps0=(window.__sliderFps||[5,10,15,20,24])[(window.__fpsLevel||5)-1]||24;
      // ★ 小屏节能上限：20 台并发时转码是 CPU 主因。订阅档位锁死在 480p/15fps 以内，
      //   即使用户把滑块拉到 1080/24，也只影响滑块显示，不会再把 20 台全部推满。
      //   要看清请开大屏（大屏走独立的高清参数，不受此限）。
      const q=Math.min(q0,480),fps=Math.min(fps0,15);
      // FishFleet 的实验性码率预算；不是汇城枚举到 bps 的已验证映射。
      // 实际吞吐受画面内容影响，不能仅凭平均码率推断清晰度。
      const bit_rate=window.__streamBitRate||Math.max(50000,Math.round(q*fps*150));
      const msg={type:'sub',serials:[...devices.keys()],max_size:q,max_fps:fps,bit_rate};
      msg.device_params={};
      for(const serial of devices.keys())if(visibility.get(serial)===false)msg.device_params[serial]={max_size:q,max_fps:1,bit_rate};
      if(stageTarget){msg.priority_serial=stageTarget.serial;msg.device_params[stageTarget.serial]={max_size:window.__bigMaxSize||1080,max_fps:30,bit_rate:8000000};}
      const signature=JSON.stringify(msg);if(signature!==lastSub){ws.send(signature);lastSub=signature;}
    },300); // 连续拖动只提交300ms稳定后的最终设置
  }
  function connect(){
    if(disposed||ws&&(ws.readyState===0||ws.readyState===1))return;
    const socket=ws=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/ws/all-frames');socket.binaryType='arraybuffer';
    socket.onopen=()=>{lastSub='';subscribe();};
    socket.onmessage=({data:buffer})=>{
      try{
        if(!(buffer instanceof ArrayBuffer)||buffer.byteLength<6)throw Error('invalid JPEG packet');
        const v=new DataView(buffer),n=v.getUint8(0),offset=1+n+4;
        if(!n||offset>buffer.byteLength)throw Error('invalid serial length');
        const serial=new TextDecoder().decode(new Uint8Array(buffer,1,n)),length=v.getUint32(1+n,false);
        if(!length||offset+length!==buffer.byteLength)throw Error('invalid JPEG length');
        const d=devices.get(serial);if(!d)return;
        d.received++;d.lastRx=performance.now();if(d.pending)d.overwritten++;d.pending={buffer,offset,length};schedule();
      }catch(e){error(null,e);}
    };
    socket.onclose=()=>{if(ws!==socket||disposed)return;ws=null;clearTimeout(reconnect);reconnect=setTimeout(connect,3000);};
  }
  window.registerLiveCanvas=(serial,canvas)=>{
    const old=devices.get(serial);if(old&&old.canvas===canvas)return;
    devices.set(serial,{serial,canvas,ctx:null,pending:null,inFlight:false,received:0,decoded:0,rendered:0,overwritten:0,errors:0,lastRx:null,lastRender:null});
    const card=canvas.closest?.('.phone');if(card)observer?.observe(card);
    connect();subscribe();
  };
  window.unregisterLiveCanvas=(serial,expected)=>{const d=devices.get(serial);if(d&&(!expected||d.canvas===expected)){d.pending=null;const card=d.canvas.closest?.('.phone');if(card)observer?.unobserve(card);visibility.delete(serial);devices.delete(serial);subscribe();}};
  window.reapplyStreamParams=subscribe;
  window.__jpegLive={
    setStage:(serial,canvas)=>{
      stageTarget=serial&&canvas?{serial,canvas,rendered:0,lastRender:null}:null;
      subscribe();
      const d=devices.get(serial);
      if(stageTarget&&d&&d.rendered){
        canvas.width=d.canvas.width;canvas.height=d.canvas.height;
        // 首张缓存帧可能在新的大屏帧到达前停留较久，立即按其比例布局。
        if(window.__setStageSize)window.__setStageSize(canvas.width,canvas.height);
        else window.fitStageCanvas?.();
        canvas.getContext('2d',{alpha:false}).drawImage(d.canvas,0,0);
      }
    },
    stageStats:()=>stageTarget?{serial:stageTarget.serial,rendered:stageTarget.rendered,lastRenderIdle:stageTarget.lastRender===null?null:performance.now()-stageTarget.lastRender}:null,
    hasFrame:(serial,canvas)=>{const d=devices.get(serial);return !!(d&&d.canvas===canvas&&d.rendered);},
    detail:()=>[...devices.values()].map(d=>({serial:d.serial,received:d.received,decoded:d.decoded,rendered:d.rendered,overwritten:d.overwritten,errors:d.errors,lastError:d.lastError,backend:d.backend,inFlight:d.inFlight,pending:!!d.pending,lastRxIdle:d.lastRx===null?null:performance.now()-d.lastRx,lastRenderIdle:d.lastRender===null?null:performance.now()-d.lastRender})),
    dispose:()=>{disposed=true;observer?.disconnect();clearTimeout(reconnect);clearTimeout(subTimer);if(ws)ws.close();for(const s of workers)s.worker.terminate();devices.clear();}
  };
  window.__wsAllStats=()=>({canvases:devices.size,recv:[...devices.values()].reduce((n,d)=>n+d.received,0),state:ws?ws.readyState:-1});
  window.addEventListener('beforeunload',()=>window.__jpegLive.dispose());
})();
