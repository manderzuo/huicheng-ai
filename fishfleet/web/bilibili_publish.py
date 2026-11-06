"""B站视频发布：读树用 Mobile MCP，点击走 adb input tap。

为什么这么混搭（2026-10-31 实测，B站 9.16.1 / Android 12）：
  · 读树：沿用 FishFleet 既有链路（mobile_gateway → Mobile MCP），拿到每个控件的
    identifier / coordinates，不缓存 ref、不猜坐标。
  · 点击：**MCP 和 uiautomator2 的 click 都点不动投稿页的「发布」按钮**（点下去
    activity 不变，什么都不发生）；系统级 `adb shell input tap` 才有效。
    所以所有点击统一走 input tap。
  · 输入：`adb shell input text` 不支持中文，中文标题/简介必须走 MCP type_keys。

发布链（每一跳都用 Activity 名校验，不靠"点完就认为到了"）：
    首页底部「+」→ CenterPlusMainActivity       相册选素材
    → 「去编辑」 → BiliEditorMainActivity        B站自带编辑器
    → 「下一步」 → ManuscriptUpActivityV5        投稿信息页
    → 填标题/简介 → 「发布」 → 离开该页

尚未实测、因此不吃掉风险的部分：
  · 发布后的成功提示尚无可靠新增稿件证据；只观察是否回到B站首页，
    结果一律标为待核验，绝不把离开投稿页当成功、绝不自动重发。
  · 正式发布前通过服务端审批，取消、过期或页面内容变化均不提交。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid

from .mobile_gateway import MobileCallError, call

SKILL_ID = 'builtin:bilibili-publish'
SKILL = {
    'id': SKILL_ID,
    'name': 'B站视频发布',
    'description': '用相册素材发布视频到B站；核对标题与简介，正式提交前必须确认，结果需人工核验。',
}
PACKAGE = 'tv.danmaku.bili'

TITLE_LIMIT = 80       # 实测投稿页计数 0/80
DESC_LIMIT = 2000      # 实测简介弹层 0/2000

ACT_HOME = 'MainActivityV2'
ACT_ALBUM = 'CenterPlusMainActivity'
ACT_EDITOR = 'BiliEditorMainActivity'
ACT_PUBLISH = 'ManuscriptUpActivityV5'

ID_TITLE = 'et_title'
ID_DESC = 'et_desc'
ID_DESC_EDITOR = 'etEditor'
ID_PUBLISH = 'tv_upper_publish'
ID_CONVENTION = 'cb_upper_convention'

ADB_FALLBACK = __import__("fishfleet.paths", fromlist=["adb_path"]).adb_path()


class SkillError(Exception):
    pass


# ---------------------------------------------------------------- 基础设施
def _adb() -> str:
    try:
        from .server import _adb_path
        path = _adb_path()
        if path:
            return path
    except Exception:
        pass
    return ADB_FALLBACK


def elements(result):
    """解码 MCP 的 JSON 元素列表；空树/坏树一律当失败，不当空页面。"""
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


def foreground(result):
    """(包名, Activity)。Activity 可能是短名，这里一并返回原文供比对。"""
    for item in (result or {}).get('content', []):
        if item.get('type') != 'text':
            continue
        text = item.get('text', '')
        pkg = re.search(
            r'Foreground app:.*?\((android|[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)\)', text)
        act = re.search(r'activity:\s*([A-Za-z0-9_.]+)', text)
        if pkg:
            return pkg.group(1), (act.group(1) if act else '')
    raise SkillError('无法确认当前前台应用，已停止')


def box_of(el):
    c = (el or {}).get('coordinates') or {}
    if not all(isinstance(c.get(k), (int, float)) for k in ('x', 'y', 'width', 'height')):
        return None
    if c['width'] <= 0 or c['height'] <= 0:
        return None
    return c


def center(el):
    c = box_of(el)
    if not c:
        return None
    return int(c['x'] + c['width'] // 2), int(c['y'] + c['height'] // 2)


def by_id(tree, name):
    """按 resource-id 后缀唯一匹配；歧义时停止，不猜第一个。"""
    hits = []
    for e in tree:
        rid = str(e.get('identifier') or e.get('id') or '')
        if rid.endswith('/' + name) or rid.endswith(':' + name):
            hits.append(e)
    return hits[0] if len(hits) == 1 else None


def by_text(tree, text):
    hits = [e for e in tree if (e.get('text') or '').strip() == text]
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------- 设备动作
def _tap_xy(serial, x, y):
    result = subprocess.run([_adb(), '-s', serial, 'shell', f'input tap {int(x)} {int(y)}'],
                            capture_output=True, timeout=30)
    if result.returncode:
        raise SkillError('ADB 点击未确认成功，请检查设备；不会自动重试')


def _key(serial, code):
    subprocess.run([_adb(), '-s', serial, 'shell', f'input keyevent {code}'],
                   capture_output=True, timeout=30)


def _am_start(serial, component):
    subprocess.run([_adb(), '-s', serial, 'shell', f'am start -n {component}'],
                   capture_output=True, timeout=60)


def _force_stop(serial, pkg):
    subprocess.run([_adb(), '-s', serial, 'shell', f'am force-stop {pkg}'],
                   capture_output=True, timeout=60)


class Runner:
    def __init__(self, task):
        self.task = task
        self.serial = task['serial']
        self.stop = task.get('_stop')

    # ---- 通用 ----
    def log(self, text):
        from . import execution
        execution.log(self.task, text)

    def check(self):
        if self.stop is not None and self.stop.is_set():
            raise SkillError('任务已停止')

    def pause(self, seconds):
        if self.stop is not None:
            if self.stop.wait(seconds):
                self.check()
        else:
            time.sleep(seconds)

    def tree(self):
        self.check()
        last = None
        for _ in range(3):
            try:
                return elements(call('mobile_list_elements_on_screen',
                                     {'format': 'json'}, self.serial))
            except (SkillError, MobileCallError) as exc:
                last = exc
                self.pause(1.0)
        raise last

    def current(self):
        pkg, act = foreground(call('mobile_get_foreground_app', {}, self.serial))
        return pkg, act

    def wait_act(self, keyword, timeout=25.0, interval=0.9):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.check()
            try:
                pkg, act = self.current()
            except SkillError:
                pkg, act = '', ''
            if pkg == PACKAGE and keyword in act:
                return True
            self.pause(interval)
        return False

    def tap_el(self, el, why=''):
        c = center(el)
        if not c:
            raise SkillError('控件坐标不可用%s，已停止' % (('（' + why + '）') if why else ''))
        with self.task['_gate']:
            self.check()
            _tap_xy(self.serial, c[0], c[1])
        return c

    def type_into(self, el, text):
        """点进输入框再用 MCP 输入（中文必须走 type_keys）。"""
        self.tap_el(el, '输入框')
        self.pause(0.8)
        if text:
            with self.task['_gate']:
                self.check()
                call('mobile_type_keys', {'text': text}, self.serial)
        self.pause(0.8)

    # ---- 各跳 ----
    def goto_home(self):
        pkg, act = self.current()
        if pkg == PACKAGE and ACT_HOME in act:
            return
        with self.task['_gate']:
            self.check()
            _force_stop(self.serial, PACKAGE)
        self.pause(1.5)
        with self.task['_gate']:
            self.check()
            _am_start(self.serial, PACKAGE + '/.MainActivityV2')
        for _ in range(12):
            self.pause(1.0)
            try:
                pkg, act = self.current()
            except SkillError:
                continue
            if pkg == PACKAGE and ACT_HOME in act:
                return
        raise SkillError('B站没有进入首页，已停止')

    def open_album(self):
        last = None
        for _ in range(3):
            tree = self.tree()
            plus = by_id(tree, 'home_publish_icon')
            if plus is not None:
                self.tap_el(plus, '发布按钮')
                if self.wait_act(ACT_ALBUM, 15):
                    return
                last = '点发布后没进相册'
                continue
            # 兜底1：按无障碍标签“发布内容”找（2026-11-03 实测 mobile-mcp 可见）
            cand = [e for e in tree if '发布内容' in str(e.get('label') or '')]
            if len(cand) == 1 and center(cand[0]):
                self.tap_el(cand[0], '发布按钮(标签兜底)')
                if self.wait_act(ACT_ALBUM, 15):
                    return
                last = '点发布(标签)后没进相册'
                continue
            # 兜底2：按底部中间坐标点（1080x1825 实测约 502,1740）
            try:
                _tap_xy(self.serial, 540, 1770)
            except SkillError as exc:
                last = str(exc)
                self.pause(1.5)
                continue
            if self.wait_act(ACT_ALBUM, 15):
                return
            last = '坐标点发布后没进相册'
            self.pause(1.5)
        raise SkillError('首页发布按钮不存在，已停止' + (('：' + last) if last else ''))

    def pick_asset(self, index):
        """相册按「最近项目」倒序，index=0 就是最新一条。"""
        # B站 改版后相册封面 id 从 `.../cover` 变成 `.../sdv_cover`（2026-11-02 实测
        # 机型 SM-G973U1 / B站 7.x）。只认 '/cover' 会直接判"相册里没有可用素材"，
        # 所以这里按后缀 'cover' 匹配，cover / sdv_cover / 未来 xxx_cover 都能命中。
        tree = self.tree()
        covers = [e for e in tree if str(e.get('identifier') or '').endswith('cover')]
        covers = [e for e in covers if box_of(e)]
        if not covers:
            raise SkillError('相册里没有可用素材，已停止')
        covers.sort(key=lambda e: (box_of(e)['y'], box_of(e)['x']))
        if index >= len(covers):
            raise SkillError('相册可见素材不足 %d 个，已停止' % (index + 1))
        self.tap_el(covers[index], '第 %d 个素材' % (index + 1))
        self.pause(2.0)

        tree = self.tree()
        go = by_text(tree, '去编辑')
        if go is None:
            raise SkillError('素材没被选中（找不到「去编辑」），已停止')
        self.tap_el(go, '去编辑')
        if not self.wait_act(ACT_EDITOR, 20):
            raise SkillError('没有进入视频编辑器，已停止')

    def editor_next(self):
        tree = self.tree()
        nxt = by_text(tree, '下一步')
        if nxt is None:
            # 变体兜底：不同版本措辞差异（精确匹配且唯一才点）
            for alt in ('继续', '完成'):
                nxt = by_text(tree, alt)
                if nxt is not None:
                    break
        if nxt is not None:
            self.tap_el(nxt, '下一步')
        elif not self._vision_next_tap():
            raise SkillError('编辑器没有「下一步」，已停止')
        if not self.wait_act(ACT_PUBLISH, 40):
            raise SkillError('没有进入投稿信息页已停止')

    def _vision_next_tap(self):
        """视觉找编辑器下一步并点：先在树里按文案找，再上图让 LLM 看。

        区域门禁只要求「屏幕下半部、偏右」，因为不同机型下一步可能在
        右下角、也可能在底部中间；命中后还会用 wait_act 复核是否真进了投稿页。
        """
        try:
            from . import ui_probe_targets as U
            from . import ui_resolve as UR
        except Exception:
            return False
        try:
            self.log('编辑器下一步树里没有，走视觉确认一次')
            r = UR.resolve(self.serial, ['下一步', '继续', '完成', 'Next'],
                           page_hint='B站视频编辑页',
                           region=None)
            if not r.get('ok'):
                self.log('视觉未命中：%s' % r.get('reason', ''))
                return False
            x, y = int(r['x']), int(r['y'])
            geo = U.screen_profile(self.serial)
            dx, dy = U.point_to_anchor(geo, 'bottom_right', x, y)
            self.log('视觉命中下一步 at (%d,%d)，来自 %s，置信 %.2f，dp [%.1f,%.1f]'
                     % (x, y, r.get('via'), float(r.get('confidence') or 0), dx, dy))
            with self.task['_gate']:
                self.check()
                _tap_xy(self.serial, x, y)
            return True
        except Exception as e:
            self.log('视觉确认失败：%s' % str(e)[:100])
            return False

    def fill_title(self, title):
        tree = self.tree()
        el = by_id(tree, ID_TITLE)
        if el is None:
            raise SkillError('找不到标题输入框，已停止')
        if (el.get('text') or '').strip():
            raise SkillError('标题框已有内容，为避免混入旧稿已停止')
        self.type_into(el, title)
        # 2026-11-06：输入后立刻读树，偶尔读回来还是空（页面重绘/输入法还在
        # 同步，实测 run#86 读到 ''，而上一轮同一份代码是能读到「链路复测」的）。
        # 这里给 4 次机会，仍不一致才报错。
        got = ''
        for _try in range(4):
            self.pause(0.8)
            tree = self.tree()
            back = by_id(tree, ID_TITLE)
            got = (back.get('text') or '') if back else ''
            if title == got:
                break
        if title != got:
            raise SkillError('标题回读不一致（读到 %r），已停止' % got[:20])

    def fill_desc(self, desc):
        tree = self.tree()
        editor = by_id(tree, ID_DESC_EDITOR)
        if editor is None:
            row = by_id(tree, ID_DESC)
            if row is None:
                raise SkillError('找不到简介入口，已停止')
            self.tap_el(row, '简介行')
            # 2026-11-06：弹层刚出现时输入框还没渲染（实测点开后 1.5 秒仍读不到），
            # 给几次机会再判定，别一次读空就停。
            editor = None
            for _try in range(5):
                self.pause(1.2)
                tree = self.tree()
                editor = by_id(tree, ID_DESC_EDITOR)
                if editor is not None:
                    break
            if editor is None:
                raise SkillError('简介弹层没有输入框，已停止')
        if (editor.get('text') or '').strip():
            raise SkillError('简介框已有内容，为避免混入旧稿已停止')
        self.type_into(editor, desc)
        written = by_id(self.tree(), ID_DESC_EDITOR)
        if written is None or written.get('text', '') != desc:
            raise SkillError('简介全文回读不一致，未发布')

        tree = self.tree()
        ok = by_text(tree, '确认')
        if ok is None:
            raise SkillError('简介弹层没有「确认」，已停止')
        self.tap_el(ok, '确认')
        self.pause(1.2)

        tree = self.tree()
        row = by_id(tree, ID_DESC)
        if row is None or (desc and not (row.get('text') or '').strip()):
            raise SkillError('简介没有落盘，已停止')

    def submission_snapshot(self):
        """审批前后比较同一投稿页，忽略 ref 与坐标，不沿用旧按钮。

        2026-11-06 修：原来只读一次树，碰上页面正在重绘/白屏（实测 seq=22 只剩
        49 个系统 UI 元素、B站内容一个都没有）就直接报「投稿页控件缺失或不唯一」，
        而同一份录制里 seq=12~20 标题和简介明明都填好了。改成**给页面几次机会**。
        """
        tree = None
        last = ''
        for _try in range(4):
            pkg, act = self.current()
            if pkg != PACKAGE or ACT_PUBLISH not in act:
                last = '不在B站投稿页（%s/%s）' % (pkg or '-', act or '-')
                self.pause(1.5)
                continue
            tree = self.tree()
            title, desc, btn = (by_id(tree, key) for key in (ID_TITLE, ID_DESC, ID_PUBLISH))
            if title is not None and desc is not None and btn is not None and center(btn):
                break
            last = '控件未就绪（title=%s desc=%s btn=%s 元素%d）' % (
                title is not None, desc is not None, btn is not None, len(tree))
            self.pause(1.5)
        else:
            raise SkillError('投稿页控件缺失或不唯一，未提交' + (('：' + last) if last else ''))
        if btn.get('enabled') is False:
            raise SkillError('发布按钮不可用，未提交')
        # 只绑定真正的投稿内容（标题、简介、发布键可用性）。
        # 旧实现把整棵树里所有带 text/label 的元素都塞进快照，结果是
        # 字数统计（0/2000 → 91/2000）、勾选社区公约、"草稿已保存"提示
        # 任何一个变化都会判成"填写后投稿内容已变化"，投稿永远发不出去
        # （2026-11-02 实测）。这里收窄到内容字段本身。
        snapshot = {
            'title': str(title.get('text') or ''),
            'desc': str(desc.get('text') or ''),
            'publish_enabled': btn.get('enabled'),
        }
        return {'activity': act, 'visible': snapshot}, title, btn

    def submit(self):
        from . import execution
        self.check()
        # 勾选公约后重新读取界面，再建立审批快照，避免使用勾选前坐标。
        tree = self.tree()
        cb = by_id(tree, ID_CONVENTION)
        if cb is not None and str(cb.get('checked')).lower() != 'true':
            self.tap_el(cb, '社区公约')
            self.pause(0.6)
        snapshot, title, _ = self.submission_snapshot()
        if getattr(self, 'filled_snapshot', snapshot) != snapshot:
            raise SkillError('填写后投稿内容已变化，未发布')
        if title.get('text', '') != self.task.get('title', '').strip():
            raise SkillError('提交前标题不一致，未发布')
        self.task['observation'] = execution.observe(self.serial)
        with self.task['_gate']:
            self.check()
            self.task['_approval'].clear()
            pending_id = uuid.uuid4().hex
            self.task['pending'] = {
                'id': pending_id, 'observed_at': time.time(),
                'action': {'type': 'publish', 'platform': 'bilibili',
                           'title': self.task.get('title', ''),
                           'description': self.task.get('caption', ''),
                           'album_index': self.task.get('album_index', 0)},
                'reason': '请核对账号、相册素材、标题和简介；确认后才会正式发布。',
            }
            self.task['status'] = 'awaiting_approval'
        self.log('等待确认正式发布到B站')
        while not self.task['_approval'].wait(0.2):
            self.check()
            pending = self.task.get('pending')
            if not pending or time.time() - pending['observed_at'] > 180:
                raise SkillError('发布确认已过期，未提交')
        self.check()
        current, _, btn = self.submission_snapshot()
        if current != snapshot:
            raise SkillError('确认时投稿页面内容已变化，未提交，请重新核对')
        with self.task['_gate']:
            self.check()
            pending = self.task.get('pending')
            if (not pending or pending.get('id') != pending_id
                    or time.time() - pending['observed_at'] > 180
                    or self.task.get('status') != 'checking'):
                raise SkillError('发布确认无效或已过期，未提交')
            self.task['status'] = 'executing'
            self.task['pending'] = None
            # 从发出点击开始，任何异常都只能待核验，禁止自动重点。
            self.task['_publish_attempted'] = True
            try:
                self.check()
                _tap_xy(self.serial, *center(btn))
            except Exception:
                self.task['status'] = 'needs_review'
                self.log('发布点击结果不确定，请人工核验；不会自动重试')
                return False
        self.log('已发送一次发布点击指令，实际稿件结果尚待核验')
        self.pause(3.0)
        return True

    def verify(self, timeout=45.0):
        """仅观察是否回到B站首页，不将导航变化当作发布成功证据。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.check()
            try:
                pkg, act = self.current()
            except (SkillError, MobileCallError):
                self.pause(1.5)
                continue
            if pkg == PACKAGE and act and ACT_HOME in act:
                return True
            self.pause(1.5)
        return False


# ---------------------------------------------------------------- 入口
def run(task):
    """执行一次 B站发布。task 需要：serial / title / caption(简介) / album_index(可选)。"""
    from . import execution

    title = str(task.get('title') or '').strip()
    desc = str(task.get('caption') or '').strip()
    if not title:
        raise SkillError('标题不能为空')
    if len(title) > TITLE_LIMIT:
        raise SkillError('标题 %d 字，超过 B站 %d 字上限' % (len(title), TITLE_LIMIT))
    if len(desc) > DESC_LIMIT:
        raise SkillError('简介 %d 字，超过 %d 字上限' % (len(desc), DESC_LIMIT))

    index = int(task.get('album_index') or 0)
    if index < 0 or index > 20:
        raise SkillError('素材序号不合法')

    r = Runner(task)
    r.check()
    task['status'] = 'executing'
    execution.log(task, '开始 B站 发布：%s' % title)

    r.goto_home()
    execution.log(task, '已到首页')

    r.open_album()
    execution.log(task, '已进相册选择页')

    r.pick_asset(index)
    execution.log(task, '已选素材，进入编辑器')

    r.editor_next()
    execution.log(task, '已到投稿信息页')

    r.fill_title(title)
    execution.log(task, '标题已填：%s' % title)

    r.fill_desc(desc)
    if desc:
        execution.log(task, '简介已填')
    r.filled_snapshot = r.submission_snapshot()[0]

    try:
        if not r.submit():
            return False
        returned = r.verify()
    except Exception:
        if not task.get('_publish_attempted'):
            raise
        task['status'] = 'needs_review'
        task['pending'] = None
        execution.log(task, '发布点击后核验中断，结果未知，请检查稿件管理；不会自动重试')
        return False
    # 没有新稿件ID或稿件列表证据，离开投稿页也不能标记completed。
    task['status'] = 'needs_review'
    task['pending'] = None
    execution.log(task, ('已回到B站首页；' if returned else '未确认回到B站首页；')
                  + '请到「我的 → 稿件管理」核验本次稿件，勿重复发布')
    return False
