"""Bounded real-time control. A successful socket write is NOT device execution."""
from __future__ import annotations
import asyncio
import collections
import math
import os
import queue
import threading
import time
import uuid
from urllib.parse import urlsplit
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

MAX_TARGETS = 20
QUEUE_SIZE = 64
MAX_AGE = .75
GESTURE_TIMEOUT = 10.0
WRITER_IDLE_SECONDS = 60.0
MAX_CONNECTIONS = 4
_CONNECTION_LOCK = threading.Lock()
_CONNECTION_COUNT = 0
DIAGNOSTICS = collections.deque(maxlen=2048)
_DIAG_LOCK = threading.Lock()


def enabled():
    return os.environ.get('FISHFLEET_LIVE_CONTROL', '1') != '0'


def diag(stage, **fields):
    with _DIAG_LOCK:
        DIAGNOSTICS.append(dict(stage=stage, monotonic=time.monotonic(), **fields))


def integer(v, lo, hi):
    if type(v) is not int or not lo <= v <= hi:
        raise ValueError('invalid_integer')
    return v


def targets_of(m):
    targets = m.get('targets')
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_TARGETS:
        raise ValueError('invalid_targets')
    if any(not isinstance(s, str) or not s or len(s) > 256 for s in targets):
        raise ValueError('invalid_targets')
    if len(set(targets)) != len(targets):
        raise ValueError('duplicate_targets')
    return targets


def dimensions(m):
    return integer(m.get('source_width'), 1, 16384), integer(m.get('source_height'), 1, 16384)


def point(m, w, h):
    return integer(m.get('x'), 0, w-1), integer(m.get('y'), 0, h-1)


def origin_allowed(ws):
    try:
        o = urlsplit(ws.headers.get('origin', ''))
        host = urlsplit('http://' + ws.headers.get('host', ''))
        return (o.scheme in ('http', 'https') and o.hostname in ('localhost', '127.0.0.1', '::1')
                and host.hostname in ('localhost', '127.0.0.1', '::1')
                and (o.port or (443 if o.scheme == 'https' else 80)) == (host.port or 80))
    except ValueError:
        return False


class Job:
    def __init__(self, hub, serial, sess, token, gid, w, h, notify):
        self.hub, self.serial, self.sess, self.token = hub, serial, sess, token
        self.gid, self.w, self.h, self.notify = gid, w, h, notify
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.down = False
        self.ending = False
        self.last = (0, 0)
        self.created = self.touched = time.monotonic()
        self.geometry = (sess.real_width, sess.real_height, getattr(sess,'width',None), getattr(sess,'height',None))

    def coords(self, x, y):
        rw, rh = self.sess.real_width, self.sess.real_height
        return min(rw-1, int(x*rw/self.w)), min(rh-1, int(y*rh/self.h))

    def fail(self, reason):
        if not self.cancelled.is_set():
            diag('failed', gesture_id=self.gid, serial=self.serial, reason=reason)
            self.notify(dict(type='error', gesture_id=self.gid, serial=self.serial, reason=reason))
        self.cancelled.set()


class Writer:
    def __init__(self, hub, serial):
        self.hub, self.serial = hub, serial
        self.q = queue.Queue(QUEUE_SIZE)
        self.active = None
        self.last_activity = time.monotonic()
        self.thread = threading.Thread(target=self.run, name='live-control-'+serial, daemon=True)
        self.thread.start()

    def put(self, job, phase, seq, x, y):
        try:
            self.q.put_nowait((job, phase, seq, x, y, time.monotonic()))
            diag('queued', gesture_id=job.gid, serial=self.serial, phase=phase, seq=seq,
                 depth=self.q.qsize())
            return True
        except queue.Full:
            job.fail('queue_full')
            return False

    def finish(self, job):
        if job.finished.is_set():
            return
        # Same writer that sent DOWN attempts release. Never clear ownership early.
        try:
            if job.down:
                t = time.monotonic()
                ok = job.sess.owned_touch(job.token, 'up', *job.last)
                diag('release_write', gesture_id=job.gid, serial=self.serial, ok=ok,
                     seconds=time.monotonic()-t)
                if not ok:
                    job.fail('release_write_failed')
        except Exception:
            job.fail('release_failed')
        finally:
            self.last_activity = time.monotonic()
            job.sess.release_gesture(job.token)
            job.finished.set()
            if self.active is job:
                self.active = None
            self.hub.release(job)

    def run(self):
        while True:
            active = self.active
            if active and not active.finished.is_set() and (active.cancelled.is_set() or time.monotonic()-active.touched > GESTURE_TIMEOUT):
                if not active.cancelled.is_set(): active.fail('timeout')
                self.finish(active)
            try:
                item = self.q.get(timeout=.05)
            except queue.Empty:
                job = self.active
                if job and (job.cancelled.is_set() or time.monotonic()-job.touched > GESTURE_TIMEOUT):
                    if not job.cancelled.is_set(): job.fail('timeout')
                    self.finish(job)
                # Reservation and reclamation share the hub lock. A reserved but
                # not-yet-enqueued job protects its writer from reclamation.
                with self.hub.lock:
                    if (self.active is None and self.q.empty() and self.serial not in self.hub.jobs
                            and time.monotonic()-self.last_activity > WRITER_IDLE_SECONDS
                            and self.hub.writers.get(self.serial) is self):
                        del self.hub.writers[self.serial]
                        return
                continue
            self.last_activity = time.monotonic()
            job, phase, seq, x, y, queued = item
            try:
                if job.finished.is_set(): continue
                self.active = job
                age = time.monotonic()-queued
                if job.cancelled.is_set():
                    self.finish(job); continue
                if self.hub.get_session(self.serial) is not job.sess:
                    job.fail('session_replaced'); self.finish(job); continue
                if phase in ('down','move','up','scroll') and job.geometry != (job.sess.real_width, job.sess.real_height, getattr(job.sess,'width',None), getattr(job.sess,'height',None)):
                    job.fail('geometry_changed'); self.finish(job); continue
                if age > MAX_AGE:
                    job.fail('stale_event'); self.finish(job); continue
                if phase in ('down', 'move', 'up'):
                    p = job.coords(x, y)
                    job.last = p
                    t = time.monotonic()
                    ok = job.sess.owned_touch(job.token, phase, *p)
                    diag('write', gesture_id=job.gid, serial=self.serial, seq=seq, phase=phase,
                         x=p[0], y=p[1], age_seconds=age, seconds=time.monotonic()-t, ok=bool(ok))
                    if not ok:
                        job.fail('socket_write_failed'); self.finish(job); continue
                    job.down = phase != 'up'
                    if phase == 'up': self.finish(job)
                elif phase == 'key':
                    ok = job.sess.owned_key(job.token, x)
                    diag('write', serial=self.serial, phase='key', ok=bool(ok), age_seconds=age)
                    if not ok: job.fail('key_write_failed')
                    self.finish(job)
                elif phase == 'scroll':
                    p = job.coords(*x[:2])
                    ok = job.sess.owned_scroll(job.token, *p, x[2], x[3])
                    diag('write', serial=self.serial, phase='scroll', ok=bool(ok), age_seconds=age)
                    if not ok: job.fail('scroll_write_failed')
                    self.finish(job)
            except Exception:
                job.fail('writer_exception'); self.finish(job)
            finally:
                self.q.task_done()


class Hub:
    def __init__(self, getter, recording=lambda s: False):
        self.get_session, self.recording = getter, recording
        self.writers, self.jobs = {}, {}
        self.lock = threading.Lock()

    def release(self, job):
        with self.lock:
            if self.jobs.get(job.serial) is job:
                del self.jobs[job.serial]

    def reserve(self, serials, gid, w, h, notify, *, check_orientation=True):
        result = []
        with self.lock:
            try:
                for s in serials:
                    sess = self.get_session(s)
                    if sess is None: raise ValueError('no_stream:'+s)
                    if self.recording(s): raise ValueError('recording_unsupported:'+s)
                    if check_orientation and (w > h) != (sess.real_width > sess.real_height):
                        raise ValueError('orientation_unsupported:'+s)
                    if s in self.jobs: raise ValueError('busy:'+s)
                    if s not in self.writers and len(self.writers) >= MAX_TARGETS:
                        raise ValueError('writer_limit')
                    token = uuid.uuid4().hex
                    if not sess.claim_gesture(token): raise ValueError('busy:'+s)
                    job = Job(self, s, sess, token, gid, w, h, notify)
                    self.jobs[s] = job
                    result.append(job)
                    if s not in self.writers: self.writers[s] = Writer(self, s)
                return result
            except Exception:
                for j in result:
                    j.sess.release_gesture(j.token)
                    self.jobs.pop(j.serial, None)
                raise


def make_router(get_session, recording=lambda s: False):
    router = APIRouter()
    hub = Hub(get_session, recording)
    router.control_hub = hub

    @router.get('/api/control/config')
    async def config():
        return dict(enabled=enabled(), protocol=1, max_targets=MAX_TARGETS,
                    scope='same-orientation proportional coordinates; recording unsupported')

    @router.get('/api/control/diagnostics')
    async def diagnostics(limit: int = 200):
        with _DIAG_LOCK:
            rows = list(DIAGNOSTICS)[-max(1, min(limit, 2048)):]
        return dict(records=rows, capacity=2048,
                    meaning='write means socket write only, NOT device execution')

    @router.websocket('/ws/control')
    async def control(ws: WebSocket):
        global _CONNECTION_COUNT
        if not enabled() or not origin_allowed(ws):
            await ws.close(code=1008); return
        with _CONNECTION_LOCK:
            admitted = _CONNECTION_COUNT < MAX_CONNECTIONS
            if admitted: _CONNECTION_COUNT += 1
        if not admitted:
            await ws.close(code=1008); return
        try:
            await control_admitted(ws)
        finally:
            with _CONNECTION_LOCK:
                _CONNECTION_COUNT -= 1

    async def control_admitted(ws):
        await ws.accept()
        loop = asyncio.get_running_loop()
        outgoing = asyncio.Queue(128)
        gestures = {}
        all_jobs = []
        connection_failed = asyncio.Event()
        def notify(msg):
            def put():
                if not outgoing.full():
                    outgoing.put_nowait(msg)
                else:
                    # A peer not reading replies must not accumulate background work.
                    for job in all_jobs: job.cancelled.set()
                    connection_failed.set()
            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:
                pass  # Disconnected TestClient/application loop already closed.
        async def sender():
            try:
                while True:
                    message = await outgoing.get()
                    await asyncio.wait_for(ws.send_json(message), timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                for job in all_jobs: job.cancelled.set()
                try: await asyncio.wait_for(ws.close(code=1011), timeout=1.0)
                except Exception: pass
        async def wait_previous(serials):
            previous = [j for j in all_jobs if j.serial in serials and j.ending and not j.finished.is_set()]
            deadline = loop.time() + .1
            while any(not j.finished.is_set() for j in previous) and loop.time() < deadline:
                await asyncio.sleep(.002)
            # reserve() reports explicit busy if an ending writer is still blocked.
        send_task = asyncio.create_task(sender())
        try:
            while True:
                if send_task.done() or connection_failed.is_set():
                    try: await asyncio.wait_for(ws.close(code=1011),timeout=1.0)
                    except Exception: pass
                    break
                try:
                    text = await asyncio.wait_for(ws.receive_text(), timeout=1)
                except asyncio.TimeoutError:
                    for gid, g in list(gestures.items()):
                        if time.monotonic()-g['at'] > GESTURE_TIMEOUT:
                            for j in g['jobs']: j.fail('timeout')
                            del gestures[gid]
                    continue
                gid = None
                try:
                    import json
                    if len(text) > 16384: raise ValueError('message_too_large')
                    m = json.loads(text)
                    if not isinstance(m, dict): raise ValueError('invalid_message')
                    kind = m.get('type'); gid = m.get('gesture_id')
                    if kind in ('begin', 'move', 'end', 'cancel'):
                        if not isinstance(gid, str) or not 1 <= len(gid) <= 128: raise ValueError('invalid_gesture_id')
                        seq = integer(m.get('seq'), 0, 2**31-1)
                    if kind == 'begin':
                        if seq != 0 or gid in gestures or len(gestures) >= 4: raise ValueError('invalid_begin')
                        ts = targets_of(m); w,h = dimensions(m); x,y = point(m,w,h)
                        diag('received', phase='begin', gesture_id=gid, seq=seq, targets=ts,
                             source_width=w, source_height=h, x=x, y=y)
                        if len(all_jobs) + len(ts) > 80: raise ValueError('connection_busy')
                        await wait_previous(ts)
                        jobs = hub.reserve(ts,gid,w,h,notify)
                        gestures[gid] = dict(jobs=jobs, seq=0, w=w,h=h,at=time.monotonic())
                        all_jobs.extend(jobs)
                        for j in jobs: hub.writers[j.serial].put(j,'down',seq,x,y)
                        notify(dict(type='accepted',gesture_id=gid,seq=seq,targets=ts))
                    elif kind in ('move','end','cancel'):
                        g = gestures.get(gid)
                        if not g or seq <= g['seq']: raise ValueError('invalid_sequence')
                        x,y = point(m,g['w'],g['h'])
                        g['seq'],g['at'] = seq,time.monotonic()
                        diag('received',phase=kind,gesture_id=gid,seq=seq,x=x,y=y)
                        for j in g['jobs']:
                            j.touched = time.monotonic()
                            if kind != 'move': j.ending = True
                            if kind == 'cancel': j.cancelled.set()
                            else: hub.writers[j.serial].put(j,'move' if kind=='move' else 'up',seq,x,y)
                        if kind != 'move':
                            del gestures[gid]
                            notify(dict(type='ended',gesture_id=gid,seq=seq,status='queued_not_executed'))
                    elif kind == 'ping':
                        if not isinstance(gid,str) or gid not in gestures: raise ValueError('unknown_gesture')
                        g = gestures[gid]
                        g['at'] = time.monotonic()
                        for j in g['jobs']: j.touched = g['at']
                        notify(dict(type='pong',gesture_id=gid))
                    elif kind in ('key','scroll'):
                        ts = targets_of(m)
                        if kind == 'scroll':
                            w,h = dimensions(m); x,y = point(m,w,h)
                            hv = [m.get('h',0),m.get('v',0)]
                            if any(type(v) not in (float,int) or not math.isfinite(v) or abs(v)>32 for v in hv): raise ValueError('invalid_scroll')
                            payload=(x,y,*hv)
                        else:
                            w,h=1080,1920
                            payload=m.get('key')
                            if not isinstance(payload,str) or payload.lower() not in ('home','back','menu','recent','recents','volume_up','volume_down','power','enter','delete','backspace'):
                                raise ValueError('invalid_key')
                        if len(all_jobs) + len(ts) > 80: raise ValueError('connection_busy')
                        diag('received', phase=kind, targets=ts)
                        await wait_previous(ts)
                        jobs = hub.reserve(ts,None,w,h,notify,check_orientation=kind!='key'); all_jobs.extend(jobs)
                        for j in jobs: j.ending = True
                        for j in jobs: hub.writers[j.serial].put(j,kind,0,payload,0)
                        notify(dict(type='accepted',action=kind,targets=ts))
                    else: raise ValueError('unknown_type')
                    # Keep connection memory bounded; completed jobs no longer need disconnect cleanup.
                    all_jobs[:] = [j for j in all_jobs if not j.finished.is_set()]
                    if len(all_jobs)>80: raise ValueError('connection_busy')
                except (ValueError,TypeError,KeyError) as exc:
                    if isinstance(gid, str) and gid in gestures:
                        for j in gestures.pop(gid)['jobs']: j.fail('protocol_error')
                    notify(dict(type='error',gesture_id=gid if isinstance(gid,str) else None,
                                reason=str(exc)[:300]))
        except (WebSocketDisconnect,RuntimeError):
            pass
        finally:
            for j in all_jobs: j.cancelled.set()
            send_task.cancel()
            try: await send_task
            except (asyncio.CancelledError,RuntimeError): pass
    return router
