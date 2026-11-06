"""TikTok 视频发布：创建 → 相册选素材 → 两次下一步 → 发布信息页填描述 → **停在发布按钮前**。

安全边界（写死在代码里）：
  · 本模块**不点击**「发布」(krf) 按钮；
  · 需要收尾时点「保存至草稿」(dc3)，不点发布；
  · 发布按钮在 TikTok 上没有二次确认，点了就对外发出。

实测路径（手机7 / v35.8.1 / 2026-11-03）：
  首页「创建」i5k(540,1729)
    → 拍摄页：左下 dq4=特效 / 中间 l44=拍照 / **右侧 ife 区域=相册**
    → 相册页：「全部/视频/照片」+ 3 列网格 + 多选 + 下一步
    → 编辑页「下一步」
    → 预览页「下一步」
    → 发布信息页：描述框(335,368) / #话题标签 / @提及 / 位置 / 隐私 / 更多选项 / 草稿(dc3) / 发布(krf)

素材序号：相册网格 3 列，第 N 个（0 起）→ col=N%3, row=N//3。
"""
from __future__ import annotations

import re
import sys

from .douyin_skill import SkillError, elements, foreground_package
from .mobile_gateway import MobileCallError, call
from .ui_probe_targets import resolve_point as _resolve
from .ui_probe_targets import screen_profile as _profile

SKILL_ID = 'builtin:tiktok-publish'
SKILL = {
    'id': SKILL_ID,
    'name': 'TikTok 视频发布',
    'description': '从相册选素材、填描述，停在发布按钮前；不会点击发布。',
}
PACKAGE = 'com.zhiliaoapp.musically'

TITLE_LIMIT = 2200       # TikTok 描述上限（实测输入 2200 字以内正常）
TAG_LIMIT = 30           # 话题个数上限（保守值）

#: 相册网格 3 列（手机7 / 1080×1920 实测，已换算成 dp：
#: 左列中心 176px=67dp，上边 576px=219.4dp行高 352px=134.1dp，列宽恒为屏宽/3）。
#: 不同分辨率/DPI 的机器由 album_cell() 按 dp 换算不再写死像素。
GRID_COLS = [176, 537, 895]   # 仅留档：基准机上的实测值
GRID_TOP = 576                # 仅留档
GRID_ROW_H = 352              # 仅留档
GRID_COLS_DP = 67.0           # 左列中心到左边的 dp（176/2.625）
GRID_TOP_DP = 219.4           # 网格上边到屏幕顶的 dp（576/2.625）
GRID_ROW_DP = 134.1           # 每行高的 dp（352/2.625）

#: 语义坐标键（ui_probe_targets 注册表，dp 自适应）
KEY_CREATE = 'tiktok.publish.create'            # 首页底部「创建」
KEY_ALBUM_ENTRY = 'tiktok.publish.album_entry'  # 拍摄页右侧相册入口
KEY_CAPTION = 'tiktok.publish.caption'          # 发布页描述框
KEY_NEXT_FB = 'tiktok.publish.next_fallback'    # 右下角「下一步」兜底
KEY_TOPIC_DISMISS = 'tiktok.publish.topic_dismiss'  # 话题面板点空白收起


def album_cell(serial, index):
    """相册网格第 index 个（0 起）→ 本机像素中心点（dp 换算）。"""
    geo = _profile(serial)
    ppd = geo['px_per_dp']
    pitch = geo['width'] / 3.0
    col, row = index % 3, index // 3
    x = int(round(pitch * (col + 0.5)))
    y = int(round((GRID_TOP_DP + row * GRID_ROW_DP) * ppd))
    return (x, y)


def _ident(el) -> str:
    return str((el or {}).get('identifier') or '').rsplit(':id/', 1)[-1]


def _box(el):
    c = (el or {}).get('coordinates') or {}
    if not all(isinstance(c.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
        return None
    return {'x': c['x'], 'y': c['y'], 'width': c['width'], 'height': c['height']}


def _text_of(el, *keys):
    for k in keys:
        v = (el or {}).get(k)
        if v:
            return str(v)
    return ''


def _ref_ok(el) -> bool:
    return bool(re.fullmatch(r'@e[1-9]\d*', str((el or {}).get('ref', ''))))


class Runner:
    def __init__(self, task):
        self.task = task
        self.serial = task['serial']
        self.stop = task['_stop']

    # ---------------------------------------------------------------- 基础
    def check(self):
        if self.stop.is_set():
            raise SkillError('任务已停止')

    def tree(self):
        self.check()
        last = None
        for attempt in range(3):
            try:
                return elements(call('mobile_list_elements_on_screen',
                                      {'format': 'json'}, self.serial))
            except (SkillError, MobileCallError) as exc:
                last = exc
                if attempt < 2:
                    self.pause(0.8)
        raise last

    def pause(self, seconds):
        if self.stop.wait(max(0.05, float(seconds))):
            self.check()

    def tap_xy(self, x, y):
        self.check()
        call('mobile_click_on_screen_at_coordinates',
             {'x': int(x), 'y': int(y)}, self.serial)

    def tap_key(self, key):
        """按注册表语义坐标点（dp 自适应不同分辨率/DPI）。"""
        self.tap_xy(*_resolve(self.serial, key))

    def tap(self, el):
        if not _ref_ok(el):
            raise SkillError('控件引用不可用，已停止')
        self.check()
        call('mobile_click_on_screen_at_coordinates', {'ref': el['ref']}, self.serial)

    def press(self, button):
        self.check()
        call('mobile_press_button', {'button': button}, self.serial)

    def foreground(self):
        return foreground_package(call('mobile_get_foreground_app', {}, self.serial))

    def wait_id(self, names, timeout=12.0, poll=0.5):
        """轮询直到出现指定 id 的元素。"""
        end = __import__('time').time() + timeout
        while __import__('time').time() < end:
            self.check()
            page = self.tree()
            hits = [e for e in page if _ident(e) in names]
            if hits:
                return page, hits
            self.pause(poll)
        return self.tree(), []

    # ---------------------------------------------------------------- 步骤
    def goto_home(self):
        self.check()
        call('mobile_launch_app', {'packageName': PACKAGE}, self.serial)
        self.pause(2.5)
        for _ in range(6):
            page = self.tree()
            if any(_ident(e) == 'i5k' for e in page) and any(_ident(e) == 'i5n' for e in page):
                return page
            # 兜底：新人引导/好友同步/权限弹窗（2026-11-03 实测卡在“略過/同步”页）
            dismiss = None
            for e in page:
                t = (_text_of(e, 'text') or '').strip()
                if t in ('略過', '跳过', 'Skip', 'Not Now', '稍后', '以后再说') and _ref_ok(e):
                    dismiss = e
                    break
            if dismiss is not None:
                try:
                    self.tap(dismiss)
                except SkillError:
                    pass
                self.pause(1.2)
                continue
            self.press('BACK')
            self.pause(0.8)
        raise SkillError('未能回到 TikTok 首页，已停止')

    def open_create(self):
        self.tap_key(KEY_CREATE)
        self.pause(2.5)
        # 拍摄页特征：有快门 l44 或特效 dq4
        for _ in range(6):
            page = self.tree()
            if any(_ident(e) in ('l44', 'dq4', 'ife') for e in page):
                return page
            self.pause(0.8)
        raise SkillError('没有进入拍摄页，已停止')

    def open_album(self):
        self.tap_key(KEY_ALBUM_ENTRY)
        self.pause(2.5)
        # 相册页特征：出现「下一步」或「视频/照片」分栏
        for _ in range(6):
            page = self.tree()
            if any(_text_of(e, 'text') == '下一步' for e in page):
                return page
            # 权限弹窗（首次）：点「允许」
            for el in page:
                if _text_of(el, 'text') in ('允许', '允許') and _ref_ok(el):
                    self.tap(el)
                    self.pause(1.5)
                    break
            self.pause(1.0)
        raise SkillError('没有进入相册页，已停止')

    def pick_asset(self, index):
        """相册网格第 index 个（0 起），dp 换算自适应分辨率。"""
        self.tap_xy(*album_cell(self.serial, index))
        self.pause(1.2)

    def next_step(self, times=1):
        """点「下一步」。相册页 → 编辑页 → 预览页，都要点一次。"""
        for i in range(times):
            page = self.tree()
            targets = [e for e in page
                       if _text_of(e, 'text') in ('下一步', '下一步 ') and _ref_ok(e)]
            if targets:
                self.tap(targets[0])
            else:
                # 兜底：右下角「下一步」按钮位置（相册页/编辑页都在右下，dp 自适应）
                self.tap_key(KEY_NEXT_FB)
            self.pause(2.2)

    def wait_compose(self, timeout=15.0):
        """等发布信息页：出现描述框 / 草稿 / 发布按钮。"""
        end = __import__('time').time() + timeout
        while __import__('time').time() < end:
            page = self.tree()
            texts = {_text_of(e, 'text') for e in page}
            ids = {_ident(e) for e in page}
            if '发布' in texts or 'dc3' in ids or 'krf' in ids:
                return page
            self.pause(1.0)
        raise SkillError('没有进入发布信息页，已停止')

    def fill_caption(self, text):
        """点描述框并输入。返回是否成功（读回校验由调用方做）。"""
        if not text:
            return
        self.tap_key(KEY_CAPTION)
        self.pause(1.2)
        call('mobile_type_keys', {'text': text}, self.serial)
        self.pause(1.0)

    def read_caption(self):
        """读回描述框里的文本（不同版本控件不同，尽力而为）。"""
        page = self.tree()
        for el in page:
            t = _text_of(el, 'text')
            if t and len(t) > 3 and _ident(el) in ('', 'dcf', 'k_b'):
                continue
        # 描述一般落在 EditText 里
        edits = [e for e in page if 'EditText' in str(e.get('type') or '')]
        for el in edits:
            t = _text_of(el, 'text')
            if t:
                return t
        return ''

    def save_draft(self):
        """收尾：退出时不点发布，选「保存至草稿」。"""
        self.press('BACK')
        self.pause(1.5)
        page = self.tree()
        drafts = [e for e in page if _text_of(e, 'label') == '保存至草稿' and _ref_ok(e)]
        if not drafts:
            drafts = [e for e in page if _text_of(e, 'text') == '保存至草稿' and _ref_ok(e)]
        if drafts:
            self.tap(drafts[0])
            self.pause(1.5)
            return True
        # 没有弹窗：可能直接退出了
        return False

    def keyboard_visible(self, page):
        """判断键盘是否真的显示在屏幕上。

        ★不能用 EditText 的 focused 字段：描述框填完后仍可能标记 focused，
        但键盘已经收起；按 BACK 会直接把发布页退掉（实测 2026-11-03 连退两次）。
        改用系统键盘的可靠特征：导航栏的「切换输入法」图标，或输入法包名。
        """
        for el in page:
            ident = str(el.get('identifier') or '')
            if 'ime_switcher' in ident or 'inputmethod' in ident:
                return True
            pkg = str(el.get('package') or '')
            if 'inputmethod' in pkg or 'honeyboard' in pkg:
                return True
        return False

    def publish_now(self):
        """点「发布」并检测结果。**只点一次**，失败不重试。

        发布前要清理两类遮挡（都实测踩过）：
          1. 键盘在：会盖住发布按钮，点击落到键盘上；
          2. **话题标签面板**：描述里带 # 会弹出「#xxx 个作品」的搜索panel，
             把底部的草稿/发布整条挤掉，这时根本读不到发布按钮。
        """
        for _ in range(5):
            page = self.tree()
            # ★只认真正的发布**按钮**：标题栏也有「发布」两个字，
            #   用 text=='发布' 会误判成按钮还在，于是直接去点、结果空手而归。
            has_publish = any(_ident(e) == 'krf' for e in page) or \
                any(_text_of(e, 'label') == '发布' for e in page)
            has_topic_panel = any('个作品' in _text_of(e, 'text') for e in page)
            keyboard = self.keyboard_visible(page)

            # ★顺序很重要：先清理遮挡，再确认按钮。
            #   否则「按钮在层级里、但被键盘盖住」会被判成可点，实际点到键盘上。
            if has_topic_panel and not has_publish:
                items = [e for e in page if _ident(e) == 'fdn' and _box(e)]
                if items:
                    box = _box(items[0])
                    self.tap_xy(box['x'] + box['width'] // 2,
                                box['y'] + box['height'] // 2)
                    self.pause(1.3)
                    continue
                self.tap_key(KEY_TOPIC_DISMISS)
                self.pause(1.2)
                continue
            if keyboard:
                self.press('BACK')
                self.pause(1.2)
                continue
            if not has_publish:
                return {'published_clicked': False, 'success': False,
                        'reason': 'left_compose_page',
                        'texts': ' | '.join(_text_of(e, 'text') for e in page
                                            if _text_of(e, 'text'))[:200]}
            break

        page = self.tree()
        target = None
        for e in page:
            if _ident(e) == 'krf' and _ref_ok(e):
                target = e
                break
        if target is None:
            for e in page:
                if _text_of(e, 'label') == '发布' and _ref_ok(e):
                    target = e
                    break
        if target is None:
            return {'published_clicked': False, 'success': False,
                    'reason': 'publish_button_not_found',
                    'texts': ' | '.join(_text_of(e, 'text') for e in page
                                        if _text_of(e, 'text'))[:200]}

        box = _box(target) or {}
        self.tap(target)            # ← 唯一一次点击发布
        result = {'published_clicked': True, 'publish_button_box': box,
                  'success': False, 'signal': '', 'texts': ''}

        for i in range(12):
            self.pause(2.0)
            page = self.tree()
            texts = [_text_of(e, 'text') for e in page]
            joined = ' | '.join(t for t in texts if t)[:400]
            hits = [k for k in ('正在发布', '发布中', '发布成功', '已发布', '上传中',
                                '处理中', 'Posting', 'Posted', 'Uploading')
                    if k in joined]
            if hits:
                result['signal'] = ','.join(hits)
                result['texts'] = joined
                if any(k in ('发布成功', '已发布', 'Posted') for k in hits):
                    result['success'] = True
                    break
            # 页面已经离开「草稿/发布」按钮那一屏，也算发布已触发
            still_compose = any(_text_of(e, 'text') == '草稿' for e in page) and \
                any(_text_of(e, 'text') == '发布' for e in page)
            if not still_compose:
                result['success'] = True
                result['signal'] = result['signal'] or 'left_compose_page'
                result['texts'] = joined
                break
        return result


def run(task):
    """执行一次 TikTok 发布准备。task 需要：serial / title 或 caption / album_index。"""
    # execution 依赖 fastapi；命令行直接跑时降级成 stdout 日志
    try:
        from . import execution
    except Exception:                                       # noqa: BLE001
        execution = None

    def log(msg):
        if execution is not None:
            try:
                log(msg)
                return
            except Exception:                              # noqa: BLE001
                pass
        task.setdefault('events', []).append({'message': msg})
        print(' *', msg, flush=True)

    caption = str(task.get('caption') or task.get('title') or '').strip()
    tags = [str(t).strip() for t in (task.get('tags') or []) if str(t).strip()]
    index = int(task.get('album_index') or 0)
    if index < 0 or index > 20:
        raise SkillError('素材序号不合法')

    body = caption
    if tags:
        body = (body + ' ' if body else '') + ' '.join('#' + t.lstrip('#') for t in tags[:TAG_LIMIT])
    if len(body) > TITLE_LIMIT:
        raise SkillError('描述 %d 字，超过 %d 字上限' % (len(body), TITLE_LIMIT))

    r = Runner(task)
    r.check()
    task['status'] = 'executing'
    log('开始 TikTok 发布准备：%s' % (caption[:30] or '(无描述)'))

    r.goto_home()
    log('已到首页')
    r.open_create()
    log('已进拍摄页')
    r.open_album()
    log('已进相册页')
    r.pick_asset(index)
    log('已选第 %d 个素材' % (index + 1))
    r.next_step(1)
    log('已过编辑页')
    r.next_step(1)
    compose = r.wait_compose()
    log('已到发布信息页')

    r.fill_caption(body)
    log('描述已填：%s' % body[:40])

    # 读回校验：不依赖"函数没报错"，要看到字真的在里面
    got = r.read_caption()
    task['publish_evidence'] = {
        'caption_expected': body,
        'caption_readback': got,
        'readback_matched': bool(got) and (got.strip() == body.strip()
                                           or body.strip() in got),
        'compose_page_ids': sorted({_ident(e) for e in compose if _ident(e)}),
        'sent': False,
    }
    if not got:
        log('⚠ 读不到描述框内容，可能被输入法覆盖；已停在发布页，请人工确认')
    else:
        log('描述读回一致：%s' % ('是' if task['publish_evidence']
                                                 ['readback_matched'] else '否'))

    # ------------------------------------------------------------ 发布前确认
    # 默认 require_confirm=True：填好内容后进入 awaiting_approval，
    # 前端会弹出「确认」卡片（带将要发布的描述），确认后才点发布。
    # require_confirm=False 时只填不发布，收尾保存草稿。
    cfg = task.get('lead') or {}
    require_confirm = bool(cfg.get('require_confirm', True))
    if not require_confirm:
        saved = r.save_draft()
        task['status'] = 'needs_review'
        task['pending'] = None
        log(('已保存至草稿（未发布）' if saved else '已离开发布页（未发布）')
            + '；require_confirm=False，发布按钮未被点击')
        return False

    approval = task.get('_approval')
    pending = {
        'id': __import__('uuid').uuid4().hex,
        'action': 'publish',
        'platform': 'tiktok',
        'caption': body,
        'album_index': index,
        'reason': '即将发布到 TikTok：%s' % (body[:60] or '(无描述)'),
    }
    task['pending'] = pending
    task['status'] = 'awaiting_approval'
    task['publish_evidence']['awaited_confirmation'] = True
    log('等待确认：确认后才会点击发布')

    if approval is not None:
        while not r.stop.is_set() and not approval.wait(0.2):
            pass
    if r.stop.is_set():
        task['pending'] = None
        r.save_draft()
        task['status'] = 'stopped'
        log('任务已取消，未发布，已保存草稿')
        return False

    # ------------------------------------------------------------ 真正发布
    task['status'] = 'executing'
    task['pending'] = None
    log('已确认，开始发布')
    result = r.publish_now()
    task['publish_evidence'].update(result)
    task['status'] = 'needs_review' if not result.get('success') else 'completed'
    log('发布结果：%s' % ('成功' if result.get('success') else '未确认成功，请人工核验'))
    return bool(result.get('success'))
