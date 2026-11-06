"""误点恢复模块的离线自检：只验证状态判定，不碰真机。

用法：python -B fishfleet/web/xiaohongshu_recover_selftest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))
sys.path.insert(0, str(_HERE.parent))

from fishfleet.web.xiaohongshu_recover import detect   # noqa: E402

failures = []


def el(kind, text, y=500):
    return {'type': kind, 'text': text,
            'coordinates': {'x': 10, 'y': y, 'width': 100, 'height': 40}}


def check(name, got, want):
    if got == want:
        print('OK   %s' % name)
    else:
        print('FAIL %s：期望 %s，实际 %s' % (name, want, got))
        failures.append(name)


NOTE = 'com.xingin.matrix.notedetail.NoteDetailActivity'
FEED = 'com.xingin.matrix.detail.activity.DetailFeedActivity'

check('首页', detect('com.xingin.xhs.index.v2.IndexActivityV2', []), 'home')
check('搜索结果', detect('com.xingin.alioth.search.GlobalSearchActivity', []), 'search')
check('普通笔记（底部有「说点什么…」占位）',
      detect(NOTE, [el('TextView', '说点什么...', 1673)]), 'note')
check('视频笔记', detect(FEED, [el('TextView', '说点什么...', 1673)]), 'note')
check('评论区展开（出现「赞和收藏」页签）',
      detect(FEED, [el('TextView', '评论', 491), el('TextView', '赞和收藏', 491),
                    el('TextView', '喜欢TA就给个评论支持一下～', 1679)]), 'sheet')
check('用户主页', detect('com.xingin.matrix.v2.profile.newpage.NewOtherUserActivity',
                        [el('TextView', '私信', 693)]), 'profile')
check('私信会话', detect('com.xingin.xhs.chat.ChatActivity', []), 'chat')
check('外链页', detect('com.xingin.webview.ExternalWebViewActivityV2', []), 'web')
check('AI 搜索页', detect('com.xingin.xhs.search.SearchAgentPageActivity', []), 'web')

print()
if failures:
    print('失败 %d 项：%s' % (len(failures), '、'.join(failures)))
    raise SystemExit(1)
print('全部通过')
