# -*- coding: utf-8 -*-
"""新机接入模拟测试（一键验证 + 自动修正）。

主人定的规矩：
  · 平时 20 台主板机同品牌同分辨率同系统，只查一台；
  · 新机（不同品牌/分辨率/系统）接入时才跑全流程模拟；
  · 只有这时候才调 LLM + 视觉（OCR 靠 VLM 直接读图，不另装引擎）确认坐标与界面；
  · 发现偏移就地修（写本机 dp 档案），修完重跑验证；
  · 发布模拟永远停在确认页（到 awaiting_approval 即判通过并主动停掉，绝不真发）。

对外只有两个函数：
  start_onboard(serial) -> oid（后台线程跑，不阻塞 HTTP）
  onboard_status(oid) / onboard_stop(oid)
报告落盘：reports/onboard_{devicekey}_{ts}.md
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid

_JOBS = {}
_JOBS_LOCK = threading.Lock()

PLATFORMS = [
    {'id': 'douyin', 'pkg': 'com.ss.android.ugc.aweme',
     'pub': 'builtin:douyin-publish', 'lead': 'builtin:douyin-lead'},
    {'id': 'xiaohongshu', 'pkg': 'com.xingin.xhs',
     'pub': 'builtin:xiaohongshu-publish', 'lead': 'builtin:xiaohongshu-lead'},
    {'id': 'bilibili', 'pkg': 'tv.danmaku.bili',
     'pub': 'builtin:bilibili-publish', 'lead': 'builtin:bilibili-lead'},
    {'id': 'tiktok', 'pkg': 'com.zhiliaoapp.musically',
     'pub': 'builtin:tiktok-publish', 'lead': 'builtin:tiktok-lead'},
    {'id': 'facebook', 'pkg': 'com.facebook.katana',
     'pub': 'builtin:facebook-publish', 'lead': 'builtin:facebook-lead'},
]

FINISHED = ('completed', 'stopped', 'failed', 'needs_review', 'interrupted')
CHILD_TIMEOUT = 600
REPLY_TIMEOUT = 360
SIM_PUBLISH = False   # 默认不跑发布模拟（防止误发）；显式开启才跑
POLL_EVERY = 3


def _adb():
    import os
    cands = [os.environ.get('ADB_PATH') or '',
             __import__('fishfleet.paths', fromlist=['adb_path']).adb_path(), 'adb']
    import shutil
    for c in cands:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return c
    return 'adb'


def _run(serial, *args, timeout=30):
    try:
        p = subprocess.run([_adb(), '-s', serial] + list(args),
                           capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='ignore')
        return (p.stdout or '') + (p.stderr or '')
    except Exception:
        return ''


def _job(oid):
    with _JOBS_LOCK:
        return _JOBS.get(oid)


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
LIVE_LOG = os.path.join(_ROOT, 'reports', 'onboard_live.log')


def _log(oid, msg):
    job = _job(oid)
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    if job is None:
        return
    job['log'].append(line)
    job['log'] = job['log'][-400:]
    if job.get('file_log'):
        try:
            with open(LIVE_LOG, 'a', encoding='utf-8') as f:
                f.write('%s %s\n' % (oid, line))
        except Exception:
            pass


def _stopped(oid):
    job = _job(oid)
    return bool(job and job['stop'].is_set())


def _installed_apps(serial):
    out = _run(serial, 'shell', 'pm', 'list', 'packages', timeout=30)
    pkgs = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('package:'):
            pkgs.add(line[len('package:'):].strip())
    return pkgs


def _app_version(serial, pkg):
    out = _run(serial, 'shell', 'dumpsys', 'package', pkg, timeout=20)
    for line in out.splitlines():
        s = line.strip()
        if s.startswith('versionName='):
            return s.split('=', 1)[1].strip()
    return ''


def _pick_mp4_asset():
    try:
        from . import workspace_assets as WA
        cands = []
        for aid, row in (WA.ASSETS or {}).items():
            fn = str(row.get('filename', ''))
            if fn.lower().endswith('.mp4'):
                cands.append((int(row.get('bytes') or 9e18), aid, row))
        if not cands:
            return None
        cands.sort()
        return cands[0][1]
    except Exception:
        return None


def _start_child(serial, oid, **kw):
    from .execution import StartRequest, start as _start, TASKS
    req = StartRequest(serial=serial,
                       quick_entry_id='builtin:onboard',
                       quick_entry_name='新机模拟测试',
                       quick_batch_id=oid, **kw)
    created = _start(req)
    cid = (created.get('id') if isinstance(created, dict) else None) or \
        (created.id if hasattr(created, 'id') else None)
    if not cid:
        # start 返回的是 public(task) 字典
        for t in list(TASKS.values()):
            if t.get('quick_batch_id') == oid and t.get('serial') == serial:
                cid = t['id']
                break
    return cid


def _wait_child(oid, cid, timeout, stop_states=('awaiting_approval',) + FINISHED):
    from .execution import TASKS
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _stopped(oid):
            return {'status': 'stopped', 'reason': '编排被手动停止'}
        t = TASKS.get(cid)
        if not t:
            return {'status': 'missing', 'reason': '子任务丢失'}
        st = t.get('status')
        if st in stop_states:
            return {'status': st,
                    'reason': t.get('failure_reason') or '',
                    'events': [e.get('message', '') for e in (t.get('events') or [])][-8:]}
        time.sleep(POLL_EVERY)
    return {'status': 'timeout', 'reason': '子任务 %ds 未结束' % timeout}


def _start_child_safe(serial, oid, **kw):
    """下发子任务；手机忙先清残留再试。名单里没有就强制刷新名单再试两次；
    最终失败抛清错并附现场快照（adb 名单/管理报名单到底有没有这台）。"""
    if _job(oid) is None and not str(oid).startswith('onboard_'):
        raise ValueError('内部参数顺序错误：第二个参数应为编排ID，实际 %r' % (oid,))
    from .execution import StartRequest, start as _start, TASKS  # noqa: F811
    import time as _t
    last = None
    for attempt in (1, 2, 3):
        try:
            cid = _start_child(serial, oid, **kw)
        except Exception as e:
            msg = str(e)
            last = e
            busy = '已有AI任务' in msg or 'busy' in msg.lower()
            missing = '404' in msg or '不存在' in msg
            if not busy and not missing:
                raise
            if busy:
                j = _job(oid)
                for c in list((j or {}).get('child_ids', [])):
                    _stop_child(c)
                _t.sleep(2)
            else:
                try:
                    from .server import get_manager
                    get_manager().refresh()
                except Exception:
                    pass
                _t.sleep(2 if attempt == 1 else 4)
                _log(oid, '设备名单里找不到手机已强制刷新名单再试（第%d次）' % attempt)
            continue
        j = _job(oid)
        if j is not None and cid:
            j.setdefault('child_ids', []).append(cid)
        return cid
    snap = _snapshot_presence(serial)
    _log(oid, '下发仍失败放弃：%s；现场快照：%s' % (last, snap))
    raise last


def _snapshot_presence(serial):
    """只读快照：adb 名单 / 管理报名单里到底有没有这台。"""
    try:
        import subprocess as _sp
        p = _sp.run([_adb(), 'devices'], capture_output=True, text=True, timeout=15)
        adb_have = [l.split()[0] for l in (p.stdout or '').splitlines()
                    if l.strip().endswith('device') and not l.startswith('List ')]
    except Exception as e:
        adb_have = ['ERR:%s' % e]
    try:
        from .server import get_manager
        mgr = get_manager()
        alld = mgr.all()
        present = serial in [d.serial for d in alld]
        total = len(alld)
    except Exception as e:
        total, present = '?', 'ERR:%s' % e
    if isinstance(adb_have, list):
        n_adb, in_adb = len(adb_have), (serial in adb_have)
    else:
        n_adb, in_adb = '?', adb_have
    return 'adb名单%s台含本机=%s；管理报名单%s台含本机=%s' % (n_adb, in_adb, total, present)


def _code_version():
    """代码指纹：整个 onboard.py 内容的 md5 前 8 位，日志里一眼看出跑的是哪版。"""
    try:
        import hashlib
        with open(__file__, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()[:8]
    except Exception:
        return 'unknown'


def _stop_child(cid):
    try:
        from .execution import stop as _stop
        _stop(cid)
    except Exception:
        pass


def _wait_child_watched(oid, serial, cid, platform, timeout, stop_states):
    """等子任务 + AI哨位同行：判死刑哨位会提前停单，不让它烧满超时。

    只用于采集模拟；发布/回复不动。哨位只读事件+一次视觉确认，
    停单走本模块自己的 _stop_child。
    """
    import threading as _th
    try:
        from . import ai_director as _AD
    except Exception:
        return _wait_child(oid, cid, timeout, stop_states=stop_states)
    _stop = _th.Event()
    _wd = _th.Thread(target=_AD.watch_task,
                     args=(oid, serial, cid, platform,
                           lambda m: _log(oid, m), _stop, _stop_child),
                     daemon=True, name='onboard-watch')
    _wd.start()
    try:
        return _wait_child(oid, cid, timeout, stop_states=stop_states)
    finally:
        _stop.set()
        _wd.join(timeout=10)


def _settle_child(oid, cid, r):
    """子任务没停在可收尾状态就地停掉（超时/丢失时），避免残留占机。"""
    r = dict(r)
    if r.get('status') in FINISHED or r.get('status') == 'awaiting_approval':
        return r
    _log(oid, '子任务未结束（%s）先停掉防止占机' % r.get('status'))
    _stop_child(cid)
    r['settled'] = True
    return r


def _classify_blockers(blob):
    """纯函数：按关键词把卡点分类。返回 (retry, why)。
    登录墙/更新弹窗不重试（转人工）；网络抖动、权限弹窗、界面差异给一次重试机会。"""
    b = blob or ''
    if any(k in b for k in ('一键登录', '手机号登录', '验证码登录', '账号登录', '登录即代表')):
        return False, '疑似登录墙（需人工登录）'
    if any(k in b for k in ('发现新版本', '立即更新', '去更新', '版本更新', '升级')):
        return False, '疑似应用更新弹窗（需人工确认）'
    if not b.strip():
        return True, '读不到界面树（通道抖动可能）'
    return True, '疑似界面差异/时机问题'


def _diagnose_block(oid, serial, platform, kind, reason):
    """失败诊断：只读 dump + 分类，不点不动。返回 {retry, why}。

    2026-11-05：通道抖动与界面差异分开记。tool_error/Mobile MCP 是工具侧
    偶发（41号曾因此被误判成界面差异），单列一类，重跑的是当前步。
    """
    if reason and ('tool_error' in reason or 'Mobile MCP' in reason):
        _log(oid, '诊断%s·%s：通道抖动（%s）' % (platform, kind, reason[:120]))
        return {'retry': True, 'why': '通道抖动（工具侧偶发），重跑当前步'}
    try:
        from . import ui_selfcheck as SC
        nodes = SC.dump_nodes(serial) or []
    except Exception as e:
        _log(oid, '诊断读树失败：%s' % str(e)[:80])
        return {'retry': True, 'why': '诊断时读树失败（通道抖动可能）'}
    blob = ' '.join((n.get('text', '') + ' ' + n.get('desc', '')) for n in nodes)
    retry, why = _classify_blockers(blob)
    _log(oid, '诊断%s·%s：%s' % (platform, kind, why))
    return {'retry': retry, 'why': why}


def _config_problem(reason):
    r = reason or ''
    return any(k in r for k in ('标题', '描述', '素材', 'asset', '关键词', '未安装', '参数'))


def _maybe_retry_pub(oid, serial, p, asset_id, cid, r):
    r = dict(r)
    if r.get('status') == 'awaiting_approval' or _stopped(oid):
        return r, cid
    if _config_problem(r.get('reason', '')):
        return r, cid
    try:
        from . import ui_resolve as _URd
        _dd = _URd.dismiss(serial, log=lambda m: _log(oid, m), force=True)
        if _dd.get('how') in ('back', 'close', 'vision', 'grant'):
            _log(oid, '失败前先清了弹窗：%s' % _dd.get('how'))
    except Exception:
        pass
    d = _diagnose_block(oid, serial, p['id'], 'publish', r.get('reason', ''))
    try:
        from . import ui_resolve as _UR
        _desc = _UR.describe(serial)
        if _desc:
            _log(oid, '兜底分析（视觉）：%s' % _desc)
            r['page_desc'] = _desc
    except Exception:
        pass
    r = _settle_child(oid, cid, r)
    if not d.get('retry'):
        r['reason'] = (r.get('reason') or r.get('status')) + '［诊断：%s］' % d['why']
        return r, cid
    _log(oid, '发布模拟 %s 诊断可重试（%s），重跑一次' % (p['id'], d['why']))
    cid2 = _start_child_safe(
        serial, oid, goal='新机模拟测试·%s发布（停确认页·重试）' % p['id'],
        script_id=p['pub'], asset_id=asset_id,
        title='自适应验证', caption='新机模拟测试停确认页',
        tags=[], clear_existing=False)
    r2 = dict(_wait_child(oid, cid2, CHILD_TIMEOUT))
    r2 = _settle_child(oid, cid2, r2)
    r2['retried'] = True
    r2['reason'] = (r2.get('reason') or r2.get('status')) + '［已重试1次：%s］' % d['why']
    return r2, cid2


def _maybe_retry_collect(oid, serial, p, lead, cid, r):
    r = dict(r)
    if r.get('status') == 'completed' or _stopped(oid):
        return r, cid
    if _config_problem(r.get('reason', '')):
        return r, cid
    try:
        from . import ui_resolve as _URd
        _dd = _URd.dismiss(serial, log=lambda m: _log(oid, m), force=True)
        if _dd.get('how') in ('back', 'close', 'vision', 'grant'):
            _log(oid, '失败前先清了弹窗：%s' % _dd.get('how'))
    except Exception:
        pass
    d = _diagnose_block(oid, serial, p['id'], 'collect', r.get('reason', ''))
    try:
        from . import ui_resolve as _UR
        _desc = _UR.describe(serial)
        if _desc:
            _log(oid, '兜底分析（视觉）：%s' % _desc)
            r['page_desc'] = _desc
    except Exception:
        pass
    r = _settle_child(oid, cid, r)
    if not d.get('retry'):
        r['reason'] = (r.get('reason') or r.get('status')) + '［诊断：%s］' % d['why']
        return r, cid
    # AI 指挥恢复（2026-11-05 接入）：偏离出现后不再盲重跑，先由 LLM 亲自
    # 把手机恢复到可重采状态（只导航清障，不碰采集逻辑，不写文件）。
    # 核验通过才重派老技能；不通过直接返回失败（盲重跑已被证明只是复现失败）。
    _log(oid, 'AI恢复 %s …（LLM亲自清障，预算12步/10次/240秒）' % p['id'])
    try:
        from . import ai_director as _AD
        _dr = _AD.guided_recovery(oid, serial, p['id'], lambda m: _log(oid, m))
    except Exception as _e:                          # noqa: BLE001
        _dr = {'ok': False, 'why': 'AI模块异常：%s' % type(_e).__name__,
               'steps': [], 'patches': [], 'llm_calls': 0}
    r['ai_recovery'] = {'ok': bool(_dr.get('ok')), 'why': _dr.get('why'),
                        'llm_calls': _dr.get('llm_calls', 0),
                        'patch_count': len(_dr.get('patches') or [])}
    for _pp in (_dr.get('patches') or []):
        _log(oid, 'AI补丁提议（待拍板不写文件）：%s %s 起因 %s'
             % (_pp.get('file'), _pp.get('lines'), (_pp.get('why') or '')[:80]))
    if not _dr.get('ok'):
        r['reason'] = ((r.get('reason') or r.get('status'))
                       + '［AI恢复失败：%s］' % _dr.get('why'))
        return r, cid
    _log(oid, '采集模拟 %s 诊断可重试（%s），AI已清障，重跑一次' % (p['id'], d['why']))
    cid2 = _start_child_safe(
        serial, oid, goal='新机模拟测试·%s采集（重试）' % p['id'],
        script_id=p['lead'], lead=lead)
    r2 = dict(_wait_child_watched(oid, serial, cid2, p['id'], CHILD_TIMEOUT,
                                  stop_states=FINISHED))
    r2 = _settle_child(oid, cid2, r2)
    r2['retried'] = True
    r2['reason'] = (r2.get('reason') or r2.get('status')) + '［已重试1次：%s］' % d['why']
    return r2, cid2


def _cleanup_test_leads(serial: str, since_ts: float):
    """删掉本次编排产生的测试线索（只删该手机、该时间之后、仍是待办的；
    已被认领/处理的不碰如有残留会如实报告）。"""
    cleaned, kept = {}, []
    try:
        from . import lead_store as L
        conn = L._db()
        with conn:
            for p in L.known_platforms():
                try:
                    name = L.ensure(p)
                except Exception:
                    continue
                try:
                    cur = conn.execute(
                        'SELECT COUNT(*) FROM %s WHERE collected_serial=? '
                        'AND collected_at>=? AND status IN (?,?,?,?)' % name,
                        (serial, since_ts, L.PENDING, L.DRAFTED,
                         L.FAILED, L.SKIPPED))
                    n = cur.fetchone()[0]
                except Exception:
                    continue
                if not n:
                    continue
                conn.execute(
                    'DELETE FROM %s WHERE collected_serial=? '
                    'AND collected_at>=? AND status IN (?,?,?,?)' % name,
                    (serial, since_ts, L.PENDING, L.DRAFTED,
                     L.FAILED, L.SKIPPED))
                cleaned[p] = n
    except Exception as e:
        kept.append('清理失败：%s' % str(e)[:100])
    return cleaned, kept


def _describe_key(key, t):
    exp = t.get('expect') or {}
    bits = exp.get('text_in') or []
    base = t.get('note') or key
    if bits:
        base += '（找%s）' % '、'.join(str(b) for b in bits[:3])
    return base


def _vision_repair(oid, serial, items):
    """对自检 miss 的键逐个视觉定位→门禁→入库。返回 (修好数, 需人工数)。"""
    from . import ui_probe_targets as U
    from . import vision_locate as V
    fixed, human = [], []
    for it in items:
        if _stopped(oid):
            break
        if it.get('result') != 'miss':
            continue
        key = it['key']
        t = U.TARGETS.get(key, {})
        desc = _describe_key(key, t)
        _log(oid, '视觉定位 %s …' % key)
        try:
            found = V.locate(serial, desc, page_hint=str(t.get('page', '')))
            verdict = V.validate_and_convert(serial, key, found)
        except Exception as e:
            human.append({'key': key, 'reason': '视觉失败：%s' % str(e)[:80]})
            continue
        if verdict.get('ok'):
            from .ui_probe_targets import device_key, load_profile, save_profile, screen_profile
            geo = screen_profile(serial)
            dk = device_key(geo)
            prof = load_profile(dk) or {}
            offs = dict(prof.get('dp_offsets') or {})
            offs[key] = verdict['dp']
            prof['dp_offsets'] = offs
            prof['device_key'] = dk
            prof['serial'] = serial
            save_profile(dk, prof)
            fixed.append({'key': key, 'dp': verdict['dp'],
                          'drift': verdict.get('drift'),
                          'confidence': verdict.get('confidence')})
            _log(oid, '已修正 %s -> dp %s（漂移 %s 置信度 %s）'
                 % (key, verdict['dp'], verdict.get('drift'),
                    verdict.get('confidence')))
        else:
            human.append({'key': key, 'reason': verdict.get('reason', '未通过门禁'),
                          'detail': {k: v for k, v in verdict.items()
                                     if k in ('dp_candidate', 'confidence', 'found')}})
            _log(oid, '转人工 %s：%s' % (key, verdict.get('reason', '')))
    return fixed, human


def _worker(oid, serial):
    job = _job(oid)
    if job is None:
        return
    report = {'serial': serial, 'steps': [], 'repairs': [], 'human': [],
              'sims': [], 'verdict': 'unknown', 'cleaned': {}, 'clean_notes': []}
    started_ts = time.time()
    try:
        import os as _osb
        _log(oid, '编排代码版本 onboard.py mtime=%s code=%s' % (
            _osb.path.getmtime(__file__), _code_version()))
    except Exception:
        pass
    try:
        import os as _osb
    except Exception:
        pass
    try:
        from . import ui_probe_targets as U
        from . import ui_selfcheck as SC

        geo = U.screen_profile(serial)
        dk = U.device_key(geo)
        _log(oid, '几何 %dx%d@%d → dp %s（%s）' %
             (geo['width'], geo['height'], geo['dpi'],
              [geo['dp_w'], geo['dp_h']], geo['dp_family']))
        report['screen'] = geo
        report['device_key'] = dk
        report['sanity'] = U.sanity_check(geo)
        for w in (report['sanity'].get('warnings') or []):
            _log(oid, '注意：' + w)

        pkgs = _installed_apps(serial)
        plats = [p for p in PLATFORMS if p['pkg'] in pkgs]
        _want = (job or {}).get('platforms') or []
        if _want:
            _known = {p['id'] for p in plats}
            _miss = [w for w in _want if w not in _known]
            if _miss:
                _log(oid, '指定平台未安装或不可测，已跳过：' + '、'.join(_miss))
            plats = [p for p in plats if p['id'] in _want]
        report['apps'] = [{'platform': p['id'], 'pkg': p['pkg'],
                           'version': _app_version(serial, p['pkg'])}
                          for p in plats]
        missing = [p['id'] for p in PLATFORMS if p['pkg'] not in pkgs]
        if missing:
            _log(oid, '未安装跳过：' + '、'.join(missing))
            report['missing_apps'] = missing

        # ---- 自检（硬导航逐个应用，保证覆盖）----
        try:
            rep = SC.selfcheck_all_apps(serial, apply_fix=True, hard_nav=True)
        except Exception as e:
            rep = {'ok': False, 'summary': {}, 'items': [],
                   'notes': ['自检异常：%s' % e]}
        report['selfcheck'] = rep.get('summary', {})
        report['selfcheck_notes'] = rep.get('notes', [])
        _log(oid, '自检 %s' % rep.get('summary', {}))
        fixed, human = _vision_repair(oid, serial, rep.get('items', []))
        report['repairs'].extend(fixed)
        if report['repairs']:
            report['pending_promotion'] = {
                x['key']: x['dp'] for x in report['repairs']
                if x.get('dp')}
        report['human'].extend(human)
        prof_changed = bool(fixed)

        # ---- 模拟子任务：发布（到确认页即停）+ 采集 ----
        asset_id = _pick_mp4_asset()
        if not asset_id:
            _log(oid, '素材库无 MP4，发布模拟跳过')
            report['steps'].append({'step': 'publish', 'status': 'skipped',
                                    'reason': '无可用 MP4 素材'})
        _last_issue = ''
        _phase_total = 1 + 2 * len(plats) + 1   # 自检 + 每平台采/回 + 收尾
        _phase_no = 1                            # 自检已过
        for p in plats:
            if _stopped(oid):
                break
            try:
                _done = [s['kind'] for s in report['sims']
                         if s.get('platform') == p['id']]
                _fails = sum(1 for s in report['sims']
                             if s.get('status') == 'fail')
                _pmap = [{'phase': '自检', 'state': 'done'}]
                for _pp in plats:
                    for _kk in ('collect', 'reply'):
                        _hit = [s for s in report['sims']
                                if s.get('platform') == _pp['id']
                                and s.get('kind') == _kk]
                        _pmap.append({'phase': '%s%s' % (_pp['id'], _kk),
                                      'state': (_hit[-1]['status']
                                                if _hit else 'pending')})
                _pmap.append({'phase': '结论清理', 'state': 'pending'})
                try:
                    from . import ai_director as _ADG
                    _plan = _ADG.plan_step(
                        p['id'], _done, _last_issue, serial,
                        {'plan': _pmap, 'failed': _fails,
                         'milestone': '第%d步/共%d步' % (_phase_no + 1, _phase_total)})
                except Exception:
                    _plan = {'next': 'collect', 'note': '计划异常走默认', 'expect': ''}
                _log(oid, 'AI定步 %s[%s]：%s（%s）'
                     % (p['id'], '第%d步/共%d步' % (_phase_no + 1, _phase_total),
                        _plan.get('next'), _plan.get('note')))
                _last_issue = ''
                _pn = _plan.get('next')
                if _pn == 'finish_early':
                    _log(oid, 'AI建议提前收尾，停止后继平台，直接结论清理')
                    break
                if _pn in ('skip_platform', 'next_platform'):
                    _log(oid, 'AI建议跳过 %s 本轮' % p['id'])
                    report['sims'].append({'platform': p['id'], 'kind': 'collect',
                                            'status': 'skipped',
                                            'reason': 'AI建议跳过：%s' % _plan.get('note')})
                    _phase_no += 2
                    continue
                if _pn == 'reply' and 'collect' not in _done:
                    _log(oid, 'AI想先回复但本轮采集未做，按依赖先采集')
                # 发布模拟
                if asset_id and (job or {}).get('sim_publish', SIM_PUBLISH):
                    _log(oid, '发布模拟 %s …' % p['id'])
                    cid = _start_child_safe(
                        serial, oid, goal='新机模拟测试·%s发布（停确认页）' % p['id'],
                        script_id=p['pub'], asset_id=asset_id,
                        title='自适应验证', caption='新机模拟测试停确认页',
                        tags=[], clear_existing=False)
                    r = _wait_child(oid, cid, CHILD_TIMEOUT)
                    r, cid = _maybe_retry_pub(oid, serial, p, asset_id, cid, r)
                    if r['status'] == 'awaiting_approval':
                        _stop_child(cid)
                        _log(oid, '发布模拟 %s 到达确认页（已主动停掉未真发）' % p['id'])
                        report['sims'].append({'platform': p['id'], 'kind': 'publish',
                                                'status': 'pass', 'detail': 'reached_confirm'})
                    else:
                        _log(oid, '发布模拟 %s 未达确认页：%s' %
                             (p['id'], r.get('reason', r['status'])))
                        report['sims'].append({'platform': p['id'], 'kind': 'publish',
                                                'status': 'fail',
                                                'reason': r.get('reason', r['status']),
                                                'events': r.get('events', [])})
                # 采集模拟（只读小量）
                _log(oid, '采集模拟 %s …' % p['id'])
                from .execution import LeadOptions
                lead = LeadOptions(keywords=['测试'], videos_per_keyword=1,
                                    comments_per_video=3, target_comments=3,
                                    collect_author_id=True,
                                    collect_commenter_id=True,
                                    pace_factor=0.6,
                                    platform=p['id'])
                cid = _start_child_safe(
                    serial, oid, goal='新机模拟测试·%s采集' % p['id'],
                    script_id=p['lead'], lead=lead)
                r = _wait_child_watched(oid, serial, cid, p['id'], CHILD_TIMEOUT,
                                        stop_states=FINISHED)
                r, cid = _maybe_retry_collect(oid, serial, p, lead, cid, r)
                if r['status'] == 'completed':
                    _log(oid, '采集模拟 %s 完成' % p['id'])
                    report['sims'].append({'platform': p['id'], 'kind': 'collect',
                                            'status': 'pass'})
                else:
                    _log(oid, '采集模拟 %s 未完成：%s' %
                         (p['id'], r.get('reason', r['status'])))
                    report['sims'].append({'platform': p['id'], 'kind': 'collect',
                                            'status': 'fail',
                                            'reason': r.get('reason', r['status']),
                                            'events': r.get('events', [])})
                    _last_issue = str(r.get('reason', r['status']))[:120]
                try:
                    from . import ai_director as _ADV
                    from .execution import TASKS as _TSK
                    _ct = _TSK.get(cid) or {}
                    _st = _ct.get('lead_stats') or _ct.get('stats') or {}
                    _nl = _st.get('leads')
                    if _nl is None:
                        _nl = len(_ct.get('leads') or [])
                    _ok, _iss = _ADV.verify_step(
                        p['id'], 'collect',
                        {'status': 'completed' if r['status'] == 'completed' else 'fail',
                         'leads': _nl, 'detail': str(r.get('reason', ''))[:120]})
                except Exception:
                    _ok, _iss = (r['status'] == 'completed'), ''
                _log(oid, 'AI核验 %s采集：%s%s'
                     % (p['id'], '通过' if _ok else '偏离', '（%s）' % _iss if _iss else ''))
                if not _ok and not _last_issue:
                    _last_issue = _iss

                _log(oid, '模拟回复 %s …' % p['id'])
                _rsim = _reply_sim(oid, serial, p, started_ts)
                report['sims'].append(_rsim)
                if str(_rsim.get('status')) != 'skipped':
                    try:
                        _ok2, _iss2 = _ADV.verify_step(
                            p['id'], 'reply',
                            {'status': str(_rsim.get('status')),
                             'detail': str(_rsim.get('reason') or _rsim.get('detail') or '')[:120]})
                    except Exception:
                        _ok2, _iss2 = (_rsim.get('status') == 'pass'), ''
                    _log(oid, 'AI核验 %s回复：%s%s'
                         % (p['id'], '通过' if _ok2 else '偏离',
                            '（%s）' % _iss2 if _iss2 else ''))
                    if not _ok2 and not _last_issue:
                        _last_issue = _iss2 or str(_rsim.get('reason', ''))[:120]
                _phase_no += 2
            except Exception as e:
                _log(oid, '本轮 %s 异常跳过不阻塞后继平台' % p['id'])
                report['sims'].append({'platform': p['id'], 'kind': 'dispatch',
                                        'status': 'fail',
                                        'reason': '%s: %s' % (type(e).__name__, str(e)[:200])})
                continue
        # ---- 结论 ----
        fails = [s for s in report['sims'] if s['status'] == 'fail']
        if _stopped(oid):
            report['verdict'] = 'stopped'
        elif fails or report['human']:
            report['verdict'] = 'needs_review'
        else:
            report['verdict'] = 'pass'
        _log(oid, '结论：%s（失败 %d，需人工 %d）' %
             (report['verdict'], len(fails), len(report['human'])))
    except Exception as e:
        report['verdict'] = 'error'
        report['error'] = '%s: %s' % (type(e).__name__, str(e)[:200])
        _log(oid, '编排异常：%s' % report['error'])
    finally:
        # 收尾必做：删掉本次采集模拟写入共享中转站的测试线索
        # （只删本机、本次、仍待办的；已被认领的不碰如实报告）
        try:
            cleaned, notes = _cleanup_test_leads(serial, started_ts)
            report['cleaned'] = cleaned
            report['clean_notes'] = notes
            total = sum(cleaned.values())
            _log(oid, '已清理测试线索 %d 条 %s' % (
                total, dict(cleaned) if cleaned else '（无新增待办残留）'))
            for n in notes:
                _log(oid, n)
        except Exception as e:
            report['clean_notes'] = ['清理异常：%s' % str(e)[:100]]
        job = _job(oid)
        if job is not None:
            job['report'] = report
            job['status'] = 'done' if report.get('verdict') != 'stopped' else 'stopped'
            try:
                import os as _os
                from . import ui_probe_targets as U
                dk = report.get('device_key') or 'unknown'
                safe = ''.join(c if (c.isalnum() or c in '-_') else '_' for c in dk)
                path = _os.path.join(_os.path.abspath(_os.path.join(
                    _os.path.dirname(__file__), '..', '..', 'reports')),
                    'onboard_%s_%d.md' % (safe, int(time.time())))
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(_render_md(oid, report))
                job['report_path'] = path
            except Exception as e:
                _log(oid, '报告落盘失败：%s' % e)


def _next_steps(r):
    """结论直说：要进行XX测试；自动测试结果是YY。"""
    steps = []
    for s in r.get('sims', []):
        if s.get('status') == 'pass':
            continue
        p, k = s.get('platform'), s.get('kind')
        auto = s.get('reason') or s.get('detail') or s.get('status')
        if k == 'dispatch':
            steps.append('【%s】要进行手动下发测试：在群控手动选中该手机下发一条同类任务看能否开始；自动测试结果：下发失败（%s）。' % (p, auto))
        elif k == 'publish':
            steps.append('【%s发布】要进行手动发布测试：在该手机手动走一遍发布流程到确认页（只核对不点发布），记录卡在哪一步；自动测试结果：%s。' % (p, s.get('status')))
        elif k == 'collect':
            steps.append('【%s采集】要进行手动采集测试：手动搜一个关键词进结果页翻两屏，确认列表与评论可读；自动测试结果：%s。' % (p, s.get('status')))
        else:
            steps.append('【%s·%s】要进行手动复核：在手机上按上述现象复核一次；自动测试结果：%s。' % (p, k, auto))
    for h in r.get('human', []):
        steps.append('【%s】要进行人工核对测试：打开对应页面肉眼核对坐标，把实测点位告诉我；自动测试结论：%s。' % (h.get('key'), h.get('reason')))
    if r.get('verdict') == 'error':
        steps.append('编排异常中断（%s）。要进行重跑测试：重跑一次新机模拟测试看是否复现。' % (r.get('error') or ''))
    return steps


def _render_md(oid, r):
    L = ['# 新机模拟测试报告', '',
         '- 手机：`%s`（%s）' % (r.get('serial'), r.get('device_key')),
         '- 结论：**%s**' % r.get('verdict', '?')]
    sc = r.get('screen') or {}
    L.append('- 几何：%sx%s@%s → dp %s（%s）' % (
        sc.get('width'), sc.get('height'), sc.get('dpi'),
        [sc.get('dp_w'), sc.get('dp_h')], sc.get('dp_family')))
    if r.get('apps'):
        L += ['', '## 应用', '']
        for a in r['apps']:
            L.append('- %s `%s` 版本 %s' % (a['platform'], a['pkg'],
                                           a.get('version') or '未知'))
    if r.get('missing_apps'):
        L.append('- 未安装跳过：' + '、'.join(r['missing_apps']))
    if r.get('selfcheck') is not None:
        L += ['', '## 自检', '', '`%s`' % r['selfcheck']]
    for n in r.get('selfcheck_notes', []):
        L.append('- 自检备注：%s' % n)
    if r.get('repairs'):
        L += ['', '## 自动修正', '']
        for x in r['repairs']:
            L.append('- %s → dp %s（漂移 %s，置信度 %s）' % (
                x['key'], x['dp'], x.get('drift'), x.get('confidence')))
    if r.get('pending_promotion'):
        L += ['', '## 待拍板：是否覆盖到其它手机', '']
        for k, v in r['pending_promotion'].items():
            L.append('- %s → dp %s' % (k, v))
        L.append('- 当前选择：%s' % ('已覆盖全部手机'
                                    if r.get('promoted') == 'all' else
                                    ('仅本机' if r.get('promoted') else '待选择')))
    if r.get('human'):
        L += ['', '## 需人工', '']
        for x in r['human']:
            L.append('- %s：%s' % (x.get('key'), x.get('reason')))
    if r.get('sims'):
        L += ['', '## 模拟任务', '']
        for s in r['sims']:
            L.append('- %s·%s：%s %s' % (s['platform'], s['kind'],
                                         s['status'], s.get('reason') or s.get('detail') or ''))
            for ev in s.get('events', [])[-4:]:
                L.append('  - %s' % ev)
    if r.get('error'):
        L += ['', '## 编排异常', '', r['error']]
    if r.get('cleaned') or r.get('clean_notes'):
        L += ['', '## 测试线索清理（不污染中转站）', '']
        for p, n in (r.get('cleaned') or {}).items():
            L.append('- %s：删 %d 条待办测试线索' % (p, n))
        for n in r.get('clean_notes', []):
            L.append('- %s' % n)
    ns = _next_steps(r)
    if ns:
        L += ['', '## 下一步（人工验证）', '']
        for _i, _t in enumerate(ns, 1):
            L.append('%d. %s' % (_i, _t))
    L += ['', '_oid %s_' % oid]
    return '\n'.join(L)


def start_onboard(serial: str, platforms=None):
    from .server import get_manager
    if not get_manager().get(serial):
        raise ValueError('设备不存在或不在线')
    oid = 'onboard_' + uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        for o, j in list(_JOBS.items()):
            if j.get('serial') == serial and j.get('status') == 'running':
                raise ValueError('该手机已有进行中的模拟测试')
        _JOBS[oid] = {'serial': serial, 'status': 'running',
        'platforms': [str(x).strip() for x in (platforms or []) if str(x).strip()],
                      'stop': threading.Event(), 'log': [],
                      'report': None, 'report_path': '',
                      'started_at': time.time(), 'file_log': True}
    th = threading.Thread(target=_worker, args=(oid, serial),
                          name='onboard-' + serial, daemon=True)
    th.start()
    return oid


def _pick_test_lead(serial, since_ts, platform='douyin'):
    """从中转站随机挑一条「本次采集写入、仍是待办」的线索。"""
    from . import lead_store as L
    try:
        conn = L._db()
        name = L.ensure(platform)
        cur = conn.execute(
            'SELECT * FROM %s WHERE collected_serial=? AND collected_at>=? '
            'AND status=? ORDER BY RANDOM() LIMIT 1' % name,
            (serial, since_ts, L.PENDING))
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _claim_row(platform, lead_id, serial):
    """把选中的线索标成已认领（record_extract 要求该状态）。"""
    from . import lead_store as L
    try:
        conn = L._db()
        name = L.ensure(platform)
        with conn:
            cur = conn.execute(
                'UPDATE %s SET status=?, claimed_by=?, claimed_at=?, '
                'attempts=COALESCE(attempts,0)+1 WHERE id=? AND status=?' % name,
                (L.CLAIMED, serial, time.time(), lead_id, L.PENDING))
            return cur.rowcount == 1
    except Exception:
        return False


def _reply_sim_bili(oid, serial, p, since_ts):
    """B站模拟回复：定点打开该评论者的私信会话、填草稿，停在发送键前。"""
    from . import bilibili_dm as BD
    from . import lead_store as L
    row = _pick_test_lead(serial, since_ts, 'bilibili')
    if not row:
        return {'platform': 'bilibili', 'kind': 'reply', 'status': 'fail',
                'reason': '中转站里没有本次采集写入的待办线索'}
    uid = ''
    for key in ('commenter_douyin_id', 'author_uid', 'commenter_uid'):
        uid = str(row.get(key) or '').strip()
        if uid:
            break
    who = str(row.get('comment_author') or '')[:20]
    if not uid:
        return {'platform': 'bilibili', 'kind': 'reply', 'status': 'fail',
                'reason': '该线索没有可定位的UID（@%s）' % who}
    _log(oid, '模拟回复：中转站随机选中 @%s（UID %s），开始打开私信' % (who, uid))
    if not _claim_row('bilibili', row['id'], serial):
        return {'platform': 'bilibili', 'kind': 'reply', 'status': 'fail',
                'reason': '线索认领失败'}
    text = '新机模拟测试：仅生成草稿，不发送'
    try:
        BD.open_dm(serial, uid)
        BD.fill(serial, text)
    except Exception as exc:  # noqa: BLE001
        L.record_extract('bilibili', row['id'], serial, 'error',
                         {'failure': {'detail': str(exc)[:200]}})
        return {'platform': 'bilibili', 'kind': 'reply', 'status': 'fail',
                'reason': '%s: %s' % (type(exc).__name__, str(exc)[:150])}
    L.record_extract('bilibili', row['id'], serial, 'ok', {'detail': 'draft_filled'})
    _log(oid, '模拟回复 @%s：草稿已填，停在发送键前，未发送' % who)
    return {'platform': 'bilibili', 'kind': 'reply', 'status': 'pass',
            'person': who, 'detail': '草稿已填，停在发送键前'}


def _relaunch_app(serial, pkg, oid=''):
    """测试专用：force-stop 目标App 后立刻拉回前台。

    与 B 端 deeplink.open_profile(force_stop=True) 同一套行为：
    杀前台会让任务栈里的下一个应用顶上来（实测会闪一下 FB），
    所以必须**立刻** monkey 拉起目标 App 再走直达，否则回来的是 FB。
    这里在测试文件内复刻，不改 B 端任何代码（主人定的：测试期禁止覆盖B端）。
    """
    try:
        from .deeplink import adb as _dl_adb
        _dl_adb(serial, 'shell', 'am', 'force-stop', pkg)
        time.sleep(0.8)
        _dl_adb(serial, 'shell', 'monkey', '-p', pkg, '-c',
                'android.intent.category.LAUNCHER', '1')
        time.sleep(2.2)
        if oid:
            _log(oid, '已重启 %s 并拉回前台（杀进程后防顶层应用抢占）' % pkg)
        return True
    except Exception as exc:                       # noqa: BLE001
        if oid:
            _log(oid, '重启 %s 失败（继续原流程）：%s' % (pkg, type(exc).__name__))
        return False


def _reply_sim_xhs(oid, serial, p, since_ts):
    """小红书模拟回复：中转站领一条 → 定位开会话 → 填草稿，停发。

    调的是 B 端固化函数（open_chat/draft_message，直达优先 user_id），
    只到 drafted，绝不点发送。中转站键是 redbook（表 leads_redbook），
    联系对象字段 author_red_id，溯源字段 commenter_user_id。

    2026-11-05：回复前先 force-stop 小红书再拉回（B端已修的同款行为），
    避免残留页面/被下个任务栈应用（FB）抢占导致直达落错地方。
    """
    from . import xiaohongshu_lead as XL
    from . import lead_store as L
    row = _pick_test_lead(serial, since_ts, 'redbook')
    if not row:
        return {'platform': 'xiaohongshu', 'kind': 'reply', 'status': 'fail',
                'reason': '中转站里没有本次采集写入的待办线索'}
    xhs_id = str(row.get('author_red_id') or '').strip()
    uid = str(row.get('commenter_user_id') or '').strip()
    who = str(row.get('comment_author') or '')[:20]
    if not xhs_id:
        return {'platform': 'xiaohongshu', 'kind': 'reply', 'status': 'fail',
                'reason': '该线索没有可定位的小红书号（@%s）' % who}
    _log(oid, '模拟回复：中转站随机选中 @%s（线索 %s），开始定位'
         % (who, str(row.get('id'))[:8]))
    if not _claim_row('redbook', row['id'], serial):
        return {'platform': 'xiaohongshu', 'kind': 'reply', 'status': 'fail',
                'reason': '线索认领失败'}
    text = '新机模拟测试：仅生成草稿，不发送'
    try:
        _relaunch_app(serial, 'com.xingin.xhs', oid)
        st = XL.open_chat(serial, xhs_id, user_id=uid)
        if not st or not st.get('opened'):
            reason = (st or {}).get('reason') or '未打开会话'
            L.record_extract('redbook', row['id'], serial, 'error',
                             {'failure': {'detail': reason}})
            return {'platform': 'xiaohongshu', 'kind': 'reply', 'status': 'fail',
                    'reason': '未进入私信会话页：%s' % reason}
        dr = XL.draft_message(serial, xhs_id, text, already_open=True,
                              expect_nickname=who, user_id=uid)
        if not dr or not dr.get('drafted'):
            reason = (dr or {}).get('reason') or '草稿未就绪'
            L.record_extract('redbook', row['id'], serial, 'error',
                             {'failure': {'detail': reason}})
            return {'platform': 'xiaohongshu', 'kind': 'reply', 'status': 'fail',
                    'reason': '草稿未填好：%s' % reason}
    except Exception as exc:                       # noqa: BLE001
        try:
            L.record_extract('redbook', row['id'], serial, 'error',
                             {'failure': {'detail': str(exc)[:200]}})
        except Exception:
            pass
        return {'platform': 'xiaohongshu', 'kind': 'reply', 'status': 'fail',
                'reason': '%s: %s' % (type(exc).__name__, str(exc)[:150])}
    L.record_extract('redbook', row['id'], serial, 'found',
                     {'detail': 'draft_filled'})
    _log(oid, '模拟回复 @%s：草稿已填，停在发送键前，未发送' % who)
    return {'platform': 'xiaohongshu', 'kind': 'reply', 'status': 'pass',
            'person': who, 'detail': '草稿已填，停在发送键前'}


def _reply_sim(oid, serial, p, since_ts):
    """模拟回复：真去定位这个人、真开私信会话、真填草稿，但停在发送键前。"""
    if p['id'] == 'bilibili':
        return _reply_sim_bili(oid, serial, p, since_ts)
    if p['id'] == 'xiaohongshu':
        return _reply_sim_xhs(oid, serial, p, since_ts)
    if p['id'] != 'douyin':
        return {'platform': p['id'], 'kind': 'reply', 'status': 'skipped',
                'reason': '模拟回复暂只接入抖音/B站/小红书'}
    from . import douyin_extract
    from . import lead_store as L
    row = _pick_test_lead(serial, since_ts, 'douyin')
    if not row:
        return {'platform': 'douyin', 'kind': 'reply', 'status': 'fail',
                'reason': '中转站里没有本次采集写入的待办线索'}
    who = str(row.get('comment_author') or '')[:20]
    _log(oid, '模拟回复：中转站随机选中 @%s（线索 %s），开始定位'
         % (who, str(row.get('id'))[:8]))
    if not _claim_row('douyin', row['id'], serial):
        return {'platform': 'douyin', 'kind': 'reply', 'status': 'fail',
                'reason': '线索认领失败（可能已被别的任务拿走）'}
    entry = (douyin_extract.SEARCH_ACCOUNT
             if str(row.get('commenter_douyin_id') or '').strip()
             else douyin_extract.FROM_VIDEO_COMMENTS)
    box = {}

    def _work():
        try:
            from .mobile_gateway import device_session
            with device_session(serial):
                box['r'] = douyin_extract.run_extract(
                    dict(row), serial,
                    dm_text='新机模拟测试：仅生成草稿，不发送',
                    entry=entry)
        except Exception as exc:  # noqa: BLE001
            box['e'] = '%s: %s' % (type(exc).__name__, str(exc)[:200])

    th = threading.Thread(target=_work, daemon=True, name='onboard-reply')
    th.start()
    th.join(REPLY_TIMEOUT)
    if th.is_alive():
        return {'platform': 'douyin', 'kind': 'reply', 'status': 'fail',
                'reason': '模拟回复 %ds 未结束（已放弃等待，请人工确认页面）'
                          % REPLY_TIMEOUT}
    if 'e' in box:
        L.record_extract('douyin', row['id'], serial, 'error',
                         {'failure': {'detail': box['e']}})
        return {'platform': 'douyin', 'kind': 'reply', 'status': 'fail',
                'reason': box['e']}
    result = box.get('r') or {}
    L.record_extract('douyin', row['id'], serial,
                     result.get('extract_result') or 'unknown',
                     result.get('evidence'), result.get('seconds') or 0)
    ok = (result.get('extract_result') == 'ok')
    _log(oid, '模拟回复 @%s：%s（草稿已填，停在发送键前，未发送）'
         % (who, '成功' if ok else result.get('extract_result')))
    return {'platform': 'douyin', 'kind': 'reply',
            'status': 'pass' if ok else 'fail',
            'reason': '' if ok else str(result.get('extract_result')),
            'person': who,
            'detail': '草稿已填，停在发送键前' if ok else ''}


def promote(oid: str, scope: str = 'all'):
    """自愈完成后主人拍板：覆盖到所有手机，还是只留本机。

    scope='all'  ：把本次修正写进机群覆盖（_fleet.json），所有机型都吃这套 dp
    scope='this' ：什么都不写，本机档案已经生效，其它手机继续用默认
    """
    job = _job(oid)
    if not job:
        raise KeyError('任务不存在')
    rep = job.get('report') or {}
    pend = dict(rep.get('pending_promotion') or {})
    from . import ui_probe_targets as U
    res = U.promote_keys(pend, scope)
    job['promoted'] = scope
    rep['promoted'] = scope
    _log(oid, '覆盖选择：%s（%d 项）' % (
        '全部手机' if scope == 'all' else '仅本机', len(pend)))
    return {'ok': True, 'scope': scope, 'count': len(pend),
            'written': res.get('written') or {}}


def onboard_status(oid: str):
    job = _job(oid)
    if not job:
        raise KeyError('任务不存在')
    return {'onboard_id': oid, 'serial': job['serial'],
            'status': job['status'],
            'promoted': job.get('promoted') or '',
            'pending_promotion': (job.get('report') or {}).get('pending_promotion') or {},
            'log': list(job['log'][-60:]), 'report': job['report'],
            'report_path': job['report_path']}


def onboard_stop(oid: str):
    job = _job(oid)
    if not job:
        raise KeyError('任务不存在')
    job['stop'].set()
    # 顺手停掉本次编排下发的子任务
    try:
        from .execution import TASKS, stop as _stop_task
        for t in list(TASKS.values()):
            if t.get('quick_batch_id') == oid and \
                    t.get('status') not in FINISHED:
                try:
                    _stop_task(t['id'])
                except Exception:
                    pass
    except Exception:
        pass
    return {'ok': True}
