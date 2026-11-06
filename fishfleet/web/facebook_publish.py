"""Facebook 发帖（新版流程，2026-11-03 18:00 实测）。

**新版是三步**，和旧版 create_post（编辑器里直接有 POST）不同：

    首页「在想些什麼？」
      → ComposerActivity（编辑器，有「下一步」）
      → 输入正文 → 「下一步」
      → ComposerPreviewActivity（预览页，有「發佈」、貼文分享對象、同時分享到）
      → 「發佈」 = 真正的对外发布，**不可撤销**

安全设计（照抖音/小红书/B站的做法）：
  · 默认 dry-run：只填内容，**停在「發佈」前**，绝不点
  · 真发布必须显式 `--confirm`
  · 只点一次，结果未知就报「需人工核验」，**绝不自动重试**
  · 退出时明确选「捨棄貼文」，不留草稿

用法：
  python fishfleet/web/facebook_publish.py "正文"            # dry-run
  python fishfleet/web/facebook_publish.py "正文" --confirm   # 真发布（需主人授权）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fishfleet.web import facebook_templates as T  # noqa: E402
from fishfleet.web.facebook_live import DeviceSession, setup_utf8_stdout  # noqa: E402
from fishfleet.web.facebook_reply import ensure_home  # noqa: E402
from fishfleet.web.ui_probe_targets import resolve_point as _resolve  # noqa: E402


def _text(e: dict) -> str:
    return (e.get('label') or e.get('text') or '').strip()


def _activity(s: DeviceSession) -> str:
    fg = s.foreground()
    return (fg.activity if fg else '') or ''


def _norm(s):
    return (s or '').replace('，', ',').replace('項目', '项目').replace('貼文', '贴文')


def attach_latest_video(s: DeviceSession, say=None) -> bool:
    def _say(m):
        try:
            if say:
                say(m)
        except Exception:
            pass
        print('  ' + m)
    """在发帖编辑器里点「圖庫」→ 选第一格（transfer 刚传入的最新素材）→ 回到编辑器。
    2026-11-04 在手机8实测：圖库第一格即最新传入视频（標籤「影片，項目1…」繁体）。
    匹配时做繁简/全半角归一化；实在找不到则按第一格坐标点。"""
    for _ in range(8):
        els = s.dump()
        gallery = [e for e in els if '图库' in _text(e) or '圖庫' in _text(e)
                   or '相册' in _text(e) or '照片' in _text(e)]
        if gallery:
            break
        time.sleep(2.0)
    no_gallery = not gallery
    if no_gallery:
        _say('图库按钮未出现，改按坐标点')
        try:
            # 编辑器「图库」兜底 → 注册表 dp 自适应
            s.tap(*_resolve(s.device, 'facebook.composer.gallery_fallback'))
            time.sleep(3.0)
        except Exception:
            _say('编辑器里找不到图库按钮')
            return False
    else:
        s.tap_element(gallery[0])
        time.sleep(3.0)
    picked = False
    for _ in range(4):
        els2 = s.dump()
        cands = [e for e in els2 if '项目1' in _norm(_text(e))]
        if not cands:
            cells = [e for e in els2
                     if (_norm(_text(e)).startswith('影片') or _norm(_text(e)).startswith('相片'))]
            # 优先可点的 Button，不过滤 type（不同 dump 源 type 写法可能不同）
            # 优先勾选框（CompoundButton）：点它只选中不进预览；Button 会进预览页
            btns = [e for e in cells if e.get('type') == 'CompoundButton'] or cells
            # 取最左上那一格
            def _xy(e):
                m = __import__('re').search(r'at=(\d+),(\d+)', str(e))
                return (int(m.group(1)), int(m.group(2))) if m else (9999, 9999)
            if btns:
                cands = [sorted(btns, key=_xy)[0]]
        if cands:
            _say('选中图库第一格')
            try:
                # 点勾选框（CompoundButton）；若点中的是预览按钮则退回选勾选框
                s.tap_element(cands[0])
            except Exception as exc:
                _say('点选失败')
                pass
            time.sleep(2.0)
            # 再点「繼續」确认选择，回到编辑器
            els3 = s.dump()
            cont = [e for e in els3 if _text(e) in ('繼續', '下一步', '完成', '確定', 'Done', 'Next')]
            if cont:
                _say('点确认：' + _text(cont[0]))
                try:
                    s.tap_element(cont[0])
                except Exception:
                    pass
                time.sleep(2.0)
            picked = True
            break
        time.sleep(1.5)
    if not picked:
        # 兜底：第一格中心（注册表 dp 自适应）
        try:
            s.tap(*_resolve(s.device, 'facebook.composer.first_cell'))
            picked = True
        except Exception:
            pass
    for _ in range(10):
        time.sleep(2.0)
        act = _activity(s)
        if 'composer.activity.ComposerActivity' in act:
            _say('已回到编辑器')
            return True
    _say('选后 activity=' + _activity(s))
    return False


def open_editor(s: DeviceSession) -> bool:
    els = s.dump()
    entry = T.find('home', 'composer', els)
    if entry is not None:
        s.tap_element(entry)
        for _ in range(6):
            time.sleep(2.0)
            if 'composer.activity.ComposerActivity' in _activity(s):
                return True
        return False
    # 新版首页没有「在想些什麼？」输入框时（2026-11-04 实测）：点右上「建立」
    # → 底部弹层选「貼文」→ 进 ComposerActivity。
    create = T.find('home', 'create', els)
    if create is None:
        for e in els:
            if _text(e).startswith('建立，') or _text(e).startswith('Create,'):
                create = e
                break
    if create is None:
        print('  找不到发帖入口（旧版输入框与新建按钮都不在）')
        return False
    s.tap_element(create)
    time.sleep(2.5)
    for _ in range(3):
        els2 = s.dump()
        post = [e for e in els2 if _text(e) == '貼文' or _text(e) == 'Post']
        if post:
            s.tap_element(post[0])
            break
        time.sleep(1.5)
    for _ in range(6):
        time.sleep(2.0)
        if 'composer.activity.ComposerActivity' in _activity(s):
            return True
    return False


def fill_body(s: DeviceSession, body: str) -> bool:
    s.type_anywhere(body, submit=False)
    time.sleep(1.5)
    els = s.dump()
    return any(body[:16] in ((e.get('text') or '') + (e.get('label') or '')) for e in els)


def click_next(s: DeviceSession) -> bool:
    els = s.dump()
    nxt = [e for e in els if _text(e) == '下一步']
    if not nxt:
        return False
    s.tap_element(nxt[0])
    for _ in range(6):
        time.sleep(2.0)
        if 'ComposerPreviewActivity' in _activity(s):
            return True
    return False


def find_publish(s: DeviceSession):
    els = s.dump()
    for e in els:
        if _text(e) == '發佈':
            return e
    return None


def discard(s: DeviceSession) -> bool:
    """退出编辑器并「捨棄貼文」，不留草稿。"""
    els = s.dump()
    close = [e for e in els if _text(e) == '關閉']
    if not close:
        s.back()
        time.sleep(2.0)
        els = s.dump()
        close = [e for e in els if _text(e) == '關閉']
    if close:
        s.tap_element(close[0])
        time.sleep(2.5)
    e2 = s.dump()
    dis = [e for e in e2 if _text(e) in ('捨棄貼文', '丟棄貼文', 'DISCARD')]
    if dis:
        s.tap_element(dis[0])
        time.sleep(2.0)
        print('   已捨棄貼文，未发布、未留草稿')
        return True
    print('   没找到「捨棄貼文」，请人工检查是否留下草稿')
    return False


def main_with(serial: str = 'SERIAL0001', body: str = '', confirm: bool = False,
              album_index=None, log=None, wait_confirm=None) -> int:
    """给 skill 层用的入口（不读 sys.argv）。

    返回 0 = 已到待发布状态（或 dry-run 成功）；非 0 = 失败。
    **confirm=False 时绝不点「發佈」**。

    wait_confirm（2026-11-06 新增，主人要求）：
      到预览页并识别到「發佈」后，**把决定权交回上层**。
      以前 `confirm=False` 会直接 `discard()`（丢草稿退出），
      于是界面既没有「确认」按钮、也没有可确认的东西 —— 主人实测：
      「识别到发布后直接报未点击然后退出，确认键我也没找到」。
      现在传了 wait_confirm 就**保持会话不退出**，等它返回 True 才点发布；
      返回 False 才丢弃草稿。
    """
    def say(msg):
        if log:
            try:
                log(msg)
            except Exception:                            # noqa: BLE001
                pass

    with DeviceSession(serial) as s:
        if not ensure_home(s):
            say('回不到首页')
            return 1
        if not open_editor(s):
            say('没进 ComposerActivity')
            return 1
        # 视频发布：先挂最新传入的视频（transfer 已推到相册第一格），再填正文。
        # album_index 暂只支持 0（最新）；其它值报不支持，不瞎点。
        if album_index not in (None, 0):
            say('只支持最新素材（album_index=0），已中止')
            discard(s)
            return 1
        if not attach_latest_video(s, say):
            say('没能挂上最新视频')
            discard(s)
            return 1
        if not fill_body(s, body):
            say('正文回读失败')
            discard(s)
            return 1
        if not click_next(s):
            say('没进预览页')
            discard(s)
            return 1
        btn = find_publish(s)
        if btn is None:
            say('找不到「發佈」键')
            discard(s)
            return 1

        if wait_confirm is not None:
            # 停在预览页等上层（人工确认），**会话保持不退出、不丢弃草稿**。
            say('已到预览页，识别到「發佈」，等待人工确认（保持会话，未丢弃草稿）')
            go = False
            try:
                go = bool(wait_confirm(btn))
            except Exception as exc:                      # noqa: BLE001
                say('等待确认异常：%s' % type(exc).__name__)
            if not go:
                say('未获确认，已放弃本次发布')
                discard(s)
                return 0
            btn2 = find_publish(s) or btn
            s.tap_element(btn2)
            time.sleep(6.0)
            still = any(_text(e) == '發佈' for e in s.dump())
            say('结果未知，需人工核验（不重试）' if still else '已点一次，需人工核验')
            return 1 if still else 0

        if not confirm:
            say('已到预览页，识别到「發佈」，**未点击**')
            discard(s)
            return 0
        s.tap_element(btn)
        time.sleep(6.0)
        still = any(_text(e) == '發佈' for e in s.dump())
        say('结果未知，需人工核验（不重试）' if still else '已点一次，需人工核验')
        return 1 if still else 0


def main() -> int:
    setup_utf8_stdout()
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    confirm = '--confirm' in sys.argv
    if not args:
        print('用法: python facebook_publish.py "正文" [--confirm]')
        return 2
    body = args[0]

    with DeviceSession('SERIAL0001') as s:
        if not ensure_home(s):
            print('回不到首页')
            return 1

        print('① 打开发帖编辑器')
        if not open_editor(s):
            print('   ✗ 没进 ComposerActivity')
            return 1
        print('   ✓ ComposerActivity')

        print('② 写入正文')
        if not fill_body(s, body):
            print('   ✗ 正文回读失败，中止（未发布）')
            discard(s)
            return 1
        print('   ✓ 回读一致')

        print('③ 下一步 → 预览页')
        if not click_next(s):
            print('   ✗ 没进预览页，中止')
            discard(s)
            return 1
        print('   ✓ ComposerPreviewActivity')

        btn = find_publish(s)
        # 顺便把当前隐私范围读出来（只读，不改）
        els = s.dump()
        aud = [e for e in els if _text(e).startswith('貼文分享對象')]
        print(f'④ 识别「發佈」键：{btn["ref"] if btn else None}（**未点击**）')
        print(f'   当前分享对象：{_text(aud[0]) if aud else "未知"}')
        if btn is None:
            print('   ✗ 找不到发布键，中止')
            discard(s)
            return 1

        if not confirm:
            print()
            print('=== DRY-RUN：内容已填好、已到预览页，**停在「發佈」前，没有发布** ===')
            print('要真正发布请加 --confirm')
            discard(s)
            return 0

        print('⑤ 确认发布，点「發佈」（只点一次）')
        s.tap_element(btn)
        time.sleep(6.0)
        els2 = s.dump()
        still = any(_text(e) == '發佈' for e in els2)
        if still:
            print('   结果未知：「發佈」仍在页面上，请人工核验；**不重试**')
            return 1
        print(f'   已点一次，当前 Activity={_activity(s)}')
        print('   **结果需人工核验**（去自己主页看是否出现新帖），不会自动重试')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
