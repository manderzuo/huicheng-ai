"""独立创作流水线原型；不修改群控服务，不自动发布。"""
import json, os, re, shutil, sys, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from urllib.parse import urlsplit
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=5, thread_name_prefix='studio-video')
app = FastAPI(title='FishFleet 独立创作流水线')

@app.middleware('http')
async def local_origin_only(request, call_next):
    # 本地服务仍可能遭遇浏览器跨站请求：拒绝外部来源和跨站 Fetch。
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        host = request.headers.get('host', '')
        if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and urlsplit(origin).netloc.lower() != host.lower()):
            return JSONResponse({'detail':'拒绝跨站请求'}, status_code=403)
    return await call_next(request)


def path_for(pid):
    if not re.fullmatch(r'[a-f0-9]{32}', pid):
        raise HTTPException(404, '项目不存在')
    return DATA / pid


def load(pid):
    try:
        return json.loads((path_for(pid) / 'project.json').read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, '项目不存在')


def save(p):
    d = path_for(p['id']); tmp = d / 'project.tmp'
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, d / 'project.json')


def find(p, iid):
    item = next((x for x in p['prompts'] if x['id'] == iid), None)
    if item is None: raise HTTPException(404, '视频任务不存在')
    return item


PROMPT_KEYS = ('prompt', 'text', 'content', '提示词', '画面描述', '描述', '内容', '文案', 'description', 'body')


def prompt_text(item):
    """模型可能返回纯字符串，也可能返回带标题等字段的对象；两种都要能取到正文。"""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in PROMPT_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip(): return value.strip()
        strings = [v.strip() for v in item.values() if isinstance(v, str) and v.strip()]
        if strings: return max(strings, key=len)   # 取信息量最大的一段
        return json.dumps(item, ensure_ascii=False)
    return ''


def extract_prompt_list(text):
    """尽量从模型输出里取出提示词列表；数量由调用方补齐，不在此处一票否决。"""
    if not isinstance(text, str): return []
    decoder = json.JSONDecoder()
    best = []
    for i, c in enumerate(text):
        if c not in '[{': continue
        try: obj, _ = decoder.raw_decode(text[i:])
        except ValueError: continue
        if isinstance(obj, dict):
            for key in ('prompts', 'data', 'items', 'list', 'result'):
                if isinstance(obj.get(key), list): obj = obj[key]; break
            else: continue
        if not isinstance(obj, list): continue
        values = []
        for item in obj:
            value = prompt_text(item)
            if value and len(value) <= 4000: values.append(value)
        if len(values) > len(best): best = values
    return best


MAX_REFS = 9
QUALITIES = ('480p', '720p')
ASPECTS = ('1:1', '4:3', '3:4', '9:16', '16:9')


def project_refs(p):
    """兼容旧项目（单张 reference）与新项目（references 列表）。"""
    names = p.get('references')
    if isinstance(names, list) and names:
        return names
    old = p.get('reference')
    return [old] if old else []


@app.post('/api/studio/projects')
async def create(topic: str = Form(...), count: int = Form(...), duration: int = Form(...),
                 resolution: str = Form('720p'), aspect: str = Form('16:9'),
                 output_dir: str = Form(''),
                 reference: list[UploadFile] = File(default=[])):
    if not topic.strip() or len(topic) > 2000 or not 1 <= count <= 20 or not 5 <= duration <= 15:
        raise HTTPException(400, '请填写话题，数量为1–20，时长为5–15秒')
    resolution = (resolution or '720p').lower()
    if resolution not in QUALITIES: raise HTTPException(400, '画质只支持 480P 或 720P')
    if aspect not in ASPECTS: raise HTTPException(400, '画面比例不受支持')
    # 参考图不是必填，也允许一次给多张，用于人物/场景/风格分别对齐。
    uploads = [u for u in (reference or []) if u is not None and (u.filename or '').strip()]
    if len(uploads) > MAX_REFS: raise HTTPException(400, '参考图最多 %d 张' % MAX_REFS)
    from PIL import Image
    import io
    saved = []
    for index, upload in enumerate(uploads):
        suffix = Path(upload.filename or '').suffix.lower()
        if suffix not in ('.jpg', '.jpeg', '.png', '.webp'): raise HTTPException(400, '参考图仅支持 JPG、PNG、WebP')
        raw = await upload.read(10 * 1024 * 1024 + 1)
        if not raw or len(raw) > 10 * 1024 * 1024: raise HTTPException(400, '参考图为空或超过10MB')
        try:
            image = Image.open(io.BytesIO(raw)); image.verify()
            if image.format not in ('JPEG', 'PNG', 'WEBP'): raise ValueError('format')
        except Exception: raise HTTPException(400, '参考图不是有效图片')
        saved.append(('reference%d%s' % (index, suffix), raw))
    pid = uuid.uuid4().hex; d = path_for(pid); d.mkdir()
    for name, raw in saved: (d / name).write_bytes(raw)
    p = {'id':pid, 'topic':topic.strip(), 'count':count, 'duration':duration,
         'resolution':resolution, 'aspect':aspect, 'output_dir':str(resolve_output_dir(output_dir)),
         'references':[name for name, _ in saved], 'created_at':time.time(), 'prompts':[], 'assignments':[]}
    with LOCK: save(p)
    return p


def default_output_dir():
    base = os.environ.get('USERPROFILE') or str(Path.home())
    return Path(base) / 'Downloads' / 'FishFleet-视频生成'


def resolve_output_dir(raw):
    text = str(raw or '').strip()
    if not text: return default_output_dir()
    path = Path(text).expanduser()
    if not path.is_absolute(): raise HTTPException(400, '下载文件夹需要填绝对路径')
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, '下载文件夹不可创建：%s' % exc)
    if not os.access(path, os.W_OK): raise HTTPException(400, '下载文件夹不可写')
    return path


class FolderPick(BaseModel):
    initial: str = ''


@app.post('/api/studio/pick-folder')
def pick_folder(body: FolderPick):
    """在服务所在机器弹系统文件夹选择框；取消则返回空。"""
    import subprocess
    script = (
        'Add-Type -AssemblyName System.Windows.Forms | Out-Null;'
        '$d = New-Object System.Windows.Forms.FolderBrowserDialog;'
        '$d.Description = "选择视频下载文件夹";'
        '$d.ShowNewFolderButton = $true;'
        'if ($args[0]) { try { $d.SelectedPath = $args[0] } catch {} };'
        'if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }'
    )
    try:
        proc = subprocess.run(
            ['powershell', '-NoProfile', '-STA', '-Command', script, str(body.initial or '')],
            capture_output=True, text=True, timeout=180)
        chosen = (proc.stdout or '').strip().splitlines()
        return {'path': chosen[-1] if chosen else ''}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, '打开文件夹选择框失败：%s' % str(exc)[:200])


@app.post('/api/studio/open-folder')
def open_folder(body: FolderPick):
    import subprocess
    target = resolve_output_dir(body.initial)
    try:
        subprocess.Popen(['explorer', str(target)])
        return {'ok': True, 'path': str(target)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, '打开文件夹失败：%s' % str(exc)[:200])


@app.get('/api/studio/projects')
def list_projects():
    """列出项目，供界面恢复上次结果（只读）。"""
    rows = []
    for f in DATA.glob('*/project.json'):
        try:
            p = json.loads(f.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            continue
        first = next((x.get('text', '') for x in p.get('prompts', [])), '')
        rows.append({'id': p['id'], 'topic': p.get('topic', ''), 'created_at': p.get('created_at', 0),
                     'count': p.get('count'), 'resolution': p.get('resolution'),
                     'aspect': p.get('aspect'), 'output_dir': p.get('output_dir', ''),
                     'first_prompt': first[:120], 'prompt_count': len(p.get('prompts', [])),
                     'ready': sum(1 for x in p.get('prompts', []) if x.get('status') == 'ready')})
    rows.sort(key=lambda r: r.get('created_at', 0), reverse=True)
    return {'projects': rows[:50]}


@app.delete('/api/studio/projects/{pid}')
def delete_project(pid: str):
    """删除整条生成记录（含项目内视频文件）。生成中的项目不允许删。"""
    folder = path_for(pid)
    with LOCK:
        p = load(pid)
        if any(x.get('status') in ('queued', 'running', 'submitted', 'downloading') for x in p.get('prompts', [])):
            raise HTTPException(409, '还有任务在生成中，等结束再删')
        shutil.rmtree(folder, ignore_errors=True)
    return {'ok': True, 'id': pid}


@app.get('/api/studio/projects/{pid}')
def get_project(pid: str):
    with LOCK: return load(pid)


@app.post('/api/studio/projects/{pid}/prompts')
def make_prompts(pid: str):
    with LOCK:
        p = load(pid)
        if p['prompts']: raise HTTPException(409, '已有提示词，避免覆盖已有生成任务')
    from fishfleet.web.server import _llm_chat
    refs = project_refs(p)
    if refs:
        fields = '主体与场景、动作的起点过程结果、镜头景别视角运镜速度焦点、时间轴、连续性、参考图用途、声音设计、负面约束'
        ref_rule = ('共有 %d 张参考图，按给出的顺序对应图片1、图片2…；参考图只用于锁定人物外观、场景或风格的一致性，'
                    '并在每条提示词的“参考图用途”里写明用到了哪几张、用于什么；' % len(refs))
    else:
        fields = '主体与场景、动作的起点过程结果、镜头景别视角运镜速度焦点、时间轴、连续性、声音设计、负面约束'
        ref_rule = ('本次没有提供任何参考图，属于纯文字生成：绝对不要出现“参考图”“参考图片”“图生图”“与图一致”'
                    '“参考图用途”等字样，也不要虚构未提供的素材；')
    instruction = ('你是短视频创意导演。根据话题，生成恰好指定数量的中文视频提示词，保持主题一致，但各条在场景、主体动作、镜头、节奏或叙事上有实质区别。'
                   '每条按' + fields + '组织。'
                   + ref_rule +
                   '按总时长安排2到3个先后阶段，避免动作同时发生；保持主体外观、道具、空间方向和光线连续。'
                   '避免无意切镜、变形、身份漂移、道具消失、动作跳跃、无关字幕或水印。只返回JSON数组，元素为可直接交给视频模型的非空字符串。'
                   '只使用话题里出现或可合理推断的内容，不得声称看过未提供的素材。')
    import base64, mimetypes
    base_text = {'话题': p['topic'], '数量': p['count'], '时长秒': p['duration'],
                 '画质': p.get('resolution', '720p'), '画面比例': p.get('aspect', '16:9'),
                 '参考图张数': len(refs)}
    content = [{'type': 'text', 'text': json.dumps(base_text, ensure_ascii=False)}]
    for index, name in enumerate(refs, 1):
        path = path_for(pid) / name
        if not path.is_file(): continue
        mime = mimetypes.guess_type(name)[0] or 'image/jpeg'
        if len(refs) > 1:
            content.append({'type': 'text', 'text': '参考图 %d：' % index})
        content.append({'type': 'image_url', 'image_url': {'url': 'data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode()}})
    # 文字模型若不支持图片输入会直接返回空内容，这里准备一份纯文字备选。
    text_only = [{'type': 'text', 'text': json.dumps(
        {**base_text, '说明': '参考图会随视频生成一起提交，但这里不需要读取图片内容；'
                              '请在“参考图用途”里写明将按顺序使用图片1、图片2…，不要编造图中细节'},
        ensure_ascii=False)}]

    def collect(payload, calls=6):
        import time as _time
        values = []
        for _ in range(calls):
            need = p['count'] - len(values)
            if need <= 0: break
            extra = [] if not values else [
                {'type': 'text', 'text': '已有以下提示词，请勿重复；还需补充 %d 条，并与已有内容有实质区别。'
                                         '请返回包含全部 %d 条的完整 JSON 数组：\n%s'
                                         % (need, p['count'], json.dumps(values, ensure_ascii=False))}]
            tokens = min(16000, max(2400, p['count'] * 800))
            try:
                result = _llm_chat('auto', [{'role': 'system', 'content': instruction},
                                            {'role': 'user', 'content': payload + extra}], tokens)
            except Exception:            # 单次调用失败不放弃，继续试
                _time.sleep(1.5); continue
            got = extract_prompt_list(result)
            if not got:                  # 模型偶尔返回空：重试，而不是直接判失败
                _time.sleep(1.5); continue
            for value in got:
                if value not in values: values.append(value)
            values = values[:p['count']]
        return values

    note = ''
    try:
        values = collect(content) if refs else collect(text_only)
        if refs and len(values) < p['count']:
            values = collect(text_only)
            if values: note = '参考图内容这次未被模型读取，提示词按文字生成；参考图仍会随视频一起提交'
        if len(values) < p['count']:
            raise ValueError('模型只给出 %d 条可用提示词（需要 %d 条），请重试或减少数量'
                             % (len(values), p['count']))
    except Exception as exc: raise HTTPException(502, '提示词生成失败：'+str(exc)[:300])
    with LOCK:
        p = load(pid)
        if p['prompts']: raise HTTPException(409, '提示词已生成，请勿重复提交')
        p['prompts'] = [{'id':uuid.uuid4().hex,'text':v,'status':'draft','review':None,'video_url':None,'error':None} for v in values]
        if note: p['prompt_note'] = note
        save(p); return p


class TextEdit(BaseModel):
    text: str

@app.put('/api/studio/projects/{pid}/prompts/{iid}')
def edit_prompt(pid: str, iid: str, body: TextEdit):
    if not body.text.strip() or len(body.text)>4000: raise HTTPException(400,'提示词不能为空或超过4000字')
    with LOCK:
        p=load(pid); item=find(p,iid)
        if item['status'] != 'draft': raise HTTPException(409,'任务已提交，不能修改提示词')
        item['text']=body.text.strip(); save(p); return item


class Confirm(BaseModel):
    confirmed: bool = False


def build_mcp_args(client, item, project, pid, iid, output_path):
    """按不同 MCP 服务的参数约定组装请求；顺序与界面图片1、图片2…一致。"""
    refs = [str(path_for(pid) / name) for name in project_refs(project)]
    tool = (getattr(client, 'tool_name', '') or '').lower()
    if tool.startswith('seedance'):
        args = {'prompt': item['text'], 'duration': project['duration'],
                'resolution': project.get('resolution', '720p'),
                'ratio': project.get('aspect', '16:9'),
                'idempotency_key': 'studio-%s-%s' % (pid, iid)}
        if refs:
            args['image_paths'] = refs
        args['output_path'] = str(output_path)
        return args
    args = {'prompt': item['text'], 'duration': project['duration'],
            'resolution': project.get('resolution', '720p'),
            'aspect_ratio': project.get('aspect', '16:9')}
    if refs:
        args['reference_image'] = refs[0]
        args['reference_images'] = refs
    return args


TASK_ID_RE = re.compile(r'(?:task[_-]?id|taskId|video[_-]?id)["\']?\s*[:=]\s*["\']?([A-Za-z0-9_\-]{6,})', re.I)


def extract_task_id(text):
    match = TASK_ID_RE.search(text or '')
    return match.group(1) if match else ''


def mcp_text(result):
    """把 MCP 返回压成一段可读文本，便于保存任务号与错误原因。"""
    if isinstance(result, dict) and isinstance(result.get('structuredContent'), dict) and not result.get('isError'):
        return json.dumps(result['structuredContent'], ensure_ascii=False)
    if isinstance(result, dict) and isinstance(result.get('content'), list):
        texts = [x.get('text', '') for x in result['content']
                 if isinstance(x, dict) and x.get('type') == 'text']
        return '\n'.join(t for t in texts if t).strip()
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)


def finish_video(pid, iid, target):
    """校验落盘文件并登记为 ready，同时复制到用户指定文件夹。"""
    import av
    if not target.is_file() or target.stat().st_size <= 0:
        raise ValueError('没有可用的本地视频文件')
    if target.stat().st_size > 1024**3: raise ValueError('视频超过1GiB')
    with av.open(str(target)) as container:
        if not container.streams.video: raise ValueError('生成文件不是有效视频')
    saved_path = ''
    try:
        with LOCK:
            latest = load(pid)
            index = next((n for n, x in enumerate(latest['prompts']) if x['id'] == iid), 0) + 1
            folder = resolve_output_dir(latest.get('output_dir'))
        out = folder / ('视频%d_%s.mp4' % (index, iid[:6]))
        shutil.copyfile(target, out)
        saved_path = str(out)
    except Exception:  # noqa: BLE001
        saved_path = ''
    with LOCK:
        p = load(pid); item = find(p, iid)
        item.update(status='ready', video_url=f'/api/studio/projects/{pid}/video/{iid}',
                    error=None if saved_path else '已生成，但复制到下载文件夹失败',
                    saved_path=saved_path)
        save(p)


class PullByTask(BaseModel):
    task_id: str = ''
    item_id: str = ''


@app.post('/api/studio/projects/{pid}/pull')
def pull_by_task(pid: str, body: PullByTask):
    """手工粘贴任务号回查：用于任务号丢失或后台显示已完成的情况。"""
    task_id = (body.task_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_\-]{6,}', task_id):
        raise HTTPException(400, '任务号格式不对')
    with LOCK:
        p = load(pid)
        if body.item_id:
            item = find(p, body.item_id)
        else:
            item = next((x for x in p['prompts'] if x.get('status') != 'ready'), None)
        if item is None: raise HTTPException(400, '没有可写入的条目')
        item['task_id'] = task_id
        save(p)
        iid = item['id']
    return pull_item(pid, iid)


@app.post('/api/studio/projects/{pid}/items/{iid}/pull')
def pull_item(pid: str, iid: str):
    """用保存下来的任务号向 MCP 查询并下载已完成的结果。"""
    with LOCK:
        p = load(pid); item = find(p, iid); task_id = item.get('task_id') or ''
    if not task_id:
        raise HTTPException(400, '这条记录没有任务号，无法回查；请向服务端确认任务后重新生成')
    from fishfleet.content.mcp_client import MCPVideoClient
    client = MCPVideoClient()
    target = path_for(pid) / (iid + '.mp4')
    temp = path_for(pid) / (iid + '.' + uuid.uuid4().hex + '.part.mp4')
    try:
        status = call_with_retry(client, 'seedance_status', {'task_id': task_id}, attempts=3, base_delay=4)
        text = mcp_text(status)
        save_response(pid, iid, text, 'submitted')
        state, error = parse_task_status(text)
        if error:
            raise UpstreamVideoFailed('上游失败：' + error[:200])
        if state in ('failed','cancelled','canceled','error'):
            raise UpstreamVideoFailed('上游任务状态：'+state)
        if state not in ('completed', 'succeeded', 'success', 'done', 'finished'):
            return {'ok': False, 'state': '当前状态：%s' % (state or '未知')}
        # download 只在服务端任务完成时才会写入。
        down = call_with_retry(client, 'seedance_download',
                               {'task_id': task_id, 'output_path': str(temp)},
                               attempts=3, base_delay=5)
        dtext = mcp_text(down)
        with LOCK:
            p = load(pid); item = find(p, iid)
            item['last_response'] = dtext[:4000]
            save(p)
        if isinstance(down, dict) and down.get('isError'):
            return {'ok': False, 'state': dtext[:400]}
        if not temp.is_file() or temp.stat().st_size<=0: raise ValueError('下载没有产生有效文件')
        import av
        with av.open(str(temp)) as container:
            if not container.streams.video: raise ValueError('下载文件不是有效视频')
        temp.replace(target)
        finish_video(pid, iid, target)
        with LOCK:
            p = load(pid); item = find(p, iid)
            return {'ok': True, 'saved_path': item.get('saved_path', '')}
    except Exception as exc:  # noqa: BLE001
        with LOCK:
            p=load(pid); item=find(p,iid)
            if item.get('status') != 'ready':
                item.update(status='failed' if isinstance(exc, UpstreamVideoFailed) else 'recovering',
                            error=('上游失败：' if isinstance(exc, UpstreamVideoFailed) else '查询或下载暂时失败，稍后自动重试：')+str(exc)[:250])
                save(p)
        return {'ok': False, 'state': (type(exc).__name__ + ': ' + str(exc))[:400]}
    finally:
        temp.unlink(missing_ok=True)
        client.close()


RETRYABLE = re.compile(r'50[0234]|503|502|500|超时|timed?\s*out|unavailable|不可用|网关请求失败', re.I)


def call_with_retry(client, tool, args, attempts=3, base_delay=6):
    """网关 5xx / 超时重试。必须靠幂等键保证不会重复建任务或重复扣费。"""
    import time as _time
    last = None
    for index in range(attempts):
        try:
            result = client._call(tool, args)
            if isinstance(result, dict) and result.get('isError'):
                raise RuntimeError(mcp_text(result)[:400] or 'MCP 返回错误')
            return result
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not RETRYABLE.search(str(exc)) or index + 1 >= attempts:
                raise
            _time.sleep(base_delay * (index + 1))
    raise last


@app.post('/api/studio/projects/{pid}/items/{iid}/retry')
def retry_item(pid: str, iid: str):
    """用同一个幂等键重提：网关报错但已建任务时，服务端会返回原任务而不是新建。"""
    raise HTTPException(409, '禁止自动重新提交：上游可能已经受理并计费，请先核查任务号')


def save_response(pid, iid, dump, status=None):
    with LOCK:
        p = load(pid); item = find(p, iid)
        item['last_response'] = (dump or '')[:4000]
        found = extract_task_id(dump) if status == 'submitted' and not item.get('task_id') else ''
        if found: item['task_id'] = found
        if status and item.get('status') != 'ready': item['status'] = status
        state, _ = parse_task_status(dump)
        if state in ('queued','pending','processing','running','completed','succeeded','failed','cancelled','canceled','error'):
            item['upstream_status'] = state
        save(p)
        return item.get('task_id') or ''


def parse_task_status(text):
    """解析状态文本，返回 (状态, 错误)。不能靠子串判断：JSON 里常有 "error": null。"""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        state = str(data.get('status') or '').lower()
        error = data.get('error')
        if isinstance(error, dict): error = error.get('message') or json.dumps(error, ensure_ascii=False)
        return state, (str(error) if error else '')
    return '', ''


class UpstreamVideoFailed(Exception):
    pass


def run_seedance(client, args, pid, iid, target, wait_minutes=25):
    """拆成 提交 → 轮询 → 下载 三步：查询阶段的偶发 5xx 单独重试，任务号先落盘。"""
    import time as _time
    submit_args = {k: v for k, v in args.items() if k != 'output_path'}
    # 提交响应不明时不能证明上游没有创建任务，绝不自动重提。
    result = client._call('seedance_submit', submit_args)
    dump = mcp_text(result)
    if isinstance(result, dict) and result.get('isError'):
        raise ValueError(dump[:300] or '提交结果未确认')
    task_id = save_response(pid, iid, dump, 'submitted')
    if not task_id:
        raise ValueError('提交返回里没有任务号：' + dump[:200])

    deadline = _time.time() + wait_minutes * 60
    while _time.time() < deadline:
        try:
            status = call_with_retry(client, 'seedance_status', {'task_id': task_id},
                                     attempts=6, base_delay=5)
        except Exception as exc:
            with LOCK:
                p=load(pid); item=find(p,iid); item.update(status='submitted',error='查询暂时失败，正在自动重试：'+str(exc)[:220]); save(p)
            _time.sleep(15)
            continue
        text = mcp_text(status)
        save_response(pid, iid, text)
        with LOCK:
            p=load(pid); item=find(p,iid); item['error']=None; save(p)
        state, error = parse_task_status(text)
        if error:
            raise UpstreamVideoFailed('上游任务失败：' + error[:200])
        if state in ('completed', 'succeeded', 'success', 'done', 'finished'):
            break
        if state in ('failed', 'cancelled', 'canceled', 'error'):
            raise UpstreamVideoFailed('上游任务失败：' + text[:200])
        _time.sleep(6)
    else:
        raise TimeoutError('等待超时，任务仍在生成；可用任务号继续拉取')

    with LOCK:
        p=load(pid); item=find(p,iid); item.update(status='downloading',error=None); save(p)
    down = call_with_retry(client, 'seedance_download',
                           {'task_id': task_id, 'output_path': str(target)},
                           attempts=4, base_delay=6)
    if isinstance(down, dict) and down.get('isError'):
        raise ValueError(mcp_text(down)[:300])


def work(pid, iid):
    from fishfleet.content.mcp_client import MCPVideoClient
    with LOCK:
        p=load(pid); item=find(p,iid)
        if item['status'] != 'queued': return
        item['status']='running'; save(p)
        target=path_for(pid)/(iid+'.mp4')
    args=None
    client=None
    try:
        client=MCPVideoClient()
        if not client.enabled: raise RuntimeError('视频生成未启用（config.video_generation.enabled=false）')
        if client.transport == 'stdio':
            if not client.command: raise RuntimeError('stdio 传输未配置 command')
        elif client.transport == 'http':
            if not client.server: raise RuntimeError('HTTP 传输未配置 server')
        else:
            raise RuntimeError('未知传输方式 '+str(client.transport))
        args=build_mcp_args(client,item,p,pid,iid,target)
        if str(client.tool_name).startswith('seedance'):
            run_seedance(client,args,pid,iid,target)
            finish_video(pid,iid,target)
            return
        result=client._call(client.tool_name,args)
        dump=mcp_text(result)
        # 无论成败都先把原始返回与任务号存下来：否则出错后无法回查到上游任务。
        save_response(pid,iid,dump)
        if isinstance(result,dict) and result.get('isError'):
            raise ValueError(dump[:300] or 'MCP 返回错误')
        raw=result
        if isinstance(raw,dict) and isinstance(raw.get('content'),list):
            joined=dump
            try: raw=json.loads(joined)
            except ValueError: raw={'path':joined}
        if not isinstance(raw,dict):
            raise ValueError('服务返回格式不支持')
        status=str(raw.get('status','')).lower()
        if status and status not in ('done','success','completed','finished'): raise ValueError('上游状态为 '+status+'，需人工核查')
        source=raw.get('video_path') or raw.get('path') or raw.get('file') or ''
        if source and Path(source).is_file():
            source=Path(source)
            if source.stat().st_size > 1024**3: raise ValueError('视频超过1GiB')
            import av
            with av.open(str(source)) as container:
                if not container.streams.video: raise ValueError('生成文件不是有效视频')
            if source.resolve()!=target.resolve(): shutil.copyfile(source,target)
        elif target.is_file() and target.stat().st_size>0:
            pass  # seedance_generate 已按 output_path 直接落盘
        else:
            raise ValueError('服务未返回可读取的本地视频；请核查上游结果')
        # 生成完成即落盘到用户指定文件夹，不需要再次确认。
        saved_path = ''
        try:
            with LOCK:
                latest = load(pid)
                index = next((n for n, x in enumerate(latest['prompts']) if x['id'] == iid), 0) + 1
                folder = resolve_output_dir(latest.get('output_dir'))
            target_file = folder / ('视频%d_%s.mp4' % (index, iid[:6]))
            shutil.copyfile(target, target_file)
            saved_path = str(target_file)
        except Exception as exc:  # noqa: BLE001
            saved_path = ''
            # 落盘失败不改变“已生成”这一事实，只记录原因。
        with LOCK:
            p=load(pid); item=find(p,iid)
            item.update(status='ready',video_url=f'/api/studio/projects/{pid}/video/{iid}',
                        error=None if saved_path else '已生成，但复制到下载文件夹失败', saved_path=saved_path)
            save(p)
    except Exception as exc:
        with LOCK:
            p=load(pid); item=find(p,iid)
            task_id=item.get('task_id') or ''
            if isinstance(exc, UpstreamVideoFailed):
                item.update(status='failed', error=str(exc)[:300])
            elif task_id:
                item.update(status='recovering', error='查询或下载暂时失败，将自动回查：'+str(exc)[:240])
            else:
                item.update(status='unknown', error='提交结果未确认，可能已受理；请先核查上游，切勿重复提交：'+str(exc)[:260])
            save(p)
        if task_id and not isinstance(exc, UpstreamVideoFailed): schedule_recovery(pid,iid)
    finally:
        if client: client.close()


@app.get('/api/studio/video/doctor')
def video_doctor():
    """只调用无副作用的体检工具，不提交生成、不产生费用。"""
    from fishfleet.content.mcp_client import MCPVideoClient
    client = MCPVideoClient()
    try:
        if not client.enabled: return {'ok': False, 'detail': '视频生成未启用'}
        tool = 'seedance_doctor' if str(client.tool_name).startswith('seedance') else client.tool_name
        result = client._call(tool, {})
        texts = [x.get('text', '') for x in (result.get('content') or [])
                 if isinstance(x, dict) and x.get('type') == 'text']
        return {'ok': not result.get('isError'), 'detail': ('\n'.join(texts) or '无返回内容')[:1500]}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'detail': (type(exc).__name__ + ': ' + str(exc))[:500]}
    finally:
        client.close()


@app.post('/api/studio/projects/{pid}/generate')
def generate(pid: str, body: Confirm):
    if not body.confirmed: raise HTTPException(400,'生成可能产生费用，必须明确确认')
    from fishfleet.content.mcp_client import MCPVideoClient
    client = MCPVideoClient()
    try:
        ready = client.enabled and ((client.transport == 'stdio' and client.command)
                                    or (client.transport == 'http' and client.server))
        if not ready:
            raise HTTPException(400,'视频生成服务未就绪：请在 fishfleet/config/config.yaml 的 video_generation 配置 '
                                    'stdio 的 command/args 或 HTTP 的 server，并确认 enabled=true'
                                    '（当前 enabled=%s，transport=%s，command=%s，server=%s）。未提交任何生成任务，也未产生费用'
                                    % ('true' if client.enabled else 'false', client.transport,
                                       client.command or '空', client.server or '空'))
    finally:
        client.close()
    with LOCK:
        p=load(pid)
        if len(p['prompts']) != p['count']: raise HTTPException(400,'请先生成并确认提示词')
        items=[x for x in p['prompts'] if x['status']=='draft']
        if not items: raise HTTPException(409,'没有可提交的提示词；已提交或待回查的任务不会重复扣费')
        for x in items: x['status']='queued'; x['error']=None
        for x in items: x['status']='queued'
        save(p)
    for x in items: POOL.submit(work,pid,x['id'])
    return p

@app.get('/api/studio/projects/{pid}/video/{iid}')
def video(pid: str, iid: str):
    with LOCK:
        p=load(pid); item=find(p,iid)
        if item['status'] != 'ready': raise HTTPException(404,'视频尚不可预览')
    return FileResponse(path_for(pid)/(iid+'.mp4'), media_type='video/mp4')

class Review(BaseModel):
    decision: Literal['approved','rejected']

@app.post('/api/studio/projects/{pid}/items/{iid}/review')
def review(pid: str, iid: str, body: Review):
    with LOCK:
        p=load(pid); item=find(p,iid)
        if item['status'] != 'ready': raise HTTPException(409,'只有生成完成的视频可审核')
        item['review']=body.decision; item['reviewed_at']=time.time()
        if body.decision == 'rejected':
            p['assignments']=[x for x in p.get('assignments',[]) if x['item_id'] != iid]
        save(p); return item

@app.get('/api/studio/devices')
def devices():
    import httpx
    try:
        r=httpx.get('http://127.0.0.1:8765/api/devices',timeout=20); r.raise_for_status(); return r.json()
    except Exception: raise HTTPException(503,'无法连接群控设备服务，请先启动 FishFleet')

class Assignment(BaseModel):
    item_id: str
    serial: str
class Plan(BaseModel):
    assignments: list[Assignment]
@app.post('/api/studio/projects/{pid}/plan')
def plan(pid: str, body: Plan):
    if not body.assignments: raise HTTPException(400,'请选择发布分配')
    with LOCK:
        p=load(pid)
        for x in body.assignments:
            if not x.serial.strip() or find(p,x.item_id)['review']!='approved': raise HTTPException(400,'只能分配已逐条审核通过的视频')
        p['assignments']=[x.model_dump() for x in body.assignments]; save(p); return {'assignments':p['assignments'],'notice':'仅保存计划，不会自动发布；发布接入尚未完成'}

# 仅回查已有任务号；永不在恢复时重新提交生成。
RECOVERY_LOCK = threading.Lock()
RECOVERING = set()

def schedule_recovery(pid, iid):
    key=(pid,iid)
    with RECOVERY_LOCK:
        if key in RECOVERING:return
        RECOVERING.add(key)
    threading.Thread(target=recover_item,args=key,daemon=True,name='studio-recovery').start()


def recover_item(pid, iid):
    key=(pid,iid)
    try:
        while True:
            try:
                with LOCK:
                    p=load(pid); item=find(p,iid)
                    if item.get('status') not in ('recovering','submitted','downloading') or not item.get('task_id'): return
                result=pull_item(pid,iid)
                if result.get('ok'): return
                with LOCK:
                    p=load(pid); item=find(p,iid)
                    if item.get('status')=='ready': return
                    if str(result.get('state','')).startswith('UpstreamVideoFailed:'):
                        item.update(status='failed',error=result['state'][:300]); save(p); return
                    item.update(status='recovering',error='查询或下载未完成，正在自动回查：'+str(result.get('state',''))[:200]); save(p)
            except Exception as exc:
                try:
                    with LOCK:
                        p=load(pid); item=find(p,iid)
                        item.update(status='recovering',error='自动回查暂时失败：'+str(exc)[:200]); save(p)
                except Exception: return
            time.sleep(15)
    finally:
        with RECOVERY_LOCK: RECOVERING.discard(key)

@app.on_event('startup')
def resume_known_tasks():
    for f in DATA.glob('*/project.json'):
        try:
            with LOCK:
                p=json.loads(f.read_text(encoding='utf-8')); changed=False; recover=[]
                for x in p.get('prompts',[]):
                    if x.get('status') in ('queued','running','submitted','downloading','recovering'):
                        if x.get('task_id'):
                            x.update(status='recovering',error='服务重启，正在自动回查已有任务'); recover.append(x['id'])
                        else:
                            x.update(status='unknown',error='服务重启，提交结果未知；请核查上游，未自动重试')
                        changed=True
                if changed:save(p)
            for iid in recover:schedule_recovery(p['id'],iid)
        except (ValueError,OSError,KeyError):pass

app.mount('/', StaticFiles(directory=str(BASE / 'static'), html=True), name='studio')
