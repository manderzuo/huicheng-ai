"""抖音回复（任务化）：领中转站线索 → 搜抖音号 → 进主页 → 打开私信 → 填草稿，**不点发送**。

为什么要有这个模块：
  以前抖音回复是前端 `for` 循环逐条调 `/leads/extract`，没有任务 ID，
  所以执行记录里看不到、状态也不会变（2026-11-03 主人反馈）。
  这里把它做成和「小红书私信 / B站私信」一样的任务：有状态、有日志、可停止。

安全边界：
  · 不点发送键；草稿填好即停（douyin_extract 里写死）。
  · 每条都 real 写回中转站（record_extract），失败也记 error 不重试。
"""
from __future__ import annotations

from . import douyin_extract, lead_store
from .mobile_gateway import device_session

SKILL_ID = 'builtin:douyin-dm'
SKILL = {
    'id': SKILL_ID,
    'name': '抖音私信',
    'description': '领中转站线索，按抖音号搜主页并填私信草稿，停在发送键前，不发送。',
}
PLATFORM = 'douyin'


def run(task):
    from . import execution

    serial = task['serial']
    stop = task['_stop']
    lead = task.get('lead') or {}
    dm_text = str(lead.get('dm_template') or '').strip()
    limit = max(1, min(200, int(lead.get('reply_limit') or 10)))
    interval = max(0, min(600, int(lead.get('interval_seconds') or 0)))

    task['status'] = 'executing'
    execution.log(task, '开始抖音回复：上限 %d 条（只填草稿，不点发送）' % limit)
    # 2026-11-06：自动确保目标 App 在前台（主人要求，不能要求人工先切好）。
    # 不在前台就用 adb 拉起（约 0.1 秒判断 + 启动等待），绝不 force-stop。
    try:
        from .app_foreground import ensure_app_front as _ensure_front
        if not _ensure_front(serial, 'com.ss.android.ugc.aweme',
                             log=lambda m: execution.log(task, m)):
            execution.log(task, '自动启动 com.ss.android.ugc.aweme 后仍未到前台，继续尝试')
    except Exception as _exc:                      # noqa: BLE001
        execution.log(task, '自动启动检查异常（继续）：%s' % type(_exc).__name__)


    done = ok = fail = 0
    while done < limit and not stop.is_set():
        row = lead_store.claim(PLATFORM, serial,
                               require_nonempty=('commenter_douyin_id',),
                               skip_done_accounts=True, newest_first=True)
        if row is None:
            execution.log(task, '池里没有可回复的线索了')
            break
        done += 1
        who = row.get('comment_author') or '未知'
        uid = row.get('commenter_douyin_id') or ''
        execution.log(task, '[%d/%d] 领取：%s（抖音号 %s）' % (done, limit, who, uid))
        try:
            with device_session(serial, stop_event=stop):
                result = douyin_extract.run_extract(dict(row), serial,
                                                    dm_text=dm_text, stop=stop,
                                                    entry='search_account')
            res = result.get('extract_result')
            lead_store.record_extract(PLATFORM, row['id'], serial, res,
                                      result.get('evidence'), result.get('seconds', 0.0))
            if res == 'found':
                ok += 1
                execution.log(task, '  已填入草稿（停在发送键前）：%s' % who)
            else:
                fail += 1
                execution.log(task, '  未成功（%s）：%s' % (res, who))
        except Exception as exc:                       # noqa: BLE001
            fail += 1
            try:
                lead_store.record_extract(PLATFORM, row['id'], serial, 'error',
                                          {'failure': {'type': type(exc).__name__,
                                                       'detail': str(exc)[:300]}})
            except Exception:
                pass
            execution.log(task, '  出错：%s' % str(exc)[:120])
        if interval and done < limit and not stop.is_set():
            stop.wait(interval)

    task['status'] = 'stopped' if stop.is_set() else 'completed'
    task['end_reason'] = '已处理 %d 条：成功 %d、失败 %d' % (done, ok, fail)
    execution.log(task, '回复结束：成功 %d / 共 %d（失败 %d）' % (ok, done, fail))
    return True
