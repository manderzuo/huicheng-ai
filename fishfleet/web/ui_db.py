"""抖音三链「流程信息数据库」。

三层
----
证据层（只增不改）  run → snapshot → element / probe / anchor_obs / failure
期望层（可版本化）  expect → expect_rule / expect_box
流程层（半自动）    flow_step
校对结果           check_run → check_item

存储
----
SQLite 单文件：``data/ui_db/douyin_flow.db``（标准库，零依赖，可整份拷走）

三条使用路径
------------
1. ``ingest(jsonl)``  把录制器的 JSONL 灌进库（幂等，可重放重建）
2. ``propose(run)``   从实测反推期望（status='proposed'，人工审后 promote）
3. ``check_device(serial)`` / ``check_run(run_id)``  校对，出五态报告

判定五态
--------
ok       第 1 判据命中（通常 id 还在）
shifted  第 2+ 判据命中（id 换了，但还有救）—— 新机适配的核心信号
moved    命中但归一化坐标位移超容差（布局变了，点击会错位）
missing  全部判据不命中（配合 appear_when 区分「状态没到」与「真缺失」）
extra    命中多个（脚本要求唯一，会拒绝点击）
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / 'data' / 'ui_db' / 'douyin_flow.db'
RECORD_DIR = ROOT / 'data' / 'ui_record'

PACKAGE = {
    'douyin': 'com.ss.android.ugc.aweme',
    'xiaohongshu': 'com.xingin.xhs',
    'bilibili': 'tv.danmaku.bili',
    'tiktok': 'com.zhiliaoapp.musically',
    'facebook': 'com.facebook.katana',
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_key TEXT UNIQUE,
  serial TEXT NOT NULL, plat TEXT NOT NULL, app_version TEXT,
  screen_w INTEGER, screen_h INTEGER, density INTEGER,
  chain TEXT NOT NULL, task_id TEXT,
  started_at REAL, ended_at REAL, outcome TEXT,
  jsonl_path TEXT, note TEXT
);

CREATE TABLE IF NOT EXISTS snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  seq INTEGER, ts REAL, step TEXT, activity TEXT,
  page_guess TEXT, page_scores TEXT,
  node_count INTEGER, peer_count INTEGER, peer_json TEXT,
  raw_ref TEXT,
  UNIQUE(run_id, seq)
);

CREATE TABLE IF NOT EXISTS element (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snap_id INTEGER NOT NULL,
  identifier TEXT, text TEXT, label TEXT, type TEXT,
  x INTEGER, y INTEGER, w INTEGER, h INTEGER,
  enabled INTEGER, selected INTEGER, focused INTEGER, value TEXT,
  ref TEXT
);
CREATE INDEX IF NOT EXISTS ix_el_snap  ON element(snap_id);
CREATE INDEX IF NOT EXISTS ix_el_ident ON element(identifier);
CREATE INDEX IF NOT EXISTS ix_el_label ON element(label);

CREATE TABLE IF NOT EXISTS probe (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snap_id INTEGER NOT NULL,
  want TEXT NOT NULL, page TEXT, name TEXT,
  rule TEXT,
  hits INTEGER, hit_by TEXT, verdict TEXT,
  hits_noid INTEGER,              -- 去掉 id 判据后的命中数（NULL=本就没写 id）
  id_ok INTEGER, text_ok INTEGER, label_ok INTEGER, tre_ok INTEGER,
  got_element_id INTEGER,
  visible INTEGER, visible_ratio REAL,
  norm_x REAL, norm_y REAL, norm_w REAL, norm_h REAL
);
CREATE INDEX IF NOT EXISTS ix_probe_snap ON probe(snap_id);
CREATE INDEX IF NOT EXISTS ix_probe_want ON probe(want);

CREATE TABLE IF NOT EXISTS anchor_obs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snap_id INTEGER NOT NULL, page TEXT, anchor_name TEXT, hits INTEGER
);

CREATE TABLE IF NOT EXISTS failure (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, snap_id INTEGER, step TEXT, want TEXT,
  error TEXT, stack TEXT, candidates TEXT
);

CREATE TABLE IF NOT EXISTS expect (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plat TEXT NOT NULL, app_version TEXT NOT NULL,
  page TEXT NOT NULL, name TEXT NOT NULL,
  chain TEXT, hard_dependency INTEGER DEFAULT 0,
  multi INTEGER DEFAULT 0,          -- 1=列表型控件（pick_all），允许多命中
  appear_when TEXT DEFAULT '',
  confidence REAL DEFAULT 0, seen_runs INTEGER DEFAULT 0,
  seen_serials TEXT, status TEXT DEFAULT 'active', note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_expect
  ON expect(plat, app_version, page, name);

CREATE TABLE IF NOT EXISTS expect_rule (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expect_id INTEGER NOT NULL, ord INTEGER NOT NULL,
  kind TEXT NOT NULL, pattern TEXT NOT NULL,
  hit_rate REAL DEFAULT 0, note TEXT
);
CREATE INDEX IF NOT EXISTS ix_rule_exp ON expect_rule(expect_id);

CREATE TABLE IF NOT EXISTS expect_box (
  expect_id INTEGER PRIMARY KEY,
  norm_x REAL, norm_y REAL, norm_w REAL, norm_h REAL, tol REAL DEFAULT 0.04
);

CREATE TABLE IF NOT EXISTS flow_step (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plat TEXT, chain TEXT NOT NULL, step_no INTEGER NOT NULL,
  name TEXT NOT NULL, from_page TEXT, action TEXT, target TEXT,
  to_page TEXT, expect_ok TEXT, risk TEXT,
  stop_point INTEGER DEFAULT 0, evidence INTEGER DEFAULT 1, note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_step ON flow_step(plat, chain, step_no);

CREATE TABLE IF NOT EXISTS check_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT, plat TEXT, app_version TEXT, checked_at REAL,
  scope TEXT, total INTEGER, ok INTEGER, shifted INTEGER,
  moved INTEGER, missing INTEGER, extra INTEGER, score REAL
);

CREATE TABLE IF NOT EXISTS check_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  check_run_id INTEGER NOT NULL,
  page TEXT, name TEXT, verdict TEXT,
  expected_json TEXT, actual_json TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_ci_run ON check_item(check_run_id);
"""


# --------------------------------------------------------------------- 连接
def connect(path=None) -> sqlite3.Connection:
    target = Path(path or DB_PATH)
    if str(target) != ':memory:':
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.executescript(SCHEMA)
    # 轻量迁移：老库补新列（SQLite 不支持 IF NOT EXISTS 加列）
    for sql in ("ALTER TABLE expect ADD COLUMN multi INTEGER DEFAULT 0",
                "ALTER TABLE probe ADD COLUMN hits_noid INTEGER"):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    return conn


# --------------------------------------------------------------------- 工具
def _eid(node) -> str:
    """去掉包名前缀的混淆短 id。"""
    ident = str((node or {}).get('identifier') or '')
    return ident.rsplit(':id/', 1)[-1] if ':id/' in ident else ident


def _box(node):
    c = (node or {}).get('coordinates') or {}
    try:
        x, y = float(c['x']), float(c['y'])
        w, h = float(c['width']), float(c['height'])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def screen_of(nodes, fallback=(1080, 1920)):
    """屏幕尺寸：优先取元素的右下边界最大值（系统栏会在 0 与全宽之间）。"""
    right = bottom = 0.0
    for node in nodes or []:
        box = _box(node)
        if not box:
            continue
        x, y, w, h = box
        right = max(right, x + w)
        bottom = max(bottom, y + h)
    if right < 200 or bottom < 200:
        return fallback
    return int(right), int(bottom)


def _norm(node, size):
    box = _box(node)
    if not box or not size:
        return None
    x, y, w, h = box
    sw, sh = float(size[0]), float(size[1])
    if sw <= 0 or sh <= 0:
        return None
    return round(x / sw, 4), round(y / sh, 4), round(w / sw, 4), round(h / sh, 4)


def visible_ratio(node, size):
    """元素在屏幕内的可见比例（0-1）。"""
    box = _box(node)
    if not box or not size:
        return 0.0
    x, y, w, h = box
    sw, sh = float(size[0]), float(size[1])
    left, top = max(0.0, x), max(0.0, y)
    right, bottom = min(sw, x + w), min(sh, y + h)
    if right <= left or bottom <= top:
        return 0.0
    return round((right - left) * (bottom - top) / (w * h), 3)


# --------------------------------------------------------- 单判据独立匹配
def rule_match(node, kind: str, pattern: str) -> bool:
    """按单条判据匹配。与 templates.matches 的「与」关系不同——
    这里刻意拆开，才能算出「id 废了但 label 还能救」。"""
    if kind == 'id':
        return _eid(node) == pattern
    if kind == 'text':
        return str(node.get('text') or '').strip() == pattern
    if kind == 'label_re':
        return bool(__import__('re').search(pattern, str(node.get('label') or '')))
    if kind == 'text_re':
        return bool(__import__('re').search(pattern, str(node.get('text') or '')))
    if kind == 'type':
        return pattern in str(node.get('type') or '')
    return False


# 2026-11-06：这些控件留在模板里（脚本要按名字取），但**不参与校对**。
# 判据：出现与否取决于内容/账号状态，或本身就多命中（list 型）。
# 放进来的每一行都要写清原因，避免以后有人误删。
SKIP_EXPECT = {
    ('douyin', 'feed', 'desc'),            # 有些视频没有描述文案
    ('douyin', 'profile_more', 'hide'),    # 「不让她看」多命中（列表项）
    ('douyin', 'chat', 'quick_phrase'),    # 快捷短语多命中
    ('bilibili', 'video_detail_portrait', '相关推荐-番剧卡'),   # 推荐位随机
    ('bilibili', 'video_detail_portrait', '卡片更多操作'),     # 卡片菜单，部分卡没有
    ('bilibili', 'video_detail_comments', 'UP 主身份标'),      # 只有认证UP主有
    ('bilibili', 'video_detail_comments', '置顶标记'),         # 只有置顶评论有
    ('bilibili', 'video_detail_portrait', '标签/话题按钮'),   # 一屏多个话题标签
    ('bilibili', 'video_detail_portrait', '标题行'),          # 标题可能多行
    # B站评论区：私信/发布链只是「路过」详情页，评论区并未展开，
    # 拿采集链的期望去卡它们必然 missing（2026-11-06 实测拖到 0.92）。
    ('bilibili', 'video_detail_comments', '回复'),
    ('bilibili', 'video_detail_comments', '更多'),
    ('bilibili', 'video_detail_comments', '点赞'),
    ('bilibili', 'video_detail_comments', '点踩'),
    ('bilibili', 'video_detail_comments', '排序切换'),
    ('bilibili', 'video_detail_comments', '热门评论标题'),
    # 列表型控件：一屏内有多个同类（笔记卡/评论条目），永远不可能唯一
    ('xiaohongshu', 'note_detail', 'comment_btn'),
    ('xiaohongshu', 'note_detail', 'favorite_btn'),
    ('xiaohongshu', 'note_detail', 'like_btn'),
    ('xiaohongshu', 'note_detail', 'follow'),
    ('xiaohongshu', 'note_detail', 'comment_box'),   # 私信链路过详情页时不存在
    ('douyin', 'profile', 'works_tab'),              # 部分账号主页没有作品 tab
    ('bilibili', 'home_feed', '全部分区入口'),      # 首页分区条，发布链路过时未渲染
    ('bilibili', 'home_feed', '当前分区 tab'),
    ('bilibili', 'home_feed', '顶部分区 tab 条'),
    ('facebook', 'page_detail', 'event_card'),   # 活动卡片只有办活动的页才有
    ('facebook', 'page_detail', 'avatar'),        # 部分商家/粉丝页头像在别的 id 下
    ('xiaohongshu', 'chat', 'quota_hint'),           # 首次私信才有的配额提示
    ('tiktok', 'dm_chat', 'greeting'),            # 晚安贴纸只在空会话首帧出现，填草稿后必消失
    ('facebook', 'dm_thread', 'cancel_reply'),       # 只在「正在回复某条」时出现
    ('facebook', 'dm_thread', 'hint'),               # 输入框为空时的提示语
    ('facebook', 'menu', 'pages'),               # 菜单项顺序/可见性随账号变化
    ('xiaohongshu', 'comments', 'author_tag'),      # 只有作者本人回复才有「作者」标记
}


def _templates(plat: str):
    """按平台取模板库。各平台 Target 字段不同（抖音有 id，小红书刻意没有 id）。

    返回 None 表示该平台**还没有模板库**（如 TikTok）：此时只能存原始证据，
    不做页面判定、不生成期望——如实说明，而不是拿别的平台的库硬套。
    """
    if plat == 'xiaohongshu':
        from . import xiaohongshu_templates as T
        return T
    if plat == 'bilibili':
        from . import bilibili_templates as T
        return T
    if plat == 'facebook':
        from . import facebook_templates as T
        return T
    if plat == 'tiktok':
        try:
            from . import tiktok_templates as T      # noqa: F401
            return T
        except Exception:
            return None
    from . import douyin_templates as T
    return T


def _tmatch(T, el, target) -> bool:
    """跨平台匹配一个 Target。

    各平台模板库接口不统一：
      · 抖音 / 小红书 有 matches(el, target)
      · Facebook 的 matches 需要第三个参数（同页元素列表）
      · B站**没有 matches**，只有 identify()（靠 ANCHORS 的 id+正则）
    所以这里先试模板库自己的，失败再按 Target 字段做通用「与」匹配。
    """
    fn = getattr(T, 'matches', None)
    if callable(fn):
        try:
            return bool(fn(el, target))
        except TypeError:
            try:
                return bool(fn(el, target, [el]))
            except Exception:
                pass
        except Exception:
            pass

    def g(key):
        return str(getattr(target, key, '') or '')

    ident = str(el.get('identifier') or el.get('id') or '')
    short = ident.split(':id/')[-1] if ':id/' in ident else ident
    text = str(el.get('text') or '').strip()
    label = str(el.get('label') or '').strip()
    typ = str(el.get('type') or '')
    if g('id') and short != g('id'):
        return False
    if g('text') and text != g('text'):
        return False
    if g('text_re') and not re.search(g('text_re'), text):
        return False
    if g('label_re') and not re.search(g('label_re'), label):
        return False
    for key in ('role', 'type'):
        want = g(key).lower()
        if want and want not in typ.lower():
            return False
    # 2026-11-06 补：Facebook 模板用的是 `type_re`（正则），此前 _tmatch 不认它，
    # 于是 FB 所有靠 type_re 定位的控件（如 dm_thread/input）永远匹配不上。
    if g('type_re') and not re.search(g('type_re'), typ):
        return False
    return bool(g('id') or g('text') or g('text_re') or g('label_re')
                or g('role') or g('type') or g('type_re'))


def _page_of(T, nodes, activity=''):
    """页面判定。各平台模板库接口不统一，这里按能力依次适配：

    · 抖音       page_of(dump)
    · 小红书     page_of(dump, activity)
    · B站        identify(elements)
    · 其它/新增  通用兜底：按 identity 锚点命中数打分（>=1 才认定）
    """
    ident = getattr(T, 'identify', None)
    if callable(ident):
        try:
            # B站的 identify 读 e.get('id')，而录制/入库格式是 identifier；
            # 不补这层别名，B站每一页都会判成空（实测 69/69 全空）。
            for el in nodes:
                if 'id' not in el:
                    el['id'] = el.get('identifier') or ''
            got = ident(nodes)
            if got:
                return got
        except Exception:
            pass
    fn = getattr(T, 'page_of', None)
    if callable(fn):
        try:
            return fn(nodes, activity) or ''
        except TypeError:
            try:
                return fn(nodes) or ''
            except Exception:
                pass
        except Exception:
            pass
    pages = getattr(T, 'PAGES', None) or {}
    matches = getattr(T, 'matches', None)
    if not pages or not callable(matches):
        return ''
    best, best_score = '', 0
    for key, page in pages.items():
        ident_targets = getattr(page, 'identity', ()) or ()
        if not ident_targets:
            continue
        score = 0
        for target in ident_targets:
            try:
                if any(matches(el, target) for el in nodes):
                    score += 1
            except Exception:
                pass
        if score > best_score:
            best, best_score = key, score
    return best if best_score else ''


def target_rules(target):
    """把一个 Target 拆成"判据组"序列，供校对时逐级降级。

    顺序（从最严到最松）：
      0 组  全部判据（与原 matches 完全等价 —— 保证"不会比原代码更宽松"）
      1 组  去掉 id（id 是混淆产物，最易随版本变；文字通常还在）
      2 组起 单条判据兜底

    各平台 Target 字段不同：抖音有 `id`，小红书/facebook 没有（getattr 兼容）。

    2026-11-06 补：之前只认 id/type/text/label_re/text_re，
    漏掉了 `role`（TikTok 用它，如 `role='EditText'`）和 `type_re`（Facebook 用它）。
    后果是这些控件**判据组为空 → probe 直接跳过 → 期望层永远不会为它们建条目**，
    于是 B站/TK 的私信输入框、FB 的输入框从来没进过校对集合。
    现在统一映射成 `['type', ...]`（后者按子串判断），与 `_tmatch` 保持一致。
    """
    def g(key):
        return str(getattr(target, key, '') or '')

    all_rules = []
    if g('id'):
        all_rules.append(['id', g('id')])
    if g('type'):
        all_rules.append(['type', g('type')])
    # role 与 type 语义相同（控件类型），合并成同一条判据，避免重复
    elif g('role'):
        all_rules.append(['type', g('role')])
    if g('type_re'):
        all_rules.append(['type', g('type_re')])
    if g('text'):
        all_rules.append(['text', g('text')])
    if g('label_re'):
        all_rules.append(['label_re', g('label_re')])
    if g('text_re'):
        all_rules.append(['text_re', g('text_re')])
    if not all_rules:
        return []
    groups = [all_rules]
    no_id = [r for r in all_rules if r[0] != 'id']
    if no_id and len(no_id) != len(all_rules):
        groups.append(no_id)
    for rule in all_rules:
        if [rule] not in groups:
            groups.append([rule])
    return groups


# --------------------------------------------------------------------- 入库
def ingest(jsonl_path, conn=None, chain='unknown', app_version='',
           plat='douyin', task_id='', force=False,
           since=None, until=None, key_suffix='') -> dict:
    """把一个 JSONL 灌进库。幂等：同一文件重复灌会因 run_key 唯一而跳过。

    force=True 时先清掉同 run_key 的旧数据再重入。
    since/until 用于把同一个录制文件按任务时间窗切成多次 run
    （三条链共写一个文件，必须切开，否则 chain 与时间会混在一起）。
    """
    path = Path(jsonl_path)
    if not path.exists():
        return {'ok': False, 'reason': '文件不存在：%s' % path}
    own = conn is None
    conn = conn or connect()
    try:
        run_key = path.stem + (key_suffix or '')
        row = conn.execute('SELECT id FROM run WHERE run_key=?', (run_key,)).fetchone()
        if row and not force:
            return {'ok': True, 'run_id': row['id'], 'skipped': '已入库'}
        if row and force:
            old = row['id']
            for sql in ('DELETE FROM probe WHERE snap_id IN '
                        '(SELECT id FROM snapshot WHERE run_id=?)',
                        'DELETE FROM anchor_obs WHERE snap_id IN '
                        '(SELECT id FROM snapshot WHERE run_id=?)',
                        'DELETE FROM element WHERE snap_id IN '
                        '(SELECT id FROM snapshot WHERE run_id=?)',
                        'DELETE FROM snapshot WHERE run_id=?',
                        'DELETE FROM run WHERE id=?'):
                conn.execute(sql, (old,))
            conn.commit()
        events = []
        with path.open('r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        if since is not None:
            events = [e for e in events if (e.get('ts') or 0) >= since]
        if until is not None:
            events = [e for e in events if (e.get('ts') or 0) <= until]
        if not events:
            return {'ok': False, 'reason': '没有可用事件（时间窗内为空）'}
        serial = events[0].get('serial') or ''
        started = events[0].get('ts') or 0
        ended = events[-1].get('ts') or 0
        cur = conn.execute(
            'INSERT INTO run(run_key,serial,plat,app_version,chain,task_id,'
            'started_at,ended_at,outcome,jsonl_path) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (run_key, serial, plat, app_version, chain, task_id,
             started, ended, 'unknown', str(path)))
        run_id = cur.lastrowid

        T = _templates(plat)
        has_tpl = T is not None
        seq = 0
        dumps = 0
        last_nodes = None
        last_seq = None
        last_page = ''
        last_activity = ''
        _ACT_RE = re.compile(r'activity:\s*([A-Za-z0-9_.$]+)')
        for ev in events:
            # 前台应用事件只用来提供 activity 上下文（小红书页面判定必须靠它）
            if ev.get('kind') == 'context':
                _m = _ACT_RE.search(str(ev.get('result') or ''))
                if _m:
                    last_activity = _m.group(1)
                continue
            if ev.get('kind') not in ('dump',):
                if ev.get('kind') == 'dump_dup' and last_seq is not None:
                    # 重复树：快照仍记一条，但不重复存元素，省 70% 体积。
                    # 页面判定必须继承上一条 —— 树完全一样，判不出只是没重算。
                    conn.execute(
                        'INSERT INTO snapshot(run_id,seq,ts,step,page_guess,'
                        'node_count,peer_count,peer_json) VALUES(?,?,?,?,?,?,?,?)',
                        (run_id, ev.get('seq'), ev.get('ts'), 'dup',
                         last_page, ev.get('node_count') or 0, 1, json.dumps(
                             {'reuse_seq': last_seq}, ensure_ascii=False)))
                continue
            nodes = ev.get('nodes') or []
            if not nodes:
                continue
            dumps += 1
            seq = ev.get('seq') or dumps
            size = screen_of(nodes)
            # 页面判定：离线转换的批注（page_hint）优先，真实录制靠 page_of
            if has_tpl:
                page = (ev.get('page_hint') or '').strip()
                if page not in T.PAGES:
                    page = _page_of(T, nodes,
                                    (ev.get('activity') or last_activity))
            else:
                # 没有模板库的平台（如 TikTok）：只能用离线批注，绝不猜
                page = (ev.get('page_hint') or '').strip()
            scores = {}
            if has_tpl:
                for key, pg in T.PAGES.items():
                    try:
                        scores[key] = sum(1 for t in pg.identity
                                          if any(_tmatch(T, el, t) for el in nodes))
                    except TypeError:
                        # facebook_templates 的 matches 签名不同（需要第三个参数），
                        # 打分只是诊断信息，算不出来就留 0，绝不让它挡住入库。
                        scores[key] = 0
                    except Exception:
                        scores[key] = 0
            cur = conn.execute(
                'INSERT INTO snapshot(run_id,seq,ts,step,activity,page_guess,'
                'page_scores,node_count) VALUES(?,?,?,?,?,?,?,?)',
                (run_id, seq, ev.get('ts'), ev.get('kind'),
                 ev.get('activity') or last_activity, page, json.dumps(scores), len(nodes)))
            snap_id = cur.lastrowid
            last_nodes, last_seq = nodes, seq
            last_page = page or last_page

            ids = {}
            for node in nodes:
                box = _box(node) or (0, 0, 0, 0)
                cur = conn.execute(
                    'INSERT INTO element(snap_id,identifier,text,label,type,'
                    'x,y,w,h,enabled,selected,focused,value,ref) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (snap_id, node.get('identifier') or '', node.get('text') or '',
                     node.get('label') or '', node.get('type') or '',
                     int(box[0]), int(box[1]), int(box[2]), int(box[3]),
                     int(bool(node.get('enabled', True))),
                     int(bool(node.get('selected'))),
                     int(bool(node.get('focused'))),
                     node.get('value') or '', node.get('ref') or ''))
                ids[id(node)] = cur.lastrowid

            # 页面锚点观测
            for pkey, pg in (T.PAGES.items() if has_tpl else ()):
                for t in pg.identity:
                    try:
                        hits = sum(1 for el in nodes if _tmatch(T, el, t))
                    except TypeError:
                        # FB 的 matches 签名不同；锚点观测只是诊断，算不出就跳过
                        continue
                    if hits:
                        conn.execute(
                            'INSERT INTO anchor_obs(snap_id,page,anchor_name,hits) '
                            'VALUES(?,?,?,?)', (snap_id, pkey, t.name, hits))

            # 控件探测：只记「当前判定页面」的 targets，噪音可控
            if has_tpl and page in T.PAGES:
                for name, target in T.PAGES[page].targets.items():
                    # 2026-11-06：有些控件**必须留在模板里**（脚本代码要按名字取它），
                    # 但它出现与否取决于内容/账号状态（比如抖音 feed/desc 有些视频没描述、
                    # B站UP主身份标只有认证号才有）。这类一律跳过校对，
                    # 否则每次采集都会固定掉一项，分数永远上不去。
                    if (plat, page, name) in SKIP_EXPECT:
                        continue
                    groups = target_rules(target)
                    if not groups:
                        continue
                    primary = groups[0]          # 全判据，与 templates.matches 等价
                    flags, cand = {}, []
                    for node in nodes:
                        for kind, pattern in primary:
                            if rule_match(node, kind, pattern):
                                flags[kind] = True
                        if all(rule_match(node, k, p) for k, p in primary):
                            cand.append(node)
                    # 去掉 id 后还能唯一命中吗？→ 判断 id 是否冗余（换版本时的救命判据）
                    hits_noid = None
                    if any(k == 'id' for k, _ in primary) and len(primary) > 1:
                        fallback = [r for r in primary if r[0] != 'id']
                        hits_noid = sum(
                            1 for n in nodes
                            if all(rule_match(n, k, p) for k, p in fallback))
                    hits = len(cand)
                    got = cand[0] if cand else None
                    # 列表型判别：多命中且命中元素的 id 完全一致（评论条目/头像/正文
                    # 这类重复同类元素），与「真歧义」（不同控件撞同一判据）区分开。
                    idents = {_eid(n) for n in cand}
                    same_id = len(idents) == 1 and next(iter(idents), '') != ''
                    if hits == 1:
                        verdict = 'ok'
                    elif hits > 1 and same_id:
                        verdict = 'list'
                    elif hits > 1:
                        verdict = 'extra'
                    else:
                        verdict = 'none'
                    nb = _norm(got, size) if got else (None, None, None, None)
                    conn.execute(
                        'INSERT INTO probe(snap_id,want,page,name,rule,hits,hit_by,'
                        'hits_noid,verdict,id_ok,text_ok,label_ok,tre_ok,got_element_id,'
                        'visible,visible_ratio,norm_x,norm_y,norm_w,norm_h) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (snap_id, '%s/%s' % (page, name), page, name,
                         json.dumps(groups, ensure_ascii=False), hits,
                         next((k for k, _ in primary if flags.get(k)), ''),
                         hits_noid, verdict,
                         int(bool(flags.get('id'))), int(bool(flags.get('text'))),
                         int(bool(flags.get('label_re'))), int(bool(flags.get('text_re'))),
                         ids.get(id(got)) if got else None,
                         int(visible_ratio(got, size) > 0.5) if got else 0,
                         visible_ratio(got, size) if got else 0.0,
                         nb[0], nb[1], nb[2], nb[3]))
        conn.commit()
        return {'ok': True, 'run_id': run_id, 'dumps': dumps,
                'events': len(events), 'path': str(path)}
    finally:
        if own:
            conn.close()


def ingest_dir(conn=None, chain='unknown', plat='douyin', app_version='') -> list:
    """把 data/ui_record 下所有 jsonl 灌进库。"""
    own = conn is None
    conn = conn or connect()
    out = []
    try:
        for path in sorted(RECORD_DIR.glob('*.jsonl')):
            out.append(ingest(path, conn=conn, chain=chain,
                              plat=plat, app_version=app_version))
    finally:
        if own:
            conn.close()
    return out


# ------------------------------------------------------------------- 反推期望
def propose(run_id, conn=None, min_hits=1) -> dict:
    """从一次 run 的实测反推 expect（status='proposed'）。

    规则（保守）：
      · 该 want 在 ≥min_hits 个快照里只命中 1 个 → 生成期望
      · 判据顺序沿用代码里的稳定度排序（id > text > label_re > text_re）
      · 坐标取命中样本的中位数归一化值
    """
    own = conn is None
    conn = conn or connect()
    try:
        run = conn.execute('SELECT * FROM run WHERE id=?', (run_id,)).fetchone()
        if not run:
            return {'ok': False, 'reason': 'run 不存在'}
        rows = conn.execute(
            'SELECT want,page,name,hits,verdict,id_ok,text_ok,label_ok,tre_ok,'
            'norm_x,norm_y,norm_w,norm_h,rule FROM probe WHERE snap_id IN '
            '(SELECT id FROM snapshot WHERE run_id=?)', (run_id,)).fetchall()
        grouped = {}
        for row in rows:
            grouped.setdefault(row['want'], []).append(row)
        made = 0
        for want, items in grouped.items():
            # list 型（同一 id 重复出现）也算可信期望，自动标 multi
            uniq = [r for r in items if r['verdict'] in ('ok', 'list')]
            if len(uniq) < min_hits:
                continue
            is_multi = 1 if uniq[0]['verdict'] == 'list' else 0
            page, name = uniq[0]['page'], uniq[0]['name']
            groups = json.loads(uniq[0]['rule'] or '[]')
            if not groups:
                continue
            box = None
            xs = [r['norm_x'] for r in uniq if r['norm_x'] is not None]
            if xs:
                xs.sort()
                ys = sorted(r['norm_y'] for r in uniq if r['norm_y'] is not None)
                ws = sorted(r['norm_w'] for r in uniq if r['norm_w'] is not None)
                hs = sorted(r['norm_h'] for r in uniq if r['norm_h'] is not None)
                mid = len(xs) // 2
                box = (xs[mid], ys[mid] if ys else 0, ws[mid] if ws else 0,
                       hs[mid] if hs else 0)
            conn.execute(
                'INSERT OR IGNORE INTO expect(plat,app_version,page,name,chain,'
                'multi,confidence,seen_runs,seen_serials,status,note) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (run['plat'], run['app_version'] or '', page, name, run['chain'],
                 is_multi, round(len(uniq) / max(1, len(items)), 2), 1,
                 json.dumps([run['serial']], ensure_ascii=False),
                 'proposed', '由 run#%d 反推' % run_id))
            exp = conn.execute(
                'SELECT id FROM expect WHERE plat=? AND app_version=? AND page=? AND name=?',
                (run['plat'], run['app_version'] or '', page, name)).fetchone()
            if not exp:
                continue
            eid = exp['id']
            have = conn.execute('SELECT COUNT(*) c FROM expect_rule WHERE expect_id=?',
                                (eid,)).fetchone()['c']
            if have == 0:
                for order, group in enumerate(groups):
                    conn.execute(
                        'INSERT INTO expect_rule(expect_id,ord,kind,pattern,hit_rate) '
                        'VALUES(?,?,?,?,?)',
                        (eid, order, 'group',
                         json.dumps(group, ensure_ascii=False), 1.0))
                if box:
                    conn.execute(
                        'INSERT OR REPLACE INTO expect_box(expect_id,norm_x,'
                        'norm_y,norm_w,norm_h,tol) VALUES(?,?,?,?,?,?)',
                        (eid, box[0], box[1], box[2], box[3], 0.04))
                made += 1
        conn.commit()
        return {'ok': True, 'run_id': run_id, 'expects': made, 'wants': len(grouped)}
    finally:
        if own:
            conn.close()


def promote(plat='douyin', app_version=None, conn=None) -> dict:
    """把 proposed 转正（人工审过之后再跑）。"""
    own = conn is None
    conn = conn or connect()
    try:
        if app_version is None:
            cur = conn.execute(
                "UPDATE expect SET status='active' WHERE plat=? AND status='proposed'",
                (plat,))
        else:
            cur = conn.execute(
                "UPDATE expect SET status='active' WHERE plat=? AND app_version=? "
                "AND status='proposed'", (plat, app_version))
        conn.commit()
        return {'ok': True, 'active': cur.rowcount}
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------------- 校对
@dataclass
class Coverage:
    total: int = 0
    ok: int = 0
    shifted: int = 0
    moved: int = 0
    missing: int = 0
    extra: int = 0
    hard_missing: list = field(default_factory=list)
    items: list = field(default_factory=list)
    plat: str = ''
    app_version: str = ''
    serial: str = ''
    scope: str = ''

    @property
    def score(self) -> float:
        """可用率。moved 也算通过 —— 脚本点击用的是当次 dump 的 ref，
        从不使用缓存坐标，所以坐标偏移不影响功能，只作提示。"""
        if not self.total:
            return 0.0
        return round((self.ok + self.shifted + self.moved) / self.total, 3)

    @property
    def usable(self) -> bool:
        """硬依赖一个都不能缺——缺了这条链必挂。"""
        return not self.hard_missing

    def summary(self) -> str:
        head = '%s 校对 %d 项：命中 %d（换形态 %d）、位移 %d、缺失 %d、歧义 %d，评分 %.2f' % (
            self.plat or 'douyin', self.total, self.ok + self.shifted, self.shifted,
            self.moved, self.missing, self.extra, self.score)
        if self.hard_missing:
            head += '｜硬依赖缺失：' + '、'.join(self.hard_missing[:6])
        return head


def _parse_group(kind, pattern):
    """把 expect_rule 还原成判据组 [[kind,pattern],...]。兼容旧式单条存法。"""
    if kind == 'group':
        try:
            data = json.loads(pattern)
            if (isinstance(data, list) and data
                    and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in data)):
                return [(str(a), str(b)) for a, b in data]
        except ValueError:
            pass
    return [(kind, pattern)]


_VERDICT_RANK = {'ok': 0, 'shifted': 1, 'moved': 2, 'extra': 3, 'missing': 4}


def _eval_expect(nodes, exp, rules, box, size):
    """在单个快照里评估一条期望，返回 (verdict, detail, hit_node)。

    不写库、不累加统计——聚合交给 check_run（同一控件跨 100+ 快照只算一条）。
    """
    multi = bool(exp['multi']) if 'multi' in exp.keys() else False
    hit_node, hit_ord, ambiguous = None, None, False
    for order, rule in enumerate(rules):
        pats = _parse_group(rule['kind'], rule['pattern'])
        cand = [n for n in nodes if all(rule_match(n, k, p) for k, p in pats)]
        if len(cand) == 1:
            hit_node, hit_ord = cand[0], order
            break
        if len(cand) > 1:
            if multi:
                # 列表型控件（评论条目/头像/正文）本来就该多个
                hit_node, hit_ord = cand[0], order
                break
            ambiguous = True
            break
    if ambiguous:
        return 'extra', '命中多个，脚本要求唯一', None
    if hit_node is None:
        when = (exp['appear_when'] or '').strip()
        return 'missing', ('状态依赖（%s）' % when) if when else '全部判据不命中', None
    verdict = 'ok' if hit_ord == 0 else 'shifted'
    detail = '通过'
    if verdict == 'shifted':
        pats = _parse_group(rules[hit_ord]['kind'], rules[hit_ord]['pattern'])
        detail = '第 %d 套判据命中（%s）' % (
            (hit_ord or 0) + 1, ' & '.join('%s=%s' % (k, p) for k, p in pats))
    # 坐标：列表型控件的 y 随滚动天然变化，比了就是制造假阳性，只对唯一控件比
    if box and not multi and box['norm_x'] is not None:
        nb = _norm(hit_node, size)
        if nb:
            tol = box['tol'] or 0.06
            dx = abs((nb[0] or 0) - (box['norm_x'] or 0))
            dy = abs((nb[1] or 0) - (box['norm_y'] or 0))
            if dx > tol or dy > tol:
                verdict = 'moved'
                detail = '坐标偏移 dx=%.3f dy=%.3f（容差 %.2f）' % (dx, dy, tol)
    return verdict, detail, hit_node


def _load_expects(conn, plat, app_version):
    if app_version:
        rows = conn.execute(
            "SELECT * FROM expect WHERE plat=? AND app_version=? AND status='active'",
            (plat, app_version)).fetchall()
    else:
        ver = conn.execute(
            "SELECT app_version FROM expect WHERE plat=? AND status='active' "
            "ORDER BY id DESC LIMIT 1", (plat,)).fetchone()
        rows = []
        if ver:
            app_version = ver['app_version']
            rows = conn.execute(
                "SELECT * FROM expect WHERE plat=? AND app_version=? "
                "AND status='active'", (plat, app_version)).fetchall()
    rules_map, boxes = {}, {}
    for exp in rows:
        rs = conn.execute('SELECT * FROM expect_rule WHERE expect_id=? ORDER BY ord',
                          (exp['id'],)).fetchall()
        rules_map[exp['id']] = [{'kind': r['kind'], 'pattern': r['pattern']} for r in rs]
        box = conn.execute('SELECT * FROM expect_box WHERE expect_id=?',
                           (exp['id'],)).fetchone()
        boxes[exp['id']] = dict(box) if box else None
    return rows, rules_map, boxes, app_version


def check_run(run_id, conn=None) -> Coverage:
    """离线校对：拿一次 run 录到的每个快照逐页比对。"""
    own = conn is None
    conn = conn or connect()
    try:
        run = conn.execute('SELECT * FROM run WHERE id=?', (run_id,)).fetchone()
        if not run:
            return Coverage(scope='run#%d 不存在' % run_id)
        expects, rules_map, boxes, version = _load_expects(conn, run['plat'],
                                                           run['app_version'])
        cov = Coverage(plat=run['plat'], app_version=version or '',
                       serial=run['serial'], scope='run#%d' % run_id)
        cur = conn.execute(
            'INSERT INTO check_run(serial,plat,app_version,checked_at,scope) '
            'VALUES(?,?,?,?,?)',
            (run['serial'], run['plat'], cov.app_version, time.time(), cov.scope))
        check_id = cur.lastrowid
        snaps = conn.execute('SELECT * FROM snapshot WHERE run_id=? ORDER BY seq',
                             (run_id,)).fetchall()
        # 按 want 聚合：同一控件跨上百个快照只算一条，取「最好」的那次。
        # 一个控件在某次快照没出现（页面刚切、输入框还没弹出）不该算缺失。
        buckets = {}
        for snap in snaps:
            page = snap['page_guess']
            if not page:
                continue
            mine = [e for e in expects if e['page'] == page]
            if not mine:
                continue
            nodes = [dict(r) for r in conn.execute(
                'SELECT * FROM element WHERE snap_id=?', (snap['id'],))]
            for node in nodes:
                bx = (node['x'], node['y'], node['w'], node['h'])
                node['coordinates'] = {'x': bx[0], 'y': bx[1],
                                       'width': bx[2], 'height': bx[3]}
            size = screen_of(nodes)
            for exp in mine:
                rules = rules_map.get(exp['id']) or []
                if not rules:
                    continue
                want = '%s/%s' % (exp['page'], exp['name'])
                v, d, h = _eval_expect(nodes, exp, rules, boxes.get(exp['id']), size)
                buckets.setdefault(want, {'exp': exp, 'rules': rules,
                                          'items': []})['items'].append((v, d, h))
        for want, bucket in buckets.items():
            exp, rules, items = bucket['exp'], bucket['rules'], bucket['items']
            best_v, best_d, best_h = min(items, key=lambda x: _VERDICT_RANK[x[0]])
            n_hit = sum(1 for v, _, _ in items if v in ('ok', 'shifted'))
            detail = '%s；快照命中 %d/%d' % (best_d or '-', n_hit, len(items))
            cov.total += 1
            if best_v == 'ok':
                cov.ok += 1
            elif best_v == 'shifted':
                cov.shifted += 1
            elif best_v == 'moved':
                cov.moved += 1
            elif best_v == 'extra':
                cov.extra += 1
            else:
                cov.missing += 1
                if exp['hard_dependency']:
                    cov.hard_missing.append(want)
            cov.items.append({'page': exp['page'], 'name': exp['name'],
                              'verdict': best_v, 'detail': detail})
            conn.execute(
                'INSERT INTO check_item(check_run_id,page,name,verdict,'
                'expected_json,actual_json,detail) VALUES(?,?,?,?,?,?,?)',
                (check_id, exp['page'], exp['name'], best_v,
                 json.dumps([_parse_group(r['kind'], r['pattern']) for r in rules],
                            ensure_ascii=False),
                 json.dumps({'text': best_h.get('text'),
                             'label': best_h.get('label'),
                             'identifier': best_h.get('identifier')} if best_h else None,
                            ensure_ascii=False),
                 detail))
        conn.execute(
            'UPDATE check_run SET total=?,ok=?,shifted=?,moved=?,missing=?,'
            'extra=?,score=? WHERE id=?',
            (cov.total, cov.ok, cov.shifted, cov.moved, cov.missing,
             cov.extra, cov.score, check_id))
        conn.commit()
        return cov
    finally:
        if own:
            conn.close()


def check_device(serial, plat='douyin', app_version='', conn=None,
                 log=None) -> Coverage:
    """实时校对：dump 当前屏，只校当前页面能校的那部分（不导航、不点击）。"""
    own = conn is None
    conn = conn or connect()
    try:
        T = _templates(plat)
        if T is None:
            return Coverage(plat=plat, serial=serial,
                            scope='该平台还没有模板库，无法校对')
        from .ui_resolve import dump as ui_dump
        nodes = ui_dump(serial) or []
        if not nodes:
            return Coverage(plat=plat, serial=serial, scope='dump 为空')
        page = _page_of(T, nodes)
        if log:
            log('当前页面判定：%s（%d 个元素）' % (page or '未知', len(nodes)))
        expects, rules_map, boxes, version = _load_expects(conn, plat, app_version)
        mine = [e for e in expects if e['page'] == page]
        if not mine:
            return Coverage(plat=plat, serial=serial, app_version=version or '',
                            scope='页面 %s 无期望条目' % (page or '未知'))
        cov = Coverage(plat=plat, app_version=version or '', serial=serial,
                       scope='device:%s/%s' % (serial, page))
        cur = conn.execute(
            'INSERT INTO check_run(serial,plat,app_version,checked_at,scope) '
            'VALUES(?,?,?,?,?)',
            (serial, plat, cov.app_version, time.time(), cov.scope))
        check_id = cur.lastrowid
        size = screen_of(nodes)
        for exp in mine:
            rules = rules_map.get(exp['id']) or []
            if not rules:
                continue
            want = '%s/%s' % (exp['page'], exp['name'])
            v, d, h = _eval_expect(nodes, exp, rules, boxes.get(exp['id']), size)
            cov.total += 1
            if v == 'ok':
                cov.ok += 1
            elif v == 'shifted':
                cov.shifted += 1
            elif v == 'moved':
                cov.moved += 1
            elif v == 'extra':
                cov.extra += 1
            else:
                cov.missing += 1
                if exp['hard_dependency']:
                    cov.hard_missing.append(want)
            cov.items.append({'page': exp['page'], 'name': exp['name'],
                              'verdict': v, 'detail': d})
            conn.execute(
                'INSERT INTO check_item(check_run_id,page,name,verdict,'
                'expected_json,actual_json,detail) VALUES(?,?,?,?,?,?,?)',
                (check_id, exp['page'], exp['name'], v,
                 json.dumps([_parse_group(r['kind'], r['pattern']) for r in rules],
                            ensure_ascii=False),
                 json.dumps({'text': (h or {}).get('text'),
                             'label': (h or {}).get('label'),
                             'identifier': (h or {}).get('identifier')} if h else None,
                            ensure_ascii=False),
                 d))
        conn.execute(
            'UPDATE check_run SET total=?,ok=?,shifted=?,moved=?,missing=?,'
            'extra=?,score=? WHERE id=?',
            (cov.total, cov.ok, cov.shifted, cov.moved, cov.missing,
             cov.extra, cov.score, check_id))
        conn.commit()
        return cov
    finally:
        if own:
            conn.close()


# --------------------------------------------------------------------- 风险审计
def audit_dependencies(conn, plat='douyin', version=None) -> dict:
    """扫出「单点依赖」控件 —— 新机上最先断的那批。

    判定依据两类实测数据：
      · 判据组里有没有「不含 id」的备选组
      · probe.hits_noid：去掉 id 后实测命中数（1=有救，>1=不唯一，0=没救了）

    返回 {'single': [...], 'weak': [...], 'safe': [...]}
    """
    if version:
        rows = conn.execute(
            "SELECT * FROM expect WHERE plat=? AND app_version=? AND status='active'",
            (plat, version)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM expect WHERE plat=? AND status='active' ORDER BY page,name",
            (plat,)).fetchall()
    single, weak, safe = [], [], []
    for exp in rows:
        want = '%s/%s' % (exp['page'], exp['name'])
        rules = conn.execute(
            'SELECT * FROM expect_rule WHERE expect_id=? ORDER BY ord',
            (exp['id'],)).fetchall()
        groups = [_parse_group(r['kind'], r['pattern']) for r in rules]
        non_id = [g for g in groups if not any(k == 'id' for k, _ in g)]
        stat = conn.execute(
            'SELECT MAX(hits_noid) hn, MIN(hits_noid) lo, COUNT(*) n '
            'FROM probe WHERE want=?', (want,)).fetchone()
        hn = stat['hn'] if stat else None
        item = {'want': want, 'page': exp['page'], 'name': exp['name'],
                'hard': bool(exp['hard_dependency']), 'multi': bool(exp['multi']),
                'has_backup': bool(non_id), 'hits_noid': hn,
                'probes': (stat['n'] if stat else 0)}
        if not non_id:
            single.append(item)          # 只有 id 一条命
        elif hn is None or hn != 1:
            weak.append(item)            # 有备选，但实测不唯一 / 没测过
        else:
            safe.append(item)            # 备选实测唯一，可降级
    return {'single': single, 'weak': weak, 'safe': safe}


# --------------------------------------------------------------------- 报表
def markdown_report(cov: Coverage, limit=40) -> str:
    lines = ['# UI 库校对报告', '',
             '- 范围：%s' % cov.scope,
             '- 设备：%s　平台：%s　App 版本：%s'
             % (cov.serial or '-', cov.plat or '-', cov.app_version or '-'),
             '- 结论：%s' % ('可用' if cov.usable else '**该链不可用**（硬依赖缺失）'),
             '', '## 汇总', '', '| 项 | 数 |', '|---|---|',
             '| 合计 | %d |' % cov.total,
             '| 命中(ok) | %d |' % cov.ok,
             '| 换形态(shifted) | %d |' % cov.shifted,
             '| 位移(moved) | %d |' % cov.moved,
             '| 缺失(missing) | %d |' % cov.missing,
             '| 歧义(extra) | %d |' % cov.extra,
             '| 评分 | %.2f |' % cov.score, '',
             '## 非 ok 明细', '', '| 页面 | 控件 | 判定 | 说明 |', '|---|---|---|---|']
    bad = [i for i in cov.items if i['verdict'] != 'ok'][:limit]
    for item in bad:
        lines.append('| %s | %s | %s | %s |' % (item['page'], item['name'],
                                                item['verdict'], item['detail']))
    if not bad:
        lines.append('| - | - | - | 全部命中 |')
    return '\n'.join(lines)
