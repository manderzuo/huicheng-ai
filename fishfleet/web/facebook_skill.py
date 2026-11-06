"""Facebook 三件套 skill 入口：发布 / 采集 / 私信回复。

对齐项目里其它平台的写法（抖音、小红书、B站的 `run(task)`）：
    task['serial']  设备串号
    task['lead']    参数（title/description/keywords/reply_text…）
    task['_stop']   停止信号
    _log(task, msg)     写执行记录

三个入口都**不会自动发送/发布**：
  · 发布：填好内容、停在「發佈」前，等确认
  · 采集：只读，入中转站
  · 私信：填草稿、识别发送键，不点
"""
from __future__ import annotations

import time


def _log(task, msg):
    """延迟导入 execution：它依赖 fastapi，本机测试环境可能没装。"""
    try:
        from . import execution
        execution.log(task, msg)
    except Exception:                                    # noqa: BLE001
        print(msg)


def _serial(task) -> str:
    return task.get('serial') or (task.get('lead') or {}).get('serial') or ''


def _stop(task) -> bool:
    ev = task.get('_stop')
    try:
        return bool(ev.is_set())
    except Exception:
        return False


# ---------------------------------------------------------------- 发布
def run_publish(task):
    """发视频/图文到 Facebook：填内容 → 到预览页 → **停在「發佈」前等人工确认**。

    2026-11-06 修（主人实测）：原来到预览页只是把 status 设成 awaiting_approval，
    **没有设 task['pending']、也没有等确认事件**。而前端「确认」按钮的显示条件是
    `t.pending && t.status==='awaiting_approval'` —— 所以界面上一片空白，
    主人「确认键也没找到」，任务还自己 discard 掉草稿退出了。
    现在补齐三件事：
      ① 设 pending（界面据此渲染「确认」按钮）
      ② 会话保持不退出（等确认期间预览页还在）
      ③ 收到确认后回来点「發佈」并核验
    """
    import uuid
    from . import facebook_publish

    lead = task.get('lead') or {}
    # 文案优先读任务顶层字段（与抖音/B站一致），再回退 lead 内字段
    caption = (str(task.get('caption') or '').strip()
               or str(lead.get('description') or lead.get('caption') or '').strip())
    entry = task.get('album_index')
    if entry is None:
        entry = lead.get('album_index')
    serial = _serial(task)
    _log(task, 'Facebook 发布：文案 %d 字' % len(caption))

    stop = task.get('_stop')
    approval = task.get('_approval')

    def _wait_confirm(btn):
        """到预览页后调用：设 pending → 等人工确认 → 返回是否继续点击。"""
        if approval is None:
            # 没有确认事件通道时，保持"只看不点"的保守行为
            _log(task, '没有确认通道，保持不点击')
            return False
        approval.clear()
        task['pending'] = {
            'id': uuid.uuid4().hex,
            'action': {'type': 'publish', 'platform': 'facebook',
                       'caption': caption},
            'reason': 'Facebook 已到预览页并识别到「發佈」；确认后才会点击发布。',
            'observed_at': time.time(),
        }
        task['status'] = 'awaiting_approval'
        _log(task, '等待确认正式发布（预览页已就绪，草稿未丢弃）')
        while not (stop and stop.is_set()) and not approval.wait(.2):
            pass
        if stop and stop.is_set():
            _log(task, '收到停止指令，不发布')
            task['pending'] = None
            return False
        pend = task.get('pending')
        if not pend or time.time() - pend['observed_at'] > 300:
            _log(task, '发布确认已过期或已被取消，未提交')
            task['pending'] = None
            return False
        task['pending'] = None
        task['status'] = 'executing'
        _log(task, '已确认，开始点击「發佈」')
        return True

    try:
        rc = facebook_publish.main_with(serial=serial, body=caption,
                                        confirm=False, album_index=entry,
                                        log=lambda m: _log(task, m),
                                        wait_confirm=_wait_confirm)
    except Exception as exc:                              # noqa: BLE001
        _log(task, '发布失败：%s: %s' % (type(exc).__name__, exc))
        task['status'] = 'failed'
        return
    if rc == 0:
        _log(task, '本轮结束（已按确认结果处理，或未获确认而放弃）')
        if task.get('status') in (None, 'queued', 'executing'):
            task['status'] = 'completed'
    else:
        _log(task, '发布结果未知，需人工核验')
        task['status'] = 'needs_review'


# ---------------------------------------------------------------- 采集
def run_lead(task):
    """按关键词采集评论（含唯一 ID）→ 入中转站。只读，不发送。"""
    from . import facebook_collect_keyword

    lead = task.get('lead') or {}
    serial = _serial(task)
    keywords = lead.get('keywords') or []
    target = int(lead.get('target_comments') or lead.get('target') or lead.get('videos_per_keyword') or 20)
    per = int(lead.get('comments_per_video') or 8)
    _sort = (lead.get('sort_by') or lead.get('sort') or '默认排序')
    _days = int(lead.get('max_age_days') or 0)
    if not keywords:
        keywords = ['iPhone']
    _log(task, 'Facebook 采集：%d 个关键词，目标 %d 条（排序=%s，X天=%s，手机=%s）'
                 % (len(keywords), target, _sort, (_days or '不限'), serial))

    total = 0
    for kw in keywords:
        if _stop(task):
            _log(task, '收到停止指令，收尾退出')
            break
        _log(task, '  关键词：%s' % kw)
        try:
            n = facebook_collect_keyword.collect_for_skill(
                serial, kw, target, per, task=task)
        except Exception as exc:                          # noqa: BLE001
            _log(task, '  关键词 %s 失败：%s' % (kw, type(exc).__name__))
            continue
        total += n
        _log(task, '  本词入库 %d 条（累计 %d）' % (n, total))
    _log(task, '采集结束：累计入库 %d 条' % total)
    task['status'] = 'completed'


# ---------------------------------------------------------------- 私信
def run_dm(task):
    """领中转站线索 → 打开主页 → 進私信 → 按评论语种翻译 → 填草稿 → 识别发送键（不发送）。"""
    from . import facebook_dm_send

    task['status'] = 'executing'   # 否则界面一直显示"排队中"，状态与事实不符
    lead = task.get('lead') or {}
    serial = _serial(task)
    # 2026-11-06 修（主人实测：设 1 条，却一直在跑）：
    # 前端传的字段是 `reply_limit` —— 和抖音 / 小红书 / B站 / TikTok 完全一致。
    # 这里原来读的是 `max_steps` / `limit`，**两个字段前端都不传**，
    # 于是 `or 20` 永远生效：不管设几条，实际都跑 20 条。
    limit = int(lead.get('reply_limit') or lead.get('max_steps')
                or lead.get('limit') or 20)
    _log(task, 'Facebook 私信：最多处理 %d 条' % limit)
    try:
        stats = facebook_dm_send.run_for_skill(serial, limit, task=task)
    except Exception as exc:                              # noqa: BLE001
        _log(task, '私信失败：%s: %s' % (type(exc).__name__, exc))
        task['status'] = 'failed'
        return
    _log(task, '私信结束：尝试 %d，成功 %d，跳过 %d，失败 %d'
                 % (stats.get('tried', 0), stats.get('ok', 0),
                    stats.get('locked', 0), stats.get('fail', 0)))
    task['status'] = 'completed'


SKILLS = {
    'builtin:facebook-publish': run_publish,
    'builtin:facebook-lead': run_lead,
    'builtin:facebook-dm': run_dm,
}
