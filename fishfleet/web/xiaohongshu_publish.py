"""小红书笔记发布：基于当前 Mobile MCP UI 树定位，提交只执行一次。

与抖音发布（douyin_skill）的关键差异，全部来自 2026-10-31 的实测：
  · 小红书控件 id 全部是 `0_resource_name_obfuscated`，**不能靠 id 定位**，
    只能靠 Activity + text/label + 类型，且要求唯一命中。
  · 发布页「发布笔记」按钮空内容时**不带 disabled**，所以点击前必须自己校验内容非空。
  · 标题和正文是两个没有 id 的 EditText，只能按 y 坐标（标题在上、正文在下）区分。
  · 从素材到发布页要经过「预处理页 →（视频时）编辑页 → 发布页」，需逐级点「下一步」。

尚未实测、因此**不实现**的部分（宁可停下也不猜）：
  · 本次新增作品的权威核验：尚无稳定成功提示或作品身份/增量证据。
    离开发布页、浏览到同名作品都只标 needs_review，要求人工核验，绝不自动重复点击。
  · 视频分享 intent 是否能拉起小红书编辑页：代码里会尝试，失败即停止，不降级为
    "在相册里盲选第一张素材"这种危险兜底。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from . import xiaohongshu_templates as templates
from .mobile_gateway import MobileCallError, call

SKILL_ID = 'builtin:xiaohongshu-publish'
SKILL = {
    'id': SKILL_ID,
    'name': '小红书发布指定视频',
    'description': '用选定素材及文案发布小红书笔记；提交前人工确认，结果未知时人工核验。',
}
PACKAGE = templates.PKG

TITLE_LIMIT = 20      # 实测：输入 23 字，回读只剩 20 字，输入框**静默硬截断**；剩余字数显示 0
# 正文上限：2026-10-31 实测连续写入到 6100 字仍未被截断，说明远高于旧版 1000 字。
# 真实上限未测到，这里取实测安全值 6000 作为防呆上限，避免误拒正常长文。
BODY_LIMIT = 6000
TAG_LIMIT = 10
INPUT_CHUNK_LIMIT = 2000  # mobile_gateway.validate 的单次输入上限

# 实测支持的素材类型与对应分享 mime（单张 SEND 才能直达发布页，见下方说明）
VIDEO_SUFFIXES = {'.mp4': 'video/mp4', '.mov': 'video/quicktime'}
IMAGE_SUFFIXES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                  '.png': 'image/png', '.webp': 'image/webp'}


class SkillError(Exception):
    pass


# ---------------------------------------------------------------- MCP 解析
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
            try:
                value = json.loads(match.group(0))
            except ValueError:
                continue
        if isinstance(value, list) and value and all(isinstance(e, dict) for e in value):
            return value
    raise SkillError('无法读取当前 UI 树，已停止，未猜测坐标')


def foreground_package(result):
    """解析 Mobile MCP 前台应用；无法确认时安全停止。"""
    for item in (result or {}).get('content', []):
        if item.get('type') == 'text':
            match = re.search(
                r'Foreground app:.*?\((android|[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)\)',
                item.get('text', ''))
            if match:
                return match.group(1)
    raise SkillError('无法确认当前前台应用，已停止')


def foreground_activity(result):
    """从同一段前台描述里取 Activity；取不到返回空串（不阻断主流程）。"""
    for item in (result or {}).get('content', []):
        if item.get('type') == 'text':
            match = re.search(r'activity:\s*([A-Za-z0-9_.]+)', item.get('text', ''))
            if match:
                return match.group(1)
    return ''


# ---------------------------------------------------------------- 选择器
def pick(tree, page, name):
    """要求当前树中恰有一个匹配；0 个或多个都视作无法确认。"""
    target = templates.PAGES[page].targets[name]
    hits = [e for e in tree if templates.matches(e, target)
            and re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', '')))]
    if len(hits) != 1:
        raise SkillError('控件 %s/%s 不存在或不唯一，已停止' % (page, name))
    return hits[0]


def pick_or_none(tree, page, name):
    try:
        return pick(tree, page, name)
    except SkillError:
        return None


def input_boxes(tree):
    """返回 (标题框, 正文框)。两个 EditText 没有 id，只能用纵向顺序区分。"""
    rows = templates.edit_texts(tree)
    if len(rows) < 2:
        raise SkillError('发布页输入框少于 2 个，页面可能未就绪，已停止')
    return rows[0], rows[1]


def page_guard(activity, dump):
    """确认当前确实在小红书发布链路里；返回页面 key 或 None。"""
    # Activity 的类名不一定以应用包名开头（如 com.xingin.capa.*）。
    # 包名由 run 独立核验；这里仅信任 templates 中已采集的精确 Activity，
    # 防止 page_of 的锚点兜底把外部 Activity 误识别成发布页。
    known_activities = {p.activity for p in templates.PAGES.values() if p.activity}
    if activity and activity not in known_activities:
        return None
    return templates.page_of(dump, activity)


# ---------------------------------------------------------------- 内容校验
def clean_tags(tags):
    out = []
    for item in tags or []:
        value = str(item).strip().lstrip('#').strip()
        if value and value not in out:
            out.append(value)
    return out


def build_body(caption, tags):
    """正文 + 话题行。话题直接以内联 # 形式写入正文（小红书发布页没有独立话题框）。"""
    body = caption.strip()
    if tags:
        body = body + '\n' + ' '.join('#' + t for t in tags)
    return body


def content_matches(actual, expected):
    return actual == expected or (actual.rstrip(' ') == expected and actual.startswith(expected))


def match_work_card(dump, title):
    """在「我」主页的作品网格里找标题匹配的卡片。

    实测 label 形如：`视频,测试发布请忽略,来自老学究,0赞，0阅读`
    或 `笔记,<标题>,来自<作者>,<N>赞，`。只按第 2 段比对标题，不比对作者与计数。
    """
    title = title.strip()
    if not title:
        return ''
    hits = []
    for el in dump:
        label = (el.get('label') or '').strip()
        if not label:
            continue
        parts = [p.strip() for p in label.split(',')]
        if len(parts) < 3:
            continue
        kind, card_title = parts[0], parts[1]
        if kind not in ('视频', '笔记', '图文'):
            continue
        if not parts[2].startswith('来自'):
            continue
        if card_title and card_title == title:
            hits.append(label)
    # 同名作品不具有唯一身份；此结果仅供人工浏览参考，不能证明本次新增。
    return hits[0] if len(hits) == 1 else ''


# ---------------------------------------------------------------- 主流程
def run(task):
    from . import execution
    from .workspace_assets import ROOT, asset
    from ..core.media import MediaPusher
    from .server import _adb_path

    serial = task['serial']
    stop = task['_stop']
    row = asset(task['asset_id'])

    title = task.get('title', '').strip()
    caption = task.get('caption', '').strip()
    tags = clean_tags(task.get('tags'))

    # ---- 参数校验（在碰手机之前完成，避免半途失败留下脏状态）
    suffix = Path(row['filename']).suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        is_video, mime = True, VIDEO_SUFFIXES[suffix]
    elif suffix in IMAGE_SUFFIXES:
        is_video, mime = False, IMAGE_SUFFIXES[suffix]
    else:
        raise SkillError('不支持的素材类型 %s；仅支持视频 MP4/MOV 或图片 JPG/PNG/WebP' % suffix)
    if not title and not caption:
        raise SkillError('标题和正文不能同时为空')
    if len(title) > TITLE_LIMIT:
        raise SkillError('标题 %d 字，超过小红书 %d 字惯例上限，请修改后重试'
                         % (len(title), TITLE_LIMIT))
    if len(caption) > BODY_LIMIT:
        raise SkillError('正文 %d 字，超过 %d 字上限，请修改后重试' % (len(caption), BODY_LIMIT))
    if len(tags) > TAG_LIMIT:
        raise SkillError('话题 %d 个，超过 %d 个上限，请修改后重试' % (len(tags), TAG_LIMIT))
    body = build_body(caption, tags)
    # 测试/批量模式可显式允许清掉发布页残留草稿（默认 False，保护用户内容）
    clear_existing = bool(task.get('clear_existing', False))

    def check():
        if stop.is_set():
            raise SkillError('任务已停止')

    def pause(seconds):
        if stop.wait(seconds):
            check()

    def note(msg):
        execution.log(task, msg)

    def tree():
        check()
        return elements(call('mobile_list_elements_on_screen', {'format': 'json'}, serial))

    def foreground():
        check()
        raw = call('mobile_get_foreground_app', {}, serial)
        return foreground_package(raw), foreground_activity(raw)

    def tap(ref):
        with task['_gate']:
            check()
            call('mobile_click_on_screen_at_coordinates', {'ref': ref}, serial)

    def input_text(value):
        # 空串不符合 gateway 合约；分段之间不回读、不切焦点，最终整体验证。
        for offset in range(0, len(value), INPUT_CHUNK_LIMIT):
            with task['_gate']:
                check()
                call('mobile_type_keys', {'text': value[offset:offset + INPUT_CHUNK_LIMIT]}, serial)

    def press(button):
        with task['_gate']:
            check()
            call('mobile_press_button', {'button': button}, serial)

    def require_app():
        pkg, _ = foreground()
        if pkg != PACKAGE:
            raise SkillError('小红书不在前台，已停止')

    def settle(rounds=6, gap=0.8):
        """等页面稳定：连续两次元素数一致才认为加载完。"""
        last = -1
        for _ in range(rounds):
            pause(gap)
            current = tree()
            if len(current) == last:
                return current
            last = len(current)
        return tree()

    # ---- 1. 把素材交给小红书
    # 实测（2026-10-31）：
    #   · 视频与图片各自走单张 SEND，都能**直达** CapaPostNotePlatformActivity（发布页）；
    #   · SEND_MULTIPLE 传多图只会打开相册页且不预选，多图必须走 UI 手选圆点，故此处不支持多图。
    task['status'] = 'executing'
    kind = '视频' if is_video else '图片'
    note('正在传入选定%s并核验媒体库' % kind)
    pusher = MediaPusher(serial)
    with task['_gate']:
        check()
        if is_video:
            result = pusher.ensure_video(str(ROOT / row['filename']))
            uri, ok = result.content_uri, result.ok
        else:
            pushed = pusher.push(str(ROOT / row['filename']))
            uri, _media_id = pusher.wait_for_media(pushed.remote_path) if pushed.ok else ('', '')
            ok = pushed.ok
    check()
    if not ok or not re.fullmatch(
            r'content://media/external(?:_primary)?/(?:video|images)/media/\d+', uri or ''):
        raise SkillError('%s传入或媒体库识别未确认，未继续发布' % kind)

    note('正在通过分享入口打开小红书')
    cmd = [_adb_path(), '-s', serial, 'shell', 'am', 'start',
           '-a', 'android.intent.action.SEND', '-t', mime,
           '--eu', 'android.intent.extra.STREAM', uri,
           '-p', PACKAGE, '--grant-read-uri-permission']
    with task['_gate']:
        check()
        response = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding='utf-8', errors='replace', timeout=30)
    if response.returncode or 'Error:' in response.stdout or 'Error:' in response.stderr:
        raise SkillError('无法通过分享入口拉起小红书发布页，请改用发布页手动流程')

    # ---- 2. 逐级推进到发布页
    deadline = time.monotonic() + 60
    page = None
    heal_tries, heal_next = 0, time.monotonic() + 4
    while time.monotonic() < deadline:
        check()
        ui = tree()
        pkg, activity = foreground()
        if pkg != PACKAGE:
            pause(0.8)
            continue
        page = page_guard(activity, ui)
        if page == 'post_page':
            note('已进入发布笔记页')
            break
        if page == 'video_preview':
            btn = pick_or_none(ui, 'video_preview', 'next')
            if btn:
                note('视频预处理页：点下一步')
                tap(btn['ref'])
                pause(1.5)
                continue
        if page == 'video_edit':
            btn = pick_or_none(ui, 'video_edit', 'next')
            if btn:
                note('视频编辑页：点下一步（不改特效/文字/贴纸）')
                tap(btn['ref'])
                pause(1.8)
                continue
        if page == 'image_edit':
            btn = pick_or_none(ui, 'image_edit', 'next')
            if btn:
                note('图片编辑页：点下一步（不改滤镜/文字/贴纸）')
                tap(btn['ref'])
                pause(1.8)
                continue
        if page == 'post_entry':
            raise SkillError('停在发布入口弹窗，说明分享入口未接通；已停止，未自动选相册素材')
        if page == 'album':
            raise SkillError('停在相册选择页，说明分享入口未接通；已停止，避免盲选素材')
        pause(0.8)
        # 自愈：固定元素找不到时，用元素树模糊匹配再不行上图让 LLM 看
        if heal_tries < 2 and time.monotonic() > heal_next:
            heal_tries += 1
            heal_next = time.monotonic() + 6
            from . import ui_resolve as UR
            _h = UR.heal_tap(serial, ['下一步', '继续', '发布笔记', '发布'],
                             page_hint='小红书发布流程',
                             log=lambda m: note(m))
            if _h.get('tapped'):
                pause(1.8)
                continue
    else:
        raise SkillError('未能在限时内到达发布笔记页，已停止')

    # ---- 3. 填表前确认页面是干净的
    require_app()
    ui = tree()
    if page_guard(foreground()[1], ui) != 'post_page':
        raise SkillError('发布页识别失败，已停止')
    title_box, body_box = input_boxes(ui)
    if (title_box.get('text') or '').strip() or (body_box.get('text') or '').strip():
        if not clear_existing:
            raise SkillError('发布页已有文字，为避免覆盖已有内容已停止')
        note('发布页有残留草稿（已显式允许清理），正在清空')
        import subprocess as _sp
        adb = _adb_path()
        for _round in range(3):
            for _box in (title_box, body_box):
                try:
                    tap(_box['ref'])
                    pause(0.6)
                except Exception:
                    pass
                with task['_gate']:
                    check()
                    _sp.run([adb, '-s', serial, 'shell',
                             'am', 'broadcast', '-a', 'XW_CLEAR_TEXT'],
                            capture_output=True, timeout=15)
                pause(0.6)
            ui = tree()
            title_box, body_box = input_boxes(ui)
            if not (title_box.get('text') or '').strip() and not (body_box.get('text') or '').strip():
                break
        else:
            raise SkillError('残留草稿清理失败，未覆盖写入')
        note('残留草稿已清空')
    note('填写前：' + templates.state_summary(ui))

    # 2026-11-06 改为**先填正文、最后填标题**。
    # 原因：小红书发布页在切换输入焦点时会重置标题框 —— 原来「先标题、后正文」，
    # 正文一填完标题就没了，补填一次也仍然读不到（实测 run#109 补填后依旧
    # 「标题回读不一致」）。把标题放在最后一步填，就不会再被清掉。
    tap(body_box['ref'])
    input_text(body)
    pause(0.7)
    require_app()
    ui = tree()
    title_box, body_box = input_boxes(ui)
    if not content_matches(body_box.get('text') or '', body):
        raise SkillError('正文回读不一致，未发布')

    tap(title_box['ref'])
    input_text(title)
    pause(0.7)
    require_app()
    ui = tree()
    title_box, body_box = input_boxes(ui)
    note('填写后：' + templates.state_summary(ui))
    # 标题最多校验 3 次（页面重绘/输入法同步偶尔会读空）
    got_title = (title_box.get('text') or '')
    if got_title != title:
        for _retry in range(3):
            note('标题未读到（%r），第 %d 次补填' % (got_title[:12], _retry + 1))
            pause(0.8)
            tap(title_box['ref'])
            input_text(title)
            pause(0.8)
            require_app()
            ui = tree()
            title_box, body_box = input_boxes(ui)
            got_title = (title_box.get('text') or '')
            if got_title == title:
                break
    if got_title != title:
        raise SkillError('标题回读不一致（读到 %r），未发布' % got_title[:20])
    if not content_matches(body_box.get('text') or '', body):
        raise SkillError('正文回读不一致，未发布')

    # 收起键盘，避免遮挡底部发布按钮
    press('BACK')
    pause(0.8)
    require_app()
    ui = tree()
    title_box, body_box = input_boxes(ui)
    if (title_box.get('text') or '') != title or not content_matches(body_box.get('text') or '', body):
        raise SkillError('收起输入面板后内容校验失败，未发布')

    # ---- 4. 交人工确认
    task['observation'] = execution.observe(serial)
    publish_btn = pick(tree(), 'post_page', 'publish')
    with task['_gate']:
        check()
        task['_approval'].clear()
        task['pending'] = {
            'id': uuid.uuid4().hex,
            'action': {'type': 'publish', 'platform': 'xiaohongshu', 'asset_id': task['asset_id'],
                       'title': title, 'description': caption, 'tags': tags},
            'reason': '请核对素材、标题、正文和话题；确认后才会点「发布笔记」。',
            'observed_at': time.time(),
        }
        approval_id = task['pending']['id']
        task['status'] = 'awaiting_approval'
    note('等待确认正式发布')
    while not stop.is_set() and not task['_approval'].wait(0.2):
        pass
    check()

    with task['_gate']:
        check()
        if not task.get('pending') or time.time() - task['pending']['observed_at'] > 180:
            raise SkillError('发布确认已过期，未提交')

    # ---- 5. 只点一次发布
    require_app()
    ui = tree()
    if page_guard(foreground()[1], ui) != 'post_page':
        raise SkillError('确认时已不在发布页，未提交')
    title_box, body_box = input_boxes(ui)
    if (title_box.get('text') or '') != title or not content_matches(body_box.get('text') or '', body):
        raise SkillError('确认时内容已被改动，未提交')
    publish_btn = pick(ui, 'post_page', 'publish')
    try:
        with task['_gate']:
            check()
            if not task.get('pending') or time.time() - task['pending']['observed_at'] > 180:
                raise SkillError('发布确认已过期，未提交')
            if task['pending'].get('id') != approval_id or task.get('status') != 'checking':
                raise SkillError('发布确认无效，未提交')
            task['status'] = 'executing'
            task['pending'] = None  # 锁内消费审批；即使调用结果未知也不允许重放
            call('mobile_click_on_screen_at_coordinates', {'ref': publish_btn['ref']}, serial)
    except MobileCallError:
        with task['_gate']:
            task['status'] = 'needs_review'
        note('发布点击结果未知，请人工检查笔记；不会自动重试')
        return
    note('已发送一次点击指令；是否提交成功尚未确认，不会自动重复点击')

    # ---- 6. 只读浏览核验：标题同名卡可能是旧作品，不证明本次发布新增。
    # 尚无稳定的成功提示、作品唯一身份或提交前后增量证据，因此无论是否
    # 浏览到同名卡，都保持 needs_review；绝不以标题或「公开 N」计数判 completed。
    deadline = time.monotonic() + 60
    evidence = ''
    while time.monotonic() < deadline and not stop.is_set():
        pause(2.0)
        try:
            pkg, activity = foreground()
            if pkg != PACKAGE:
                continue
            ui = tree()
        except (SkillError, MobileCallError):
            continue
        # 发布页还在，说明还没提交完，继续等
        if any('发布笔记' == (e.get('text') or '').strip() for e in ui):
            continue
        # 找底栏「我」，进主页核对作品
        me = next((e for e in ui
                   if (e.get('label') or '').strip() == '我'
                   and re.fullmatch(r'@e[1-9]\d*', str(e.get('ref', '')))), None)
        if me is None:
            pause(1.0)
            continue
        try:
            tap(me['ref'])
            pause(2.5)
            profile = tree()
        except (SkillError, MobileCallError):
            continue
        card = match_work_card(profile, title)
        if card:
            evidence = '浏览到标题精确匹配的作品卡（可能为旧作品）：' + card
            break
        # 主页没匹配上，滚动一次再看
        try:
            with task['_gate']:
                check()
                call('mobile_swipe_on_screen', {'direction': 'up', 'distance': 500}, serial)
            pause(1.2)
            profile = tree()
            card = match_work_card(profile, title)
            if card:
                evidence = '浏览到标题精确匹配的作品卡（可能为旧作品）：' + card
        except (SkillError, MobileCallError):
            pass
        break

    task['status'] = 'needs_review'
    if evidence:
        note(evidence + '；未取得本次新增作品的权威证据，请人工核验，勿重复发布')
    else:
        note('未取得本次新增作品的权威证据，请人工核验笔记是否已发布，勿重复发布')
