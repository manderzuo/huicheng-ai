/* ============================================================
   FishFleet — 新布局交互层（1:1 汇城）
   覆盖 app.js 的大屏逻辑，接管左大屏 / 中菜单 / 右面板
   ============================================================ */

(function () {
  'use strict';

  const $raw = (id) => document.getElementById(id);
  // 安全占位：找不到元素时返回"吸收一切"的对象
  const _noop = () => {};
  const _base = {
    addEventListener: _noop,
    removeEventListener: _noop,
    appendChild: _noop,
    removeChild: _noop,
    querySelector: () => null,
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1, height: 1, right: 1, bottom: 1 }),
    focus: _noop, blur: _noop, click: _noop,
    textContent: '', innerHTML: '', value: '', src: '', checked: false,
  };
  const $n = new Proxy(_base, {
    get(t, k) {
      if (k in t) return t[k];
      if (k === 'style' || k === 'classList' || k === 'dataset') return $n;
      if (k === 'children' || k === 'childNodes') return [];
      return typeof k === 'symbol' ? undefined : _noop;
    },
    set(t, k, v) { t[k] = v; return true; },
  });
  const $ = (id) => $raw(id) || $n;
  const stage = $('stage-canvas');
  const stageEmpty = $('stage-empty');
  const stageBadge = $('stage-badge');

  let player = null;          // H264Player
  let curSerial = null;       // 当前大屏设备
  let curDev = null;          // 当前设备对象
  let stageW = 0, stageH = 0; // 设备真实分辨率
  let displayRotation = 0;
  function rotateLocalDisplay(){if(!curSerial||!stage)return;displayRotation=(displayRotation+90)%360;drag=null;fitStageCanvas();toast(`显示画面旋转至 ${displayRotation}°`);}
  let _openSeq = 0;           // ★ 并发守卫：只让最后一次 openBigscreen 生效


  /* ==================== 浮动菜单：右键弹出 + 可拖动 ==================== */

  // ★ 扇出目标必须在任何入口都可用：右键菜单与大屏设置菜单是两条打开路径，
  //   若只在 showFloatMenu 内定义，设置菜单点“旋转屏幕”会报 fanoutSerials is not a function。
  function menuSerials() {
    const cur = window.__ctxSerial || curSerial || curDev?.serial || window.S?.selected;
    const m = window.S?.multi;
    if (!m || !m.size) return cur ? [cur] : [];
    const list = [...new Set(cur ? [cur, ...m] : [...m])];
    return list.filter((s) => (window.S?.devices || []).some((d) => d.serial === s && d.state === 'online'));
  }
  window.fanoutSerials = menuSerials;
  function fanoutSerials() { return menuSerials(); }
  function fanoutLabel(sn) {
    const d = (window.S?.devices || []).find((x) => x.serial === sn);
    return d ? `${String(d.index).padStart(2, '0')}号` : String(sn).slice(-6);
  }
  window.fanoutLabel = fanoutLabel;
  async function fanoutMenu(hint, fn, opts = {}) {
    const labelOf = window.fanoutLabel;
    const targets = opts.targets || (window.fanoutSerials || menuSerials)();
    if (!targets.length) throw new Error('没有可操作的在线手机');
    if (targets.length <= 1) return fn(targets[0]);
    if (opts.danger && !window.confirm(`${hint}将作用于 ${targets.length} 台手机（${targets.map(labelOf).join('、')}），继续？`)) return { ok: false, cancelled: true };
    toast(`${hint}下发中（${targets.length} 台）…`);
    let ok = 0; const bad = [], rets = [];
    for (const s of targets) {
      try { const r = await fn(s); rets.push({ serial: s, r }); if (r && r.ok) ok++; else bad.push(labelOf(s)); }
      catch (e) { bad.push(labelOf(s)); rets.push({ serial: s, r: null, error: String(e) }); }
    }
    toast(bad.length ? `${hint}完成：${ok}/${targets.length} 台成功，失败：${bad.join('、')}` : `${hint}完成：${targets.length} 台全部成功`);
    return { ok: bad.length === 0, okCount: ok, bad, rets };
  }
  window.fanoutMenu = fanoutMenu;

  const fmEl = $raw('float-menu');
  let fmShown = false;
  let menuAnchor = null;
  function positionAnchoredMenu() {
    if (!fmShown || !menuAnchor?.isConnected) return;
    const r=menuAnchor.getBoundingClientRect(), gap=8, margin=8;
    const w=fmEl.offsetWidth||190, h=fmEl.offsetHeight||460;
    let left=r.right+gap;
    if(left+w>innerWidth-margin)left=r.left-w-gap;
    fmEl.style.left=Math.max(margin,Math.min(innerWidth-w-margin,left))+'px';
    fmEl.style.top=Math.max(margin,Math.min(innerHeight-h-margin,r.top))+'px';
  }
  window.addEventListener('resize',positionAnchoredMenu);
  document.addEventListener('scroll',positionAnchoredMenu,true);
  window.positionAnchoredMenu=positionAnchoredMenu;

  function positionStageSettings() {
    if (!fmEl?.classList.contains('stage-settings')) return;
    const box = document.getElementById('stage');
    if (!box || box.classList.contains('hidden')) return;
    const r = box.getBoundingClientRect(), width = fmEl.offsetWidth || 190;
    const maxLeft = Math.max(8, window.innerWidth - width - 8);
    // 右侧空间不足时放左侧，避免菜单遮住大屏；两侧都不足时仍限制在视口内。
    const preferred = r.right + 8 <= maxLeft ? r.right + 8 : r.left - width - 8;
    fmEl.style.left = Math.max(8, Math.min(maxLeft, preferred)) + 'px';
    fmEl.style.top = Math.max(8, Math.min(window.innerHeight - fmEl.offsetHeight - 8, r.top)) + 'px';
  }
  window.positionStageSettings = positionStageSettings;
  window.addEventListener('resize', positionStageSettings);

  function showFloatMenu(x, y, anchor=null) {
    menuAnchor=anchor;
    // 多选扇出的目标集合（函数本体在模块顶层，右键与设置菜单共用）。
    window.__menuSerials = menuSerials();
    if (!fmEl) return;
    fmEl.classList.remove('stage-settings');

    // ★ 先显示，才能量到真实高度（内容多时高度会到 88vh）
    fmEl.style.left = '0px';
    fmEl.style.top = '0px';
    fmEl.classList.add('show');

    const w = fmEl.offsetWidth || 190;
    const h = fmEl.offsetHeight || 460;
    const left = Math.min(x, window.innerWidth - w - 12);
    const top = Math.min(y, window.innerHeight - h - 12);
    fmEl.style.left = Math.max(8, left) + 'px';
    fmEl.style.top = Math.max(8, top) + 'px';
    fmShown = true;
    positionAnchoredMenu();

    // 系统设置入口立即可用，不等待应用列表加载。
    ensureSettingsShortcuts(document.getElementById('menu-apps'));
    menuLayoutApply();

    // ★ 关键：弹出时加载这台手机真实安装的应用（对齐汇城 get_apk_list）
    const serial = window.__ctxSerial || curSerial || curDev?.serial || window.S?.selected;
    if (serial) {
      fmEl.dataset.serial = serial;
      const n = (window.__menuSerials || [serial]).length;
      const nameEl = $('fm-name');
      if (nameEl && n > 1) nameEl.title = `将作用于 ${n} 台手机`;
      try { loadPhoneApps(serial); } catch (e) { console.error('[apps]', e); }
    }
  }

  function hideFloatMenu() {
    if (!fmEl) return;
    fmEl.classList.remove('show', 'stage-settings');
    delete fmEl.dataset.serial;
    ++_appsRequest;
    fmShown = false;
    menuAnchor=null;
  }
  window.showFloatMenu = showFloatMenu;
  window.hideFloatMenu = hideFloatMenu;

  // 点击空白处关闭菜单
  document.addEventListener('mousedown', (e) => {
    if (!fmShown || fmEl?.classList.contains('stage-settings')) return;
    if (fmEl && fmEl.contains(e.target)) return;
    hideFloatMenu();
  });

  // 菜单顶部拖动
  const fmHead = fmEl?.querySelector('.fm-head');
  if (fmHead) {
    let drag = null;
    fmHead.addEventListener('mousedown', (e) => {
      if (e.target.tagName === 'BUTTON' || menuAnchor) return;
      if (fmEl.classList.contains('stage-settings')) {
        const r = fmEl.getBoundingClientRect();
        fmEl.classList.remove('stage-settings');
        fmEl.style.left = r.left + 'px'; fmEl.style.top = r.top + 'px';
      }
      drag = { x: e.clientX, y: e.clientY, l: fmEl.offsetLeft, t: fmEl.offsetTop };
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!drag) return;
      fmEl.style.left = (drag.l + e.clientX - drag.x) + 'px';
      fmEl.style.top = (drag.t + e.clientY - drag.y) + 'px';
    });
    document.addEventListener('mouseup', () => { drag = null; });
  }

  // 关闭按钮
  $raw('fm-close')?.addEventListener('click', hideFloatMenu);

  // 手机卡片：右键 / 双击标题 弹出菜单
  document.addEventListener('contextmenu', (e) => {
    const card = e.target.closest('.phone');
    if (card) {
      e.preventDefault();
      const serial = card.dataset.serial;
      if (serial) {
        const dev = (window.S?.devices || []).find((d) => d.serial === serial);
        // 右键菜单独立绑定设备，不改大屏的 curDev（旋转时用于坐标换算）。
        // ★ 关键修复：右键也要记住"当前操作的设备"，
        //   否则菜单里 curSerial 为空，所有操作都提示"请先选设备"
        window.__ctxSerial = serial;
        try { if (window.S) window.S.selected = serial; } catch (e) {}
        $('fm-idx').textContent = String(dev?.index || '').padStart(2, '0') || '01';
        $('fm-name').textContent = dev?.serial || serial;
        e.stopImmediatePropagation();
        showFloatMenu(0,0,card);
      }
      return;
    }
    // 大屏同样右键打开锚定菜单，不再发送返回键。
    const large=e.target.closest('#stage');
    if (large && curSerial) {
      e.preventDefault();e.stopImmediatePropagation();
      window.__ctxSerial=curSerial;
      $('fm-idx').textContent=String(curDev?.index||'').padStart(2,'0');
      $('fm-name').textContent=curSerial;
      showFloatMenu(0,0,large);
    }
  }, true);


  // ★ 清空画布（切换设备时防止残留花屏最后一帧）
  //   ★★ 绝对不能用 getContext('webgl')！canvas 一旦有了 webgl 上下文，
  //      就再也拿不到 2d 上下文了，会导致画面完全无法绘制。
  function clearStage() {
    if (!stage) return;
    document.getElementById('stage')?.classList.add('hidden');
    const ctx = stage.getContext('2d');
    if (ctx) {
      ctx.fillStyle = '#000';
      ctx.fillRect(0, 0, stage.width || 408, stage.height || 720);
    }
  }


  // ★★ 按 canvas 真实像素宽高比适配 CSS 尺寸（不同机型编码分辨率不同：
  //    1080x1920 → 408x720 (0.567)；1080x2280 → 344x720 (0.478)。
  //    写死 9/16 会把后者横向拉伸 15%，导致画面变形 + 点击坐标偏移。
  function fitStageCanvas() {
    if (!stage) return;
    const cw = stage.width;
    const ch = stage.height;
    if (!cw || !ch) return;
    const host = stage.parentElement;
    if (!host) return;
    const ratio = window.__bigSizeRatio || 0.85;
    const aiHost = host.closest('#rp-chat .ai-live-stage-host');
    const observe = aiHost?.closest('.exec-observe');
    // AI 复用同一 canvas；尺寸按观察区而不是主界面视口计算。
    const maxW = aiHost ? Math.max(1, aiHost.clientWidth - 2) : Math.max(120, Math.min(900, innerWidth - 32) * Math.min(1, ratio));
    // 大屏底部打字栏（.stage-input.show）出现时给它预留 52px否则会被画布盖住
    const inputH = host.querySelector?.('.stage-input.show') ? 52 : 0;
    const maxH = aiHost ? Math.max(1, Math.min(innerHeight - 160, (observe?.clientHeight || innerHeight - 120) - 100) - inputH) : Math.max(120, Math.min(innerHeight - 110, 950) * Math.min(1, ratio) - 44 - inputH);
    const odd=displayRotation%180!==0;
    const scale = Math.min(maxW / (odd?ch:cw), maxH / (odd?cw:ch));
    const width = Math.round(cw * scale), height = Math.round(ch * scale);
    const boundW=odd?height:width,boundH=odd?width:height;
    const _inputH = host.querySelector?.('.stage-input.show') ? 52 : 0;
    host.style.width = (boundW + 2) + 'px';host.style.height = (boundH + 46 + _inputH) + 'px';
    stage.style.width = width + 'px';stage.style.height = height + 'px';
    stage.style.transformOrigin='center';stage.style.transform=`rotate(${displayRotation}deg)`;
    stage.style.left=((boundW-width)/2)+'px';stage.style.top=(44+(boundH-height)/2)+'px';
  }
  window.fitStageCanvas = fitStageCanvas;
  window.rotateLocalDisplay = rotateLocalDisplay;
  window.addEventListener('resize', fitStageCanvas);
  if (window.ResizeObserver && stage?.parentElement) {
    // guard：样式未变化时直接返回，避免“改样式→触发观察→再改样式”的 ResizeObserver 循环报错。
    let lastW = '', lastH = '', lastBw = '', lastBh = '';
    new ResizeObserver(() => {
      const host = stage.parentElement;
      if (stage.style.width === lastW && stage.style.height === lastH &&
          host.style.width === lastBw && host.style.height === lastBh) return;
      lastW = stage.style.width; lastH = stage.style.height;
      lastBw = host.style.width; lastBh = host.style.height;
      fitStageCanvas();
    }).observe(stage.parentElement);
  }
  // 旋转或尺寸变化后，触控坐标必须以最新编码尺寸换算，否则点按位置会偏。
  window.__setStageSize = (w, h) => {
    if (w > 0 && h > 0 && curDev?.width && curDev?.height) {
      const long = Math.max(curDev.width, curDev.height), short = Math.min(curDev.width, curDev.height);
      [stageW, stageH] = w > h ? [long, short] : [short, long];
    }
    fitStageCanvas();
  };


  // ★ 文字输入（对齐汇城 XWKeyboard 广播方案）
  let _textBox = null;
  function initTextInput() {
    _textBox = document.getElementById('stage-text');
    if (!_textBox || _textBox._bound) return;
    _textBox._bound = true;

    // Enter 发送
    _textBox.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendTextToPhone(_textBox.value);
      }
    });

    const btn = document.getElementById('stage-text-send');
    if (btn) {
      btn.addEventListener('click', () => sendTextToPhone(_textBox.value));
    }
    const clr = document.getElementById('stage-text-clear');
    if (clr) {
      clr.addEventListener('click', () => {
        if (!curSerial) return;
        post('/api/text/clear', { serial: curSerial }).then(() => {
          if (_textBox) _textBox.value = '';
        });
      });
    }
  }

  async function sendTextToPhone(txt) {
    if (!curSerial || !txt) return;
    const r = await post('/api/text', { serial: curSerial, text: txt });
    if (r && r.ok) {
      toast('已输入');
      if (_textBox) _textBox.value = '';
    } else {
      toast('输入失败');
    }
  }
  window.sendTextToPhone = sendTextToPhone;

  // ★★ 直接键盘（对齐汇城）：大屏打开时 PC 按键直达手机不走输入框。
  // 字母数字符号空格回车退格方向键直达；中文走不了键码请走底部输入框。
  const DIRECT_KEYCODES = {
    KeyA:29,KeyB:30,KeyC:31,KeyD:32,KeyE:33,KeyF:34,KeyG:35,KeyH:36,KeyI:37,
    KeyJ:38,KeyK:39,KeyL:40,KeyM:41,KeyN:42,KeyO:43,KeyP:44,KeyQ:45,KeyR:46,
    KeyS:47,KeyT:48,KeyU:49,KeyV:50,KeyW:51,KeyX:52,KeyY:53,KeyZ:54,
    Digit0:7,Digit1:8,Digit2:9,Digit3:10,Digit4:11,Digit5:12,Digit6:13,
    Digit7:14,Digit8:15,Digit9:16,
    Numpad0:144,Numpad1:145,Numpad2:146,Numpad3:147,Numpad4:148,
    Numpad5:149,Numpad6:150,Numpad7:151,Numpad8:152,Numpad9:153,
    NumpadAdd:157,NumpadSubtract:156,NumpadMultiply:155,NumpadDivide:154,NumpadDecimal:158,
    Space:62,Minus:69,Equal:70,BracketLeft:71,BracketRight:72,Backslash:73,
    Semicolon:74,Quote:75,Backquote:68,Comma:55,Period:56,Slash:76,
  };
  let directKbdOn = true;
  try { directKbdOn = localStorage.getItem('ff_kbd_direct') !== '0'; } catch (e) {}
  let __keyChain = Promise.resolve();
  function postDirectKey(code, shift) {
    const serial = curSerial;
    if (!serial) return;
    __keyChain = __keyChain.then(() =>
      post('/api/key', { serial, key: '', code, shift: !!shift }).catch(() => {})
    ).catch(() => {});
  }
  function paintDirectKbdBtn() {
    const b = document.getElementById('stage-kbd');
    if (!b) return;
    b.setAttribute('aria-pressed', String(!!directKbdOn));
    b.style.opacity = directKbdOn ? '1' : '.45';
  }
  function initDirectKeys() {
    const b = document.getElementById('stage-kbd');
    if (b && !b._bound) {
      b._bound = true;
      b.onclick = (e) => {
        e.stopPropagation();
        directKbdOn = !directKbdOn;
        try { localStorage.setItem('ff_kbd_direct', directKbdOn ? '1' : '0'); } catch (err) {}
        paintDirectKbdBtn();
        toast(directKbdOn ? '直接键盘已开：在大屏上直接打字，中文用拼音选词自动发送' : '直接键盘已关');
      };
    }
    paintDirectKbdBtn();
    if (document.__directKeysBound) return;
    document.__directKeysBound = true;
    document.addEventListener('keydown', (e) => {
      if (!directKbdOn || !curSerial) return;
      const box = document.getElementById('stage');
      if (!box || box.classList.contains('hidden')) return;
      const tag = (e.target.tagName || '').toLowerCase();
      // 隐藏输入框是直达键盘的一部分不算“在输入”
      if (e.target.id !== 'stage-ime' && (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      // 输入法组字中（拼音选词）：一个键都别拦也别转发，等 compositionend 整串发
      if (e.isComposing || e.keyCode === 229 || e.key === 'Unidentified') return;
      const k = e.key;
      if (!k || k === 'Shift' || k === 'Control' || k === 'Alt' || k === 'Meta' || k === 'CapsLock') return;
      if (k === 'Escape' || k === 'F1' || k === 'F2' || k === 'F5') return;
      let code = null, shift = false;
      if (k === 'Enter') code = 66;
      else if (k === 'Backspace') code = 67;
      else if (k === 'Tab') code = 61;
      else if (k === 'Delete') code = 112;
      else if (k === 'ArrowUp') code = 19;
      else if (k === 'ArrowDown') code = 20;
      else if (k === 'ArrowLeft') code = 21;
      else if (k === 'ArrowRight') code = 22;
      else if (e.code && DIRECT_KEYCODES[e.code] !== undefined) {
        code = DIRECT_KEYCODES[e.code];
        if (/^Key[A-Z]$/.test(e.code)) shift = (k !== k.toLowerCase());
        else shift = !!e.shiftKey;
      }
      if (code === null) return;
      e.preventDefault();
      postDirectKey(code, shift);
    });
  }
  window.initDirectKeys = initDirectKeys;

  // ★★ 隐藏输入框：接电脑输入法（拼音选词）的组字结果整串直达手机。
  // 英文快道走键码（上面的 keydown），中文组字走这里（compositionend → 熊猫广播）。
  function initDirectIme() {
    if (document.__directImeBound) return;
    document.__directImeBound = true;
    let box = document.getElementById('stage-ime');
    if (!box) {
      box = document.createElement('input');
      box.id = 'stage-ime';
      box.setAttribute('autocomplete', 'off');
      box.setAttribute('aria-label', '电脑输入法直达手机');
      box.style.cssText = 'position:fixed;left:0;top:0;width:4px;height:20px;opacity:0;pointer-events:none;border:0;padding:0;';
      document.body.append(box);
    }
    let composing = false;
    box.addEventListener('compositionstart', () => { composing = true; });
    box.addEventListener('compositionend', () => {
      composing = false;
      const text = box.value;
      box.value = '';
      if (text && curSerial) post('/api/text', { serial: curSerial, text });
    });
    box.addEventListener('input', () => {
      // 非组字状态下进了字说明快道漏了（如生僻符号）走广播补发，不断字
      if (composing) return;
      const text = box.value;
      box.value = '';
      if (text && curSerial && document.getElementById('stage') && !document.getElementById('stage').classList.contains('hidden')) {
        post('/api/text', { serial: curSerial, text });
      }
    });
    window.focusDirectIme = () => {
      if (!directKbdOn) return;
      const st = document.getElementById('stage');
      if (!st || st.classList.contains('hidden')) return;
      try { box.focus({ preventScroll: true }); } catch (e) { try { box.focus(); } catch (_) {} }
    };
  }
  window.initDirectIme = initDirectIme;


  // ★★ 手机应用列表（对齐汇城：显示该机所有已安装应用，点击直接启动）
  let _appsRequest = 0;

  // 系统设置快捷入口：菜单一打开就显示，不等应用列表（aapt 可能较慢）。
  const SETTINGS_SHORTCUTS = [
    ['settings', '设置'], ['connections', '连接设置'], ['wifi', '手机 Wi-Fi'],
    ['ime', '管理输入法'], ['language', '语言和输入法'], ['accessibility', '无障碍设置'],
  ];
  function ensureSettingsShortcuts(ul) {
    if (!ul || ul.querySelector('li[data-setting]')) return;
    const frag = document.createDocumentFragment();
    for (const [target, label] of SETTINGS_SHORTCUTS) {
      const li = document.createElement('li');
      li.dataset.setting = target;
      const icon = document.createElement('i');
      icon.textContent = target === 'wifi' ? '📶' : '⚙';
      const span = document.createElement('span');
      span.textContent = label;
      li.append(icon, span);
      frag.append(li);
    }
    ul.prepend(frag);
  }
  window.ensureSettingsShortcuts = ensureSettingsShortcuts;

  async function loadPhoneApps(serial, refresh = false) {
    const ul = document.getElementById('menu-apps');
    if (!ul || !serial) return;
    const request = ++_appsRequest;
    ul.innerHTML = '<li class="fm-loading">加载应用列表…</li>';
    try {
      const r = await fetch(`/api/devices/${encodeURIComponent(serial)}/apps${refresh ? '?refresh=true' : ''}`);
      const d = await r.json();
      if (request !== _appsRequest) return;
      if (!r.ok || !d.ok || !Array.isArray(d.apps)) {
        ul.innerHTML = '<li class="fm-loading">获取应用失败，请检查设备连接后刷新</li>';
        ensureSettingsShortcuts(ul);
        return;
      }
      if (!d.apps.length) {
        ul.innerHTML = '<li class="fm-loading">没有已安装的第三方应用</li>';
        ensureSettingsShortcuts(ul);
        return;
      }
      // 已识别应用优先，同组按显示名称排序。
      const byName = (a, b) => String(a.name || a.package).localeCompare(String(b.name || b.package), 'zh-CN');
      const known = d.apps.filter((a) => a.known).sort(byName);
      const other = d.apps.filter((a) => !a.known).sort(byName);
      const all = known.concat(other);

      const ICONS = {
        douyin: '🎵', xhs: '📕', bili: '📺', kuaishou: '⚡', wechat: '💬',
        qq: '🐧', taobao: '🛒', jd: '🏬', alipay: '💰', pdd: '🛍️',
        dingtalk: '📌', settings: '⚙️', camera: '📷', gallery: '🖼️',
        youtube: '▶️', instagram: '📸', zhihu: '📘', weibo: '🌐',
        music: '🎧', video: '🎬', generic: '📦',
      };

      ul.replaceChildren();
      ensureSettingsShortcuts(ul);
      for (const a of all) {
        const li = document.createElement('li');
        li.dataset.app = a.package;
        if (a.known) li.className = 'fm-app-known';
        const icon = document.createElement('i'); icon.textContent = Object.hasOwn(ICONS, a.icon) ? ICONS[a.icon] : '📦';
        if(a.icon==='tiktok'){
          // Recognisable brand fallback, never a generic folder/package icon.
          icon.textContent='♪';icon.style.cssText='background:#050505;color:white;text-shadow:-1px -1px #25f4ee,1px 1px #fe2c55;border-radius:5px;display:inline-grid;place-items:center;width:24px;height:24px;font:bold 23px sans-serif';
        }
        const png = a.icon_png_base64;
        const hasPng = typeof png === 'string' && png.length <= 349528 && png.length % 4 === 0 && /^[A-Za-z0-9+/]+={0,2}$/.test(png);
        if (hasPng) {
          const img = document.createElement('img'); img.alt = ''; img.width = 24; img.height = 24;
          img.src = 'data:image/png;base64,' + png;
          img.onerror = () => { icon.replaceChildren(); icon.textContent = Object.hasOwn(ICONS, a.icon) ? ICONS[a.icon] : '📦'; li.title = '图标加载失败，当前为示意图标'; };
          icon.replaceChildren(img);
        }
        const name = document.createElement('span'); name.textContent = a.name || a.package;
        const nameOrigin = a.name_source === 'device_aapt' ? '名称来自设备 APK' :
          a.name_source === 'preset' ? '应用名称为预设识别' : '未获取真实应用名称，当前显示包名';
        li.title = nameOrigin + (hasPng ? '，PNG 图标来自设备 APK' : '，图标为示意');
        li.append(icon, name); ul.append(li);
      }
      console.log(`[apps] ${serial} 共 ${all.length} 个应用`);
      // 应用列表填充后菜单尺寸变化，重新贴回显示框，避免停留在初始位置。
      window.positionAnchoredMenu?.();
    } catch (e) {
      if (request === _appsRequest) ul.innerHTML = '<li class="fm-loading">加载失败</li>';
    }
  }
  window.loadPhoneApps = loadPhoneApps;
  document.getElementById('refresh-phone-apps')?.addEventListener('click', (e) => {
    e.stopPropagation();
    const serial = fmEl?.dataset.serial;
    if (!serial) return toast('请先选择手机');
    loadPhoneApps(serial, true);
  });


  // ==================== 右键菜单全部功能接线（对齐汇城） ====================

  async function api(url, body) {
    try {
      const r = await fetch(url, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const result = await r.json();
      if (!r.ok) return { ok: false, error: result?.error || result?.detail || `HTTP ${r.status}` };
      return result;
    } catch (e) { return { ok: false, error: String(e) }; }
  }

  function chooseMenuOption(title, options) {
    return new Promise(resolve=>{
      const dialog=document.createElement('dialog');dialog.className='device-props-dialog';
      const heading=document.createElement('h3');heading.textContent=title;dialog.append(heading);
      const select=document.createElement('select');select.style.cssText='display:block;min-width:300px;max-width:70vw;padding:10px;margin:12px 0;background:#292b35;color:#fff';
      options.forEach((o,i)=>{const el=document.createElement('option');el.value=String(i);el.textContent=typeof o==='string'?o:o.label;select.append(el);});
      const ok=document.createElement('button');ok.textContent='确认';ok.disabled=!options.length;const cancel=document.createElement('button');cancel.textContent='取消';
      const finish=v=>{dialog.close();dialog.remove();resolve(v);};ok.onclick=()=>finish(Number(select.value));cancel.onclick=()=>finish(null);dialog.oncancel=e=>{e.preventDefault();finish(null);};dialog.append(select,ok,cancel);document.body.append(dialog);dialog.showModal();
    });
  }
  function managePhrases(){
    return new Promise(resolve=>{
      const dialog=document.createElement('dialog');dialog.className='device-props-dialog';
      const h=document.createElement('h3');h.textContent='管理快捷短语';
      const list=document.createElement('div');list.style.cssText='max-height:45vh;overflow:auto;min-width:320px';
      const input=document.createElement('input');input.placeholder='输入新短语';input.style.cssText='display:block;width:100%;box-sizing:border-box;margin:10px 0;padding:9px;background:#111;color:#fff;border:1px solid #666';
      const add=document.createElement('button');add.textContent='添加';
      const close=document.createElement('button');close.textContent='完成';
      const read=()=>{try{const v=JSON.parse(localStorage.getItem('ff_phrases')||'[]');return Array.isArray(v)?v.filter(x=>typeof x==='string'):[];}catch(e){return [];}};
      const write=v=>localStorage.setItem('ff_phrases',JSON.stringify(v.slice(-50)));
      function render(){const items=read();list.replaceChildren();if(!items.length){const p=document.createElement('p');p.textContent='还没有短语，请在下方输入后点“添加”。';list.append(p);return;}items.forEach((text,i)=>{const row=document.createElement('div');row.style.cssText='display:flex;gap:8px;align-items:center;margin:6px 0';const span=document.createElement('span');span.textContent=text;span.style.cssText='flex:1;overflow-wrap:anywhere';const del=document.createElement('button');del.textContent='删除';del.onclick=()=>{const next=read();next.splice(i,1);write(next);render();};row.append(span,del);list.append(row);});}
      add.onclick=()=>{const text=input.value.trim();if(!text)return;const items=read();if(!items.includes(text))items.push(text);write(items);input.value='';render();};
      input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();add.click();}};
      close.onclick=()=>{dialog.close();};
      dialog.addEventListener('close',()=>{dialog.remove();resolve();});
      dialog.append(h,list,input,add,close);document.body.append(dialog);dialog.showModal();input.focus();render();
    });
  }
  function showDeviceResult(title,text){
    const dialog=document.createElement('dialog');dialog.className='device-props-dialog';const h=document.createElement('h3');h.textContent=title;const pre=document.createElement('pre');pre.textContent=text;const close=document.createElement('button');close.textContent='关闭';close.onclick=()=>{dialog.close();dialog.remove();};dialog.append(h,pre,close);document.body.append(dialog);dialog.showModal();
  }
  function askText(title, def) {
    return window.prompt(title, def || '');
  }
  function editDeviceText(title, value) {
    return new Promise(resolve => {
      const dialog=document.createElement('dialog');
      dialog.style.cssText='background:#23262e;color:white;border:1px solid #777;border-radius:10px;padding:22px;min-width:300px';
      const label=document.createElement('label');label.textContent=title;label.style.display='block';
      const input=document.createElement('input');input.value=value;input.style.cssText='display:block;width:280px;margin:14px 0;padding:9px;color:white;background:#111;border:1px solid #777';label.append(input);
      const cancel=document.createElement('button');cancel.textContent='取消';
      const save=document.createElement('button');save.textContent='保存';save.style.cssText='margin-left:12px;background:#7356df;color:white;padding:7px 18px;border:0;border-radius:5px';
      const finish=v=>{dialog.close();dialog.remove();resolve(v);};cancel.onclick=()=>finish(null);save.onclick=()=>finish(input.value);
      input.onkeydown=e=>{if(e.key==='Enter')finish(input.value);};dialog.oncancel=e=>{e.preventDefault();finish(null);};
      dialog.append(label,cancel,save);document.body.append(dialog);dialog.showModal();input.focus();input.select();
    });
  }

  function initMenuActions() {
    // ============================================================
    // ★ 手机应用列表：事件委托
    //   列表会被 loadPhoneApps() 整段重建，所以不能给每个 li 单独绑，
    //   必须在父容器上做委托，重建多少次都不丢。
    // ============================================================
    const appsUl = document.getElementById('menu-apps');
    if (appsUl && !appsUl._boundApp) {
      appsUl._boundApp = true;
      appsUl.addEventListener('click', async (e) => {
        const setting=e.target.closest('li[data-setting]');
        if(setting){e.stopPropagation();const serial=fmEl?.dataset.serial;if(!serial)return;
          const one=(s)=>api('/api/menu/settings',{serial:s,target:setting.dataset.setting});
          if (fanoutSerials().length <= 1) { const result=await one(serial);toast(result.ok?'已打开'+setting.textContent:(result.error||'打开失败')); }
          else { await fanoutMenu('打开设置页', one); }
          return;}
        const li = e.target.closest('li[data-app]');
        if (!li) return;
        e.stopPropagation();

        const serial = document.getElementById('float-menu')?.dataset.serial;
        if (!serial) { toast('请先选设备'); return; }

        const pkg = li.dataset.app;
        const act = li.dataset.activity || '';
        const label = li.querySelector('span')?.textContent || pkg;

        const one = async (s) => {
          const r = await api(`/api/devices/${encodeURIComponent(s)}/launch`, { package: pkg, activity: act });
          return { ok: r.ok, error: r.error || r.reason };
        };
        if (fanoutSerials().length <= 1) {
          toast(`正在打开「${label}」…`);
          try {
            const r = await one(serial);
            toast(r.ok ? `已发送启动请求：${label}` : `启动失败（${label}）：${r.error || '未知错误'}`);
          } catch (err) {
            toast('打开失败');
          }
        } else {
          toast(`正在打开「${label}」…`);
          await fanoutMenu(`打开${label}`, one);
        }
      });
    }

    const menu = document.getElementById('float-menu');
    if (!menu || menu._boundActions) return;
    menu._boundActions = true;

    // 独立 data-act：不参加用户 data-op 排序/隐藏，始终保留最顶部。
    const openMenuStage = (e) => {
      const entry = e.target.closest('[data-act="open-bigscreen"]');
      if (!entry || !menu.contains(entry)) return;
      if (e.type === 'keydown' && !['Enter', ' '].includes(e.key)) return;
      e.preventDefault();
      const serial = menu.dataset.serial;
      if (!serial) { toast('请先选设备'); return; }
      hideFloatMenu();
      window.openBigscreen?.(serial);
    };
    menu.addEventListener('click', openMenuStage);
    menu.addEventListener('keydown', openMenuStage);
    menu.addEventListener('click', async (e) => {
      const li = e.target.closest('li[data-op]');
      if (!li || !li.closest('#menu-list, #menu-devinfo')) return;
      const op = li.dataset.op;
      const serial = menu.dataset.serial;
      if (!serial) { toast('请先选设备'); return; }
      const dev = (window.S?.devices || []).find((d) => d.serial === serial);
      // ★ 扇出目标与执行函数都在模块顶层定义，右键菜单和大屏设置菜单两个入口共用；
      //   这里直接引用模块级函数，不再从 window 取（取到 undefined 会整段操作失败）。
      const serials = fanoutSerials();
      const multiCount = serials.length;
      const labelOf = fanoutLabel;
      // 对集合中每台顺序执行 fn(s)，汇总一次 toast；dangerous 会先弹一次总确认。
      async function fanout(hint, fn, opts = {}) {
        const targets = opts.targets || serials;
        if (targets.length <= 1) return fn(targets[0]);
        if (opts.danger && !window.confirm(`${hint}将作用于 ${targets.length} 台手机（${targets.map(labelOf).join('、')}），继续？`)) return { ok: false, cancelled: true };
        toast(`${hint}下发中（${targets.length} 台）…`);
        let ok = 0; const bad = [], rets = [];
        for (const s of targets) {
          try { const r = await fn(s); rets.push({ serial: s, r }); if (r && r.ok) ok++; else bad.push(labelOf(s)); }
          catch (e) { bad.push(labelOf(s)); rets.push({ serial: s, r: null, error: String(e) }); }
        }
        const msg = bad.length ? `${hint}完成：${ok}/${targets.length} 台成功，失败：${bad.join('、')}` : `${hint}完成：${targets.length} 台全部成功`;
        toast(msg);
        return { ok: bad.length === 0, okCount: ok, bad, rets };
      }

      switch (op) {
        case 'edit-number': {
          const v = await editDeviceText('修改编号（1–99999）', String(dev?.index ?? ''));
          if (v === null || v === '') return;
          const n = parseInt(v, 10);
          if (!/^\d+$/.test(v) || n < 1 || n > 99999) { toast('编号须为1–99999的整数'); return; }
          const r = await api(`/api/devices/${encodeURIComponent(serial)}/index`, { index: n });
          if (r.ok && dev) { dev.index = r.index; window.renderGrid?.(); }
          toast(r.ok ? `编号已改为 ${n}` : (r.error || '修改失败'));
          break;
        }

        case 'edit-name': {
          const v = await editDeviceText('修改名称（1–20字）', dev?.name || '');
          if (v === null) return;
          const r = await api(`/api/devices/${encodeURIComponent(serial)}/name`, { name: v });
          if (r.ok && dev) { dev.name = r.name; window.renderGrid?.(); }
          toast(r.ok ? '名称已保存' : (r.error || '保存失败'));
          break;
        }

        case 'reboot': {
          const r = await fanout('重启', (s) => api(`/api/devices/${encodeURIComponent(s)}/reboot`, {}), { danger: true });
          if (r && !r.rets) toast(r.ok ? '重启命令已下发' : '重启失败');
          break;
        }

        case 'install-apk': {
          const input = document.createElement('input');
          input.type = 'file';
          input.accept = '.apk';
          input.onchange = async () => {
            const f = input.files?.[0];
            if (!f) return;
            toast(`正在安装 ${f.name}…（可能较慢）`);
            const fd = new FormData();
            fd.append('file', f);
            const one = async (s) => {
              const rr = await fetch(`/api/devices/${encodeURIComponent(s)}/apk-upload`, { method: 'POST', body: fd });
              return rr.json();
            };
            if (serials.length <= 1) {
              try {
                const j = await one(serial);
                toast(j.ok ? '安装成功' : '安装失败');
              } catch (err) { toast('安装失败'); }
            } else {
              if (!window.confirm(`将 ${f.name} 安装到 ${serials.length} 台手机？`)) return;
              await fanout('安装APK', one, {});
            }
          };
          input.click();
          break;
        }

        case 'push-file': {
          const input = document.createElement('input');
          input.type = 'file';
          input.onchange = async () => {
            const f = input.files?.[0];
            if (!f) return;
            const destinations=[['相册（照片）','/sdcard/DCIM/FishFleet/'],['相册（视频）','/sdcard/Movies/FishFleet/'],['文件管理／下载','/sdcard/Download/'],['文档','/sdcard/Documents/']];
            const choice=await chooseMenuOption('导入到手机的位置',destinations.map(x=>x[0]));
            if(choice===null)return;const remote=destinations[choice][1];
            toast(`正在推送 ${f.name}…`);
            const fd = new FormData();
            fd.append('file', f);
            fd.append('remote', remote);
            const one = async (s) => {
              const rr = await fetch(`/api/devices/${encodeURIComponent(s)}/push-upload`, { method: 'POST', body: fd });
              return rr.json();
            };
            if (serials.length <= 1) {
              try {
                const j = await one(serial);
                toast(j.ok ? '推送成功' : '推送失败');
              } catch (err) { toast('推送失败'); }
            } else {
              await fanout('推送文件', one, {});
            }
          };
          input.click();
          break;
        }

        case 'pull-file': {
          let target = serial;
          if (serials.length > 1) {
            const pick = await chooseMenuOption(`从哪台手机导出（共选中 ${serials.length} 台）`, serials.map((s) => labelOf(s)));
            if (pick === null) return;
            target = serials[pick];
          }
          const kinds=['相册照片','截图','下载','视频','文档'];
          const kindIdx=await chooseMenuOption('从手机导出：选择位置',kinds);
          if(kindIdx===null)return;
          const kind=kinds[kindIdx];
          const listed=await api(`/api/menu/${encodeURIComponent(target)}/files?kind=${encodeURIComponent(kind)}`);
          if(!listed.ok){toast('读取目录失败：'+(listed.error||'未知错误'));break;}
          if(!listed.files.length){showDeviceResult('导出文件',`${kind} 目录没有可导出的文件`);break;}
          const fileIdx=await chooseMenuOption(`选择要导出的文件（${kind}）`,listed.files.map(f=>`${f.name} · ${(f.bytes/1048576).toFixed(2)}MiB`));
          if(fileIdx===null)return;
          try{const chosen=listed.files[fileIdx];const res=await fetch(`/api/menu/${encodeURIComponent(target)}/file?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(chosen.name)}&path=${encodeURIComponent(chosen.path.slice(0,chosen.path.lastIndexOf('/')))}`);if(!res.ok){const j=await res.json().catch(()=>({}));throw Error(j.detail||'下载失败');}const blob=await res.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=chosen.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);toast('已请求下载：'+chosen.name);}catch(e){toast('导出失败：'+e.message);}
          break;
        }

        case 'get-clipboard': {
          if (serials.length <= 1) {
            const r = await api(`/api/devices/${encodeURIComponent(serial)}/clipboard`);
            if (r.ok) {
              try { await navigator.clipboard.writeText(r.text || ''); } catch (e) {}
              showDeviceResult('手机剪贴板',r.text||'（剪贴板为空，或系统限制当前应用读取）');
            } else showDeviceResult('手机剪贴板读取失败',r.error||'请检查设备剪贴板权限');
            break;
          }
          const texts = [];
          for (const s of serials) {
            try {
              const r = await api(`/api/devices/${encodeURIComponent(s)}/clipboard`);
              texts.push(`${labelOf(s)}：${r.ok ? (r.text || '（空）') : '读取失败'}`);
            } catch (e) { texts.push(`${labelOf(s)}：读取失败`); }
          }
          showDeviceResult(`手机剪贴板（${serials.length} 台）`, texts.join('\n'));
          break;
        }

        case 'adb-cmd': {
          const cmd = askText('ADB Shell 命令', 'wm size');
          if (!cmd) return;
          if (serials.length <= 1) {
            const r = await api(`/api/devices/${encodeURIComponent(serial)}/shell`, { command: cmd });
            alert((r.stdout || '') + (r.stderr ? '\n[stderr]\n' + r.stderr : ''));
            break;
          }
          const outs = [];
          for (const s of serials) {
            try {
              const r = await api(`/api/devices/${encodeURIComponent(s)}/shell`, { command: cmd });
              outs.push(`—— ${labelOf(s)} ——\n${r.stdout || ''}${r.stderr ? '\n[stderr]\n' + r.stderr : ''}`);
            } catch (e) { outs.push(`—— ${labelOf(s)} ——\n执行失败`); }
          }
          alert(outs.join('\n\n'));
          break;
        }

        case 'auto-swipe': {
          const dialog=document.createElement('dialog');dialog.className='device-props-dialog';
          dialog.innerHTML='<h3>自动滑屏</h3><label>方向 <select id="swipe-dir"><option value="up">向上滑</option><option value="down">向下滑</option><option value="left">向左滑</option><option value="right">向右滑</option></select></label><p><label>运行时长（秒） <input id="swipe-total" type="number" min="1" max="86400" value="60"></label></p><p><label>每次滑动（秒） <input id="swipe-duration" type="number" min="0.1" max="5" step="0.1" value="0.3"></label></p><p><label>滑动间隔（秒） <input id="swipe-interval" type="number" min="0.1" max="3600" step="0.1" value="3"></label></p><button data-act="start">开始</button> <button data-act="stop">停止</button> <button data-act="close">关闭</button><p class="swipe-status" role="status"></p>';
          dialog.addEventListener('click',async event=>{const action=event.target.dataset.act;if(!action)return;if(action==='close'){dialog.close();dialog.remove();return;}const status=dialog.querySelector('.swipe-status');event.target.disabled=true;try{
            const one = (s) => api(`/api/devices/${encodeURIComponent(s)}/auto-swipe`,{action,direction:dialog.querySelector('#swipe-dir').value,total:Number(dialog.querySelector('#swipe-total').value),duration:Number(dialog.querySelector('#swipe-duration').value),interval:Number(dialog.querySelector('#swipe-interval').value)});
            if (serials.length <= 1) {
              const r = await one(serial);
              status.textContent=!r.ok?'操作失败：'+(r.error||r.reason||'未知错误'):r.already?'自动滑屏已在运行':r.stopping?'停止中，等待当前操作结束':r.running?'自动滑屏已开始':'自动滑屏已停止';
            } else {
              const rr = await fanout('自动滑屏', one, {});
              status.textContent = (rr && rr.cancelled) ? '已取消' : (rr && rr.ok ? `已下发 ${serials.length} 台` : `部分失败：${(rr.bad || []).join('、')}`);
            }
          }finally{event.target.disabled=false;}});
          dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
          break;
        }

        case 'record-action': {
          const status=await api(`/api/devices/${encodeURIComponent(serial)}/record/status`);
          const choice=await chooseMenuOption(status.recording?`录制中：已录 ${status.steps} 步`:'录制动作',status.recording?['停止并保存录制']:['开始录制此手机的点击、滑动、输入及按键']);
          if(choice===null)return;
          const r=await api(`/api/devices/${encodeURIComponent(serial)}/record/${status.recording?'stop':'start'}`,{});
          showDeviceResult('录制动作',r.ok?(status.recording?`已保存：${r.name}\n共 ${r.steps} 步，可从「执行动作」选择`:'已开始录制；操作完成后再次点击「录制动作」停止并保存。'):(r.error||'操作失败'));
          break;
        }

        case 'play-action': {
          const list=await api('/api/actions');const scripts=list.scripts||[];
          if(!scripts.length){showDeviceResult('执行动作','还没有录制或保存的动作，请先录制并停止保存。');break;}
          const idx=await chooseMenuOption('选择已录制／保存的动作',scripts.map(s=>s.name));if(idx===null)return;
          if(!confirm(`在${serials.length}台手机执行「${scripts[idx].name}」？敏感步骤仍需确认。`))return;
          await fanout('执行动作',(s)=>api(`/api/devices/${encodeURIComponent(s)}/record/play`,{script_id:scripts[idx].id}),{});
          showDeviceResult('执行动作','任务已创建，可在AI执行工作台核对状态及敏感操作。');
          break;
        }

        case 'run-task': {
          const name = askText('要执行的任务（为空则执行默认）', '');
          if (name === null) break;
          const r = await fanout('下发任务',(s)=>api(`/api/devices/${encodeURIComponent(s)}/task/run`, { task: name || '' }),{danger:multiCount>1});
          if (r && !r.rets) toast(r.ok ? '任务已下发' : '任务启动失败');
          break;
        }

        case 'stop-task': {
          await fanout('停止任务',(s)=>api(`/api/devices/${encodeURIComponent(s)}/task/stop`, {}),{});
          break;
        }

        case 'task-list': {
          const r = await api('/api/tasks');
          const list = Object.values(r.tasks || {}).slice(-15);
          alert(list.length
            ? list.map((t) => `${t.id?.slice(0, 8)} [${t.status}] ${t.kind || ''} ${t.device || ''}`).join('\n')
            : '暂无任务');
          break;
        }

        case 'switch-ime': {
          const r = await api(`/api/devices/${encodeURIComponent(serial)}/imes`);
          if (!r.ok || !r.imes?.length) { toast('未获取到输入法'); return; }
          const idx=await chooseMenuOption('切换输入法',r.imes.map(x=>`${x.name||x.label||x.id}${x.current?'（当前）':''}`));
          if(idx===null)return;
          const rr = await fanout('切换输入法',(s)=>api(`/api/devices/${encodeURIComponent(s)}/ime`, { ime: r.imes[idx].id }),{});
          if (rr && !rr.rets) toast(rr.ok ? '输入法已切换' : '切换失败');
          break;
        }

        case 'quick-phrase': {
          const phrases = JSON.parse(localStorage.getItem('ff_phrases') || '[]');
          const options=phrases.concat(['＋ 管理快捷短语…']);
          const idx=await chooseMenuOption(`选择快捷短语（发送到${serials.length}台手机）`,options);
          if(idx===null)return;
          if(idx===phrases.length){await managePhrases();break;}
          await fanout('发送短语',(s)=>api(`/api/text`, { serial:s, text: phrases[idx] }),{});
          break;
        }

        case 'rotate-right': {
          if(curSerial!==serial||document.getElementById('stage')?.classList.contains('hidden'))await window.openBigscreen(serial);
          rotateLocalDisplay();
          break;
        }
        // ==================== 扩展功能（对齐汇城）====================
        case 'uninstall-apk': {
          const pkg = askText('要卸载的应用包名（可从「手机应用」里复制）', '');
          if (!pkg) return;
          const r = await fanout('卸载应用',(s)=>api('/api/ext/uninstall_apk', { serial:s, package: pkg }),{danger:true});
          if (r && !r.rets) toast(r.ok ? `已卸载 ${pkg}` : '卸载失败：' + (r.message || ''));
          break;
        }

        case 'apk-info': {
          const pkg = askText('应用包名', '');
          if (!pkg) return;
          if (serials.length <= 1) {
            const r = await api('/api/ext/get_apk_info', { serial, package: pkg });
            if (!r.ok) { toast(r.error || '未安装'); return; }
            alert(
              `包名：${r.package}\n` +
              `版本：${r.version_name} (${r.version_code})\n` +
              `SDK：min ${r.min_sdk} / target ${r.target_sdk}\n` +
              `首装：${r.first_install}\n` +
              `更新：${r.last_update}\n` +
              `大小：${r.size_mb || '?'} MB\n` +
              `系统应用：${r.system ? '是' : '否'}\n` +
              `APK：${r.code_path || ''}`
            );
            break;
          }
          const lines = [];
          for (const s of serials) {
            try {
              const r = await api('/api/ext/get_apk_info', { serial: s, package: pkg });
              lines.push(`—— ${labelOf(s)} ——\n${r.ok ? `版本：${r.version_name} (${r.version_code})\n大小：${r.size_mb || '?'} MB` : '未安装'}`);
            } catch (e) { lines.push(`—— ${labelOf(s)} ——\n查询失败`); }
          }
          alert(lines.join('\n\n'));
          break;
        }

        case 'dev-info': {
          if (serials.length <= 1) {
            const r = await api('/api/ext/get_device_info', { serial });
            if (!r.ok) { toast('读取设备详情失败：' + (r.error || r.message || '未知错误')); break; }
            if (!r.model && !r.brand && !r.android_version && !r.sdk) {
              toast('读取设备详情失败：未获取到设备属性'); break;
            }
            const value = v => v === undefined || v === null || v === '' ? '未知' : v;
            alert(
              `连接设备：${serial}\n` +
              `设备属性序列号：${value(r.device_serial)}\n` +
              `型号：${value(r.model)}（${value(r.brand)}）\n` +
              `系统：Android ${value(r.android_version)}（SDK ${value(r.sdk)}）\n` +
              `构建：${value(r.build_id)}\n` +
              `架构：${value(r.cpu_abi)}\n` +
              `时区：${value(r.timezone)}\n` +
              `出厂属性密度：${value(r.density)} dpi\n` +
              `USB：${value(r.usb_mode)}\n` +
              `电池：${r.battery_level == null ? '未知' : r.battery_level + '%'}（status ${value(r.battery_status)}）\n` +
              `屏幕：${value(r.screen_state)}`
            );
            break;
          }
          const lines = [];
          for (const s of serials) {
            try {
              const r = await api('/api/ext/get_device_info', { serial: s });
              lines.push(`—— ${labelOf(s)} ——\n${r.ok ? `${r.model || ''} ${r.brand || ''} / Android ${r.android_version || '?'} / 电池${r.battery_level == null ? '未知' : r.battery_level + '%'}` : '读取失败'}`);
            } catch (e) { lines.push(`—— ${labelOf(s)} ——\n读取失败`); }
          }
          alert(lines.join('\n'));
          break;
        }

        case 'dev-density': {
          if (serials.length <= 1) {
            const r = await api('/api/ext/get_density', { serial });
            if (!r.ok || !Number.isFinite(Number(r.density)) || Number(r.density) <= 0) {
              toast('获取屏幕密度失败：' + (r.error || r.message || r.raw || '未获取到有效密度')); break;
            }
            const physical = /Physical density:\s*(\d+)/.exec(r.raw || '');
            const override = /Override density:\s*(\d+)/.exec(r.raw || '');
            const density = override ? override[1] : r.density;
            showDeviceResult('屏幕密度',`当前密度：${density} dpi\n`+(r.raw||'设备属性回退值，覆盖状态未知'));
            break;
          }
          const lines = [];
          for (const s of serials) {
            try {
              const r = await api('/api/ext/get_density', { serial: s });
              lines.push(`${labelOf(s)}：${r.ok ? r.density + ' dpi' : '失败'}`);
            } catch (e) { lines.push(`${labelOf(s)}：失败`); }
          }
          showDeviceResult('屏幕密度', lines.join('\n'));
          break;
        }

        case 'dev-usbmode': {
          const cur = await api('/api/ext/get_device_mode', { serial });
          if (!cur.ok) { toast('读取 USB 模式失败：' + (cur.error || '未知错误')); break; }
          const list = ['charging', 'mtp', 'ptp', 'rndis', 'midi', 'adb',
                        'mtp_adb', 'ptp_adb', 'rndis_adb', 'midi_adb'];
          const idx=await chooseMenuOption(`USB模式（当前：${(cur.modes||[]).join(', ')||'未知'}）`,['仅充电','文件传输 MTP','照片传输 PTP','USB网络共享','MIDI','仅ADB','文件传输＋ADB','照片传输＋ADB','网络共享＋ADB','MIDI＋ADB']);
          if(idx===null)return;
          const rr = await fanout('切换USB模式',(s)=>api('/api/ext/switch_device_mode', { serial: s, mode: list[idx] }),{danger:true,targets:serials});
          if (rr && !rr.rets) {
            toast('切换中，ADB 可能短暂断开…');
            toast(rr.ok ? (rr.confirmed ? `已切到 ${rr.applied}` : `切换请求已提交，${rr.applied} 状态未确认`) : `切换失败：${rr.error || '未知错误'}`);
          }
          break;
        }

        case 'dev-ip': {
          if (serials.length <= 1) {
            const r = await api('/api/ext/get_serial_ip', { serial });
            if (!r.ok || !r.ip) { toast('获取设备 IP 失败：' + (r.error || r.message || '未获取到 IP')); break; }
            try {
              await navigator.clipboard.writeText(r.ip);
              showDeviceResult('设备IP',`${r.ip}\n已复制到电脑剪贴板`);
            } catch (e) {
              showDeviceResult('设备IP',`${r.ip}\n未复制，请手动复制此内容`);
            }
            break;
          }
          const lines = [];
          for (const s of serials) {
            try {
              const r = await api('/api/ext/get_serial_ip', { serial: s });
              lines.push(`${labelOf(s)}：${r.ok && r.ip ? r.ip : '失败'}`);
            } catch (e) { lines.push(`${labelOf(s)}：失败`); }
          }
          showDeviceResult(`设备IP（${serials.length} 台）`, lines.join('\n'));
          break;
        }

        case 'dev-accessible': {
          const dlg = await api('/api/ext/accessible_dialog', undefined);
          if (dlg.ok && dlg.title) {
            const okTurn = window.confirm(`${dlg.title}\n${(dlg.items || []).join('\n')}\n\n${dlg.confirm || '确认转为无障碍'}？`);
            if (!okTurn) break;
          }
          const one = async (s) => {
            const installed = await api('/api/ext/install_accessible', { serial: s, apk_path: '' });
            if (!installed.ok) return installed;
            const result = await api('/api/ext/switch_accessible_mode', { serial: s, enable: true, src: 0 });
            if (!result.ok) return result;
            const after = await api('/api/ext/device_is_accessible', { serial: s });
            if (!after.ok) return { ok: false, error: '操作已提交，但无法确认无障碍状态' };
            if (!after.granted) return { ok: false, error: '操作后状态未达到预期，请在手机设置中检查' };
            return result;
          };
          const rr = await fanout('转为无障碍模式', one, { danger: true });
          if (rr && !rr.rets) toast('已转为无障碍模式');
          window.loadDevices?.();
          break;
        }

        case 'dev-accessible-reconnect': {
          const one = async (s) => {
            await api('/api/ext/switch_adb_mode', { serial: s, enable: true });
            const result = await api('/api/ext/switch_accessible_mode', { serial: s, enable: true, src: 2 });
            if (!result.ok) return result;
            const after = await api('/api/ext/device_is_accessible', { serial: s });
            if (!after.ok) return { ok: false, error: '操作已提交，但无法确认无障碍状态' };
            if (!after.granted) return { ok: false, error: '操作后状态未达到预期，请在手机设置中检查' };
            return result;
          };
          const rr = await fanout('重连无障碍模式', one, { danger: true });
          if (rr && !rr.rets) toast('无障碍模式已重连');
          window.loadDevices?.();
          break;
        }

        case 'dev-accessible-close': {
          const one = async (s) => {
            const result = await api('/api/ext/switch_accessible_mode', { serial: s, enable: false });
            if (!result.ok) return result;
            await api('/api/ext/switch_adb_mode', { serial: s, enable: true });
            return { ok: true, mode: 0, is_accessible: false };
          };
          const rr = await fanout('关闭无障碍模式', one, { danger: true });
          if (rr && !rr.rets) toast('无障碍模式已关闭，已恢复ADB');
          window.loadDevices?.();
          break;
        }

        case 'dev-hid': {
          const chk = await api('/api/ext/check_hid_app_installed', { serial });
          const mode = await api('/api/ext/is_hid_mode', { serial });
          if (!chk.ok) { toast('检查 HID 助手失败：' + (chk.error || '未知错误')); break; }
          const picked=await chooseMenuOption(`HID助手：${chk.installed?'已安装':'未安装'}；设备状态：${mode.is_hid?'进程运行中':'未确认运行'}`,['安装HID助手','开启HID','关闭HID','卸载电脑HID驱动（影响整台电脑）']);
          const act=picked===null?'':String(picked+1);
          if (act === '1') {
            await fanout('安装HID助手',(s)=>api('/api/ext/install_hid_app', { serial: s, apk_path: '' }),{});
          } else if (act === '2') {
            await fanout('开启HID',(s)=>api('/api/ext/switch_hid_mode', { serial: s, enable: true }),{});
          } else if (act === '3') {
            await fanout('关闭HID',(s)=>api('/api/ext/switch_hid_mode', { serial: s, enable: false }),{});
          } else if (act === '4') {
            if (!confirm('卸载 HID 驱动可能影响整台电脑上的设备，确认继续？')) break;
            const r = await api('/api/ext/remove_hid_driver', {});
            toast(`找到 ${r.found || 0} 个驱动，删除 ${(r.removed || []).length} 个`);
          }
          break;
        }

        case 'dev-wireless-add': {
          const input = await new Promise(resolve => {
            const dlg = document.createElement('dialog');
            dlg.className = 'device-props-dialog';
            const h = document.createElement('h3');
            h.textContent = '添加无线设备';
            const tip = document.createElement('p');
            tip.style.cssText = 'font-size:12px;color:#a9a2bd;margin:0 0 8px;max-width:360px';
            tip.textContent = '手机：设置 → 开发者选项 → 无线调试。只填 IP 会自动扫端口；有配对码就填「配对弹窗」里的 IP:端口。';
            const ipInput = document.createElement('input');
            ipInput.placeholder = '192.168.31.70:37639 或只填 IP';
            ipInput.style.cssText = 'display:block;width:320px;margin:8px 0;padding:9px;background:#111;color:#fff;border:1px solid #666';
            const codeInput = document.createElement('input');
            codeInput.placeholder = '配对码（可空，如 217711）';
            codeInput.style.cssText = ipInput.style.cssText;
            const ok = document.createElement('button');
            ok.textContent = '连接';
            const cancel = document.createElement('button');
            cancel.textContent = '取消';
            const finish = v => { dlg.close(); dlg.remove(); resolve(v); };
            ok.onclick = () => finish({ target: ipInput.value.trim(), code: codeInput.value.trim() });
            cancel.onclick = () => finish(null);
            dlg.oncancel = e => { e.preventDefault(); finish(null); };
            ipInput.onkeydown = e => { if (e.key === 'Enter') ok.click(); };
            dlg.append(h, tip, ipInput, codeInput, ok, cancel);
            document.body.append(dlg);
            dlg.showModal();
            ipInput.focus();
          });
          if (!input || !input.target) break;
          toast(input.code ? '正在配对并扫描端口…' : '正在扫描端口并连接…');
          const wr = await api('/api/ext/wireless_add', input);
          if (wr.ok) {
            toast(`已添加 ${wr.serial}`);
            window.loadDevices?.();
          } else {
            showDeviceResult('添加无线设备失败', (wr.error || '未知错误') + (wr.steps ? '\n\n' + JSON.stringify(wr.steps, null, 2) : ''));
          }
          break;
        }

        case 'dev-reconnect': {
          await fanout('重连设备',(s)=>api('/api/ext/reconnect_device', { serial: s, port: 5555 }),{danger:true});
          break;
        }

        case 'dev-restart-adb': {
          if (!confirm('重启 ADB 会让所有设备短暂断开，继续？')) return;
          toast('重启 ADB 中…');
          const r = await api('/api/ext/restart_adb', {});
          toast(r.ok ? `ADB 已重启（${r.devices} 台在线）` : '重启失败');
          break;
        }

        case 'dev-wallpaper': {
          const input=document.createElement('input');input.type='file';input.accept='image/png,image/jpeg,image/webp';
          input.onchange=async()=>{
            const f=input.files?.[0];if(!f)return;
            if(f.size>8*1024*1024){toast('图片不能超过8MiB');return;}
            toast('正在把电脑图片传入手机并打开设为壁纸…');
            const fd=new FormData();fd.append('file',f);
            const one = async (s) => {
              const res=await fetch(`/api/menu/${encodeURIComponent(s)}/wallpaper`,{method:'POST',body:fd});
              return res.json().then(j=>({ok:res.ok,note:j.note||j.detail}));
            };
            if (serials.length <= 1) {
              try{const j=await one(serial);showDeviceResult('设为壁纸',j.ok?(j.note||'已推送到手机，请在手机画面确认。'):('设为壁纸失败：'+(j.note||'未知错误')));}catch(e){toast('设为壁纸失败：'+e.message);}
            } else {
              await fanout('设为壁纸', one, {});
            }
          };
          input.click();
          break;
        }

        case 'dev-download': {
          let targets = [serial];
          if (serials.length > 1) {
            const pick = await chooseMenuOption(`导出哪台手机的截图（共选中 ${serials.length} 台）`, serials.map((s) => labelOf(s)));
            if (pick === null) return;
            targets = [serials[pick]];
          }
          try{const t0=targets[0];const r=await fetch(`/api/menu/${encodeURIComponent(t0)}/screenshot`);if(!r.ok)throw Error('截图失败');const blob=await r.blob();const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`${t0}_${Date.now()}.png`;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);toast('截图已生成，已请求保存到电脑');}catch(e){toast('截图导出失败：'+e.message);}
          break;
        }


        case 'more-func': {
          // 隐藏功能：把右键菜单里没展开的功能列出来
          const extras = [
            ['分辨率/密度', async () => {
              const r = await api(`/api/devices/${encodeURIComponent(serial)}/info`);
              alert(`分辨率：${r.width || '?'} x ${r.height || '?'}\n密度：${r.density || '?'} dpi`);
            }],
            ['获取已装应用', async () => {
              const r = await api(`/api/devices/${encodeURIComponent(serial)}/apps`);
              if (!r.ok || !Array.isArray(r.apps)) { alert('获取应用失败，请检查设备连接'); return; }
              alert(r.apps.length ? r.apps.map((a) => `${a.name || a.package}  (${a.package})`).join('\n') : '没有已安装的第三方应用');
            }],
            ['重启 ADB', async () => {
              const r = await api('/api/adb/restart', {});
              toast(r.ok ? 'ADB 已重启' : '重启失败');
            }],
            ['断开重连', async () => {
              const r = await api(`/api/devices/${encodeURIComponent(serial)}/reconnect`, {});
              toast(r.ok ? '已重连' : '重连失败');
            }],
          ];
          const chosen=await chooseMenuOption('隐藏功能',extras.map(x=>x[0]));
          const idx=chosen===null?-1:chosen;
          if (idx >= 0 && idx < extras.length) {
            try { await extras[idx][1](); } catch (e) { toast('执行失败：' + e.message); }
          }
          break;
        }
      }
    });
  }

  /* ==================== 右侧面板 ==================== */

  function initRightPanel() {
    const panel = document.getElementById('right-panel');
    if (!panel || panel._bound) return;
    panel._bound = true;
    const controlButton = panel.querySelector('[data-op="ctrl-small"]');
    controlButton?.classList.toggle('on', !!window.__ctrlSmall);
    controlButton?.setAttribute('aria-pressed', String(!!window.__ctrlSmall));
    const lockButton = panel.querySelector('[data-op="lock-big"]');
    const locked = !!window.__lockBig;
    lockButton?.classList.toggle('on', locked);
    lockButton?.setAttribute('aria-pressed', String(locked));
    document.body.classList.toggle('fixed-big', locked);

    // ---- 1. 标签页切换 ----
    panel.querySelectorAll('.rp-tab').forEach((tab) => {
      tab.addEventListener('click', () => {
        panel.querySelectorAll('.rp-tab').forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        const key = tab.dataset.tab;
        console.log('[panel] tab ->', key);
        const otgPanel = document.getElementById('rp-otg-panel');

        if (key === 'member') {
          toast('会员功能（第二期）');
        } else if (key === 'otg') {
          // ★ 用 class 切换显示，避免 absolute 覆盖定位错乱
          panel.classList.add('otg-mode');
          if (otgPanel) otgPanel.classList.remove('hidden');
          try { renderOtgPanel(); } catch (e) { console.error('[otg]', e); }
        } else if (key === 'ai') {
          panel.classList.remove('otg-mode');
          if (otgPanel) otgPanel.classList.add('hidden');
        } else if (key === 'common') {
          panel.classList.remove('otg-mode');
          if (otgPanel) otgPanel.classList.add('hidden');
        }
      });
    });

    // ---- 2. 功能按钮 ----
    panel.querySelectorAll('.rp-fn[data-op]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const op = btn.dataset.op;
        switch (op) {
          case 'more-func': {
            openMenuManager();
            break;
          }

          case 'sys-settings':
            openSysSettings();
            break;

          case 'device-list': {
            try {
              const ds = window.S?.devices || [];
              const on = ds.filter((d) => d.state === 'online').length;
              const txt = ds.map((d) =>
                `${String(d.index).padStart(2, '0')}  ${d.serial.slice(-6)}  ${d.state === 'online' ? '在线' : '离线'}  ${d.width}x${d.height}`
              ).join('\n');
              alert(`设备列表（${on}/${ds.length} 在线）\n\n${txt}`);
            } catch (e) { console.error('[device-list]', e); }
            break;
          }

          case 'rotate-small':
            if (!(window.S?.devices?.length > 0)) { toast('没有设备，无法旋转小屏'); break; }
            window.__smallRotate = ((Number(window.__smallRotate) || 0) + 90) % 360;
            document.body.dataset.smallRotation = String(window.__smallRotate);
            toast(`小屏旋转 ${window.__smallRotate}°`);
            break;
          case 'lock-big':
            window.__lockBig = !window.__lockBig;
            document.body.classList.toggle('fixed-big', window.__lockBig);
            btn.classList.toggle('on', window.__lockBig);
            btn.setAttribute('aria-pressed', String(window.__lockBig));
            requestAnimationFrame(() => window.fitStageCanvas?.());
            toast(window.__lockBig ? '大屏已固定平铺' : '大屏已恢复浮动');
            break;
          case 'ctrl-small':
            window.setSmallControlEnabled?.(!window.__ctrlSmall);
            toast(window.__ctrlSmall ? '已开启小屏点击与滑动（实时拖动与滑动时长暂未对齐）' : '已关闭小屏操控');
            break;
          default:
            console.log('[panel] 未处理:', op);
        }
      });
    });

    // ---- 3. 四个滑块 ----
    const sliders = [
      { id: 'sl-big-size', out: 'sv-big-size', apply: (v) => {
          // 大屏尺寸 1-5 → 最大宽高比例
          const ratio = [0.55, 0.7, 0.85, 1.0, 1.15][v - 1] || 0.85;
          window.__bigSizeRatio = ratio;
          const st = document.querySelector('.stage');
          if (st) {
             st.style.width = Math.round(Math.min(500, innerWidth - 24) * ratio) + 'px';
             st.style.height = Math.round(Math.min(innerHeight - 90, 950) * Math.min(1, ratio)) + 'px';
           }
          if (window.fitStageCanvas) window.fitStageCanvas();
          try { localStorage.setItem('ff_big_size', String(v)); } catch (e) {}
        } },
      { id: 'sl-small-size', out: 'sv-small-size', apply: (v) => {
          // 小屏尺寸 1-5 → 卡片最小宽度
          const w = [120, 150, 175, 200, 230][v - 1] || 175;
          document.documentElement.style.setProperty('--card-w', w + 'px');
          try { localStorage.setItem('ff_small_size', String(v)); } catch (e) {}
        } },
      { id: 'sl-quality', out: 'sv-quality', apply: (v) => {
          // 小屏画质 1-5 → 流的 max_size（下次启动生效）
          // 默认 1080：大屏放大后才清晰
          const size = [360, 480, 540, 720, 1080][v - 1] || 720;
          window.__streamMaxSize = size;
          window.__qLevel = v;
          try { localStorage.setItem('ff_quality', String(v)); } catch (e) {}
          toast(`画质 ${size}p`);
          if (window.reapplyStreamParams) setTimeout(window.reapplyStreamParams, 50);
        } },
      { id: 'sl-fps', out: 'sv-fps', apply: (v) => {
          const fps = [5, 10, 15, 20, 24][v - 1] || 15;
          window.__streamMaxFps = fps;
          window.__fpsLevel = v;
          try { localStorage.setItem('ff_fps', String(v)); } catch (e) {}
          toast(`帧率 ${fps}fps`);
          if (window.reapplyStreamParams) setTimeout(window.reapplyStreamParams, 50);
        } },
    ];

    // 一次性迁移：撤销此前画质试验将所有小屏推至1080/24fps的重负载配置。
    if(localStorage.getItem('ff_stream_profile')!=='balanced-v2'){
      localStorage.setItem('ff_quality','2');localStorage.setItem('ff_fps','2');
      const cfg=JSON.parse(localStorage.getItem('ff_cfg')||'{}');cfg.quality=2;cfg.fps=2;
      localStorage.setItem('ff_cfg',JSON.stringify(cfg));
      localStorage.setItem('ff_stream_profile','balanced-v2');
    }
    sliders.forEach(({ id, out, apply }) => {
      const sl = document.getElementById(id);
      const ob = document.getElementById(out);
      if (!sl) return;
      // 恢复上次的值
      const saved = localStorage.getItem('ff_' + id.replace('sl-', '').replace('-', '_'));
      if (saved) { sl.value = saved; if (ob) ob.textContent = saved; }
      const fire = () => {
        const v = parseInt(sl.value, 10) || 3;
        if (ob) ob.textContent = id === 'sl-fps' ? `${[5, 10, 15, 20, 24][v - 1]} fps` : String(v);
        try { apply(v); } catch (e) { console.warn('[panel] slider', id, e); }
      };
      sl.addEventListener('input', fire);
      sl.addEventListener('change', fire);
      fire();   // 初始化时应用一次
    });

    // ---- 4. 类型切换 ----
    panel.querySelectorAll('.rp-type[data-type]').forEach((b) => {
      b.addEventListener('click', () => {
        panel.querySelectorAll('.rp-type').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
        const t = b.dataset.type;
        if (t !== 'local') toast('云真机 / 云手机（第二期）');
      });
    });

    // ---- 5. 过滤 ----
    panel.querySelectorAll('.rp-filter[data-filter]').forEach((b) => {
      b.addEventListener('click', () => {
        panel.querySelectorAll('.rp-filter').forEach((x) => x.classList.remove('active'));
        b.classList.add('active');
        const f = b.dataset.filter;
        window.__deviceFilter = f;
        const scanRow = document.getElementById('rp-wifi-scan-row');
        if (scanRow) scanRow.style.display = (f === 'wifi') ? 'flex' : 'none';
        applyDeviceFilter(f);
      });
    });

    // ---- 5b. WiFi 主动扫描 ----
    async function wifiScan(deep) {
      const msg = document.getElementById('wifi-scan-msg');
      const out = document.getElementById('wifi-scan-found');
      if (msg) msg.textContent = deep ? '深度扫描中（含无线调试端口，约 1-2 分钟）…' : '扫描中…';
      if (out) { out.style.display = 'block'; out.replaceChildren(); }
      try {
        const d = await api('/api/ext/wifi_scan', { deep: !!deep });
        const found = d.found || [];
        if (out) {
          if (!found.length) {
            const p = document.createElement('div');
            p.style.cssText = 'font-size:12px;color:#9aa2b1;padding:4px 2px';
            p.textContent = `存活 ${(d.alive || []).length} 台，未发现可接入的 ADB`;
            out.append(p);
          }
          found.forEach((it) => {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;gap:8px;align-items:center;font-size:12px;padding:3px 2px';
            const name = document.createElement('span');
            name.style.cssText = 'flex:1;overflow-wrap:anywhere';
            name.textContent = `${it.ip}:${it.port} ${it.connected ? '✓ 已接入' : '未接入'}`;
            const btn = document.createElement('button');
            btn.className = 'rp-btn-sm';
            btn.textContent = it.connected ? '已连接' : '连接';
            btn.disabled = !!it.connected;
            btn.onclick = async () => {
              btn.disabled = true;
              btn.textContent = '连接中…';
              const r = await api('/api/ext/wireless_add', { target: `${it.ip}:${it.port}` });
              btn.textContent = r.ok ? '已连接' : '重试';
              btn.disabled = !!r.ok;
              if (r.ok) window.loadDevices?.();
              else if (msg) msg.textContent = r.error || '连接失败';
            };
            row.append(name, btn);
            out.append(row);
          });
        }
        if (msg) msg.textContent = `完成：存活 ${(d.alive || []).length}，发现 ${found.length}，接入 ${(d.connected || []).length}，${d.elapsed || 0}s`;
        if ((d.connected || []).length) window.loadDevices?.();
      } catch (e) {
        if (msg) msg.textContent = '扫描失败：' + e.message;
      }
    }
    const fastBtn = document.getElementById('btn-wifi-scan-fast');
    const deepBtn = document.getElementById('btn-wifi-scan-deep');
    if (fastBtn) fastBtn.onclick = () => wifiScan(false);
    if (deepBtn) deepBtn.onclick = () => wifiScan(true);

    // ---- 6. 登录（会员系统只留界面，第二期） ----
    panel.querySelectorAll('.rp-login a, .rp-login').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        toast('会员功能将在第二期开放');
      });
    });

    console.log('[panel] 右侧面板已全部接线');
  }

  function applyDeviceFilter(f) {
    window.__deviceFilter = f;
    window.renderGrid?.();
    const shown = document.querySelectorAll('#grid .phone').length;
    toast(`${f === 'all' ? '全部' : f}：${shown} 台`);
  }


  // ==================== 更多功能：右键菜单项管理（对齐汇城 moreFunSort）====================
  const MENU_LAYOUT_KEY = 'ff_menu_layout_v1';
  function menuLayoutRead() {
    try {
      const v = JSON.parse(localStorage.getItem(MENU_LAYOUT_KEY) || 'null');
      if (!v || !Array.isArray(v.order) || !Array.isArray(v.hidden)) return null;
      return { order: v.order.filter((k) => typeof k === 'string'), hidden: v.hidden.filter((k) => typeof k === 'string') };
    } catch (e) { return null; }
  }
  function menuItems() {
    return [...document.querySelectorAll('#float-menu li[data-op]')].map((li) => ({
      key: li.dataset.op,
      label: (li.textContent || '').trim() || li.dataset.op,
      section: li.closest('#menu-devinfo') ? 'devinfo' : 'list',
    }));
  }
  function menuLayoutApply() {
    const lay = menuLayoutRead();
    if (!lay) return;
    for (const ul of document.querySelectorAll('#float-menu #menu-list, #float-menu #menu-devinfo')) {
      const lis = [...ul.querySelectorAll('li[data-op]')];
      lis.forEach((li) => { li.style.display = lay.hidden.includes(li.dataset.op) ? 'none' : ''; });
      const rank = (k) => { const i = lay.order.indexOf(k); return i < 0 ? 1e6 : i; };
      lis.sort((a, b) => rank(a.dataset.op) - rank(b.dataset.op)).forEach((li) => ul.append(li));
    }
  }
  window.menuLayoutApply = menuLayoutApply;

  function openMenuManager() {
    const items = menuItems();
    if (!items.length) { toast('菜单项尚未就绪，请先打开一次右键菜单'); return; }
    const lay = menuLayoutRead() || { order: items.map((i) => i.key), hidden: [] };
    // 初始状态：已保存顺序优先，未记录的追加在后面
    let order = [...lay.order.filter((k) => items.some((i) => i.key === k))];
    items.forEach((i) => { if (!order.includes(i.key)) order.push(i.key); });
    const hidden = new Set(lay.hidden);

    const dlg = document.createElement('dialog');
    dlg.className = 'device-props-dialog';
    const h = document.createElement('h3');
    h.textContent = '更多功能 · 菜单项管理';
    const tip = document.createElement('p');
    tip.style.cssText = 'font-size:12px;color:#a9a2bd;margin:0 0 10px';
    tip.textContent = '勾选决定是否显示，↑↓ 调整顺序；保存后立即生效，并记住到本机。';
    const list = document.createElement('div');
    list.style.cssText = 'max-height:52vh;overflow:auto';

    const labelOf = (k) => (items.find((i) => i.key === k) || {}).label || k;
    function render() {
      list.replaceChildren();
      order.forEach((key, idx) => {
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:7px 4px;border-bottom:1px solid #2c2f3a';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = !hidden.has(key);
        cb.style.cssText = 'width:18px;height:18px;flex-shrink:0';
        cb.onchange = () => { cb.checked ? hidden.delete(key) : hidden.add(key); };
        const name = document.createElement('span');
        name.textContent = labelOf(key);
        name.style.cssText = 'flex:1;font-size:13.5px;color:' + (hidden.has(key) ? '#7b7488' : '#eee');
        const up = document.createElement('button');
        up.textContent = '↑'; up.title = '上移';
        up.disabled = idx === 0;
        up.onclick = () => { [order[idx - 1], order[idx]] = [order[idx], order[idx - 1]]; render(); };
        const down = document.createElement('button');
        down.textContent = '↓'; down.title = '下移';
        down.disabled = idx === order.length - 1;
        down.onclick = () => { [order[idx + 1], order[idx]] = [order[idx], order[idx + 1]]; render(); };
        row.append(cb, name, up, down);
        list.append(row);
      });
    }
    render();

    const reset = document.createElement('button');
    reset.textContent = '恢复默认';
    reset.onclick = () => { localStorage.removeItem(MENU_LAYOUT_KEY); menuLayoutApply(); dlg.close(); dlg.remove(); toast('菜单已恢复默认顺序'); };
    const save = document.createElement('button');
    save.textContent = '保存并应用';
    save.style.cssText = 'background:#6d54c8;color:#fff;border-color:#8c72e8';
    save.onclick = () => {
      const payload = { order, hidden: [...hidden] };
      try { localStorage.setItem(MENU_LAYOUT_KEY, JSON.stringify(payload)); } catch (e) { toast('保存失败：浏览器存储不可用'); return; }
      menuLayoutApply();
      dlg.close(); dlg.remove();
      toast('菜单已更新：显示 ' + (order.length - hidden.size) + ' 项');
    };
    const cancel = document.createElement('button');
    cancel.textContent = '取消';
    cancel.onclick = () => { dlg.close(); dlg.remove(); };
    dlg.addEventListener('close', () => dlg.remove());
    dlg.append(h, tip, list, reset, save, cancel);
    document.body.append(dlg);
    dlg.showModal();
  }
  window.openMenuManager = openMenuManager;

  function openSysSettings() {
    const m = document.getElementById('settings-modal');
    const body = document.getElementById('settings-body');
    if (!m || !body) { toast('设置面板不可用'); return; }
    // 2026-11-06 修（主人截图：弹窗没有背景，文字直接叠在手机画面上）：
    // `.modal` 的规则虽然写在 style.css 里，实际渲染却没生效。
    // 不再依赖外部类，把遮罩层 + 内层卡片直接钉在行内样式上。
    m.style.cssText = 'position:fixed;inset:0;z-index:2000;'
      + 'background:rgba(0,0,0,.72);display:flex;align-items:center;'
      + 'justify-content:center;padding:16px;box-sizing:border-box;';
    body.style.cssText = 'background:#23262e;color:#e6e6e6;'
      + 'border:1px solid #3a3f4b;border-radius:10px;'
      + 'box-shadow:0 12px 48px rgba(0,0,0,.6);'
      + 'width:min(560px,92vw);max-height:82vh;overflow-y:auto;'
      + 'padding:18px 20px;box-sizing:border-box;';
    body.innerHTML = `
      <h3>系统设置</h3>
      <div class="ss-row"><label>视频码率</label>
        <select id="ss-bitrate">
          <option value="1000000">1 Mbps</option>
          <option value="2000000" selected>2 Mbps</option>
          <option value="4000000">4 Mbps</option>
          <option value="8000000">8 Mbps</option>
        </select>
      </div>
      <div class="ss-row"><label>编码分辨率</label>
        <select id="ss-maxsize">
          <option value="360">360p</option>
          <option value="480">480p</option>
          <option value="720" selected>720p</option>
          <option value="1080">1080p</option>
        </select>
      </div>
      <div class="ss-row"><label>最大帧率</label>
        <select id="ss-fps">
          <option value="8">8</option>
          <option value="12">12</option>
          <option value="15" selected>15</option>
          <option value="20">20</option>
          <option value="30">30</option>
        </select>
      </div>
      <div class="ss-row"><label>同时保活路数</label>
        <input id="ss-pool" type="number" min="1" max="20" value="4">
      </div>
      <div class="ss-row"><label>显示卡片编号</label><input id="ss-shownum" type="checkbox" checked></div>
      <div class="ss-row"><label>显示卡片名称</label><input id="ss-showname" type="checkbox" checked></div>
      <div class="ss-row"><label>固定大屏</label><input id="ss-fixedbig" type="checkbox"></div>
      <div class="ss-row"><label>选中手机屏幕常亮</label>
        <span style="display:flex;gap:8px">
          <button id="ss-keepon" type="button">开启</button>
          <button id="ss-keepoff" type="button">关闭</button>
        </span>
      </div>
      <p style="font-size:12px;color:#8d8778;line-height:1.6;margin:4px 0 10px">
        屏幕常亮会写入手机系统超时设置（关闭时恢复 60 秒）；显示项与固定大屏即时生效，其余项重开设备后生效。
      </p>
      <p style="font-size:12px;color:#6b7484;line-height:1.7;margin:0 0 8px">
        链路：手机 H.264 → 服务端转 JPEG → 大小屏共享解码；触控走 scrcpy control socket。<br>
        FishFleet v1.0 · 基于汇城交互逻辑重构
      </p>
      <div class="ss-actions">
        <button id="ss-save">保存</button>
        <button id="ss-close">关闭</button>
      </div>
    `;
    m.classList.remove('hidden');
    document.getElementById('ss-close').onclick = () => m.classList.add('hidden');
    // 显示项 / 固定大屏 / 屏幕常亮（即时生效）
    const flagsRead = () => { try { return JSON.parse(localStorage.getItem('ff_ui_flags') || '{}') || {}; } catch (e) { return {}; } };
    const flagsWrite = (v) => { try { localStorage.setItem('ff_ui_flags', JSON.stringify(v)); } catch (e) {} };
    const flags = flagsRead();
    const shownum = document.getElementById('ss-shownum');
    const showname = document.getElementById('ss-showname');
    const fixedbig = document.getElementById('ss-fixedbig');
    shownum.checked = flags.showNumber !== false;
    showname.checked = flags.showName !== false;
    fixedbig.checked = !!window.__lockBig;
    document.body.classList.toggle('hide-number', !shownum.checked);
    document.body.classList.toggle('hide-model', !showname.checked);
    shownum.onchange = () => {
      document.body.classList.toggle('hide-number', !shownum.checked);
      flagsWrite({ ...flagsRead(), showNumber: shownum.checked });
    };
    showname.onchange = () => {
      document.body.classList.toggle('hide-model', !showname.checked);
      flagsWrite({ ...flagsRead(), showName: showname.checked });
    };
    fixedbig.onchange = () => {
      const btn = document.querySelector('#right-panel [data-op="lock-big"]');
      if (btn) btn.click();
    };
    const keepTarget = () => window.S?.selected || document.querySelector('#float-menu')?.dataset.serial || '';
    const keepCall = async (enabled) => {
      const serial = keepTarget();
      if (!serial) { toast('请先在网格里选中一台手机'); return; }
      const r = await post(`/api/devices/${encodeURIComponent(serial)}/keep-screen`, { enabled });
      toast(r?.ok ? (enabled ? '已开启屏幕常亮' : '已恢复默认熄灭时间') : ('操作失败：' + (r?.error || '设备未确认')));
    };
    document.getElementById('ss-keepon').onclick = () => keepCall(true);
    document.getElementById('ss-keepoff').onclick = () => keepCall(false);

    document.getElementById('ss-save').onclick = async () => {
      const br = document.getElementById('ss-bitrate').value;
      const ms = document.getElementById('ss-maxsize').value;
      const fps = document.getElementById('ss-fps').value;
      const pool = document.getElementById('ss-pool').value;
      window.__bitrate = parseInt(br, 10);
      window.__streamMaxSize = parseInt(ms, 10);
      window.__streamMaxFps = parseInt(fps, 10);
      window.__poolMax = parseInt(pool, 10);
      try {
        localStorage.setItem('ff_bitrate', br);
        localStorage.setItem('ff_maxsize', ms);
        localStorage.setItem('ff_maxfps', fps);
        localStorage.setItem('ff_pool', pool);
      } catch (e) {}
      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_bit_rate: parseInt(br, 10),
          max_size: parseInt(ms, 10),
          max_fps: parseInt(fps, 10),
        }),
      }).catch(() => null);
      m.classList.add('hidden');
      toast('设置已保存（重开设备生效）');
    };
  }
  window.openSysSettings = openSysSettings;


  // ==================== AI 配置面板 ====================

  async function openAiPanel() {
    const m = document.getElementById('ai-modal');
    if (!m) return;
    m.classList.remove('hidden');

    // 拉取当前配置
    try {
      const r = await fetch('/api/llm/config');
      const d = await r.json();
      const c = d.config || {};
      const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };

      set('ai-auto-url', c.auto?.url);
      set('ai-auto-model', c.auto?.model);
      set('ai-auto-key', '');       // 密钥不回填，留空表示不改
      set('ai-video-url', c.video?.url);
      set('ai-video-model', c.video?.model);
      set('ai-video-key', '');
      set('ai-internal-repo', c.mcp?.trae_maker_repo || '');
      set('ai-proxy-url', c.proxy?.url);
      const checked = (id, value) => { const el = document.getElementById(id); if (el) el.checked = !!value; };
      checked('ai-proxy-enabled', c.proxy?.enabled);
      checked('ai-proxy-auto', c.proxy?.targets?.includes('auto'));
      checked('ai-proxy-video', c.proxy?.targets?.includes('video'));
      const proxyStatus = document.getElementById('ai-proxy-status');
      if (proxyStatus) proxyStatus.textContent = '仅影响勾选的 API；修改后需保存，连接测试使用已保存配置。';
      const candidates = document.getElementById('ai-proxy-candidates');
      if (candidates) { candidates.replaceChildren(); candidates.hidden = true; }

      // 提示已有密钥
      const hint = (id, has) => {
        const el = document.getElementById(id);
        if (el) el.placeholder = has ? '已配置（留空则不修改）' : '请输入密钥';
      };
      hint('ai-auto-key', c.auto?.has_key);
      hint('ai-video-key', c.video?.has_key);
      console.log('[ai] 配置已加载');
    } catch (e) {
      console.warn('[ai] 加载配置失败', e);
    }
  }
  window.openAiPanel = openAiPanel;

  function initAiPanel() {
    const m = document.getElementById('ai-modal');
    if (!m || m._bound) return;
    m._bound = true;

    document.getElementById('ai-close')?.addEventListener('click', () => {
      m.classList.add('hidden');
    });

    // 点击遮罩关闭
    m.addEventListener('click', (e) => {
      if (e.target === m) m.classList.add('hidden');
    });

    // 保存
    document.getElementById('ai-save')?.addEventListener('click', async () => {
      const g = (id) => document.getElementById(id)?.value || '';
      const body = {
        auto: {
          url: g('ai-auto-url'),
          model: g('ai-auto-model'),
          key: g('ai-auto-key'),
        },
        video: {
          url: g('ai-video-url'),
          model: g('ai-video-model'),
          key: g('ai-video-key'),
        },
        proxy: {
          enabled: !!document.getElementById('ai-proxy-enabled')?.checked,
          url: g('ai-proxy-url').trim(),
          targets: ['auto', 'video'].filter(kind => document.getElementById('ai-proxy-' + kind)?.checked),
        },
      };
      try {
        const r = await fetch('/api/llm/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const d = await r.json();
        if (d.ok) {
          toast('AI 配置已保存');
          m.classList.add('hidden');
        } else {
          toast('保存失败：' + (d.error || '配置无效'));
        }
      } catch (e) {
        toast('保存失败');
      }
    });

    // 只读取环境变量 / Windows 系统设置，不扫描端口，不自动启用代理。
    document.getElementById('ai-proxy-detect')?.addEventListener('click', async () => {
      const button = document.getElementById('ai-proxy-detect');
      const status = document.getElementById('ai-proxy-status');
      const select = document.getElementById('ai-proxy-candidates');
      if (button) button.disabled = true;
      if (status) status.textContent = '正在读取本机代理设置…';
      if (select) { select.replaceChildren(); select.hidden = true; }
      try {
        const r = await fetch('/api/llm/proxy/detect');
        const d = await r.json();
        if (!r.ok || d.ok === false) throw new Error(d.error || '读取失败');
        const candidates = Array.isArray(d.candidates) ? d.candidates : [];
        if (!candidates.length) {
          if (status) status.textContent = '未发现可用的 HTTP/HTTPS 代理配置，请在代理软件中查看 HTTP/mixed 端口后手动填写。不支持自动执行 PAC。';
          return;
        }
        const input = document.getElementById('ai-proxy-url');
        if (select) {
          candidates.forEach(candidate => {
            const option = document.createElement('option');
            option.value = candidate.url;
            option.textContent = candidate.source + ' — ' + candidate.url;
            select.appendChild(option);
          });
          select.hidden = candidates.length < 2;
          select.onchange = () => { if (input) input.value = select.value; };
        }
        if (input) input.value = candidates[0].url;
        if (status) status.textContent = '已填入代理候选（未测试连通性、未开启、未保存）；请确认地址、勾选适用 API 后保存。';
      } catch (e) {
        if (status) status.textContent = '读取失败：' + e.message;
      } finally {
        if (button) button.disabled = false;
      }
    });

    // 测试连接
    // 2026-11-06 根本性改造：**测试传当前输入的值**，不再依赖已保存的配置。
    // 原来只传 kind，后端读 llm.json，用户填完不保存就点测试 → "配置不完整"。
    const test = async (kind, statusId) => {
      const el = document.getElementById(statusId);
      const g = (id) => document.getElementById(id)?.value || '';
      const pre = kind === 'video' ? 'ai-video' : 'ai-auto';
      const payload = {
        kind,
        url: g(pre + '-url').trim(),
        model: g(pre + '-model').trim(),
        key: g(pre + '-key').trim(),      // 留空则后端沿用已保存的 key
      };
      if (el) { el.textContent = '测试中…'; el.className = 'ai-status'; }
      try {
        const r = await fetch('/api/llm/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (el) {
          el.textContent = d.ok ? '✓ 连接成功' : ('✗ ' + (d.error || '失败'));
          el.className = 'ai-status ' + (d.ok ? 'ok' : 'err');
        }
      } catch (e) {
        if (el) { el.textContent = '✗ 请求失败'; el.className = 'ai-status err'; }
      }
    };
    document.getElementById('ai-auto-test')?.addEventListener('click', () => test('auto', 'ai-auto-status'));
    document.getElementById('ai-video-test')?.addEventListener('click', () => test('video', 'ai-video-status'));

      // ★ 顶栏「AI 助手」按钮
      document.getElementById('btn-open-ai')?.addEventListener('click', () => {
        window.openExecutionWorkspace?.();
      });
  }
  window.initAiPanel = initAiPanel;

/* ==================== OTG 面板（对照汇城 otg_scanning / usb_to_tcp）==================== */
async function renderOtgPanel() {
  const box = document.getElementById('rp-otg-panel');
  if (!box) return;
  box.classList.remove('hidden');
  if (window.renderHcOtgPanel) { window.renderHcOtgPanel(box); return; }

  // 1. 本机网络
  let info = { local_ips: [], candidates: [] };
  try {
    const r = await fetch('/api/otg/info');
    info = await r.json();
  } catch (e) {}

  const ips = (info.local_ips || []).join('、') || '未检测到';
  const cands = (info.candidates || []).join('、') || '—';

  box.innerHTML = `
    <div style="padding:12px;color:#c7cede;font-size:12px;line-height:1.7">
      <div style="margin-bottom:10px;color:#8e9bb3">
        手机用 <b>USB 线</b>连电脑 → 手机设置里打开
        <b>「USB 网络共享」</b> → 点下面的扫描，找到手机 IP 就能无线控制
      </div>

      <div style="background:#161a20;border:1px solid #22272f;border-radius:6px;padding:10px;margin-bottom:10px">
        <div>本机 IP：<span style="color:#8fd">${ips}</span></div>
        <div>候选网段：<span style="color:#8fd">${cands}</span></div>
      </div>

      <div style="display:flex;gap:6px;margin-bottom:8px;align-items:center">
        <input id="otg-net" placeholder="网段前缀（空=自动）"
               style="flex:1;height:30px;background:#0e1218;border:1px solid #2a2f3a;border-radius:6px;color:#dde;padding:0 8px;font-size:12px">
      </div>
      <div style="display:flex;gap:6px;margin-bottom:10px">
        <button id="otg-scan-btn"
                style="flex:1;height:30px;border-radius:6px;border:none;background:#7c5cff;color:#fff;cursor:pointer;font-size:12px">
          单次扫描
        </button>
        <button id="otg-scan3-btn"
                style="flex:1;height:30px;border-radius:6px;border:none;background:#2b6cff;color:#fff;cursor:pointer;font-size:12px">
          三次扫描
        </button>
        <button id="otg-diag-btn"
                style="flex:1;height:30px;border-radius:6px;border:1px solid #2a2f3a;background:#1c2229;color:#c7cede;cursor:pointer;font-size:12px">
          诊断
        </button>
      </div>

      <div id="otg-result" style="font-size:12px;color:#8e9bb3">
        点「扫描」开始查找 OTG 网段里的手机
      </div>
    </div>
  `;

  // ---- 三次扫描（对齐汇城）----
  document.getElementById('otg-scan3-btn')?.addEventListener('click', async () => {
    const net = (document.getElementById('otg-net')?.value || '').trim();
    const out = document.getElementById('otg-result');
    if (out) out.innerHTML = '<span style="color:#8fd">三次扫描中（约 30-120 秒）…</span>';

    try {
      const r = await fetch('/api/ext/otg_scanning_three', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ net, port: 5555, rounds: 3 }),
      });
      const d = await r.json();
      const found = d.found || [];
      const rounds = d.rounds || [];
      const roundTxt = rounds.map((x) => `第${x.round}轮: +${x.new}（累计${x.total}）`).join(' | ');

      if (!found.length) {
        out.innerHTML =
          `<div style="color:#f88">三次扫描未找到设备</div>` +
          `<div style="color:#7a8499;font-size:11px;margin-top:4px">${roundTxt}</div>` +
          `<div style="color:#7a8499;font-size:11px;margin-top:4px">` +
          `端口占用：${d.port_used_locally ? '是（可能被其他程序占用）' : '否'}<br>` +
          `检查：① 手机开了 USB 网络共享？② 手机开了无线调试？③ 防火墙？</div>`;
        return;
      }
      out.innerHTML = `<div style="color:#8fd;margin-bottom:6px">三次扫描找到 ${found.length} 台</div>` +
        `<div style="color:#7a8499;font-size:11px;margin-bottom:6px">${roundTxt}</div>` +
        found.map((f) =>
          `<div style="display:flex;align-items:center;gap:8px;margin:4px 0">
             <span style="flex:1">${f.ip}:${f.port} <span style="color:#7a8499;font-size:11px">(第${f.round}轮)</span></span>
             <button class="otg-conn" data-ip="${f.ip}" data-port="${f.port}"
                     style="height:24px;padding:0 10px;border-radius:4px;border:none;background:#2b6cff;color:#fff;cursor:pointer;font-size:11px">
               接入
             </button>
           </div>`).join('');

      out.querySelectorAll('.otg-conn').forEach((b) => {
        b.addEventListener('click', async () => {
          b.textContent = '连接中';
          try {
            const rr = await fetch('/api/otg/connect', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ip: b.dataset.ip, port: parseInt(b.dataset.port, 10) }),
            });
            const dd = await rr.json();
            b.textContent = dd.ok ? '已接入' : '失败';
            b.style.background = dd.ok ? '#2f9e44' : '#c92a2a';
          } catch (e) { b.textContent = '失败'; }
        });
      });
    } catch (e) {
      if (out) out.textContent = '扫描失败：' + e.message;
    }
  });

  // ---- 系统诊断（对齐汇城 SystemDiagnose）----
  document.getElementById('otg-diag-btn')?.addEventListener('click', async () => {
    const out = document.getElementById('otg-result');
    if (out) out.textContent = '诊断中…';
    try {
      const r = await fetch('/api/ext/get_system_config');
      const d = await r.json();
      const r2 = await fetch('/api/ext/get_usb_devices', { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
      const u = await r2.json();
      const hid = (u.devices || []).filter((x) => x.is_hid);

      out.innerHTML = `
        <div style="line-height:1.9;font-size:12px">
          <div style="color:#8fd;margin-bottom:6px;font-weight:600">系统诊断</div>
          <div>系统：${d.os} ${d.os_version}</div>
          <div>Python：${d.python}</div>
          <div>主机名：${d.hostname}</div>
          <div>ADB：<span style="color:${d.adb_exists ? '#8fd' : '#f88'}">${d.adb_exists ? '已找到' : '未找到'}</span></div>
          <div style="font-size:11px;color:#7a8499;word-break:break-all">${d.adb || ''}</div>
          <div>外网 IP：${d.external_ip || '不可用'}</div>
          <div style="margin-top:8px;color:#8fd">USB 设备：${u.count || 0} 个</div>
          <div>HID 设备：<span style="color:${hid.length ? '#8fd' : '#7a8499'}">${hid.length} 个</span></div>
          ${hid.slice(0,5).map((x) => `<div style="font-size:11px;color:#7a8499">· ${x.name}</div>`).join('')}
        </div>`;
    } catch (e) {
      if (out) out.textContent = '诊断失败：' + e.message;
    }
  });

  document.getElementById('otg-scan-btn')?.addEventListener('click', async () => {
    const net = (document.getElementById('otg-net')?.value || '').trim();
    const out = document.getElementById('otg-result');
    if (out) out.textContent = '扫描中，大约 10-60 秒…';

    try {
      const r = await fetch('/api/otg/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ net, port: 5555 }),
      });
      const d = await r.json();
      const found = d.found || [];
      if (!found.length) {
        out.innerHTML = '<span style="color:#f88">没找到设备。检查：① 手机开了 USB 网络共享？② 手机开了无线调试？</span>';
        return;
      }
      out.innerHTML = '<div style="margin-bottom:6px;color:#8fd">找到 ' + found.length + ' 台：</div>' +
        found.map((f, i) =>
          `<div style="display:flex;align-items:center;gap:8px;margin:4px 0">
             <span style="flex:1">${f.ip}:${f.port}</span>
             <button class="otg-conn" data-ip="${f.ip}" data-port="${f.port}"
                     style="height:24px;padding:0 10px;border-radius:4px;border:none;background:#2b6cff;color:#fff;cursor:pointer;font-size:11px">
               接入
             </button>
           </div>`).join('');

      out.querySelectorAll('.otg-conn').forEach((b) => {
        b.addEventListener('click', async () => {
          b.textContent = '连接中';
          try {
            const rr = await fetch('/api/otg/connect', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ ip: b.dataset.ip, port: parseInt(b.dataset.port, 10) }),
            });
            const dd = await rr.json();
            b.textContent = dd.ok ? '已接入' : '失败';
            b.style.background = dd.ok ? '#2f9e44' : '#c92a2a';
            if (dd.ok && typeof window.refreshDevices === 'function') window.refreshDevices();
          } catch (e) { b.textContent = '失败'; }
        });
      });
    } catch (e) {
      if (out) out.textContent = '扫描失败：' + e.message;
    }
  });
}
window.renderOtgPanel = renderOtgPanel;


  /* ==================== 大屏 ==================== */

  function paintEmpty(show) {
    stageEmpty.classList.toggle('hidden', !show);
    stageBadge.classList.toggle('hidden', show);
    stage.classList.toggle('live', !show);
  }

  // ==================== 播放器池（对齐汇城） ====================
  // 汇城的播放器实例长期存活，切换设备只切可见性（initVisable/cancelVisable），
  // 不销毁不重连，所以切回来瞬间就有画面。这里照做。
  const playerPool = new Map();     // serial -> H264Player
  const POOL_MAX = 8;               // 最多同时保活 8 路

  function deactivateAll() {
    for (const p of playerPool.values()) {
      p.active = false;
    }
  }

  function evictIfNeeded() {
    while (playerPool.size > POOL_MAX) {
      // 淘汰最久未用（Map 按插入顺序，第一个就是最老的）
      let victimKey = null;
      for (const [k, v] of playerPool) {
        if (!v.active) { victimKey = k; break; }
      }
      if (!victimKey) break;
      const v = playerPool.get(victimKey);
      try { v.stop(); } catch (e) {}
      playerPool.delete(victimKey);
      console.log(`[pool] 淘汰 ${victimKey}`);
    }
  }

  let aiStageMount = null;
  function restoreAiStage() {
    const mount = aiStageMount;
    if (!mount) return;
    aiStageMount = null;
    mount.resize?.disconnect();
    try { mount.visibility.disconnect(); } catch (e) {}
    if (mount.onDeviceChange) { try { mount.chat.removeEventListener('change', mount.onDeviceChange); } catch (e) {} }
    mount.box.classList.remove('ai-live-stage');
    if (mount.style === null) mount.box.removeAttribute('style');
    else mount.box.setAttribute('style', mount.style);
    if (mount.marker.parentNode) mount.marker.replaceWith(mount.box);
    else mount.parent.append(mount.box);
    mount.host.remove();
    mount.observe.classList.remove('ai-live-open');
    mount.chat.classList.remove('ai-live-open');
  }
  window.closeAiBigscreen = function () {
    if (!aiStageMount) return;
    // 复用原关闭流程，释放共享大屏目标并还原位置；不停止小屏流。
    window.closeBigscreen();
  };
  window.openAiBigscreen = async function (serial) {
    if (!serial) return false;
    const chat = document.getElementById('rp-chat');
    const observe = chat?.querySelector('.exec-observe');
    const box = document.getElementById('stage');
    if (!chat || !observe || !box || chat.classList.contains('hidden') || chat.hidden) return false;
    if (!aiStageMount) {
      const marker = document.createComment('AI stage original location');
      const parent = box.parentNode;
      const style = box.getAttribute('style');
      parent.insertBefore(marker, box);
      const host = document.createElement('div');
      host.id = 'ai-live-stage-host';
      host.className = 'ai-live-stage-host';
      observe.append(host);
      host.append(box);
      box.classList.add('ai-live-stage');
      observe.classList.add('ai-live-open');
      chat.classList.add('ai-live-open');
      // 注意：不要在这里监听 exec-device 的 change 冒泡，否则本次触发
      // 下拉的那次 change 会冒泡到 chat，刚打开就被自己关掉（下方空白）。
      // 切换由 chat_panel.js 的 select.onchange 显式 close+open，已够用。
      const visibility = new MutationObserver(() => {
        if (!chat.isConnected || chat.hidden || chat.classList.contains('hidden') || !host.isConnected) window.closeAiBigscreen();
      });
      visibility.observe(chat, { attributes: true, attributeFilter: ['class', 'hidden'] });
      const resize = window.ResizeObserver ? new ResizeObserver(() => fitStageCanvas()) : null;
      resize?.observe(host);
      aiStageMount = { chat, observe, box, host, marker, parent, style, visibility, resize, onDeviceChange: null };
    }
    try {
      await window.openBigscreen(serial, undefined, { ai: true });
      // 主界面浮动设置不应跨层盖在 AI 上；原 stage 控件保留可用。
      hideFloatMenu();
      fitStageCanvas();
      return true;
    } catch (error) {
      window.closeAiBigscreen();
      throw error;
    }
  };


  // 2026-11-07 修 BUG（大屏操作其他手机不同步）：
  //   原来大屏触控把 targets 硬编码成 [serial]，只控当前这一台。
  //   统一走 live_control.getTargets：大屏那台在 S.multi 里时，
  //   操作会广播到所有选中的在线设备；不在多选里则退回单台。
  function __lcTargets(serial) {
    try {
      const lc = window.__liveControl;
      if (lc && typeof lc.getTargets === 'function') return lc.getTargets(serial);
    } catch (e) {}
    return [serial];
  }

  window.openBigscreen = async function (serial, index, options = {}) {
    if (!serial) return;
    // 2026-11-07 修 BUG（点开大屏后群控失效）：
    //   原来无条件 closeAiBigscreen()，把群控正在用的大屏画面拆掉了。
    //   现在只在**工作台不可见**时才拆 —— 工作台可见时保持挂载，
    //   大屏继续待在群控面板里，群控面板照常能点。
    const _chatEl = document.getElementById('rp-chat');
    const _chatVisible = !!_chatEl && !_chatEl.hidden
        && !_chatEl.classList.contains('hidden')
        && _chatEl.offsetParent !== null;
    if (aiStageMount && !options.ai && !_chatVisible) window.closeAiBigscreen();
    // WAKEUP 是幂等唤醒键，不使用会把亮屏反向关闭的 POWER 键。
    post('/api/key', { serial, key: 'wakeup' }).then(r => {
      if (!r?.ok) toast('手机唤醒未确认，请检查设备状态');
    }).catch(() => toast('手机唤醒请求失败'));
    if (curSerial === serial && player && player.active) return;

    // ★ 关键：只停用旧的，不销毁（保持 WS + 解码器活着）
    deactivateAll();

    curSerial = serial;
    if (window.S) { window.S.expanded = serial; window.S.selected = serial; }
    window.__ctxSerial = serial;

    const dev = (window.S?.devices || []).find((d) => d.serial === serial) || null;
    curDev = dev;
    stageW = dev?.width || 0;
    stageH = dev?.height || 0;

    paintEmpty(false);
    document.getElementById('stage')?.classList.remove('hidden');
    stage.classList.add('live');
    // 工作台可见时不能让浮层大屏盖住它（#stage z-index:80，工作台无 z-index）
    try {
      if (_chatVisible) document.body.classList.add('stage-under-chat');
      else document.body.classList.remove('stage-under-chat');
    } catch (e) {}
    const si = document.querySelector('.stage-input');
    if (si) si.classList.add('show');
    try { window.focusDirectIme && window.focusDirectIme(); } catch (e) {}
    setTimeout(fitStageCanvas, 0);

    $('fm-idx').textContent = `#${dev?.index ?? index ?? '—'}`;
    $('fm-name').textContent = dev?.serial || serial;
    $('stage-serial').textContent = serial;
    // 大屏默认在画面右侧展示该设备的设置；菜单仍可关闭或右键重新定位。
    if (fmEl) {
      fmEl.dataset.serial = serial;
      fmEl.classList.add('show', 'stage-settings');
      positionStageSettings();
      fmShown = true;
      loadPhoneApps(serial);
    }

    document.querySelectorAll('.phone').forEach((n) => {
      n.classList.toggle('active', n.dataset.serial === serial);
    });

    if (window.__jpegLive) {
      // 本地大小屏共享解码结果；不再另起H264播放器/改共享会话参数。
      clearStage();
      _openSeq++;
      for (const p of playerPool.values()) { try { p.stop(); } catch (e) {} }
      playerPool.clear();
      player = null;
      window.__player = null;
      window.__jpegLive.setStage(serial, stage);
      document.getElementById('stage')?.classList.remove('hidden');
      $('stage-fps').textContent = 'JPEG · 大屏参数已请求';
      return;
    }

    if (!window.H264Player) {
      toast('浏览器不支持 WebCodecs');
      return;
    }

    // ★ 池里已有且活着 → 直接激活，零等待
    const cached = playerPool.get(serial);
    if (cached && cached.running && cached.decoder && cached.decoder.state === 'configured') {
      cached.active = true;
      player = cached;
      window.__player = player;
      console.log(`[stage] ${serial} 从池中激活（零等待）`);
      toast(`${serial} 已就绪`);
      return;
    }

    clearStage();
    const mySeq = ++_openSeq;
    const maxSize = window.__bigMaxSize || 720;
    const t0 = performance.now();

    // 当前每台共享一个手机会话；大屏复用小屏参数，避免默认2fps重开。
    const sharedSize = window.__jpegLive ? ([360,480,540,720,1080][(window.__qLevel || 2)-1] || 480) : maxSize;
    const sharedFps = window.__jpegLive ? ([5,10,15,20,24][(window.__fpsLevel || 5)-1] || 24) : 25;
    const sharedRate = window.__streamBitRate || Math.max(50000, Math.round(sharedSize * sharedFps * 150));
    post('/api/stream/start', { serial, max_size: sharedSize, max_fps: sharedFps, bit_rate: sharedRate }).then((res) => {
      if (mySeq !== _openSeq || curSerial !== serial) {
        console.log(`[stage] 丢弃过期请求 ${serial}`);
        return;
      }
      const ms = Math.round(performance.now() - t0);
      console.log(`[stage] ${serial} ${res?.reused ? '复用' : '新建'} ${ms}ms`);

      // 池里有旧的（但已死）→ 清掉
      const dead = playerPool.get(serial);
      if (dead) { try { dead.stop(); } catch (e) {} playerPool.delete(serial); }

      const np = new H264Player(stage);
      np.onFps = (f) => { if (np.active) $('stage-fps').textContent = f + ' fps'; };
      np.onError = (e) => { console.warn('[stage]', e); };
      np.start(serial);
      playerPool.set(serial, np);
      evictIfNeeded();

      np.active = true;
      player = np;
      window.__player = np;
    }).catch(() => {
      if (mySeq !== _openSeq || curSerial !== serial) return;
      const np = new H264Player(stage);
      np.start(serial);
      playerPool.set(serial, np);
      evictIfNeeded();
      np.active = true;
      player = np;
      window.__player = np;
    });

    toast(`正在连接 ${serial}`);
  };

  window.closeBigscreen = function (silent) {
    if (window.__jpegLive) window.__jpegLive.setStage(null, null);
    _openSeq++;
    // ★ 不销毁池里的播放器，只停用（下次切回来仍是零等待）
    deactivateAll();
    player = null;
    window.__player = null;
    clearStage();
    if (fmEl?.dataset.serial === curSerial) hideFloatMenu();
    curSerial = null;
    curDev = null;
    if (window.S) window.S.expanded = null;
    paintEmpty(true);
    stage.classList.remove('live');
    restoreAiStage();
  };

  /* ==================== 大屏触摸 ==================== */

  function toDevice(e) {
    const rect = stage.getBoundingClientRect();
    if (!rect.width || !rect.height || !Number.isInteger(stageW) || !Number.isInteger(stageH) || stageW <= 0 || stageH <= 0) return null;
    const rx = (e.clientX - rect.left) / rect.width;
    const ry = (e.clientY - rect.top) / rect.height;
    if (!Number.isFinite(rx) || !Number.isFinite(ry) || rx < 0 || ry < 0 || rx >= 1 || ry >= 1) return null;
    let ux=rx,uy=ry;
    if(displayRotation===90){ux=ry;uy=1-rx;}
    else if(displayRotation===180){ux=1-rx;uy=1-ry;}
    else if(displayRotation===270){ux=1-ry;uy=rx;}
    return { x: Math.min(stageW-1,Math.floor(ux * stageW)), y: Math.min(stageH-1,Math.floor(uy * stageH)) };
  }

  let drag = null;

  if (stage) {
    stage.style.touchAction = 'none';

    // 常驻WS只做实时单控；复杂大屏多选规则另行对照，不猜测广播对象。
    stage.addEventListener('pointerdown', e => {
      try { window.focusDirectIme && window.focusDirectIme(); } catch (_) {}
      if(e.button!==0||!curSerial||!window.__liveControl?.enabled)return;
      const serial=curSerial,w=stageW,h=stageH,fw=stage.width,fh=stage.height,rotation=displayRotation;
      const valid=()=>stage.isConnected&&serial===curSerial&&stageW===w&&stageH===h&&stage.width===fw&&stage.height===fh&&displayRotation===rotation&&!document.getElementById('stage')?.classList.contains('hidden');
      window.__liveControl.startPointer(e,{element:stage,point:toDevice,valid,targets:__lcTargets(serial),source_width:w,source_height:h});
    });
    // 明确选择旧模式时才启用松手重播；实时失败绝不补发旧操作。
    stage.addEventListener('mousedown', e => {
      try { window.focusDirectIme && window.focusDirectIme(); } catch (_) {}
      if(window.__liveControl?.enabled||e.button!==0||!curSerial)return;
      const start=toDevice(e);if(!start)return;
      drag={start,serial:curSerial,width:stageW,height:stageH,frameWidth:stage.width,frameHeight:stage.height,at:performance.now()};e.preventDefault();
    });
    stage.addEventListener('mouseup', async e => {
      const d=drag;drag=null;
      if(!d||window.__liveControl?.enabled||e.button!==0)return;
      const p=toDevice(e);
      if(!p||d.serial!==curSerial||d.width!==stageW||d.height!==stageH||d.frameWidth!==stage.width||d.frameHeight!==stage.height)return;
      const duration=Math.max(.12,Math.min(2,(performance.now()-d.at)/1000));
      const r=Math.hypot(p.x-d.start.x,p.y-d.start.y)<=30
        ?await post('/api/tap',{serial:d.serial,...p})
        :await post('/api/swipe-points',{serial:d.serial,x1:d.start.x,y1:d.start.y,x2:p.x,y2:p.y,duration});
      if(!r?.ok)toast('触控发送失败：'+(r?.error||'设备是否收到操作未知'));
    });
    document.addEventListener('mouseup',()=>{drag=null;});
    window.addEventListener('blur',()=>{drag=null;});
    window.addEventListener('livecontrolmodechange',()=>{drag=null;});

    stage.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      if (curSerial) {window.__ctxSerial=curSerial;showFloatMenu(0,0,document.getElementById('stage'));}
    });
  }

  async function post(url, body) {
    try {
      const r = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) return {ok: false, error: data?.error || data?.detail || `HTTP ${r.status}`};
      return data;
    } catch (e) { return null; }
  }


  // 滚轮滚动（汇城的鼠标滚轮支持）
  let _scrollTimer = null;
  let _scrollAcc = 0;

  if (stage) {
    stage.addEventListener('wheel', async (e) => {
      if (!curSerial) return;
      e.preventDefault();
      if (drag) return;

      const point = toDevice(e);
      if (!point) return;

      // deltaMode: 0=像素 1=行 2=页
      let delta = e.deltaY;
      if (e.deltaMode === 1) delta *= 40;
      else if (e.deltaMode === 2) delta *= 400;

      // 归一化：一次滚轮约等于 1.0（一屏）
      const v = delta===0?0:(delta>0?-1:1);
      if(!v)return;
      if(window.__liveControl?.enabled){window.__liveControl.scroll({targets:__lcTargets(curSerial),source_width:stageW,source_height:stageH,...point,h:0,v});return;}
      const result=await post('/api/scroll', { serial: curSerial, ...point, h: 0, v });
      if(!result?.ok)toast('滚轮操作失败：'+(result?.error||'设备控制通道不可用'));
    }, { passive: false });
  }

  /* ==================== 大屏工具条 ==================== */

  document.querySelectorAll('.stool[data-key]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!curSerial) return toast('请先选择手机');
      if(window.__liveControl?.enabled){window.__liveControl.key(__lcTargets(curSerial),btn.dataset.key);return;}
      await post('/api/key', { serial: curSerial, key: btn.dataset.key });
    });
  });

  $('btn-stop-stream')?.addEventListener('click', () => closeBigscreen());

  $('btn-shot')?.addEventListener('click', async () => {
    if (!curSerial) return;
    const a = document.createElement('a');
    a.href = `/api/devices/${curSerial}/screenshot?size=full&fresh=1&t=${Date.now()}`;
    a.download = `${curSerial}_${Date.now()}.png`;
    a.click();
    toast('已保存截图');
  });

  $('btn-rotate')?.addEventListener('click', async () => {
    if (!curSerial) return toast('请先选择手机');
    rotateLocalDisplay();
    fitStageCanvas();
  });

  /* ==================== 中间菜单 ==================== */

  const OP_MAP = {
    'edit-number': () => window.editNumber?.(curDev),
    'edit-name': () => window.editName?.(curDev),
    'reboot': () => doReboot(),
    'install-apk': () => window.batchInstall?.([curSerial]),
    'push-file': () => window.batchPush?.([curSerial]),
    'pull-file': () => doPullFile(),
    'get-clipboard': () => window.getClipboard?.(curSerial),
    'adb-cmd': () => window.batchAdb?.([curSerial]),
    'auto-swipe': () => window.autoSwipe?.([curSerial]),
    'record-action': () => toast('录制动作（待接入）'),
    'play-action': () => toast('执行动作（待接入）'),
    'run-task': () => toast('执行任务（待接入）'),
    'stop-task': () => toast('结束任务（待接入）'),
    'task-list': () => toast('执行列表（待接入）'),
    'switch-ime': () => doSwitchIme(),
    'quick-phrase': () => toast('快捷短语（待接入）'),
    'rotate-right': () => doRotateRight(),
    'more-func': () => toast('隐藏功能（待接入）'),
    'sys-settings': () => $('btn-settings')?.click(),
    'device-list': () => toast('设备列表（待接入）'),
    'rotate-small': () => toast('旋转小屏'),
    'lock-big': () => toast('已固定大屏'),
    'ctrl-small': () => toast('操控小屏'),
  };

  // （旧版绑定已删除：与 initMenuActions 的委托绑定冲突，会导致右键菜单全部失效）

  $('btn-reload-apps')?.addEventListener('click', () => toast('应用列表已刷新'));

  async function doReboot() {
    if (!curSerial) return;
    if (!confirm(`确定重启 ${curSerial}？`)) return;
    await post('/api/adb', { serial: curSerial, cmd: 'reboot', timeout: 10 });
    toast('重启命令已发送');
  }

  async function doSwitchIme() {
    if (!curSerial) return;
    const r = await post('/api/adb', { serial: curSerial, cmd: 'ime list -s', timeout: 8 });
    toast(r?.output ? '输入法: ' + r.output.split('\n')[0] : '已查询');
  }

  async function doRotateRight() {
    if (!curSerial) return;
    rotateLocalDisplay();
  }

  async function doPullFile() {
    if (!curSerial) return;
    const path = prompt('输入设备上的文件路径：', '/sdcard/');
    if (!path) return;
    toast('导出（待接入）: ' + path);
  }

  /* ==================== 右侧面板 ==================== */

  // chips
  document.querySelectorAll('.rp-chip').forEach((c) => {
    c.addEventListener('click', async () => {
      if (c.dataset.op === 'more-func') return showMoreMenu();
      const fn = OP_MAP[c.dataset.op];
      if (fn) {
        if (!curSerial && !['sys-settings'].includes(c.dataset.op)) {
          return toast('请先选择一台手机');
        }
        try { await fn(); } catch (e) { toast('失败: ' + e.message); }
      }
    });
  });

  // 滑块
  ['big-size', 'small-size', 'quality', 'fps'].forEach((k) => {
    const sl = $('sl-' + k);
    const v = $('sl-' + k + '-v');
    if (!sl) return;
    sl.addEventListener('input', () => {
      if (v) v.textContent = sl.value;
      applySlider(k, Number(sl.value));
    });
  });

  // 滑块的档位 → 实际参数
  // ★ 小屏订阅已锁节能上限（480p/15fps，见 app.js subscribe）：滑块可照常拖，
  //   超限部分只影响显示，不会再把 20 台并发推满；要看清请开大屏。
  const SIZE_MAP = { 1: 720, 2: 920, 3: 1080, 4: 1440, 5: 1920 };

  function applySlider(k, v) {
    try {
      const cfg = JSON.parse(localStorage.getItem('ff_cfg') || '{}');
      cfg[k] = v;
      localStorage.setItem('ff_cfg', JSON.stringify(cfg));
    } catch (e) {}

    if (!curSerial) return;

    // 大屏尺寸：立即用新尺寸重连
    if (k === 'big-size') {
      const size = SIZE_MAP[v] || 1080;
      toast(`大屏尺寸切换为 ${size}p，正在重连…`);
      window.__bigMaxSize = size;
      window.reapplyStreamParams?.();
    }
  }

  // 启动时读取保存的滑块值
  try {
    const cfg = JSON.parse(localStorage.getItem('ff_cfg') || '{}');
    ['big-size','small-size','quality','fps'].forEach((k) => {
      if (cfg[k]) {
        const sl = $raw('sl-' + k);
        const sv = $raw('sv-' + k);
        if (sl) sl.value = cfg[k];
        if (sv) sv.textContent = cfg[k];
      }
    });
    if (cfg['big-size']) window.__bigMaxSize = SIZE_MAP[cfg['big-size']] || 720;
    // 恢复画质/帧率档位
    try {
      const qs = localStorage.getItem('ff_quality');
      const fs = localStorage.getItem('ff_fps');
      if (qs) window.__qLevel = parseInt(qs, 10) || 5;
      if (fs) window.__fpsLevel = parseInt(fs, 10) || 3;
    } catch (e) {}
  } catch (e) {}

  // 设备类型
  document.querySelectorAll('.rp-type').forEach((t) => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.rp-type').forEach((x) => x.classList.remove('active'));
      t.classList.add('active');
      const ty = t.dataset.type;
      if (ty !== 'local') toast(`${t.textContent} 需要授权，暂未开放`);
    });
  });

  /* ==================== 编号网格 01-20 ==================== */

  function untaggedDevices(devices) {
    const tagged = new Set();
    (window.S?.tags?.tags || []).forEach(t => (t.serials || []).forEach(s => tagged.add(s)));
    return devices.filter(d => !tagged.has(d.serial));
  }

  function renderNumGrid() {
    const kind = window.__deviceFilter || 'all';
    const devices = (window.S?.devices || []).filter(d => kind === 'all' || (kind === 'access' ? (d.mode === 3 || d.is_accessible === true) : d.connection === kind));
    const untagged = untaggedDevices(devices);
    fillGrid($('num-grid-all'), devices);
    fillGrid($('num-grid-untag'), untagged);
    const e1 = $('cnt-all'); if (e1) e1.textContent = `${devices.filter(d => d.state === 'online').length}/${devices.length}`;
    const e2 = $('cnt-untagged'); if (e2) e2.textContent = `${untagged.filter(d => d.state === 'online').length}/${untagged.length}`;
    for (const [id, group] of [['chk-all', devices], ['chk-untagged', untagged]]) {
      const input = document.getElementById(id);
      if (!input) continue;
      const eligible = group.filter(d => d.state === 'online');
      const count = eligible.filter(d => window.S?.multi?.has(d.serial)).length;
      input.checked = eligible.length > 0 && count === eligible.length;
      input.indeterminate = count > 0 && count < eligible.length;
    }
  }
  window.renderNumGrid = renderNumGrid;

  /* ===== 「全显 / 只显已选」：控制界面里显示哪些手机（2026-11-06 补齐） =====
     这两个按钮在 index.html 里一直存在，但从来没绑过事件，点了没反应。
       全显     —— 界面里所有手机都出现（不受标签筛选限制）
       只显已选 —— 只显示下方号码格子里已勾选的手机
     显示范围放在 window.__gridScope，由 app.js 的 renderGrid() 读取。 */
  (function bindGridScope() {
    const toAll = document.getElementById('btn-show-all');
    const toSel = document.getElementById('btn-show-selected');
    const paint = (which) => {
      toAll?.classList.toggle('on', which === 'all');
      toSel?.classList.toggle('on', which === 'selected');
    };
    toAll?.addEventListener('click', () => {
      window.__gridScope = 'all';
      paint('all');
      window.renderGrid?.();
      toast('界面显示：全部手机');
    });
    toSel?.addEventListener('click', () => {
      window.__gridScope = 'selected';
      paint('selected');
      window.renderGrid?.();
      const n = window.S?.multi?.size || 0;
      toast(n ? ('界面只显示已选 ' + n + ' 台') : '还没有勾选手机，界面会是空的');
    });
    paint(window.__gridScope || 'all');
  })();

  function fillGrid(box, devices) {
    if (!box) return;
    box.replaceChildren();
    devices.forEach(dev => {
      const cell = document.createElement('div');
      cell.className = 'num-cell' + (dev.state === 'online' ? '' : ' off') + (window.S?.multi?.has(dev.serial) ? ' on' : '');
      cell.textContent = String(dev.index || '').padStart(2, '0');
      cell.title = dev.serial;
      cell.setAttribute('role', 'button');
      cell.setAttribute('aria-pressed', String(!!window.S?.multi?.has(dev.serial)));
      if (dev.state === 'online') cell.addEventListener('click', () => window.toggleMulti?.(dev.serial));
      box.appendChild(cell);
    });
  }

  for (const [id, group] of [['chk-all', devices => devices], ['chk-untagged', untaggedDevices]]) {
    document.getElementById(id)?.addEventListener('change', event => {
      const kind = window.__deviceFilter || 'all';
      const filtered = (window.S?.devices || []).filter(d => kind === 'all' || (kind === 'access' ? (d.mode === 3 || d.is_accessible === true) : d.connection === kind));
      const devices = group(filtered).filter(d => d.state === 'online');
      for (const dev of devices) event.target.checked ? window.S.multi.add(dev.serial) : window.S.multi.delete(dev.serial);
      window.renderGrid?.();
      renderNumGrid();
    });
  }

  /* ==================== 更多功能菜单 ==================== */

  function showMoreMenu() {
    const items = [
      ['设备信息', () => window.showProps?.(curDev)],
      ['设为壁纸编号', () => toast('待接入')],
      ['显示内网IP', () => toast('待接入')],
      ['快捷键', () => toast('待接入')],
      ['浅色主题', () => document.body.classList.toggle('light')],
      ['关于', () => toast('FishFleet v1.0 · AI 原生群控')],
    ];
    const html = items.map(([n], i) =>
      `<div class="ctx-item" data-i="${i}">${n}</div>`
    ).join('');

    const m = $('ctx-menu');
    m.innerHTML = html;
    m.classList.remove('hidden');
    m.style.left = '60%';
    m.style.top = '120px';

    m.querySelectorAll('[data-i]').forEach((n) => {
      n.addEventListener('click', () => {
        const fn = items[Number(n.dataset.i)][1];
        m.classList.add('hidden');
        fn();
      });
    });
  }

  /* ==================== toast ==================== */

  function toast(msg) {
    // 保留本次窗口最近提示，方便定位一闪而过的报错；不持久化到磁盘。
    window.__toastHistory = window.__toastHistory || [];
    window.__toastHistory.push({ at: Date.now(), message: String(msg) });
    if (window.__toastHistory.length > 50) window.__toastHistory.shift();
    const t = $('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove('show'), 2200);
  }
  // id="toast" 会成为 window.toast 的同名 DOM 属性，必须显式覆盖。
  window.toast = toast;

  /* ==================== 顶栏按钮 ==================== */

  $('btn-refresh')?.addEventListener('click', () => {
    window.loadDevices?.();
    toast('刷新中…');
  });

  // 顶栏设置按钮与右侧「系统设置」使用同一面板，避免两套实现互相覆盖。
  $('btn-settings')?.addEventListener('click', () => openSysSettings());

  $('settings-close')?.addEventListener('click', () => $('settings-modal').classList.add('hidden'));
  $('settings-modal')?.addEventListener('click', (e) => {
    if (e.target.id === 'settings-modal') $('settings-modal').classList.add('hidden');
  });

  /* ==================== 预启动（汇城式） ==================== */
  // 提前把可见设备的流建好，点的时候秒开
  async function prewarm() {
    // 小屏WS拥有共享流参数，旧预热API会用默认720/2fps覆盖它。
    if (window.__jpegLive) { window.reapplyStreamParams?.(); return; }
    const devs = (window.S?.devices || []).filter((d) => d.state === 'online');
    const BATCH = 6;              // 每批并发 6 台
    let done = 0;
    for (let i = 0; i < devs.length; i += BATCH) {
      const batch = devs.slice(i, i + BATCH);
      await Promise.all(batch.map(async (d) => {
        try {
          const r = await post('/api/stream/start', { serial: d.serial, max_size: window.__bigMaxSize || 720 });
          done += 1;
          if (r?.reused !== true) console.log(`[prewarm] ${d.serial} 已预热 (${done}/${devs.length})`);
        } catch (e) { /* 忽略 */ }
      }));
    }
    console.log(`[prewarm] 全部完成 ${done}/${devs.length}`);
    toast(`已预热 ${done} 台，全部秒开`);
  }

  // ★ 每次网格重绘后重新绑定拖拽
  const _origRenderGrid = window.renderGrid;
  if (typeof _origRenderGrid === 'function' && !window._dragPatched) {
    window._dragPatched = true;
    window.renderGrid = function () {
      const r = _origRenderGrid.apply(this, arguments);
      return r;
    };
  }
  // 排序统一使用 app.js 的原生拖拽，禁止再绑定 fixed 定位鼠标拖拽。

  setTimeout(() => { try { initTextInput(); } catch (e) {} }, 1200);
  setTimeout(() => { try { initDirectKeys(); } catch (e) {} }, 1200);
  setTimeout(() => { try { initDirectIme(); } catch (e) {} }, 1200);
  setTimeout(() => {
    try { initMenuActions(); } catch (e) { console.error('[initMenuActions]', e); }
  }, 1400);
  setTimeout(() => {
    try { initRightPanel(); } catch (e) { console.error('[initRightPanel]', e); }
  }, 1600);
  setTimeout(() => {
    try { initAiPanel(); } catch (e) { console.error('[initAiPanel]', e); }
  }, 1800);

  setTimeout(prewarm, 6000);

  /** 按 localStorage 保存的顺序重排 S.devices，并按新位置重新编号 */
  function applySavedOrder() {
    let order = null;
    try {
      order = JSON.parse(localStorage.getItem('ff_order') || 'null');
    } catch (e) { order = null; }
    if (!Array.isArray(order) || !order.length) return;
    if (!window.S || !Array.isArray(window.S.devices)) return;

    const map = new Map(window.S.devices.map((d) => [d.serial, d]));
    const sorted = [];
    for (const s of order) {
      const d = map.get(s);
      if (d) { sorted.push(d); map.delete(s); }
    }
    for (const d of map.values()) sorted.push(d);   // 新设备追加到末尾

    window.S.devices = sorted;
    // 编号始终等于显示位置
    sorted.forEach((d, i) => { d.index = i + 1; });
  }
  window.applySavedOrder = applySavedOrder;

  /* ==================== 卡片拖拽排序（汇城支持） ==================== */

  let _dragCard = null;
  let _dragFrom = null;
  let _dragStartX = 0, _dragStartY = 0;
  let _dragging = false;
  let _dragPlaceholder = null;
  let _dragArmed = false;
  let _dragArmTimer = null;

  function initCardDrag() {
    return; // 已弃用：与 app.js 原生拖拽冲突，不能修改卡片定位或指针事件。
    const grid = $raw('grid');
    if (!grid || grid._dragBound) return;
    grid._dragBound = true;

    grid.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      const card = e.target.closest('.phone');
      if (!card) return;
      // 点关闭按钮/右键菜单时不拖
      if (e.target.classList.contains('close-x')) return;

      _dragCard = card;
      _dragStartX = e.clientX;
      _dragStartY = e.clientY;
      _dragging = false;
      _dragArmed = false;
      _dragFrom = Array.from(grid.children).indexOf(card);

      // ★ 必须按住 180ms 才允许拖动，避免「点击」被 SetCursorPos 的
      //   mousemove 误判成拖拽（之前导致卡片顺序被意外保存）
      clearTimeout(_dragArmTimer);
      _dragArmTimer = setTimeout(() => {
        if (_dragCard === card) _dragArmed = true;
      }, 180);
    });

    document.addEventListener('mousemove', (e) => {
      if (!_dragCard) return;

      // 超过 8px 且已按住 180ms 才算拖拽（否则是点击）
      if (!_dragging) {
        if (!_dragArmed) return;
        const dx = Math.abs(e.clientX - _dragStartX);
        const dy = Math.abs(e.clientY - _dragStartY);
        if (dx < 8 && dy < 8) return;

        _dragging = true;
        const r = _dragCard.getBoundingClientRect();
        _dragCard.classList.add('dragging');
        _dragCard.style.position = 'fixed';
        _dragCard.style.width = r.width + 'px';
        _dragCard.style.height = r.height + 'px';
        _dragCard.style.left = r.left + 'px';
        _dragCard.style.top = r.top + 'px';
        _dragCard.style.zIndex = '9999';
        _dragCard.style.pointerEvents = 'none';
        _dragCard._offsetX = e.clientX - r.left;
        _dragCard._offsetY = e.clientY - r.top;

        // 占位符（保持布局）
        _dragPlaceholder = document.createElement('div');
        _dragPlaceholder.className = 'phone drag-placeholder';
        _dragPlaceholder.style.width = r.width + 'px';
        _dragPlaceholder.style.height = r.height + 'px';
        _dragCard.parentNode.insertBefore(_dragPlaceholder, _dragCard);
      }

      _dragCard.style.left = (e.clientX - _dragCard._offsetX) + 'px';
      _dragCard.style.top = (e.clientY - _dragCard._offsetY) + 'px';

      // 找最近的卡片，决定插入位置
      const cards = Array.from(grid.querySelectorAll('.phone:not(.dragging)'));
      let target = null;
      for (const c of cards) {
        const r = c.getBoundingClientRect();
        if (e.clientX >= r.left && e.clientX <= r.right &&
            e.clientY >= r.top && e.clientY <= r.bottom) {
          target = c;
          break;
        }
      }
      if (target) {
        const r = target.getBoundingClientRect();
        const before = (e.clientX - r.left) < r.width / 2;
        if (before) {
          grid.insertBefore(_dragPlaceholder, target);
        } else {
          grid.insertBefore(_dragPlaceholder, target.nextSibling);
        }
      }
    });

    document.addEventListener('mouseup', (e) => {
      clearTimeout(_dragArmTimer);
      _dragArmed = false;
      if (!_dragCard) return;
      const card = _dragCard;
      const wasDragging = _dragging;
      _dragCard = null;
      _dragging = false;
      card.classList.remove('dragging');
      card.style.cssText = '';
      if (card._offsetX !== undefined) { delete card._offsetX; delete card._offsetY; }

      if (_dragPlaceholder) {
        if (wasDragging) {
          // 把卡片放到占位符位置
          grid.insertBefore(card, _dragPlaceholder);
        }
        _dragPlaceholder.remove();
        _dragPlaceholder = null;
      }

      if (!wasDragging) return;   // 只是点击，交给 click 处理

      // ★ 按 DOM 顺序重排 S.devices 并持久化
      const order = Array.from(grid.querySelectorAll('.phone'))
        .map((n) => n.dataset.serial)
        .filter(Boolean);

      if (window.S && Array.isArray(window.S.devices)) {
        const map = new Map(window.S.devices.map((d) => [d.serial, d]));
        const sorted = order.map((s) => map.get(s)).filter(Boolean);
        for (const d of window.S.devices) {
          if (!sorted.includes(d)) sorted.push(d);
        }
        window.S.devices = sorted;

        // ★★ 按拖动后的位置重新编号（01, 02, 03 ...）
        sorted.forEach((d, i) => { d.index = i + 1; });

        try {
          localStorage.setItem('ff_order', JSON.stringify(sorted.map((d) => d.serial)));
        } catch (err) {}
        console.log('[drag] 新顺序已保存并重新编号', sorted.length);

        // 重渲染，让卡片上的大编号同步更新
        if (typeof window.renderGrid === 'function') {
          try { window.renderGrid(); } catch (err) {}
        }
      }
    });
  }

  /* ==================== 初始化 ==================== */

  paintEmpty(true);

  // 首次载入后渲染编号网格
  setTimeout(renderNumGrid, 1500);
  setTimeout(renderNumGrid, 4000);

  // 顶栏统计
  setInterval(() => {
    const ds = window.S?.devices || [];
    const on = ds.filter((d) => d.state === 'online').length;
    const e1 = document.getElementById('stat-online'); if (e1) e1.textContent = on;
    const e2 = document.getElementById('stat-total'); if (e2) e2.textContent = ds.length;
  }, 2000);

  console.log('[ui] 新布局交互层已加载');


/* ===========================================================================
 * 第七批 UX：框选多台 / 全局快捷键 / CSV 导入导出 / 改镜像名 / 版本信息
 * 对齐汇城：useBoxSelect / hotkeys-js / papaparse / ChangeMirrorName / Version
 * =========================================================================== */

/* ---------------- 1. 框选多台（useBoxSelect） ---------------- */
(function initBoxSelect() {
  let startX = 0, startY = 0, box = null, active = false;

  function ensureBox() {
    if (box) return box;
    box = document.createElement('div');
    box.id = '__selectbox';
    box.style.cssText = [
      'position:fixed', 'border:1px solid #7c5cff', 'background:rgba(124,92,255,.14)',
      'pointer-events:none', 'z-index:9997', 'display:none', 'border-radius:2px'
    ].join(';');
    document.body.appendChild(box);
    return box;
  }

  // 只在空白区域拖拽（避免和卡片拖动冲突）
  document.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    const t = e.target;
    // 从网格空白处或网格容器开始
    if (t !== document.getElementById('grid')) return;
    active = true;
    startX = e.clientX; startY = e.clientY;
    const b = ensureBox();
    b.style.display = 'block';
    b.style.left = startX + 'px';
    b.style.top = startY + 'px';
    b.style.width = '0px';
    b.style.height = '0px';
  });

  document.addEventListener('mousemove', (e) => {
    if (!active) return;
    const b = ensureBox();
    const x = Math.min(e.clientX, startX);
    const y = Math.min(e.clientY, startY);
    const w = Math.abs(e.clientX - startX);
    const h = Math.abs(e.clientY - startY);
    b.style.left = x + 'px';
    b.style.top = y + 'px';
    b.style.width = w + 'px';
    b.style.height = h + 'px';
  });

  document.addEventListener('mouseup', (e) => {
    if (!active) return;
    active = false;
    const b = ensureBox();
    b.style.display = 'none';

    const x1 = Math.min(e.clientX, startX), y1 = Math.min(e.clientY, startY);
    const x2 = Math.max(e.clientX, startX), y2 = Math.max(e.clientY, startY);
    // 仅在网格空白处单击清除多选；框选和拖动不能误清除。
    if (x2 - x1 < 5 && y2 - y1 < 5) {
      if (e.target === document.getElementById('grid') && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
        window.clearMulti?.();
      }
      return;
    }
    if (x2 - x1 < 20 || y2 - y1 < 20) return;

    const hit = [];
    document.querySelectorAll('.phone[data-serial]').forEach((card) => {
      const r = card.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      if (cx >= x1 && cx <= x2 && cy >= y1 && cy <= y2) {
        hit.push(card.dataset.serial);
      }
    });

    if (!hit.length) return;
    if (window.S && window.S.multi) {
      if (!e.ctrlKey && !e.shiftKey) window.S.multi.clear();
      hit.forEach((s) => window.S.multi.add(s));
      // 刷新 UI
      document.querySelectorAll('.phone[data-serial]').forEach((c) => {
        c.classList.toggle('selected', window.S.multi.has(c.dataset.serial));
      });
      toast(`框选 ${hit.length} 台`);
    }
  });

  console.log('[boxesel] 框选已就绪');
})();


/* ---------------- 2. 全局快捷键（hotkeys-js 对齐） ---------------- */
(function initHotkeys() {
  const KEYMAP = [
    { key: 'F1', desc: '打开快捷键帮助' },
    { key: 'F2', desc: '刷新设备列表' },
    { key: 'F5', desc: '刷新当前设备画面' },
    { key: 'ctrl+a', desc: '全选设备' },
    { key: 'ctrl+d', desc: '取消全选' },
    { key: 'ctrl+1..9', desc: '切到第 1-9 台的大屏' },
    { key: 'ctrl+left', desc: '上一台设备' },
    { key: 'ctrl+right', desc: '下一台设备' },
    { key: 'ctrl+h', desc: '当前设备回桌面' },
    { key: 'ctrl+b', desc: '当前设备返回键' },
    { key: 'ctrl+shift+s', desc: '当前设备截图' },
    { key: 'esc', desc: '关闭菜单/弹窗' },
  ];
  window.__KEYMAP = KEYMAP;

  function help() {
    alert(
      '快捷键\n\n' +
      KEYMAP.map((k) => `${k.key.padEnd(14)} ${k.desc}`).join('\n')
    );
  }
  window.showHotkeyHelp = help;

  function openByIndex(i) {
    const ds = window.S?.devices || [];
    const d = ds[i];
    if (!d) { toast(`没有第 ${i + 1} 台`); return; }
    if (window.openBigscreen) window.openBigscreen(d.serial, d.index);
  }

  function cur() {
    return window.__ctxSerial || window.S?.selected || null;
  }

  document.addEventListener('keydown', (e) => {
    // 输入框里不拦截
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable || e.target.closest?.('[contenteditable]:not([contenteditable="false"])')) return;

    const k = e.key;

    if (k === 'F1') { e.preventDefault(); help(); return; }
    if (k === 'F2') { e.preventDefault(); document.getElementById('btn-refresh')?.click(); return; }
    if (k === 'F5') {
      e.preventDefault();
      const s = cur();
      if (s) {
        const card = document.querySelector(`.phone[data-serial="${s}"]`);
        const img = card?.querySelector('img');
        if (img) img.src = `/api/devices/${s}/screenshot?fresh=1&t=${Date.now()}`;
        toast('已刷新画面');
      }
      return;
    }
    if (k === 'Escape') {
      document.getElementById('float-menu')?.classList.remove('show');
      document.getElementById('ai-modal')?.classList.add('hidden');
      document.getElementById('rp-otg-panel')?.classList.add('hidden');
      document.getElementById('right-panel')?.classList.remove('otg-mode');
      return;
    }

    if (e.ctrlKey || e.metaKey) {
      const ds = window.S?.devices || [];
      if (k.toLowerCase() === 'a' && !e.altKey && !e.shiftKey) {
        e.preventDefault();
        if (window.S?.multi) {
          window.S.multi.clear();
          ds.forEach((d) => window.S.multi.add(d.serial));
          window.renderGrid?.();
          toast(`已选 ${ds.length} 台`);
        }
        return;
      }
      if (k === 'd') {
        e.preventDefault();
        window.clearMulti?.();
        toast('已取消选择');
        return;
      }
      if (k >= '1' && k <= '9') {
        e.preventDefault();
        openByIndex(parseInt(k, 10) - 1);
        return;
      }
      if (k === 'ArrowLeft' || k === 'ArrowRight') {
        e.preventDefault();
        const s = cur();
        const idx = ds.findIndex((d) => d.serial === s);
        const nxt = k === 'ArrowLeft' ? idx - 1 : idx + 1;
        if (nxt >= 0 && nxt < ds.length) openByIndex(nxt);
        return;
      }
      if (k === 'h') {
        e.preventDefault();
        const s = cur();
        if (s) post('/api/key', { serial: s, key: 'home' });
        return;
      }
      if (k === 'b') {
        e.preventDefault();
        const s = cur();
        if (s) post('/api/key', { serial: s, key: 'back' });
        return;
      }
      if (e.shiftKey && (k === 'S' || k === 's')) {
        e.preventDefault();
        const s = cur();
        if (s) {
          fetch('/api/ext/download_image', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ serial: s, out_path: '' }),
          }).then((r) => r.json()).then((d) => toast(d.ok ? '已截图：' + d.path : '截图失败'));
        }
        return;
      }
    }
  });

  console.log('[hotkeys] 已就绪（F1 查看帮助）');
})();


/* ---------------- 3. CSV 导入导出（papaparse 对齐） ---------------- */
window.exportDevicesCsv = function () {
  const ds = window.S?.devices || [];
  const rows = [['serial', 'index', 'name', 'state', 'width', 'height', 'model']];
  ds.forEach((d) => {
    rows.push([d.serial, d.index, d.account?.name || d.name || '',
               d.state, d.width, d.height, d.model || '']);
  });
  const csv = rows.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `fishfleet_devices_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  toast(`已导出 ${ds.length} 台`);
};

window.importDevicesCsv = function () {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = '.csv';
  inp.onchange = async () => {
    const f = inp.files?.[0];
    if (!f) return;
    const text = await f.text();
    const lines = text.split(/\r?\n/).filter((x) => x.trim());
    if (lines.length < 2) { toast('CSV 内容为空'); return; }

    const header = lines[0].split(',').map((x) => x.replace(/"/g, '').trim().toLowerCase());
    const si = header.indexOf('serial');
    const ni = header.indexOf('name');
    const ii = header.indexOf('index');
    if (si < 0) { toast('CSV 缺少 serial 列'); return; }

    let changed = 0;
    for (let r = 1; r < lines.length; r++) {
      const cells = lines[r].match(/("([^"]|"")*"|[^,]*)/g)?.filter((x) => x !== '') || [];
      const clean = cells.map((c) => c.replace(/^"|"$/g, '').replace(/""/g, '"'));
      const serial = (clean[si] || '').trim();
      if (!serial) continue;
      if (ni >= 0 && clean[ni]) {
        try {
          await fetch(`/api/devices/${encodeURIComponent(serial)}/name`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: clean[ni] }),
          });
          changed++;
        } catch (e) {}
      }
      if (ii >= 0 && clean[ii]) {
        const n = parseInt(clean[ii], 10);
        if (Number.isFinite(n)) {
          try {
            await fetch(`/api/devices/${encodeURIComponent(serial)}/index`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ index: n }),
            });
          } catch (e) {}
        }
      }
    }
    toast(`导入完成，更新 ${changed} 台（刷新页面生效）`);
  };
  inp.click();
};


/* ---------------- 4. 改镜像名（ChangeMirrorName） ---------------- */
window.changeMirrorName = async function (serial) {
  const dev = (window.S?.devices || []).find((d) => d.serial === serial);
  const cur = dev?.account?.mirror_name || dev?.mirror_name || '';
  const v = prompt('镜像名称（显示在卡片上，不影响设备本身）', cur);
  if (v === null) return;
  try {
    const r = await fetch(`/api/devices/${encodeURIComponent(serial)}/name`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: v }),
    });
    const d = await r.json();
    toast(d.ok ? '镜像名已改' : '失败');
    if (typeof renderGrid === 'function') renderGrid();
  } catch (e) {
    toast('失败');
  }
};


/* ---------------- 5. 版本信息（Version） ---------------- */
window.showVersion = async function () {
  let info = {};
  try {
    const r = await fetch('/api/ext/get_system_config');
    info = await r.json();
  } catch (e) {}
  alert(
    'FishFleet v1.0\n\n' +
    `系统：${info.os || '?'} ${info.os_version || ''}\n` +
    `Python：${info.python || '?'}\n` +
    `主机：${info.hostname || '?'}\n` +
    `ADB：${info.adb || '?'}\n` +
    `外网 IP：${info.external_ip || '?'}\n\n` +
    `设备：${(window.S?.devices || []).length} 台\n` +
    `快捷键：按 F1 查看`
  );
};



/* ---------------- 6. 底部工具条绑定 ---------------- */
(function initToolbar() {
  const bind = (id, fn) => {
    document.getElementById(id)?.addEventListener('click', fn);
  };

  bind('tb-export', () => { try { window.exportDevicesCsv(); } catch (e) { toast('导出失败'); } });
  bind('tb-import', () => { try { window.importDevicesCsv(); } catch (e) { toast('导入失败'); } });
  bind('tb-hotkeys', () => { try { window.showHotkeyHelp(); } catch (e) {} });
  bind('tb-version', () => { try { window.showVersion(); } catch (e) {} });
  bind('tb-diag', async () => {
    // 复用 OTG 面板的诊断
    const p = document.getElementById('right-panel');
    const otg = document.getElementById('rp-otg-panel');
    p?.classList.add('otg-mode');
    otg?.classList.remove('hidden');
    try { renderOtgPanel(); } catch (e) {}
    setTimeout(() => document.getElementById('otg-diag-btn')?.click(), 400);
  });

  console.log('[toolbar] 底部工具条已绑定');
})();

})();

(function floatingStage(){
 const box=document.getElementById('stage'),head=document.getElementById('stage-drag-head');if(!box||!head)return;
 try{const p=JSON.parse(localStorage.getItem('ff_stage_position')||'null');if(p){box.style.left=Math.max(10,Math.min(innerWidth-100,p.x))+'px';box.style.top=Math.max(40,Math.min(innerHeight-60,p.y))+'px';}}catch(e){}
 let drag=null;head.addEventListener('pointerdown',e=>{if(box.classList.contains('ai-live-stage')||window.__lockBig||e.target.closest('button'))return;const r=box.getBoundingClientRect();drag={x:e.clientX-r.left,y:e.clientY-r.top};head.setPointerCapture(e.pointerId);});
 head.addEventListener('pointermove',e=>{if(box.classList.contains('ai-live-stage')){drag=null;return;}if(!drag)return;box.style.left=Math.max(10,Math.min(innerWidth-box.offsetWidth-10,e.clientX-drag.x))+'px';box.style.top=Math.max(40,Math.min(innerHeight-60,e.clientY-drag.y))+'px';window.positionStageSettings?.();});
 head.addEventListener('pointerup',()=>{if(!drag)return;drag=null;try{localStorage.setItem('ff_stage_position',JSON.stringify({x:box.offsetLeft,y:box.offsetTop}));}catch(e){}});
 document.getElementById('stage-close')?.addEventListener('click',()=>window.closeBigscreen?.());
})();
