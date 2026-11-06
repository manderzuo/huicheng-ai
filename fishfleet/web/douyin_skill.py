"""抖音视频发布：基于当前 Mobile MCP UI 树定位，提交只执行一次。"""
from __future__ import annotations
import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from . import douyin_templates as templates
from .mobile_gateway import call, MobileCallError

SKILL_ID = 'builtin:douyin-publish'
SKILL = {'id': SKILL_ID, 'name': '抖音发布指定视频', 'description': '用选定素材及文案发布；提交前确认，结果未知时人工核验。'}
PACKAGE = 'com.ss.android.ugc.aweme'

class SkillError(Exception):
    pass


def elements(result):
    """解码 MCP 的 JSON 元素列表；不把空/损坏的 UI 树当成有效页面。"""
    for item in (result or {}).get('content', []):
        if item.get('type') != 'text':
            continue
        text = item.get('text', '')
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            match = re.search(r'\[\s*\{.*\}\s*\]', text, re.S)
            if not match:
                continue
            try: value = json.loads(match.group(0))
            except ValueError: continue
        if isinstance(value, list) and value and all(isinstance(e, dict) for e in value):
            return value
    raise SkillError('无法读取当前 UI 树，已停止，未猜测坐标')


def foreground_package(result):
    """解析 Mobile MCP 前台应用；无法确认时安全停止。"""
    for item in (result or {}).get('content', []):
        if item.get('type') == 'text':
            match = re.search(r'Foreground app:.*?\((android|[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)\)', item.get('text', ''))
            if match:
                return match.group(1)
    raise SkillError('无法确认当前前台应用，已停止')


def foreground_activity(result):
    """解析当前前台 Activity 名；解析不到返回空串（不抛错）。

    用于区分"没进会话页"和"被弹到登录页"这类完全不同的原因：
    实测未登录账号点「私信」会跳到 account.business.login.DYLoginActivity，
    但 UI 树里仍有抖音标识，单看元素无法判断。
    """
    for item in (result or {}).get('content', []):
        if item.get('type') == 'text':
            match = re.search(r'activity:\s*([A-Za-z0-9_.$]+)', item.get('text', ''))
            if match:
                return match.group(1)
    return ''


def resolver_choice(tree, clicked):
    """先选择发布入口，再选择仅限一次；歧义时不猜测。"""
    for names in (('发布到抖音', '發布到抖音'), ('仅限一次', '僅限一次', '仅此一次')):
        hits = [e for e in tree if (e.get('text') or '').strip() in names
                and re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', '')))]
        if len(hits) > 1:
            raise SkillError('系统选择器目标不唯一，已停止')
        if hits and (hits[0].get('text') or '').strip() not in clicked:
            return hits[0]
    return None


def select(tree, page, name):
    """要求当前树中恰有一个有效引用；不缓存或猜测坐标。"""
    target = templates.PAGES[page].targets[name]
    hits = [e for e in tree if templates.matches(e, target)
            and re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', '')))]
    if len(hits) != 1:
        raise SkillError(f'控件 {page}/{name} 不存在或不唯一，已停止')
    return hits[0]


def _publish_tree_any(tree):
    """结构放宽：可见 EditText 里挑最高两枚当标题/描述，按文案找发布按钮。

    只在模板匹配失败时用（不同抖音版本 id 会改）。仍要求「至少两个输入框 +
    一个发布按钮」，找不到就返回 None，绝不猜着点。
    """
    def box(e):
        c = (e or {}).get('coordinates') or {}
        try:
            x, y = float(c['x']), float(c['y'])
            w, h = float(c['width']), float(c['height'])
        except (KeyError, TypeError, ValueError):
            return None
        if w <= 0 or h <= 0:
            return None
        return {'x': x, 'y': y, 'w': w, 'h': h}

    edits = []
    for e in tree:
        if 'EditText' not in str(e.get('type', '')):
            continue
        b = box(e)
        if not b or b['h'] < 30 or not re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', ''))):
            continue
        edits.append((b['y'], e))
    if len(edits) < 2:
        return None
    edits.sort(key=lambda t: t[0])
    title, desc = edits[0][1], edits[1][1]
    names = ('发布', '发布作品', '立即发布', '发作品', 'Publish', 'Post')
    for e in tree:
        if (e.get('text') or '').strip() in names \
                and re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', ''))):
            return title, desc, e
    return None


def publish_tree(tree):
    """先按模板精确认；认不出就用结构兜底（版本差异），都不行才报错。"""
    try:
        title = select(tree, 'publish', 'title_input')
        desc = select(tree, 'publish', 'desc_input')
        button = select(tree, 'publish', 'publish_work')
        if not all('EditText' in str(e.get('type', '')) for e in (title, desc)):
            raise SkillError('发布页输入框类型不符，已停止')
        return title, desc, button
    except SkillError:
        got = _publish_tree_any(tree)
        if got is None:
            raise
        return got

def ui_state(tree):
    """仅记录页面结构状态，不记录用户文案、截图或可复用引用。"""
    ids = {str(e.get('identifier') or '').rsplit(':id/', 1)[-1] for e in tree}
    fields = [e for e in tree if str(e.get('identifier') or '').rsplit(':id/', 1)[-1] in ('ip_', 'ibo')]
    focus = ','.join(sorted(str(e.get('identifier') or '').rsplit(':id/', 1)[-1]
                            for e in fields if e.get('focused') is True)) or '未知/无'
    return f"标题框={'有' if 'ip_' in ids else '无'}，描述框={'有' if 'ibo' in ids else '无'}，候选层={'有' if 'lgl' in ids else '无'}，输入焦点={focus}"


def topic_first(tree, expected):
    """仅选择候选层内的首项；首项须与用户最后一个话题完全一致。"""
    overlays = [e for e in tree if e.get('identifier') == PACKAGE + ':id/lgl']
    if len(overlays) != 1:
        raise SkillError('话题候选层不存在或不唯一，未选择')
    bounds = overlays[0].get('coordinates') or {}
    if not all(isinstance(bounds.get(k), (int, float)) for k in ('x','y','width','height')):
        raise SkillError('无法确认话题候选区域，未选择')
    x, y, w, h = (bounds[k] for k in ('x','y','width','height'))
    items = []
    for e in tree:
        if e.get('identifier') != PACKAGE + ':id/lgp': continue
        c = e.get('coordinates') or {}
        if not all(isinstance(c.get(k), (int, float)) for k in ('x','y','width','height')): continue
        if c['width'] <= 0 or c['height'] <= 0 or not (x <= c['x'] < x+w and y <= c['y'] < y+h): continue
        if not re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', ''))): continue
        items.append(e)
    items.sort(key=lambda e: (e['coordinates']['y'], e['coordinates']['x']))
    if not items or (items[0].get('text') or '').strip() != '#' + expected:
        raise SkillError('首个联想话题与指定话题不一致，已停止以免更改标签')
    if len(items) > 1 and items[0]['coordinates']['y'] == items[1]['coordinates']['y']:
        raise SkillError('首个联想话题位置不唯一，已停止')
    return items[0]


def content_matches(actual, expected):
    return actual == expected or (actual.rstrip(' ') == expected and actual.startswith(expected))


def unique_label(tree, text):
    hits = [e for e in tree if (e.get('text') or '').strip() == text
            and re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', '')))]
    return hits[0] if len(hits) == 1 else None


def is_own_profile(tree):
    """本人主页需同时有编辑入口和作品标签，避免将朋友页误当主页。"""
    editing = any(s in ('编辑主页', '编辑资料', '编辑个人资料') for e in tree for s in ((e.get('text') or '').strip(), (e.get('label') or '').strip()))
    works = any(s.startswith('作品,按钮') or s == '作品' for e in tree for s in ((e.get('text') or '').strip(), (e.get('label') or '').strip()))
    return editing and works


def success_banner(tree):
    """抖音发布成功后底部白色提示条：文本必须精确且唯一，避免误判按钮。"""
    hits = [e for e in tree if (e.get('text') or '').strip() == '发布成功'
            and re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', '')))]
    return '发布成功提示条' if len(hits) == 1 else ''


def newest_work(tree):
    """选择可见作品网格第一格，按位置排除草稿；不猜测不可见内容。"""
    grid = [e for e in tree if e.get('identifier') == PACKAGE+':id/i+2']
    if len(grid) != 1: return None
    bounds = grid[0].get('coordinates') or {}
    if not all(isinstance(bounds.get(k),(int,float)) for k in ('x','y','width','height')): return None
    gx,gy,gw,gh=(bounds[k] for k in ('x','y','width','height'))
    covers=[]; drafts=[]
    for e in tree:
        c=e.get('coordinates') or {}
        if not all(isinstance(c.get(k),(int,float)) for k in ('x','y','width','height')): continue
        x,y,w,h=(c[k] for k in ('x','y','width','height'))
        if w <= 0 or h <= 0 or not (gx <= x < gx+gw and gy <= y < gy+gh): continue
        if e.get('identifier') == PACKAGE+':id/jlh' and '草稿' in str(e.get('label') or ''): drafts.append(c)
        if e.get('identifier') == PACKAGE+':id/cover' and re.fullmatch(r'@e[1-9]\d*',str(e.get('ref',''))): covers.append(e)
    covers.sort(key=lambda e:(e['coordinates']['y'],e['coordinates']['x']))
    if not covers: return None
    first=covers[0]['coordinates']
    if drafts and not any(abs(d['x']-first['x'])<3 and abs(d['y']-first['y'])<3 for d in drafts): return None
    if drafts: covers=covers[1:]
    if not covers or any('置顶' in str(e.get('text') or e.get('label') or '') for e in tree): return None
    return covers[0]


def verified_result(tree, title):
    """标题前缀 + 近期发布时间同时匹配，作为「这条是我刚发的」的硬证据。

    2026-11-06 修（2号机实测 run#2ad5b80f 复现）：
      1. 发布时间元素的 id 实测是 **4v4**，旧代码只认 `vfr` → 永远取不到时间，
         于是 `len(times)!=1` 直接 return ''，核验必然失败；
      2. 发布时间文本实测带前导空格：`' · 1秒前'`，
         旧正则 `(?:·\\s*)?` 容忍不了那个空格，即便 id 对上也会判空。
    两处都是"静默失败"——不报错，只是永远核不出来，最后落到 needs_review。
    """
    if not title:
        return ''
    titles = [(e.get('text') or '').strip() for e in tree
              if str(e.get('identifier') or '').endswith(':id/desc')]
    # 发布时间：新版 4v4 / 旧版 vfr 都认；text 与 label 都可作为来源
    times = []
    for e in tree:
        short = str(e.get('identifier') or '').rsplit(':id/', 1)[-1]
        if short in ('vfr', '4v4'):
            v = (e.get('text') or e.get('label') or '').strip()
            if v:
                times.append(v)
    if len(titles) != 1 or len(times) != 1:
        return ''
    value = titles[0]
    # desc 里通常是「标题 + 空格 + 描述/话题」，用前缀匹配
    if value != title and not value.startswith(title + ' '):
        return ''
    # 容忍前导空格、中间点、以及 label 里的「发布时间：」前缀
    m = re.fullmatch(r'\s*(?:发布时间[:：])?\s*[·・\s]*'
                     r'(刚刚|\d{1,2}\s*秒前|[1-9]\d*\s*分钟前)\s*',
                     times[0])
    return '最新作品标题匹配，发布时间' + m.group(1) if m else ''


def run(task):
    from . import execution
    from .workspace_assets import asset, ROOT
    from ..core.media import MediaPusher
    from .server import _adb_path

    serial = task['serial']; stop = task['_stop']; row = asset(task['asset_id'])
    if Path(row['filename']).suffix.lower() != '.mp4':
        raise SkillError('抖音发布技能目前仅支持 MP4 视频')
    title = task.get('title', '').strip(); desc = task.get('caption', '').strip()
    tags = [str(t).strip().lstrip('#').strip() for t in task.get('tags', []) if str(t).strip().lstrip('#').strip()]
    if not title or not desc:
        raise SkillError('标题或描述为空，请先填写')
    if len(title) > 20:
        raise SkillError(f'标题有 {len(title)} 字，超过抖音20字上限，请修改后重试')
    if len(tags) > 5:
        raise SkillError('话题超过5个，请修改后重试')
    content = desc + (('\n' + ' '.join('#' + t for t in tags)) if tags else '')
    def check():
        if stop.is_set(): raise SkillError('任务已停止')
    def pause(seconds):
        if stop.wait(seconds): check()
    def foreground():
        check()
        return foreground_package(call('mobile_get_foreground_app', {}, serial))
    def tree():
        check(); return elements(call('mobile_list_elements_on_screen', {'format':'json'}, serial))
    def tap(ref):
        with task['_gate']:
            check(); call('mobile_click_on_screen_at_coordinates', {'ref': ref}, serial)
    def input_text(value):
        with task['_gate']:
            check(); call('mobile_type_keys', {'text':value}, serial)
    def note(message): execution.log(task, message)
    def require_app():
        if foreground() != PACKAGE: raise SkillError('抖音不在前台，已停止')
    def fields(ui):
        a, b, button = publish_tree(ui)
        if (a.get('text') or '') != title or not content_matches(b.get('text') or '', content):
            raise SkillError('标题或描述回读不一致，未发布')
        # 2026-11-05 主人定：正文末尾是 #话题 时下方会弹话题联想层，它只是**推荐**；
        # 不选它不会改变标签，反而选中可能把标签改成别的词。不再因为它拒绝提交。
        return button

    task['status'] = 'executing'; note('正在传入选定视频并核验媒体库')
    with task['_gate']:
        check(); result = MediaPusher(serial).ensure_video(str(ROOT / row['filename']))
    check()
    if not result.ok or not re.fullmatch(r'content://media/external(?:_primary)?/video/media/\d+', result.content_uri or ''):
        raise SkillError('视频传入或媒体库识别未确认，未继续发布')
    cmd = [_adb_path(), '-s', serial, 'shell', 'am', 'start', '-a', 'android.intent.action.SEND', '-t', 'video/mp4', '--eu', 'android.intent.extra.STREAM', result.content_uri, '-p', PACKAGE, '--grant-read-uri-permission']
    with task['_gate']:
        check(); response = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    if response.returncode or 'Error:' in response.stdout or 'Error:' in response.stderr:
        raise SkillError('无法打开抖音视频编辑入口')
    note('视频已交给抖音，正在识别页面')
    deadline = time.monotonic() + 40
    next_sent = 0          # 2026-11-06 改成计数：允许重试一次「下一步」
    clicked_choices = set()
    heal_tries, heal_next = 0, time.monotonic() + 3
    while time.monotonic() < deadline:
        check(); ui = tree(); package = foreground()
        if package == PACKAGE:
            # 2026-11-06 修：发布页里「高级设置」是个覆盖层，一旦点进去，
            # 整屏变成「发布后保存至相册 / 保存自己内容带水印 / 高清发布 /
            # 日常权限设置 / 谁可以发弹幕」，标题输入框 ip_ 不在树里 ——
            # 表现为"卡在下一步"，最后超时报「未识别到完整的编辑或发布页面」。
            # 这里先认出来并 BACK 退出，再继续正常流程。
            _blob = ' '.join(str(e.get('text') or '') for e in ui)
            if '高级设置' in _blob and ('高清发布' in _blob or '发布后保存至相册' in _blob):
                note('误入高级设置面板，先退出来')
                # 本作用域没有 press() 这个局部函数（那是别处的写法），
                # 必须走网关 call —— 2026-11-06 第一版误用 press() 直接
                # NameError 把整条发布链打崩了。
                with task['_gate']:
                    check()
                    call('mobile_press_button', {'button': 'BACK'}, serial)
                pause(1.2)
                continue
            try:
                publish_tree(ui); break
            except SkillError: pass
            # 应用内也可能弹分享确认（不同版本/机型与系统弹窗同文案）
            inapp = resolver_choice(ui, clicked_choices)
            if inapp:
                tap(inapp['ref']); clicked_choices.add((inapp.get('text') or '').strip()); pause(2); continue
            try: next_btn = select(ui, 'editor', 'next')
            except SkillError: next_btn = None
            if next_btn and next_sent < 2:
                tap(next_btn['ref']); next_sent += 1; pause(2.5); continue
        # 非抖音前台但出现选择器语义（各厂商 resolver 包名不一）同样处理；
        # resolver_choice 只认精确文案、歧义则停，误点不了别处。
        if package != PACKAGE:
            choice = resolver_choice(ui, clicked_choices)
            if choice:
                tap(choice['ref']); clicked_choices.add(choice['text'].strip()); pause(2); continue
        pause(.8)
        # 2026-11-06 重大修正：**关闭视觉自愈的点击**。
        # 实测 run#178/#179：select() 明明能精确命中「下一步」(id=tkq)，但点击后
        # 页面没进发布页，于是走到这里的自愈；LLM 视觉给了一个"可信坐标"，
        # 结果**点进了「设置」→ 高级设置面板**，把整条发布链带沟里，
        # 日志表现就是「自愈未命中」+「误入高级设置」。
        # 视觉坐标不可复核、点错代价高（可能点到发布/删除），这里只记录不动手。
        if heal_tries < 2 and time.monotonic() > heal_next:
            heal_tries += 1
            heal_next = time.monotonic() + 5
            note('页面仍未识别（第 %d 次），不做视觉点击，继续按模板重试' % heal_tries)
    else:
        try:
            _sample = [e.get('text').strip() for e in ui
                       if (e.get('text') or '').strip()][:12]
            note('页面文字样本：' + ' | '.join(_sample))
        except Exception:
            pass
        raise SkillError('未识别到完整的编辑或发布页面，已停止')

    require_app(); ui = tree(); a, b, _ = publish_tree(ui)
    if (a.get('text') or '') or (b.get('text') or ''):
        raise SkillError('发布页已有文字，为避免覆盖已有内容已停止')
    note('填写前页面状态：' + ui_state(ui))
    tap(a['ref']); input_text(title); pause(.5)
    require_app(); ui = tree(); note('标题填写后：' + ui_state(ui)); a, b, _ = publish_tree(ui)
    if (a.get('text') or '') != title or (b.get('text') or ''):
        raise SkillError('标题回读失败或描述框已变化，未发布')
    tap(b['ref']); input_text(content); pause(.5)
    # 收起键盘和话题候选层；先核验内容，再由当前 UI 树确认遮挡层已消失。
    require_app(); ui = tree(); note('描述及话题填写后：' + ui_state(ui)); a, b, _ = publish_tree(ui)
    if (a.get('text') or '') != title or (b.get('text') or '') != content:
        raise SkillError('标题或描述回读不一致，未发布')
    if any(e.get('identifier') == PACKAGE + ':id/lgl' for e in ui):
        # 2026-11-05 主人定：正文末尾是 #话题 时下方会弹联想层，**不用管、直接收起**。
        # 不选反而更安全：选中推荐词会把 #话题 改成别的标签，等于改了内容。
        note('话题联想层出现（不选，直接收起）')
        with task['_gate']:
            check(); call('mobile_press_button', {'button': 'BACK'}, serial)
        pause(.7)
        require_app(); ui = tree()
        note('收起话题联想后：' + ui_state(ui))
    # 收键盘：若上一步的 BACK 已连键盘一起收了，就别再按（再按会退出发布页）
    keyboard_on = any(e.get('focused') is True
                      and 'EditText' in str(e.get('type') or '') for e in ui)
    if keyboard_on:
        with task['_gate']:
            check(); call('mobile_press_button', {'button': 'BACK'}, serial)
        pause(.7)
    require_app(); ui = tree()
    # 2026-11-06 修：收「话题联想层 + 键盘」可能连按两次 BACK，而抖音发布页
    # 在键盘已收的情况下再吃一次 BACK 会**退回编辑页**（实测 seq=18 填好后
    # seq=21 就回到编辑器，随后报「publish/title_input 不存在或不唯一」）。
    # 这里发现被退出去就重新点一次「下一步」回到发布页，而不是直接失败。
    try:
        fields(ui)
    except SkillError:
        note('收面板时退回了编辑页，重新进入发布页')
        try:
            nb = select(ui, 'editor', 'next')
        except SkillError:
            nb = None
        if nb:
            tap(nb['ref']); pause(2)
            require_app(); ui = tree()
        fields(ui)
    note('收起输入面板后：' + ui_state(ui))
    task['observation'] = execution.observe(serial)
    require_app(); fields(tree())
    task['_approval'].clear()
    task['pending'] = {'id': uuid.uuid4().hex, 'action': {'type':'publish', 'asset_id':task['asset_id'], 'title':title, 'description':desc, 'tags':tags},
                       'reason': '请核对视频、标题、描述和话题；确认后才会发布。', 'observed_at': time.time()}
    task['status'] = 'awaiting_approval'; note('等待确认正式发布')
    while not stop.is_set() and not task['_approval'].wait(.2): pass
    check()
    if not task.get('pending') or time.time() - task['pending']['observed_at'] > 180:
        raise SkillError('发布确认已过期，未提交')
    require_app(); ui = tree(); button = fields(ui)
    try:
        with task['_gate']:
            check(); task['status'] = 'executing'
            call('mobile_click_on_screen_at_coordinates', {'ref': button['ref']}, serial)
    except MobileCallError:
        task['pending'] = None; task['status'] = 'needs_review'
        note('发布点击结果未知，请人工检查作品；不会自动重试'); return
    task['pending'] = None; note('已发送一次点击指令；是否提交成功尚未确认，不会自动重复点击')
    # ------------------------------------------------------------------
    # 发布后核验（2026-11-06 按主人实测重构）
    #
    # 2号机实测事实：点了「发作品」后视频**确实发出去了**，抖音跳到「朋友」页，
    # 页面上当场就有 `id/desc`（我们填的标题+描述）和 `id/4v4`（· 1秒前）。
    # 而旧逻辑有两个静默 bug —— 时间 id 写的是 vfr（实际 4v4）、
    # 时间文本前有空格正则不认 —— 于是「明明成功却核不出来」，白跑一趟。
    #
    # 新逻辑：
    #   ① 找「发布成功」提示条（最快，但它常是 Toast，dump 里可能根本没有）
    #   ② **直接在当前跳转页**用「标题前缀 + 刚刚/N秒前」双匹配判成功 ← 主力
    #   ③ 绝不进「我的 → 作品」核验：那里第一条可能是**私密作品或草稿**，
    #      按位置取第一条会点错（主人明确指出），宁可不做这个动作。
    # ------------------------------------------------------------------
    deadline = time.monotonic() + 60
    seen_snapshots = []
    while time.monotonic() < deadline and not stop.is_set():
        try:
            if foreground() != PACKAGE: continue
            ui = tree()
        except (SkillError, MobileCallError): continue

        # ① 提示条
        evidence = success_banner(ui)
        if evidence:
            task['status'] = 'completed'; note('发布核验通过：' + evidence); return

        # ② 当前页直接匹配（主力判据）
        evidence = verified_result(ui, title)
        if evidence:
            task['status'] = 'completed'
            note('发布核验通过：' + evidence + '（在当前跳转页匹配，未进「我的」，避免私密作品/草稿干扰）')
            return

        # 记录"这一帧看到了什么"，失败时能复盘（只留前 3 帧，避免刷屏）
        if len(seen_snapshots) < 3:
            descs = [(e.get('text') or '').strip()[:26] for e in ui
                     if str(e.get('identifier') or '').endswith(':id/desc')]
            times = []
            for e in ui:
                if str(e.get('identifier') or '').rsplit(':id/', 1)[-1] in ('vfr', '4v4'):
                    v = (e.get('text') or e.get('label') or '').strip()
                    if v:
                        times.append(v[:18])
            seen_snapshots.append('desc=%s 时间=%s' % (descs[:2], times[:2]))

        # ③ 发布页还在（t5r = 发作品按钮）→ 说明还在编辑/上传，继续等，绝不导航
        if any(str(e.get('identifier') or '').endswith(':id/t5r') for e in ui):
            pause(.4); continue
        pause(.4)

    for s in seen_snapshots:
        note('核验时看到：' + s)
    task['status'] = 'needs_review'
    note('未取得关联本作品的成功证据，请人工核验，勿重复发布')
