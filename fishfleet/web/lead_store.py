"""跨平台线索中转站：多台手机共享的客户线索池。

两条铁律：
1. **平台之间不混表**。每个平台一张独立表（leads_douyin / leads_redbook / ...），
   字段完全按该平台能采集到的东西定义，互不影响；将来加平台只加一张表。
2. **共用的是机制，不是数据**。认领、租约、重试上限、状态机、回报这些流程所有平台一致，
   所以抽成通用函数；表结构和业务字段各平台自己的。

认领是原子的（SQLite BEGIN IMMEDIATE），带租约（超时回收）与重试上限，
并记录真实结果：谁领的、发了什么、成功还是失败。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / 'data' / 'lead_pool.sqlite'

DEFAULT_LEASE = 600   # 认领后多久没回报就回收（秒）
MAX_ATTEMPTS = 3

# ---- 通用状态（所有平台一致）
PENDING = 'pending'
CLAIMED = 'claimed'
SENDING = 'sending'
DRAFTED = 'drafted'   # 已定位到人、私信会话已打开、草稿已填好，停在发送键前等人工放行
REPLIED = 'replied'
FAILED = 'failed'
UNKNOWN = 'unknown'
SKIPPED = 'skipped'

ACTIVE = (CLAIMED, SENDING)

# ---- 提取（定位）结果：只描述"有没有找到并核验出这个人"，不描述"发了没有"
FOUND = 'found'
NOT_FOUND_WITHIN_BUDGET = 'not_found_within_budget'
AMBIGUOUS = 'ambiguous'
EXTRACT_ERROR = 'error'
#: 业务上不该接触的人（例如评论人就是视频作者本人）：既不是"没找到"，也不是"出错"
EXTRACT_SKIPPED = 'skipped'
EXTRACT_RESULTS = (FOUND, NOT_FOUND_WITHIN_BUDGET, AMBIGUOUS, EXTRACT_ERROR,
                   EXTRACT_SKIPPED)

# ---- 所有平台都有的流程字段（不含业务内容）
BASE_COLUMNS = [
    ('id', "TEXT PRIMARY KEY"),
    ('status', "TEXT NOT NULL DEFAULT 'pending'"),
    ('claimed_by', "TEXT DEFAULT ''"),
    ('claimed_at', 'REAL DEFAULT 0'),
    ('lease_until', 'REAL DEFAULT 0'),
    ('attempts', 'INTEGER NOT NULL DEFAULT 0'),
    ('reply_text', "TEXT DEFAULT ''"),
    ('dm_text', "TEXT DEFAULT ''"),
    ('sent_at', 'REAL DEFAULT 0'),
    ('error', "TEXT DEFAULT ''"),
    ('collected_serial', "TEXT DEFAULT ''"),
    ('collected_at', 'REAL DEFAULT 0'),
    ('updated_at', 'REAL DEFAULT 0'),
    ('archived_at', 'REAL DEFAULT 0'),  # 仅标记归档，保留原行及 UNIQUE 去重键
    # ---- 提取（B 端定位）留痕：谁领的、谁真正执行的、结果与证据都要能审计
    # extracted_by 与 claimed_by 分开：认领的手机和执行定位的手机可能不是同一台，
    # 不能假设相等（例如认领后转交、或同一台机器换账号继续）。
    ('extract_result', "TEXT DEFAULT ''"),
    ('extract_evidence', "TEXT DEFAULT '{}'"),
    ('extract_seconds', 'REAL DEFAULT 0'),
    ('extracted_by', "TEXT DEFAULT ''"),
    ('extracted_at', 'REAL DEFAULT 0'),
]

# ---- 每个平台自己的业务字段：能采到什么就定义什么，不迁就别的平台
PLATFORM_COLUMNS = {
    # 抖音：视频作者仅为来源；联系对象必须使用从该评论直接进入主页核验的评论人账号
    'douyin': [
        ('comment_author', "TEXT NOT NULL DEFAULT ''"),   # 评论人昵称
        ('comment_text', "TEXT NOT NULL DEFAULT ''"),     # 评论正文
        ('commenter_douyin_id', "TEXT DEFAULT ''"),
    ('commenter_uid', "TEXT DEFAULT ''"),        # 评论发布者账号，绝不从视频作者复制
        ('commenter_nickname', "TEXT DEFAULT ''"),
        ('commenter_identity_source', "TEXT DEFAULT ''"),
        ('commenter_identity_evidence', "TEXT DEFAULT '{}'"),
        ('region', "TEXT DEFAULT ''"),                    # 评论 IP 属地
        ('time_text', "TEXT DEFAULT ''"),                 # 采集时的相对时间（会漂移，只作参考）
        ('video_title', "TEXT DEFAULT ''"),               # 视频标题/描述
        ('video_author', "TEXT DEFAULT ''"),              # 视频作者昵称（会重名）
        ('author_douyin_id', "TEXT DEFAULT ''"),          # 视频作者抖音号（唯一，跨机定位用）
        ('author_identity_source', "TEXT DEFAULT ''"),    # 取号路径；历史空值不是核验通过
        ('author_identity_evidence', "TEXT DEFAULT '{}'"), # 本视频直接导航及返回核验事实
        ('keyword', "TEXT DEFAULT ''"),                   # 采集时用的搜索词
        ('filters', "TEXT DEFAULT '{}'"),                 # 采集时的筛选条件 JSON
    ],
    # 小红书：联系对象是评论人，author_red_id 存评论人的小红书号（唯一键）
    'redbook': [
        ('comment_author', "TEXT NOT NULL DEFAULT ''"),
        ('comment_text', "TEXT NOT NULL DEFAULT ''"),
        ('region', "TEXT DEFAULT ''"),
        ('time_text', "TEXT DEFAULT ''"),
        ('note_title', "TEXT DEFAULT ''"),
        ('note_author', "TEXT DEFAULT ''"),
        ('author_red_id', "TEXT DEFAULT ''"),             # 评论人的小红书号（联系对象）
        ('note_id', "TEXT DEFAULT ''"),
        ('keyword', "TEXT DEFAULT ''"),
        ('filters', "TEXT DEFAULT '{}'"),
        # 2026-11-01 新增：昵称核验状态。unknown 表示读不到昵称、未验证，不能冒充已核验
        ('verify', "TEXT DEFAULT ''"),
        ('note_author_red_id', "TEXT DEFAULT ''"),
    ('commenter_user_id', "TEXT DEFAULT ''"),        # 笔记作者的小红书号（仅溯源）
    ],
    # B 站：aid 与 commenter_uid 是私信方案的两个硬锚点（都从系统 Intent 读，100% 可靠）
    'bilibili': [
        ('comment_author', "TEXT NOT NULL DEFAULT ''"),
        ('comment_text', "TEXT NOT NULL DEFAULT ''"),
        ('time_text', "TEXT DEFAULT ''"),
        ('video_title', "TEXT DEFAULT ''"),
        ('video_author', "TEXT DEFAULT ''"),
        ('aid', "TEXT DEFAULT ''"),            # 视频 ID，bilibili://video/av<aid>
        ('up_uid', "TEXT DEFAULT ''"),         # UP 的 UID
        ('commenter_uid', "TEXT DEFAULT ''"),  # 评论者 UID，bilibili://space/<uid> 直达私信
        ('bvid', "TEXT DEFAULT ''"),
        ('keyword', "TEXT DEFAULT ''"),
    ],
    # TikTok：@handle 是唯一可核验身份（等价抖音号），跨机型定位锚点
    'tiktok': [
        ('comment_author', "TEXT NOT NULL DEFAULT ''"),      # 评论人昵称
        ('comment_text', "TEXT NOT NULL DEFAULT ''"),        # 评论正文
        ('commenter_handle', "TEXT DEFAULT ''"),             # 评论人 @handle（唯一，联系对象）
        ('commenter_nickname', "TEXT DEFAULT ''"),           # 评论人主页昵称
        ('commenter_identity_source', "TEXT DEFAULT ''"),    # 取号路径
        ('commenter_identity_evidence', "TEXT DEFAULT '{}'"),# 往返核验事实
        ('time_text', "TEXT DEFAULT ''"),                    # 评论时间（相对/绝对）
        ('video_desc', "TEXT DEFAULT ''"),                   # 视频描述
        ('video_author', "TEXT DEFAULT ''"),                 # 视频作者昵称
        ('author_handle', "TEXT DEFAULT ''"),                # 视频作者 @handle（仅溯源）
        ('keyword', "TEXT DEFAULT ''"),
    ],
    # Facebook：**没有可从 UI 稳定读到的用户 ID**，这是它和抖音/小红书最大的差别。
    # 所以联系对象只能靠「昵称 + 帖子上下文」组合定位，且**昵称会重名、会改**。
    # 这里如实记录这个限制，不用别的平台那套"唯一号"假设。
    'facebook': [
        ('comment_author', "TEXT NOT NULL DEFAULT ''"),      # 评论人昵称（可能重名、可能变）
        ('comment_text', "TEXT NOT NULL DEFAULT ''"),        # 评论正文
        # **唯一身份**（2026-11-02 17:20 突破）
        # 对方主页 →「更多個人檔案設定」里直接显示：
        #   https://www.facebook.com/profile.php?id=61553716394951
        # 这个数字 ID 就是 FB 的 UID，等价抖音号，可跨手机精确复现（实测 open_url 直达主页）。
        ('commenter_profile_id', "TEXT DEFAULT ''"),         # profile.php?id= 后面的数字
        ('commenter_profile_url', "TEXT DEFAULT ''"),        # 完整链接
        ('comment_likes', "TEXT DEFAULT ''"),                # 该评论的点赞数（文本形态）
        ('comment_time', "TEXT DEFAULT ''"),                 # 评论时间（相对时间，会漂移）
        ('has_replies', "TEXT DEFAULT ''"),                  # 是否有"查看 N 条回复"
        ('post_author', "TEXT DEFAULT ''"),                  # 帖子作者昵称
        ('post_text', "TEXT DEFAULT ''"),                    # 帖子正文摘要
        ('post_kind', "TEXT DEFAULT ''"),                    # video / photo / text
        ('post_comment_count', "TEXT DEFAULT ''"),           # 帖子标注的评论总数
        ('post_url', "TEXT DEFAULT ''"),                     # 若拿到可分享链接则记录
        ('commenter_identity_source', "TEXT DEFAULT ''"),
        ('commenter_identity_evidence', "TEXT DEFAULT '{}'"),
        ('keyword', "TEXT DEFAULT ''"),
        ('collected_page', "TEXT DEFAULT ''"),               # 在哪个页面采到（home/post_detail/...）
    ],
}

# ---- 每个平台的去重键：同一平台内什么算"同一条线索"
DEDUPE_COLUMNS = {
    'douyin': ['comment_author', 'comment_text', 'video_title'],
    'redbook': ['comment_author', 'comment_text', 'note_id'],
    # B站 按「人」去重：同一个人在多个视频下评论，对私信来说只该联系一次。
    # 之前是 author+text+aid，同一人换视频就绕过判重了（实测 988 行里有 7 人重复）。
    # 带上 comment_author 是因为 uid 可能为空——空 uid 时退回按昵称判重。
    'bilibili': ['commenter_uid', 'comment_author'],
    'tiktok': ['comment_author', 'comment_text', 'video_desc'],
    # Facebook 没有唯一 ID，去重只能靠「作者+正文+帖子作者」，宁可多留一条也不误合并。
    'facebook': ['comment_author', 'comment_text', 'post_author'],
}

APP_PACKAGES = {
    'douyin': 'com.ss.android.ugc.aweme',
    'redbook': 'com.xingin.xhs',
    'bilibili': 'tv.danmaku.bili',
    'instagram': 'com.instagram.android',
    'x': 'com.twitter.android',
    'facebook': 'com.facebook.katana',
    'tiktok': 'com.zhiliaoapp.musically',
}

_LOCK = threading.RLock()
_conn: sqlite3.Connection | None = None


def table(platform: str) -> str:
    if not platform or not platform.replace('_', '').isalnum():
        raise ValueError('平台标识不合法：%r' % (platform,))
    return 'leads_' + platform


def _columns(platform: str):
    if platform not in PLATFORM_COLUMNS:
        raise ValueError('未登记的平台：%s（先在 PLATFORM_COLUMNS 里定义表结构）' % platform)
    return BASE_COLUMNS + PLATFORM_COLUMNS[platform]


def _db():
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=15)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
        _conn.execute('PRAGMA synchronous=NORMAL')
        _conn.commit()
    return _conn


def ensure(platform: str) -> str:
    """按平台 schema 建表（幂等），并补齐后加的列。返回表名。"""
    name = table(platform)
    cols = _columns(platform)
    with _LOCK:
        conn = _db()
        ddl = ', '.join('%s %s' % (c, d) for c, d in cols)
        conn.execute('CREATE TABLE IF NOT EXISTS %s (%s)' % (name, ddl))
        have = {row['name'] for row in conn.execute('PRAGMA table_info(%s)' % name)}
        for col, decl in cols:
            if col not in have:
                conn.execute('ALTER TABLE %s ADD COLUMN %s %s' % (name, col, decl))
        keys = DEDUPE_COLUMNS.get(platform) or []
        if keys:
            # 用「部分索引」把 skipped 排除在唯一约束之外：
            # 被判重后标记 skipped 的行要留在表里做审计痕迹，
            # 但普通唯一索引会把它们也算进去，导致新数据插不进来。
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_%s_key ON %s (%s) "
                "WHERE status != 'skipped'"
                % (platform, name, ', '.join(keys)))
        conn.execute('CREATE INDEX IF NOT EXISTS idx_%s_status ON %s (status)' % (platform, name))
        # claim() 按 collected_at 排序取最早/最新的待办；没有这个索引时
        # 每次都要全表扫描 + 排序（实测 906 行时 3.7ms，行数涨上去会线性变差）。
        conn.execute('CREATE INDEX IF NOT EXISTS idx_%s_collected ON %s (collected_at)'
                     % (platform, name))
        conn.commit()
    return name


def known_platforms() -> list[str]:
    return list(PLATFORM_COLUMNS)


def _now() -> float:
    return time.time()


def _row(row) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    for key in ('filters', 'author_identity_evidence', 'commenter_identity_evidence',
                'extract_evidence'):
        if key in data and isinstance(data[key], str):
            try:
                data[key] = json.loads(data[key] or '{}')
            except ValueError:
                data[key] = {}
    return data


# ---------------------------------------------------------------- 入库
def add_leads(platform, rows, serial='', reply_text='', dm_text='') -> dict:
    """按平台表结构入库；缺的字段用默认值，未知字段忽略（写进别的平台的表没有意义）。"""
    name = ensure(platform)
    allowed = {c for c, _ in PLATFORM_COLUMNS[platform]}
    added = duplicated = skipped = 0
    with _LOCK:
        conn = _db()
        with conn:
            for row in rows or []:
                data = {k: row.get(k) for k in allowed if row.get(k) is not None}
                if not str(data.get('comment_author') or '').strip() \
                        or not str(data.get('comment_text') or '').strip():
                    skipped += 1
                    continue
                data['id'] = row.get('id') or uuid.uuid4().hex[:16]
                data['collected_serial'] = serial
                data['collected_at'] = _now()
                data['updated_at'] = _now()
                data['status'] = PENDING
                # 逐条草稿优先：同一次采集里每个人文案不同，不能都用同一个参数覆盖
                data['reply_text'] = str(row.get('reply_text') or reply_text or '')[:1000]
                data['dm_text'] = str(row.get('dm_text') or dm_text or '')[:1000]
                for json_field in ('filters', 'author_identity_evidence', 'commenter_identity_evidence'):
                    if isinstance(data.get(json_field), dict):
                        data[json_field] = json.dumps(data[json_field], ensure_ascii=False)
                cols = list(data)
                sql = 'INSERT INTO %s (%s) VALUES (%s)' % (
                    name, ', '.join(cols), ', '.join('?' * len(cols)))
                try:
                    conn.execute(sql, [data[c] for c in cols])
                    added += 1
                except sqlite3.IntegrityError:
                    duplicated += 1
    return {'added': added, 'duplicated': duplicated, 'skipped': skipped}


# ---------------------------------------------------------------- 认领
def batches(platform, days: int = 60) -> list[dict]:
    """按「采集日期 + 关键词」汇总线索，供「选择任务再回复」使用。

    2026-11-06 主人要求：回复入口从「截流」里拆出来，能选具体哪一批任务来回复，
    批次以**日期 + 关键词**区分。

    线索表本来就有 `keyword` 和 `collected_at`（unix 时间戳），直接按这两列分组即可。
    返回每批的：关键词、日期、总数、各状态计数（pending/drafted/failed/skipped…）。
    """
    # 平台别名：前端/任务接口用的是 `xiaohongshu`，而线索表登记名是 `redbook`。
    # 不映射会报「未登记的平台：xiaohongshu」（主人实测，2026-11-06）。
    _ALIAS = {'xiaohongshu': 'redbook', 'xhs': 'redbook', 'dy': 'douyin',
              'bili': 'bilibili', 'fb': 'facebook', 'tk': 'tiktok'}
    raw = str(platform or '').strip()
    platform = _ALIAS.get(raw.lower(), raw)
    name = ensure(platform)
    since = _now() - max(1, int(days)) * 86400
    with _LOCK:
        conn = _db()
        rows = conn.execute(
            "SELECT COALESCE(keyword,'') AS kw, "
            "date(COALESCE(collected_at,0),'unixepoch','localtime') AS day, "
            "COALESCE(status,'') AS st, COUNT(*) AS n "
            "FROM %s WHERE COALESCE(archived_at,0)=0 AND COALESCE(collected_at,0)>=? "
            "GROUP BY kw, day, st" % name, (since,)).fetchall()

    agg: dict = {}
    for r in rows:
        key = (r['day'], r['kw'])
        item = agg.get(key)
        if item is None:
            item = {'day': r['day'], 'keyword': r['kw'], 'total': 0, 'by_status': {}}
            agg[key] = item
        item['total'] += r['n']
        item['by_status'][r['st']] = item['by_status'].get(r['st'], 0) + r['n']
    out = list(agg.values())
    for it in out:
        bs = it['by_status']
        it['pending'] = bs.get(PENDING, 0)
        it['drafted'] = bs.get(DRAFTED, 0)
        it['replied'] = bs.get(REPLIED, 0)
        it['failed'] = bs.get('failed', 0)
        it['skipped'] = bs.get('skipped', 0)
    # 新的排前面；同一天按关键词
    out.sort(key=lambda x: (x['day'], x['keyword']), reverse=True)
    return out


def claim(platform, serial, lease=DEFAULT_LEASE, require_nonempty=(),
          skip_done_accounts=False, newest_first=False, batch=None) -> dict | None:
    """原子认领一条该平台的待办线索。

    batch：{'day': '2026-11-06', 'keyword': 'iphone duo'} 形式。
           传了就**只在这一批里领**——这是「选择任务再回复」的落点，
           否则 reply 会从整个池子里捞，用户根本没法只回某一批。
           两项都可空：只给 keyword 就按关键词过滤，只给 day 就按日期过滤。

    require_nonempty 要求指定列非空（例如提取场景要求 commenter_douyin_id）：
    历史旧数据没有抖音号，认领了也必然失败，不该占掉执行位。

    skip_done_accounts 排除"已经对同一个人完成过提取"的线索：
    采集时同一个人可能在不同视频/不同评论下产生多条线索，不去重就会
    对着同一个人重复开会话、重复填草稿（实测已发生，被旧草稿校验拦下）。
    """
    if not serial:
        raise ValueError('认领需要手机标识')
    name = ensure(platform)
    allowed = {c for c, _ in _columns(platform)}
    filters = []
    for col in require_nonempty:
        if col not in allowed:
            raise ValueError('未登记的列：%s' % col)
        filters.append("COALESCE(%s,'')<>''" % col)
    where = 'status=? AND COALESCE(archived_at,0)=0'
    args: list = [PENDING]
    if filters:
        where += ' AND ' + ' AND '.join(filters)
    # 2026-11-06：按批次（日期 + 关键词）限定，给「选择任务再回复」用。
    if batch:
        _kw = str(batch.get('keyword') or '').strip()
        _day = str(batch.get('day') or '').strip()
        if _kw:
            where += " AND COALESCE(keyword,'')=?"
            args.append(_kw)
        if _day:
            where += " AND date(COALESCE(collected_at,0),'unixepoch','localtime')=?"
            args.append(_day)
    if skip_done_accounts:
        if 'commenter_douyin_id' not in allowed:
            raise ValueError('该平台没有 commenter_douyin_id，无法按人去重')
        # drafted（草稿已就绪等人工发）/ replied（已回复）都算"已完成过这个人"
        where += (" AND COALESCE(commenter_douyin_id,'')<>''"
                  ' AND commenter_douyin_id NOT IN ('
                  'SELECT commenter_douyin_id FROM %s WHERE status IN (?,?)'
                  " AND COALESCE(commenter_douyin_id,'')<>'')" % name)
        args += [DRAFTED, REPLIED]
    reclaim_expired(platform)
    now = _now()
    # newest_first：优先领最新的线索。老线索（评论是几个月甚至一年前的）
    # 抖音号往往已经改名/换号，实测找不到的比例很高，每次都白烧 100+ 秒。
    order = 'collected_at DESC' if newest_first else 'collected_at ASC'
    with _LOCK:
        conn = _db()
        conn.execute('BEGIN IMMEDIATE')
        try:
            row = conn.execute(
                'SELECT * FROM %s WHERE %s ORDER BY %s LIMIT 1' % (name, where, order),
                args).fetchone()
            if row is None:
                conn.execute('ROLLBACK')
                return None
            conn.execute(
                'UPDATE %s SET status=?,claimed_by=?,claimed_at=?,lease_until=?,'
                'attempts=attempts+1,updated_at=? WHERE id=? AND status=?' % name,
                (CLAIMED, serial, now, now + lease, now, row['id'], PENDING))
            conn.execute('COMMIT')
        except Exception:
            conn.execute('ROLLBACK')
            raise
        return _row(conn.execute('SELECT * FROM %s WHERE id=?' % name, (row['id'],)).fetchone())


def renew(platform, lead_id, serial, lease=DEFAULT_LEASE) -> bool:
    name = ensure(platform)
    with _LOCK:
        conn = _db()
        with conn:
            cur = conn.execute(
                'UPDATE %s SET lease_until=?,updated_at=? WHERE id=? AND claimed_by=? '
                'AND status IN (?,?)' % name,
                (_now() + lease, _now(), lead_id, serial, CLAIMED, SENDING))
            return (cur.rowcount or 0) == 1


def reclaim_expired(platform, max_attempts=MAX_ATTEMPTS) -> int:
    """租约过期的任务收回；超过重试上限的直接放弃，避免反复骚扰同一个人。"""
    name = ensure(platform)
    now = _now()
    with _LOCK:
        conn = _db()
        with conn:
            cur = conn.execute(
                'UPDATE %s SET status=?,claimed_by=?,error=?,updated_at=? '
                'WHERE status IN (?,?) AND lease_until>0 AND lease_until<? AND attempts>=?' % name,
                (SKIPPED, '', '多次认领都没有回报结果，已放弃', now,
                 CLAIMED, SENDING, now, max_attempts))
            cur2 = conn.execute(
                'UPDATE %s SET status=?,claimed_by=?,claimed_at=0,lease_until=0,error=?,updated_at=? '
                'WHERE status IN (?,?) AND lease_until>0 AND lease_until<? AND attempts<?' % name,
                (PENDING, '', '上一个认领超时未回报，已退回待办', now,
                 CLAIMED, SENDING, now, max_attempts))
            return (cur.rowcount or 0) + (cur2.rowcount or 0)


def begin_send(platform, lead_id, serial) -> bool:
    name = ensure(platform)
    with _LOCK:
        conn = _db()
        with conn:
            cur = conn.execute(
                'UPDATE %s SET status=?,updated_at=? WHERE id=? AND claimed_by=? AND status=?'
                % name, (SENDING, _now(), lead_id, serial, CLAIMED))
            return (cur.rowcount or 0) == 1


def finish(platform, lead_id, serial, status, reply_text=None, error='') -> bool:
    """回报结果。**保留 claimed_by**：谁做的必须留痕，不能被完成动作抹掉。"""
    if status not in (DRAFTED, REPLIED, FAILED, UNKNOWN, SKIPPED):
        raise ValueError('不支持的结果状态：%s' % status)
    name = ensure(platform)
    with _LOCK:
        conn = _db()
        with conn:
            cur = conn.execute(
                'UPDATE %s SET status=?,lease_until=0,updated_at=?,'
                'reply_text=COALESCE(?,reply_text),sent_at=?,error=? '
                'WHERE id=? AND claimed_by=? AND status IN (?,?)' % name,
                (status, _now(), reply_text,
                 _now() if status in (REPLIED, UNKNOWN) else 0,
                 str(error or '')[:500], lead_id, serial, CLAIMED, SENDING))
            return (cur.rowcount or 0) == 1


def record_extract(platform, lead_id, serial, result, evidence=None, seconds=0.0,
                   status=None) -> bool:
    """记录一次「提取（定位）」的结果。

    · result 只回答"有没有在预算内找到并核验出这个人"，不回答"发没发消息"。
    · extracted_by 是**实际执行定位的手机**，可以和 claimed_by 不同，不假设相等。
    · 默认收尾：found → drafted（草稿已填好、停在发送键前）；skipped → skipped
      （业务上不该接触的人，不占重试次数）；其余 → failed。
      调用方可用 status 显式覆盖。
    · 只允许在已认领（claimed/sending）状态下写入，防止事后篡改历史行。
    """
    if result not in EXTRACT_RESULTS:
        raise ValueError('不支持的提取结果：%s' % result)
    if not serial:
        raise ValueError('记录提取结果必须写明实际执行手机')
    if status is None:
        if result == FOUND:
            status = DRAFTED
        elif result == EXTRACT_SKIPPED:
            status = SKIPPED
        else:
            # failed 不会被 claim 再领到（claim 只找 pending），因此不存在
            # "自动无限重试"的问题；需要重试必须人工把状态复位为 pending。
            status = FAILED
    if status not in (DRAFTED, FAILED, UNKNOWN, SKIPPED):
        raise ValueError('不支持的提取收尾状态：%s' % status)
    name = ensure(platform)
    payload = json.dumps(evidence or {}, ensure_ascii=False)
    with _LOCK:
        conn = _db()
        with conn:
            cur = conn.execute(
                'UPDATE %s SET status=?,lease_until=0,updated_at=?,extract_result=?,'
                'extract_evidence=?,extract_seconds=?,extracted_by=?,extracted_at=? '
                'WHERE id=? AND status IN (?,?)' % name,
                (status, _now(), result, payload, float(seconds or 0), serial, _now(),
                 lead_id, CLAIMED, SENDING))
            return (cur.rowcount or 0) == 1


def release(platform, lead_id, serial, note='') -> bool:
    name = ensure(platform)
    with _LOCK:
        conn = _db()
        with conn:
            # attempts 归零：主动放回不等于失败重试。
            # 实测 BUG——认领→放回 的循环会让 attempts 一直累加，
            # 一旦某次真超时，就会被误判成「多次无回报」直接放弃。
            cur = conn.execute(
                'UPDATE %s SET status=?,claimed_by=?,claimed_at=0,lease_until=0,attempts=0,'
                'error=?,updated_at=? '
                'WHERE id=? AND claimed_by=? AND status=?' % name,
                (PENDING, '', str(note or '')[:500], _now(), lead_id, serial, CLAIMED))
            return (cur.rowcount or 0) == 1


# ---------------------------------------------------------------- 查询
def listing(platform, status='', limit=200, offset=0) -> list[dict]:
    name = ensure(platform)
    sql = 'SELECT * FROM %s WHERE COALESCE(archived_at,0)=0' % name
    args: list = []
    if status and status != 'all':
        sql += ' AND status=?'
        args.append(status)
    sql += ' ORDER BY collected_at DESC LIMIT ? OFFSET ?'
    args += [limit, offset]
    with _LOCK:
        return [_row(r) for r in _db().execute(sql, args).fetchall()]


def stats(platform='') -> dict:
    """单平台或全部平台的计数。平台之间分开统计，不混算业务数据。"""
    targets = [platform] if platform and platform != 'all' else known_platforms()
    result = {'platforms': {}, 'total': 0}
    for name in targets:
        ensure(name)
        reclaim_expired(name)
        with _LOCK:
            conn = _db()
            rows = conn.execute('SELECT status,COUNT(*) AS n FROM %s WHERE COALESCE(archived_at,0)=0 GROUP BY status'
                                % table(name)).fetchall()
            counts = {r['status']: r['n'] for r in rows}
            by_serial = conn.execute(
                "SELECT claimed_by AS serial,COUNT(*) AS n FROM %s "
                "WHERE COALESCE(archived_at,0)=0 AND status IN ('replied','failed','unknown','sending') AND claimed_by<>'' "
                'GROUP BY claimed_by' % table(name)).fetchall()
        total = sum(counts.values())
        result['platforms'][name] = {
            'total': total,
            'pending': counts.get(PENDING, 0),
            'claimed': counts.get(CLAIMED, 0),
            'sending': counts.get(SENDING, 0),
            'drafted': counts.get(DRAFTED, 0),
            'replied': counts.get(REPLIED, 0),
            'failed': counts.get(FAILED, 0),
            'unknown': counts.get(UNKNOWN, 0),
            'skipped': counts.get(SKIPPED, 0),
            'by_serial': {r['serial']: r['n'] for r in by_serial},
        }
        result['total'] += total
    return result


def count_by_video(platform, video_title) -> int:
    """统计某个视频已经采到多少条线索。

    采集侧用它判断"这个视频是不是已经采够了"：实测同一个视频第 1 轮能出
    15 条，第 4 轮只剩 1 条却仍要花近 8 分钟（评论要重扫一遍）。
    """
    title = str(video_title or '').strip()
    if not title:
        return 0
    name = ensure(platform)
    if 'video_title' not in {c for c, _ in _columns(platform)}:
        return 0
    with _LOCK:
        row = _db().execute(
            'SELECT COUNT(*) FROM %s WHERE video_title=?' % name, (title,)).fetchone()
    return int(row[0]) if row else 0


def exists_lead(platform, row) -> bool:
    """按该平台的去重键，判断这条线索是否已经在池里。

    采集侧用它「取号前先查重」：已经在池里的评论不必再进一次主页取号
    （实测进主页取号约 6.5 秒/条，重跑同一关键词时几乎全部重复）。
    判重键与 `DEDUPE_COLUMNS` 保持一致，避免"查重与入库口径不一致"。
    """
    keys = DEDUPE_COLUMNS.get(platform) or []
    if not keys:
        return False
    name = ensure(platform)
    where = ' AND '.join('%s=?' % key for key in keys)
    args = [str(row.get(key) or '') for key in keys]
    if not any(args):
        return False
    with _LOCK:
        found = _db().execute(
            'SELECT 1 FROM %s WHERE %s LIMIT 1' % (name, where), args).fetchone()
    return found is not None


def set_author_id(platform, video_author, douyin_id) -> int:
    """禁止按昵称批量回填账号；必须用原视频身份闭环与精确记录标识更新。"""
    raise ValueError('禁止仅凭昵称批量补作者账号：昵称不是唯一身份')


def exists_field(platform, column, value) -> bool:
    """按某个字段查是否已有记录（用于「同一个人只进池一次」这类业务去重）。

    只允许查已登记过的列，避免拼出任意 SQL。
    """
    name = ensure(platform)
    allowed = {c for c, _ in _columns(platform)}
    if column not in allowed:
        raise ValueError('未登记的列：%s' % column)
    if not str(value or '').strip():
        return False
    with _LOCK:
        row = _db().execute('SELECT 1 FROM %s WHERE %s=? LIMIT 1' % (name, column),
                            (str(value),)).fetchone()
    return row is not None


def get(platform, lead_id) -> dict | None:
    name = ensure(platform)
    with _LOCK:
        return _row(_db().execute('SELECT * FROM %s WHERE id=?' % name, (lead_id,)).fetchone())


def reset(platform='') -> int:
    targets = [platform] if platform and platform != 'all' else known_platforms()
    removed = 0
    with _LOCK:
        conn = _db()
        with conn:
            for name in targets:
                ensure(name)
                removed += conn.execute('DELETE FROM %s' % table(name)).rowcount or 0
    return removed


# ---------------------------------------------------------------- 高级线索池（独立连接，不占采集的 Python 全局锁）
POOL_STATUSES = (PENDING, CLAIMED, SENDING, DRAFTED, REPLIED, FAILED, UNKNOWN, SKIPPED)
ARCHIVABLE_STATUSES = (REPLIED, FAILED, SKIPPED)


def pool_filters(platform='all', status='all', q='', archived='active',
                 start_date='', end_date=''):
    """上海自然日，右端开区间；不把今天偷偷施加给旧调用方。"""
    from datetime import date, datetime, time as daytime, timedelta, timezone
    platform, status = platform or 'all', status or 'all'
    if platform != 'all' and platform not in PLATFORM_COLUMNS:
        raise ValueError('未登记的平台：%s' % platform)
    if status != 'all' and status not in POOL_STATUSES:
        raise ValueError('不支持的状态：%s' % status)
    if archived not in ('active', 'archived', 'all'):
        raise ValueError('archived 必须是 active、archived 或 all')
    bounds = []
    for value, last in ((start_date, False), (end_date, True)):
        if not value:
            bounds.append(None)
            continue
        try:
            day = date.fromisoformat(value)
            if day.isoformat() != value:
                raise ValueError()
            if last:
                day += timedelta(days=1)
            bounds.append(datetime.combine(day, daytime(), timezone(timedelta(hours=8))).timestamp())
        except (ValueError, OverflowError):
            raise ValueError('日期必须为有效的 YYYY-MM-DD') from None
    if start_date and end_date and start_date > end_date:
        raise ValueError('start_date 不能晚于 end_date')
    return dict(platform=platform, status=status, q=str(q or '').strip(), archived=archived,
                start_date=start_date or '', end_date=end_date or ''), bounds


_POOL_SCHEMA_KEY = None


def _pool_connection():
    # 惰性迁移且按数据库/连接缓存；普通读请求不重复 DDL 与 commit。
    global _POOL_SCHEMA_KEY
    with _LOCK:
        key = (str(DB_PATH.resolve()), id(_conn))
        if _POOL_SCHEMA_KEY != key:
            for platform in known_platforms():
                ensure(platform)
            _POOL_SCHEMA_KEY = (str(DB_PATH.resolve()), id(_conn))
    conn = sqlite3.connect(DB_PATH.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _pool_sql(filters, bounds, columns=None):
    targets = known_platforms() if filters['platform'] == 'all' else [filters['platform']]
    columns = columns or list(dict.fromkeys(c for p in known_platforms() for c, _ in _columns(p)))
    selects, args = [], []
    for platform in targets:
        own = {c for c, _ in _columns(platform)}
        fields = ','.join('"%s"' % c if c in own else 'NULL AS "%s"' % c for c in columns)
        where, values = ['1=1'], []
        if filters['archived'] != 'all':
            where.append('COALESCE(archived_at,0)' + ('=0' if filters['archived'] == 'active' else '>0'))
        if filters['status'] != 'all':
            where.append('status=?')
            values.append(filters['status'])
        for bound, op in zip(bounds, ('>=', '<')):
            if bound is not None:
                where.append('collected_at' + op + '?')
                values.append(bound)
        if filters['q']:
            # instr 而非 LIKE：用户输入的 % / _ 是字面字符，参数化防注入。
            where.append('(' + ' OR '.join('instr(lower(COALESCE(CAST("%s" AS TEXT),\'\')),lower(?))>0' % c for c in own) + ')')
            values += [filters['q']] * len(own)
        selects.append("SELECT '%s' AS platform,%s FROM %s WHERE %s" %
                       (platform, fields, table(platform), ' AND '.join(where)))
        args.extend(values)
    return ' UNION ALL '.join(selects), args, columns


def pool_query(platform='all', status='all', q='', archived='active',
               start_date='', end_date='', limit=100, offset=0):
    filters, bounds = pool_filters(platform, status, q, archived, start_date, end_date)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('limit 必须为正整数')
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError('offset 必须为非负整数')
    sql, args, _ = _pool_sql(filters, bounds)
    conn = _pool_connection()
    try:
        conn.execute('BEGIN')
        groups = conn.execute('SELECT platform,status,COUNT(*) AS n FROM (' + sql +
                              ') GROUP BY platform,status', args).fetchall()
        stats_result = {'total': 0, 'platforms': {}}
        targets = known_platforms() if filters['platform'] == 'all' else [filters['platform']]
        for p in targets:
            stats_result['platforms'][p] = dict(total=0, **{s: 0 for s in POOL_STATUSES})
        for row in groups:
            counts = stats_result['platforms'][row['platform']]
            counts[row['status']] = row['n']
            counts['total'] += row['n']
            stats_result['total'] += row['n']
        records = conn.execute('SELECT * FROM (' + sql +
                               ') ORDER BY collected_at DESC,platform ASC,id ASC LIMIT ? OFFSET ?',
                               args + [limit, offset]).fetchall()
        rows = []
        for record in records:
            own = {c for c, _ in _columns(record['platform'])} | {'platform'}
            rows.append(_row({k: record[k] for k in record.keys() if k in own}))
        return dict(rows=rows, total=stats_result['total'], limit=limit, offset=offset,
                    stats=stats_result, filters=filters, timezone='Asia/Shanghai')
    finally:
        conn.close()


def pool_capacity():
    import shutil
    conn = _pool_connection()
    try:
        conn.execute('BEGIN')
        platforms = {}
        for p in known_platforms():
            row = conn.execute('SELECT COUNT(*) AS total,SUM(CASE WHEN COALESCE(archived_at,0)>0 '
                               'THEN 1 ELSE 0 END) AS archived FROM ' + table(p)).fetchone()
            platforms[p] = dict(total=row['total'], archived=row['archived'] or 0,
                                active=row['total'] - (row['archived'] or 0))
    finally:
        conn.close()
    def size(suffix):
        try:
            return Path(str(DB_PATH) + suffix).stat().st_size
        except FileNotFoundError:
            return 0
    sizes = dict(main_bytes=size(''), wal_bytes=size('-wal'), shm_bytes=size('-shm'))
    return dict(**sizes, total_bytes=sum(sizes.values()),
                disk_free_bytes=shutil.disk_usage(DB_PATH.parent).free,
                platforms=platforms, metadata_only=True, archive_reclaims_disk=False)


def pool_export_snapshot(filters, bounds):
    """固定起始读快照，备份至临时磁盘；网络慢下载不延长源库事务或采集锁。"""
    import tempfile
    import os
    fd, filename = tempfile.mkstemp(prefix='lead-pool-export-', suffix='.sqlite')
    os.close(fd)
    source = target = None
    completed = False
    try:
        source = _pool_connection()
        source.execute('BEGIN')
        source.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()  # 固定 WAL read mark
        target = sqlite3.connect(filename)
        source.backup(target, pages=256, sleep=0.01)
        target.close()
        target = None
        completed = True
        return Path(filename)
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()
        if not completed:
            Path(filename).unlink(missing_ok=True)
