"""线索池管理 API：不启动设备、不自动删除、不在模块导入时打开数据库。"""
from __future__ import annotations

import csv
import io
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StrictBool
from starlette.background import BackgroundTask

from . import lead_store as store

router = APIRouter(prefix='/api/lead-pool', tags=['lead-pool'])
_TOKEN_LOCK = threading.Lock()
_TOKENS: dict = {}
_TOKEN_TTL = 900
_MAX_TOKENS = 16
_MAX_ARCHIVE_ROWS = 5000
_EXPORT_SLOTS = threading.BoundedSemaphore(2)


def query_filters(platform: str = 'all', status: str = 'all', q: str = '',
                  archived: str = 'active', start_date: str = '', end_date: str = ''):
    try:
        return store.pool_filters(platform, status, q, archived, start_date, end_date)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('')
def lead_pool(selection=Depends(query_filters), limit: int = Query(100, ge=1, le=200),
              offset: int = Query(0, ge=0)):
    filters, _ = selection
    return store.pool_query(**filters, limit=limit, offset=offset)


@router.get('/capacity')
def capacity():
    return store.pool_capacity()


@router.get('/batches')
def lead_batches(platform: str = Query(..., min_length=1), days: int = Query(60, ge=1, le=365)):
    """按「采集日期 + 关键词」列出线索批次，供「选择任务再回复」使用。

    2026-11-06 主人要求：回复入口从「截流」里拆出来，能选具体哪一批来回复。
    批次以**日期 + 关键词**区分；一条线索的 keyword / collected_at 就是它的批次标识。
    """
    try:
        return {'platform': platform, 'batches': store.batches(platform, days=days)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _csv_cell(name, value):
    if value is None:
        return ''
    text = str(value)
    # CSV 无类型系统。单引号文本前缀不是可执行公式，防长 ID 被 Excel 四舍五入。
    is_identifier = (name == 'id' or name.endswith('_id') or name.endswith('_uid')
                     or name in ('aid', 'bvid'))
    if text and (is_identifier or text.lstrip().startswith(('=', '+', '-', '@'))
                 or text[0] in ('\t', '\r', '\n')):
        return "'" + text
    return text


def _csv_batches(snapshot: Path, filters, bounds, batch_size=500, release_slot=None,
                 unlink_snapshot=True):
    conn = sqlite3.connect(snapshot)
    conn.row_factory = sqlite3.Row
    try:
        sql, args, columns = store._pool_sql(filters, bounds)
        columns = ['platform'] + columns
        cursor = conn.execute('SELECT * FROM (' + sql +
                              ') ORDER BY collected_at DESC,platform ASC,id ASC', args)
        buf = io.StringIO(newline='')
        writer = csv.writer(buf)
        writer.writerow(columns)
        yield b'\xef\xbb\xbf' + buf.getvalue().encode('utf-8')
        while True:
            records = cursor.fetchmany(batch_size)
            if not records:
                break
            buf.seek(0)
            buf.truncate(0)
            for row in records:
                writer.writerow([_csv_cell(c, row[c]) for c in columns])
            yield buf.getvalue().encode('utf-8')
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()
        if unlink_snapshot:
            for _ in range(5):
                try:
                    snapshot.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.05)
        if release_slot is not None:
            try:
                release_slot()
            except ValueError:
                pass


@router.get('/export')
def export(selection=Depends(query_filters)):
    filters, bounds = selection
    if not _EXPORT_SLOTS.acquire(blocking=False):
        raise HTTPException(429, '同时导出过多请稍后重试，每次只生成一份完整快照')
    try:
        snapshot = store.pool_export_snapshot(filters, bounds)
    except BaseException:
        _EXPORT_SLOTS.release()
        raise
    gen = _csv_batches(snapshot, filters, bounds, release_slot=_EXPORT_SLOTS.release,
                       unlink_snapshot=False)
    return StreamingResponse(gen,
                             media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': 'attachment; filename="lead-pool.csv"',
                                      'Cache-Control': 'no-store'},
                             background=BackgroundTask(_release_export, snapshot))


class ArchiveRequest(BaseModel):
    confirm: StrictBool = False
    preview: StrictBool = True
    platform: str = 'all'
    status: str = 'all'
    q: str = ''
    start_date: str = ''
    end_date: str = ''
    token: str = ''


def _release_export(snapshot: Path):
    try:
        snapshot.unlink(missing_ok=True)
    finally:
        try:
            _EXPORT_SLOTS.release()
        except ValueError:
            pass


def _prune_tokens():
    now = time.time()
    for key in list(_TOKENS):
        if _TOKENS[key]['expires_at'] <= now:
            del _TOKENS[key]


@router.post('/archive')
def archive(req: ArchiveRequest):
    if req.confirm is not True:
        raise HTTPException(422, '归档必须明确 confirm=true（包括预览）')
    if not req.start_date or not req.end_date:
        raise HTTPException(422, '归档必须明确选择 start_date 与 end_date')
    try:
        filters, bounds = store.pool_filters(req.platform, req.status, req.q, 'active',
                                             req.start_date, req.end_date)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    identity = str(store.DB_PATH.resolve())
    if req.preview:
        conn = store._pool_connection()
        try:
            sql, args, _ = store._pool_sql(filters, bounds, ['id', 'status', 'updated_at'])
            candidates = conn.execute('SELECT platform,id,updated_at FROM (' + sql +
                                      ") WHERE status IN ('replied','failed','skipped') LIMIT %d" % (_MAX_ARCHIVE_ROWS + 1,), args).fetchall()
        finally:
            conn.close()
        if len(candidates) > _MAX_ARCHIVE_ROWS:
            raise HTTPException(422, '可归档超过 %d 条请缩小日期范围或按平台分批归档' % _MAX_ARCHIVE_ROWS)
        selected = {}
        for row in candidates:
            selected.setdefault(row['platform'], []).append((row['id'], row['updated_at']))
        now = time.time()
        token = uuid.uuid4().hex
        with _TOKEN_LOCK:
            _prune_tokens()
            # 有界 token 数；不静默逐出仍有效的确认，满载要求稍后重试。
            if len(_TOKENS) >= _MAX_TOKENS:
                raise HTTPException(429, '待确认归档预览过多，请完成确认或等待15分钟')
            _TOKENS[token] = dict(filters=filters, identity=identity, selected=selected,
                                  expires_at=now + _TOKEN_TTL)
        return dict(preview=True, eligible_count=len(candidates),
                    platforms={p: len(rows) for p, rows in selected.items()}, token=token,
                    expires_at=now + _TOKEN_TTL, terminal_statuses=list(store.ARCHIVABLE_STATUSES))
    with _TOKEN_LOCK:
        _prune_tokens()
        record = _TOKENS.get(req.token)
        if not record:
            raise HTTPException(409, '预览 token 不存在、已使用或已过期，请重新预览')
        if record['filters'] != filters or record['identity'] != identity:
            raise HTTPException(409, '筛选条件已变化，请重新预览')
        # 持 token 锁至提交，只能一次执行；异常时保留 token 可明确重试。
        count = 0
        with store._LOCK:
            conn = store._db()
            with conn:
                for platform, rows in record['selected'].items():
                    for lead_id, updated_at in rows:
                        count += conn.execute(
                            'UPDATE %s SET archived_at=?,updated_at=? WHERE id=? '
                            'AND updated_at IS ? AND COALESCE(archived_at,0)=0 '
                            "AND status IN ('replied','failed','skipped')" % store.table(platform),
                            (time.time(), time.time(), lead_id, updated_at)).rowcount
        del _TOKENS[req.token]
    selected_count = sum(len(rows) for rows in record['selected'].values())
    return dict(preview=False, archived_count=count, selected_count=selected_count,
                skipped_count=selected_count-count)
