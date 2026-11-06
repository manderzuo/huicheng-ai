# -*- coding: utf-8 -*-
"""采集时间过滤：相对时间 -> 绝对时间戳，按任务创建时间过滤X天内评论。"""
import re
import time
from datetime import datetime, timedelta

_CN_RE = re.compile(
    r'(刚刚|刚才|\d+\s*秒前|\d+\s*分钟前|\d+\s*小时前|\d+\s*天前|昨天|前天|\d+月\d+日|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月)'
)
_EN_RE = re.compile(r'(just now|\d+\s*s\s*ago|\d+\s*m\s*ago|\d+\s*h\s*ago|\d+\s*d\s*ago|yesterday)', re.I)


def parse_time(text, base_ts=None):
    """相对时间文案 -> 时间戳。解析失败返回 None（调用方视为不过滤）。"""
    if base_ts is None:
        base_ts = time.time()
    base = datetime.fromtimestamp(base_ts)
    t = (text or '').strip().lower().replace('·', ' ')
    if not t:
        return None
    try:
        if t in ('刚刚', '刚才', 'just now'):
            return base_ts
        m = re.search(r'(\d+)\s*秒前|\d+\s*s\s*ago', t)
        if m or '秒前' in t:
            n = int(re.search(r'(\d+)', t).group(1))
            return (base - timedelta(seconds=n)).timestamp()
        if '分钟前' in t or re.search(r'\d+\s*m\s*ago', t):
            n = int(re.search(r'(\d+)', t).group(1))
            return (base - timedelta(minutes=n)).timestamp()
        if '小时前' in t or re.search(r'\d+\s*h\s*ago', t):
            n = int(re.search(r'(\d+)', t).group(1))
            return (base - timedelta(hours=n)).timestamp()
        if '天前' in t or re.search(r'\d+\s*d\s*ago', t):
            n = int(re.search(r'(\d+)', t).group(1))
            return (base - timedelta(days=n)).timestamp()
        if '前天' in t:
            return (base - timedelta(days=2)).timestamp()
        if '昨天' in t or 'yesterday' in t:
            return (base - timedelta(days=1)).timestamp()
        m = re.search(r'(\d+)月(\d+)日', t)
        if m:
            y = base.year
            dt = datetime(y, int(m.group(1)), int(m.group(2)))
            if dt > base:
                dt = datetime(y - 1, int(m.group(1)), int(m.group(2)))
            return dt.timestamp()
        m = re.search(r'(\d{4})[年-](\d{1,2})[月-](\d{1,2})', t)
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp()
        m = re.search(r'(\d{1,2})-(\d{1,2})', t)
        if m:
            y = base.year
            dt = datetime(y, int(m.group(1)), int(m.group(2)))
            if dt > base:
                dt = datetime(y - 1, int(m.group(1)), int(m.group(2)))
            return dt.timestamp()
    except Exception:
        return None
    return None


def within_days(text, max_age_days, base_ts=None):
    """0=不限；解析失败视为通过（不过滤），避免误杀。"""
    try:
        days = int(max_age_days or 0)
    except Exception:
        days = 0
    if not days or days <= 0:
        return True, None
    if base_ts is None:
        base_ts = time.time()
    ts = parse_time(text, base_ts)
    if ts is None:
        return True, None
    cutoff = base_ts - days * 86400
    return (ts >= cutoff), ts
