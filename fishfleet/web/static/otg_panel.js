/* OTG 面板：参考汇城 OTGSetting 的目标选择与网段操作。 */
(() => {
  let multi = false, rows = [], found = [];
  try { const saved = JSON.parse(localStorage.getItem('ff_otg_ranges') || '[]'); if (Array.isArray(saved)) rows = saved; } catch (_) {}
  const save = () => localStorage.setItem('ff_otg_ranges', JSON.stringify(rows));
  const ipOK = value => /^\d{1,3}(\.\d{1,3}){3}$/.test(value) && value.split('.').every(x => +x <= 255);
  const ipNum = value => value.split('.').reduce((a, b) => a * 256 + +b, 0);
  const normalize = row => {
    const r = {ip1: String(row.ip1 || ''), ip2: String(row.ip2 || row.ip1 || ''), port: Number(row.port), port2: row.port2 ? Number(row.port2) : null};
    if (!ipOK(r.ip1) || !ipOK(r.ip2) || !Number.isInteger(r.port) || r.port < 1 || r.port > 65535 || r.port2 !== null && (!Number.isInteger(r.port2) || r.port2 < r.port || r.port2 > 65535) || ipNum(r.ip2) < ipNum(r.ip1)) throw Error('请检查 IP 地址及端口范围');
    if ((ipNum(r.ip2) - ipNum(r.ip1) + 1) * ((r.port2 || r.port) - r.port + 1) > 500) throw Error('单次扫描最多 500 个地址与端口组合');
    return r;
  };
  const octets = id => `<span class="otg-ip" id="${id}">${[0,1,2,3].map(i => `${i ? '<b>·</b>' : ''}<input type="number" min="0" max="255" placeholder="${['192','168','0-255','0-255'][i]}" ${id === 'otg-ip2' && i < 3 ? 'disabled' : ''}>`).join('')}</span>`;
  const getIP = id => [...document.querySelectorAll(`#${id} input`)].map(e => e.value).join('.');
  const message = text => { const e = document.getElementById('otg-status'); if (e) e.textContent = text; };
  const api = async (url, body) => {
    const r = await fetch(url, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const data = await r.json();
    if (!r.ok) throw Error(typeof data.detail === 'string' ? data.detail : (data.error || `请求失败 ${r.status}`));
    return data;
  };
  const add = row => { const r = normalize(row); if (rows.some(x => JSON.stringify(x) === JSON.stringify(r))) return false; rows.push(r); return true; };
  function resultList() {
    const out = document.getElementById('otg-results'); if (!out) return; out.replaceChildren();
    for (const item of found) {
      const line = document.createElement('div'); line.className = 'otg-range';
      const name = document.createElement('span'); name.textContent = `${item.ip}:${item.port}`;
      const button = document.createElement('button'); button.textContent = item.connected ? '已接入' : '接入'; button.disabled = !!item.connected;
      button.onclick = async () => { button.disabled = true; button.textContent = '连接中…'; try { const d = await api('/api/otg/connect', {ip:item.ip, port:item.port}); item.connected = !!d.ok; button.textContent = d.ok ? '已接入' : '重试'; message(`${item.ip}:${item.port}：${d.message || d.error || (d.ok ? '已接入' : '连接失败')}`); if (d.ok) window.refreshDevices?.(); } catch (e) { button.textContent = '重试'; message(`接入失败：${e.message}`); } finally { button.disabled = !!item.connected; } };
      line.append(name, button); out.append(line);
    }
  }
  function list() {
    const out = document.getElementById('otg-ranges'); if (!out) return; out.replaceChildren();
    if (!rows.length) { out.textContent = '暂无数据'; return; }
    rows.forEach((r, i) => {
      const card = document.createElement('div'); card.className = 'otg-range';
      const title = document.createElement('strong'); title.textContent = r.ip1 === r.ip2 ? r.ip1 : `${r.ip1} — ${r.ip2}`;
      const info = document.createElement('div'); info.textContent = `端口 ${r.port}${r.port2 ? ' — ' + r.port2 : ''}`;
      const scan = document.createElement('button'); scan.textContent = '扫描'; scan.onclick = () => runScan([r]);
      const del = document.createElement('button'); del.textContent = '删除'; del.onclick = () => { rows.splice(i, 1); save(); list(); };
      card.append(title, info, scan, del); out.append(card);
    });
  }
  async function runScan(ranges) {
    if (!ranges.length) return message('没有可扫描的网段');
    try {
      const checked = ranges.map(normalize); message('正在扫描，请稍候…');
      const d = await api('/api/otg/scan-ranges', {ranges:checked});
      found = d.found || []; resultList(); message(`扫描完成：检查 ${d.tested} 个地址与端口，发现 ${found.length} 个开放端口；请点击接入验证 ADB。`);
    } catch (e) { message(`扫描失败：${e.message}`); }
  }
  window.renderHcOtgPanel = function(box) {
    box.innerHTML = `<div class="hc-otg"><section><header><span>第 <b>1</b> 步：启用端口</span><span class="otg-tabs"><button id="otg-one" class="on">单端口</button><button id="otg-multi">多端口</button></span></header><div class="otg-row"><label id="otg-port-label">默认端口</label><input id="otg-port" type="number" min="1" max="65535" value="5555"><span id="otg-port2-wrap" hidden> — <input id="otg-port2" type="number" min="1" max="65535" value="5555"></span><button id="otg-start" class="primary">开启</button></div><small>开启所选在线 USB 设备的 ADB TCP 端口；未多选时作用于全部在线 USB 设备。</small><small id="otg-mode-tip">启用后请手动将设备切换到 OTG 模式。</small></section><section><header>第 <b>2</b> 步：扫描设备</header><div class="otg-row">${octets('otg-ip1')}</div><div class="otg-row">${octets('otg-ip2')}</div><div class="otg-actions"><button id="otg-add">添加</button><button id="otg-range-scan" class="primary">扫描</button></div><div class="otg-actions"><button id="otg-view">全看</button><button id="otg-all-scan">全扫</button><button id="otg-all-add">全加</button></div><div id="otg-results"></div></section><section><header>网段管理</header><div class="otg-actions"><button id="otg-export">导出</button><button id="otg-import">导入</button><button id="otg-remove">删除全部</button></div><input type="file" id="otg-file" accept=".txt" hidden><button id="otg-saved-scan" class="primary otg-wide">扫描下方全部网段</button></section><div id="otg-status" role="status"></div><div id="otg-ranges"></div></div>`;
    const mode = v => { multi = v; document.getElementById('otg-port2-wrap').hidden = !v; document.getElementById('otg-start').hidden = v; document.getElementById('otg-mode-tip').hidden = v; document.getElementById('otg-port-label').textContent = v ? '端口区间' : '默认端口'; document.getElementById('otg-one').classList.toggle('on', !v); document.getElementById('otg-multi').classList.toggle('on', v); };
    document.getElementById('otg-one').onclick = () => mode(false); document.getElementById('otg-multi').onclick = () => mode(true); mode(multi);
    const first = [...document.querySelectorAll('#otg-ip1 input')], second = [...document.querySelectorAll('#otg-ip2 input')];
    first.forEach((input, i) => input.addEventListener('input', () => { if (i < 3) second[i].value = input.value; }));
    const range = () => normalize({ip1:getIP('otg-ip1'), ip2:getIP('otg-ip2'), port:document.getElementById('otg-port').value, port2:multi ? document.getElementById('otg-port2').value : null});
    document.getElementById('otg-add').onclick = () => { try { message(add(range()) ? '已添加网段' : '网段已存在'); save(); list(); } catch(e) { message(e.message); } };
    document.getElementById('otg-range-scan').onclick = () => { try { runScan([range()]); } catch(e) { message(e.message); } };
    document.getElementById('otg-saved-scan').onclick = () => runScan(rows);
    document.getElementById('otg-start').onclick = async e => {
      const port = Number(document.getElementById('otg-port').value); if (!Number.isInteger(port) || port < 1 || port > 65535) return message('端口必须在 1 到 65535 之间');
      const serials = [...(window.S?.multi || [])]; const button = e.currentTarget; button.disabled = true; message('正在开启 USB 设备端口，请稍候…');
      try { const d = await api('/api/otg/enable-port', {port, serials}); message(d.total ? `端口开启成功 ${d.success}/${d.total} 台。${d.results.filter(x => !x.ok).map(x => `${x.serial}: ${x.message}`).join('；')}` : '没有在线且已授权的 USB 设备'); } catch(err) { message(`开启失败：${err.message}`); } finally { button.disabled = false; }
    };
    const arp = async () => { const d = await api('/api/ext/get_arp_out'); if (!d.ok) throw Error(d.error || '读取失败'); return (d.entries || []).filter(x => ipOK(x.ip)); };
    document.getElementById('otg-view').onclick = async () => { try { const entries = await arp(); message(entries.length ? `局域网 ARP 记录（${entries.length}）：${entries.map(x => `${x.ip} ${x.mac}`).join('；')}` : 'ARP 表中没有地址记录'); } catch(e) { message(`局域网列表读取失败：${e.message}`); } };
    document.getElementById('otg-all-scan').onclick = async () => { try { const entries = await arp(); const port = Number(document.getElementById('otg-port').value); await runScan(entries.map(x => ({ip1:x.ip, ip2:x.ip, port}))); } catch(e) { message(`全扫失败：${e.message}`); } };
    document.getElementById('otg-all-add').onclick = async () => { try { message('正在读取设备内网地址…'); const d = await api('/api/otg/device-ips', {}); let count = 0; for (const x of d.devices || []) if (x.ok && x.ip) { try { if (add({ip1:x.ip, ip2:x.ip, port:x.port || Number(document.getElementById('otg-port').value)})) count++; } catch (_) {} } save(); list(); const errors = (d.devices || []).filter(x => !x.ok).length; message(`已新增 ${count} 个设备地址${errors ? `，${errors} 台未读取到地址` : ''}`); } catch(e) { message(`读取设备地址失败：${e.message}`); } };
    document.getElementById('otg-remove').onclick = () => { if (window.confirm('确定删除全部网段？')) { rows = []; save(); list(); } };
    document.getElementById('otg-export').onclick = () => { if (!rows.length) return message('没有可导出的网段'); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([rows.map(r => `${r.ip1}${r.ip1 === r.ip2 ? '' : '----' + r.ip2}:${r.port}${r.port2 ? '-' + r.port2 : ''}`).join('\n')], {type:'text/plain;charset=utf-8'})); a.download = '网段.txt'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); };
    document.getElementById('otg-import').onclick = () => document.getElementById('otg-file').click();
    document.getElementById('otg-file').onchange = async e => { const file = e.target.files[0]; if (!file) return; try { const incoming = (await file.text()).split(/\r?\n/).filter(x => x.trim()).map(line => { const m = line.trim().match(/^(\d+\.\d+\.\d+\.\d+)(?:----(\d+\.\d+\.\d+\.\d+))?(?::(\d+)(?:-(\d+))?)?$/); if (!m) throw Error('文件格式错误，未导入'); return normalize({ip1:m[1], ip2:m[2] || m[1], port:m[3] || 5555, port2:m[4] || null}); }); let count = 0; for (const r of incoming) if (add(r)) count++; save(); list(); message(`已导入 ${count} 条`); } catch(err) { message(err.message); } finally { e.target.value = ''; } };
    list(); resultList();
  };
})();
