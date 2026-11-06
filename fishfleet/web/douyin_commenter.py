"""从唯一可见原评论直接往返主页取号；只采集，不搜索、不发送、不缓存身份。"""
from __future__ import annotations

import re

from .douyin_lead import _visible, by_id, page_kind, parse_comment_container
from .douyin_skill import SkillError


def _comment_match(tree, author, content, screen_size):
    """只比较 strip 后的完整昵称和正文，不截断、不折叠内部空白。"""
    visible = [el for el in tree if _visible(el, screen_size)]
    if page_kind(visible) != 'comments':
        return None, visible
    matches = []
    for el in by_id(visible, 'fpb'):
        parsed = parse_comment_container(el)
        if parsed and parsed['author'] == author and parsed['content'] == content:
            matches.append(el)
    return (matches[0] if len(matches) == 1 else None), visible


def _inside(el, container):
    box, outer = el['coordinates'], container['coordinates']
    return (outer['x'] <= box['x'] and outer['y'] <= box['y']
            and box['x'] + box['width'] <= outer['x'] + outer['width']
            and box['y'] + box['height'] <= outer['y'] + outer['height'])


def _text(el):
    return str(el.get('text') or el.get('label') or '').strip()


def collect_commenter_from_comment(page, comment, *, read_tree, tap, press,
                                   pause, check, screen_size, serial='', log=None,
                                   attempts=6):
    """唯一 fpb → 容器内唯一 avatar → 主页取号 → BACK 一次 → 原评论。

    昵称只用于直接导航后的局部一致性检查，抖音号才是返回的身份标识。
    只轮询读取，不重放点击/BACK；所有回调异常（含 unknown/stop）原样
    传播，不在异常清理中额外 BACK。调用者负责提供当次页面与屏幕尺寸。

    2026-11-05 起：在主页上顺手抓**数字 uid**（抖音号直达不了，开的是空主页）。
    抓 uid 期间会离开主页去粘贴读回，抓完自动直达回同一主页，保证后面
    的 BACK → 评论页 仍成立。抓失败不影响取号，只是没有 uid 走老流程。
    """
    check()
    author = str(comment.get('author') or comment.get('comment_author') or '').strip()
    content = str(comment.get('content') or comment.get('comment_text') or '').strip()
    if not author or not content or type(attempts) is not int or attempts < 1:
        raise SkillError('评论者取号：完整昵称/正文或读取次数无效，已停止')
    container, visible = _comment_match(page, author, content, screen_size)
    if container is None:
        # AI仲裁位#1（2026-11-05）：fpb 门连败则问 AI 走文案路还是放弃。
        # 默认行为不变（放弃即抛原错）；任何异常同样回原错，绝不改变旧链。
        try:
            from . import ai_director as _AD
            _alt = _AD.arbitrate_gate(
                serial, 'comment_container',
                {'author': author, 'content': str(content)[:40]}, log=log)
        except Exception:
            _alt = None
        if _alt and _alt.get('action') == 'alt_label':
            for el in visible:
                parsed = parse_comment_container(el)
                if (parsed and parsed['author'] == author
                        and parsed['content'] == content):
                    container = el
                    break
        if container is None:
            raise SkillError('评论者取号：可见原评论不存在或不唯一，已停止')
    avatars = [el for el in by_id(visible, 'avatar') if _inside(el, container)]
    if len(avatars) != 1 or avatars[0].get('label') != author + '的头像':
        raise SkillError('评论者取号：评论容器内头像不存在、不唯一或昵称不符，已停止')
    avatar = avatars[0]
    check()
    tap(avatar)

    account = None
    nickname = ''
    for attempt in range(attempts):
        check()
        current = [el for el in read_tree() if _visible(el, screen_size)]
        names, ids = by_id(current, 'tk1'), by_id(current, '50g')
        if len(names) == 1 and len(ids) == 1:
            nickname, raw = _text(names[0]), _text(ids[0])
            match = re.fullmatch(r'抖音号[:：]\s*([A-Za-z0-9_][A-Za-z0-9_.-]{0,63})', raw)
            if nickname == author and match:
                account = match.group(1)
                break
        if attempt + 1 < attempts:
            pause(0.8)
    if account is None:
        # 备选路线（主人 2026-11-05 定）：原路是「先验 tk1/50g 再抓 ID」，
        # 新机上这两个 id 缺席就死在门口。备选改为：验号失败时**直接抓分享
        # 链接**（⋮→分享名片→复制链接→解析 uid）。抓到就用 uid 作身份依据，
        # 抖音号留空（uid 已足够直达）；抓不到才按原错误停。
        if serial:
            try:
                from . import id_capture as IC
                cap = IC.capture_and_return(serial, 'douyin', log=log)
                if log:
                    log('备选抓ID：ok=%s ident=%s' % (cap.get('ok'), cap.get('ident')))
                if cap.get('ok') and str(cap.get('ident') or ''):
                    uid = str(cap.get('ident') or '')
                    nickname = nickname or author
                    if log:
                        log('原路验号失败，已走备选：uid=%s 昵称=%s' % (uid, nickname))
                    check()
                    press('BACK')
                    for _attempt in range(attempts):
                        check()
                        page = read_tree()
                        returned, vis = _comment_match(page, author, content, screen_size)
                        if returned is not None:
                            try:
                                from . import ai_director as _AD
                                _AD.gate_ok(serial, 'comment_container')
                            except Exception:
                                pass
                            return {
                                'commenter_douyin_id': '',
                                'commenter_uid': uid,
                                'commenter_nickname': nickname,
                                'commenter_identity_source': 'fallback_share_link_uid',
                                'commenter_identity_evidence': {
                                    'comment_author': author,
                                    'comment_text': content,
                                    'avatar_label': avatar['label'],
                                    'profile_nickname': nickname,
                                    'profile_account': '',
                                    'profile_uid': uid,
                                    'route': 'fallback_after_profile_verify_failed',
                                    'returned_snapshot_equal': True,
                                },
                            }
                        if page_kind(vis) != 'comments':
                            press('BACK')
                            pause(2.0)
                        else:
                            pause(0.8)
            except Exception as _exc:              # noqa: BLE001 备选失败回原错
                if log:
                    log('备选抓ID异常：%s' % type(_exc).__name__)
        raise SkillError('评论者取号：主页昵称/唯一抖音号未严格核实，已停止')

    # 采集时抓数字 uid（主人 2026-11-05 拍板：这一步值得花时间）。
    # 抓不到只是没有 uid，后续回复走老流程，不影响线索入池。
    uid = ''
    if serial:
        try:
            from . import id_capture as IC
            cap = IC.capture_and_return(serial, 'douyin', log=log)
            if log:
                log('抓ID结果 ok=%s ident=%s reason=%s' % (cap.get('ok'), cap.get('ident'), cap.get('reason')))
            if cap.get('ok'):
                uid = str(cap.get('ident') or '')
        except Exception as _exc:   # noqa: BLE001 抓ID任何异常都不该毁掉采集
            uid = ''
            if log:
                log('抓ID异常：%s: %s' % (type(_exc).__name__, _exc))

    check()
    press('BACK')
    extra_backs = 0
    for attempt in range(attempts):
        check()
        page = read_tree()
        returned, visible = _comment_match(page, author, content, screen_size)
        if returned is not None:
            try:
                from . import ai_director as _AD
                _AD.gate_ok(serial, 'comment_container')
            except Exception:
                pass
            return {
                'commenter_douyin_id': account,
                'commenter_uid': uid,
                'commenter_nickname': nickname,
                'commenter_identity_source': 'direct_comment_profile_roundtrip',
                'commenter_identity_evidence': {
                    'comment_author': author,
                    'comment_text': content,
                    'comment_container_label': container.get('label', ''),
                    'avatar_label': avatar['label'],
                    'profile_nickname': nickname,
                    'profile_account': account,
                    'profile_uid': uid,
                    'returned_snapshot_equal': True,
                },
            }
        # 抓 uid 时会在栈上多压一层「搜索页」，所以不在评论页就再退一次。
        # 但**最多补退 1 次**：退多了会越过视频页回到 feed，后面取号全乱。
        if page_kind(visible) != 'comments' and extra_backs < 2:
            extra_backs += 1
            press('BACK')
            pause(2.0)
        elif attempt + 1 < attempts:
            pause(0.8)
    raise SkillError('评论者取号：BACK后未确认唯一原评论完整昵称及正文，已停止')
