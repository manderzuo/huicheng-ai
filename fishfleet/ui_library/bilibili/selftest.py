"""bilibili_templates 自测：确认解析器与页面识别可用。

运行：
    python -m ui_library.bilibili.selftest
或：
    python ui_library/bilibili/selftest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许从项目根直接运行
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fishfleet.web import bilibili_templates as bt  # noqa: E402

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    if not ok:
        FAILED.append(f'{name}: got {got!r}, want {want!r}')
    print(('OK  ' if ok else 'FAIL') + f'  {name}  ->  {got!r}')


def main() -> int:
    print('=== 1. 数量解析 ===')
    check('11.4万', bt.parse_count('11.4万'), 114000)
    check('9593.5万', bt.parse_count('9593.5万'), 95935000)
    check('983', bt.parse_count('983'), 983)
    check('1.5亿', bt.parse_count('1.5亿'), 150000000)
    check('空串', bt.parse_count(''), None)

    print('=== 2. 卡片 label 解析 ===')
    c = bt.parse_card_label(
        '竖版视频,梁文锋变成了光？！！！,11.4万观看,153弹幕,时长1分钟53秒,UP主凉风Zephyr,1万点赞,'
    )
    check('标题', c['title'], '梁文锋变成了光？！！！')
    check('播放', c['play'], 114000)
    check('弹幕', c['danmaku'], 153)
    check('UP', c['up'], '凉风Zephyr')
    check('点赞', c['like'], 10000)
    check('非广告', c['is_ad'], False)
    ad = bt.parse_card_label('创作推广,某课程,,,时长30秒,UP主某某,,')
    check('广告识别', ad['is_ad'], True)

    print('=== 3. 详情页元信息行 ===')
    m = bt.parse_meta_line(
        'playCount11.6万   danmaku154   2026年10月30日 11:11   BV14gxc61Eux   copyright未经作者授权禁止转载'
    )
    check('播放', m['play'], 116000)
    check('弹幕', m['danmaku'], 154)
    check('BV号', m['bvid'], 'BV14gxc61Eux')
    check('日期', m['date'], '2026年10月30日 11:11')

    print('=== 4. 互动按钮 label ===')
    check('点赞', bt.parse_action_label('点赞，15092个点赞'), ('点赞', 15092))
    check('投币', bt.parse_action_label('投币，983个投币'), ('投币', 983))
    check('收藏', bt.parse_action_label('收藏，2535个收藏'), ('收藏', 2535))
    check('分享', bt.parse_action_label('分享，1676个分享'), ('分享', 1676))
    check('不匹配', bt.parse_action_label('不喜欢'), None)

    print('=== 5. 粉丝/投稿 与 集数 ===')
    check('带视频数', bt.parse_fans('45.5万粉丝 · 11个视频'), (455000, 11))
    check('纯粉丝', bt.parse_fans('9.9万粉丝'), (99000, 0))
    check('已完结', bt.parse_episode_count('已完结，全12话'), (12, 0))
    check('连载中', bt.parse_episode_count('更新至第5话'), (0, 5))

    print('=== 6. 风险闸门 ===')
    check('点赞', bt.check_risk('like'), bt.RISK_SEND)
    check('钱包', bt.check_risk('wallet'), bt.RISK_FORBIDDEN)
    check('退出', bt.check_risk('logout'), bt.RISK_FORBIDDEN)
    check('滚动', bt.check_risk('scroll'), bt.RISK_NONE)

    print('=== 7. 页面识别 ===')
    cases = [
        ('coin_panel', [{'id': 'tv.danmaku.bili:id/pay_coins_layout'}]),
        ('share_panel', [
            {'id': 'tv.danmaku.bili:id/blank_layout'},
            {'id': 'tv.danmaku.bili:id/cancel'},
            {'text': '分享'},
        ]),
        ('story_comment_sheet', [
            {'id': 'tv.danmaku.bili:id/design_bottom_sheet'},
            {'id': 'tv.danmaku.bili:id/cmt3_recycler'},
        ]),
        ('video_story', [
            {'id': 'tv.danmaku.bili:id/story_view_pager'},
            {'id': 'tv.danmaku.bili:id/story_ctrl_layer_action'},
        ]),
        ('video_detail_portrait', [
            {'id': 'tv.danmaku.bili:id/round_container'},
            {'text': '简介'},
        ]),
        ('search_column', [{'id': 'tv.danmaku.bili:id/column_nested_container'}]),
        ('search_live', [
            {'id': 'tv.danmaku.bili:id/info_online'},
            {'text': '全部直播间 (99+）'},
        ]),
        ('ranking', [{'text': '全区排行榜'}]),
        ('playlist_square', [{'text': '片单广场'}]),
        ('settings_autoplay', [{'text': '视频详情页直接全屏'}]),
        ('settings_other', [{'text': '启用重力感应旋屏'}]),
        ('live_room', [{'id': 'tv.danmaku.bili:id/bottom_controllers_group'}]),
        ('home_feed', [
            {'id': 'tv.danmaku.bili:id/bottom_navigation'},
            {'id': 'tv.danmaku.bili:id/primary_multi_page_layout'},
        ]),
    ]
    for want, els in cases:
        check(want, bt.identify(els), want)
    check('未知页返回 None', bt.identify([{'text': '完全不相干的内容'}]), None)

    print('=== 8. 页面库 ===')
    check('页面数 >= 45', len(bt.PAGES) >= 45, True)
    check('所有页面都有 key', all(p.key for p in bt.PAGES.values()), True)
    anchored = {a[0] for a in bt.ANCHORS}
    missing = sorted(set(bt.PAGES) - anchored)
    print(f'    （无锚点页面 {len(missing)} 个：{missing}，已知例外）')

    print()
    if FAILED:
        print(f'✗ {len(FAILED)} 项失败：')
        for f in FAILED:
            print('   -', f)
        return 1
    print('✓ 全部通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
