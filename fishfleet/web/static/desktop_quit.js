/* 退出与关窗联动（2026-11-07）。
 *
 * 背景：主人反馈「点软件关闭时后台关不干净」。根因是窗口（Edge --app）和
 * 后端是两个进程，关窗口不会带走后端、Node 通道、ffmpeg 等。
 *
 * 这里做两件事：
 *   1. 页面加载后先 ping 一次 —— 刷新页面时，上一次的「关窗」请求会被取消，
 *      不会误杀（关键：刷新会触发 pagehide，必须能撤销）。
 *   2. pagehide/beforeunload 发 beacon（navigator.sendBeacon 优先，
 *      页面正在销毁时普通 fetch 会被取消）。
 *
 * 真正的「窗口关了就退」由 Python 侧监测浏览器进程句柄完成，
 * 这里的 beacon 只是兜底（例如窗口还在、页面被替换掉的情况）。
 */
(function () {
  'use strict';

  var PING_URL = '/api/desktop/ping';
  var QUIT_URL = '/api/desktop/quit';
  var armed = false;      // 只有确认页面真在跑才允许发关窗信号
  var quiting = false;

  function post(url, body) {
    try {
      return fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
        keepalive: true
      }).catch(function () {});
    } catch (e) { return Promise.resolve(); }
  }

  // 1) 上线：取消任何待处理的关闭请求
  function arm() {
    post(PING_URL, {});
    armed = true;
    // 定期心跳：窗口关掉后心跳自然断，后端 45 秒内认定退出（beacon 丢失的兜底）
    setInterval(function () {
      if (!quiting) post(PING_URL, {});
    }, 15000);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', arm, { once: true });
  } else {
    arm();
  }

  // 2) 关窗/刷新：发 beacon
  function sendQuit(reason) {
    if (!armed) return;          // 还没初始化完就跳走，不算关窗
    try {
      var payload = JSON.stringify({ reason: reason, grace: 8 });
      if (navigator.sendBeacon &&
          navigator.sendBeacon(QUIT_URL, new Blob([payload], { type: 'application/json' }))) {
        return;
      }
    } catch (e) { /* 落回 fetch */ }
    post(QUIT_URL, { reason: reason, grace: 8 });
  }

  window.addEventListener('pagehide', function () { sendQuit('pagehide'); });
  window.addEventListener('beforeunload', function () { sendQuit('beforeunload'); });

  // 3) 顶栏「退出」按钮：确认后主动收尾
  document.addEventListener('click', function (ev) {
    var btn = ev.target && ev.target.closest ? ev.target.closest('#btn-quit') : null;
    if (!btn) return;
    ev.preventDefault();
    var running = 0;
    try {
      var tasks = window.__FF_TASKS__ || [];
      running = tasks.length;
    } catch (e) { running = 0; }
    var tip = running > 0
      ? '还有 ' + running + ' 个任务在跑。\n退出会中断它们（状态会写盘，不会脏数据）。\n\n确定退出？'
      : '确定退出汇城群控AI版？\n（后台服务与手机通道会一并关闭）';
    if (!window.confirm(tip)) return;
    quiting = true;
    post(QUIT_URL, { reason: 'quit_button', grace: 1 });
    // 给后端一点时间响应，然后关掉这个页面
    setTimeout(function () {
      try { window.close(); } catch (e) { /* 浏览器可能不允许 */ }
      document.body.innerHTML =
        '<div style="font-family:system-ui;padding:40px;color:#ccc">' +
        '<h2>正在退出…</h2><p>后台服务与手机通道正在关闭，稍等几秒即可重新启动。</p></div>';
    }, 600);
  }, true);

  window.__ffQuit = sendQuit;
})();
