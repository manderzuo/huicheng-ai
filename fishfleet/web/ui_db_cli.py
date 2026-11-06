"""抖音三链信息数据库 · 命令行入口。

用法（项目根目录执行）：

    python -m fishfleet.web.ui_db_cli init
    python -m fishfleet.web.ui_db_cli seed
    python -m fishfleet.web.ui_db_cli ingest --chain lead --version 41.0.0
    python -m fishfleet.web.ui_db_cli propose --run 1
    python -m fishfleet.web.ui_db_cli promote --version 41.0.0
    python -m fishfleet.web.ui_db_cli check --run 1
    python -m fishfleet.web.ui_db_cli check --serial SERIAL0001
    python -m fishfleet.web.ui_db_cli status

设计原则：CLI 只读写库，不驱动手机；真正跑三链仍走现有任务 API
（这样"录制→入库→校对"三段解耦，任何一段出错都不影响业务）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import ui_db

# ---------------------------------------------------------------------------
# 三链流程种子数据（步骤来源：代码逐行核对，不是推测）
#   stop_point=1 → 必须人工确认才能继续；risk=send → 会对外发出内容
# ---------------------------------------------------------------------------
FLOWS = [
    # ── 发布链：douyin_skill.run ──────────────────────────────────
    ('douyin', 'publish', 1, '媒体库推送视频', '', 'push', '',
     '', 'content://media 正则匹配成功', 'none', 0,
     'MediaPusher.ensure_video；失败即停，不继续'),
    ('douyin', 'publish', 2, 'SEND intent 拉起编辑器', '', 'am start', '',
     '', 'am start 无 Error', 'none', 0,
     'am start -a android.intent.action.SEND -t video/mp4 -p com.ss.android.ugc.aweme'),
    ('douyin', 'publish', 3, '过系统选择器', '', 'tap', '编辑页/发布到抖音',
     '', '「发布到抖音」「仅限一次」精确且唯一', 'none', 0,
     '多命中即停，绝不猜；应用内同文案弹窗同样处理'),
    ('douyin', 'publish', 4, '认发布页', '', 'select', 'publish/title_input·desc_input·publish_work',
     'publish', '三个控件各唯一，且前两个 type=EditText', 'none', 0,
     '认不出退 _publish_tree_any 结构兜底（两个 EditText + 发布按钮）'),
    ('douyin', 'publish', 5, '填写标题', 'publish', 'tap+type', 'publish/title_input',
     'publish', '回读 text == 标题，且描述框仍为空', 'none', 0,
     '20 字上限在入参校验；回读不一致即停'),
    ('douyin', 'publish', 6, '填写描述与话题', 'publish', 'tap+type', 'publish/desc_input',
     'publish', '回读 text == 内容；若有话题，lgl 候选层须退场', 'none', 0,
     '有 lgl 时必须选中首项且文本 == #话题，否则拒绝'),
    ('douyin', 'publish', 7, '收键盘后复验', 'publish', 'BACK', '',
     'publish', 'fields() 三检：标题、描述、lgl 缺席', 'none', 0,
     'BACK 只收键盘，不导航'),
    ('douyin', 'publish', 8, '人工确认（停点）', 'publish', 'await', '',
     'publish', 'pending 未过期（180 秒）', 'none', 1,
     '★ 主人确认前绝不点发布；过期即作废'),
    ('douyin', 'publish', 9, '点发布并核验', 'publish', 'tap', 'publish/publish_work',
     '', '「发布成功」横幅 或 作品详情 desc+vfr 时间匹配', 'send', 0,
     '只点一次；结果未知转 needs_review，绝不自动重试'),

    # ── 采集链：douyin_lead.run ───────────────────────────────────
    ('douyin', 'lead', 1, '启动抖音并确认前台', '', 'launch', '',
     'feed', '前台包 == com.ss.android.ugc.aweme', 'none', 0,
     'launch_douyin'),
    ('douyin', 'lead', 2, '进入搜索页', 'feed', 'tap', 'feed/comment_btn? 搜索入口',
     'search', 'page_kind == search', 'none', 0,
     'goto_search；最多 5 次，找不到按 BACK 回退'),
    ('douyin', 'lead', 3, '输入关键词并提交', 'search', 'tap+type+tap', 'search/input·clear·submit',
     'search', '结果页出现视频卡片', 'none', 0,
     '有 clear 先清空；submit 不存在即停'),
    ('douyin', 'lead', 4, '应用筛选', 'search', 'tap', 'search_filter/*',
     'search', '「筛选已选：」日志出现且筛选层退场', 'none', 0,
     '排序/发布时间/时长/范围/距离，属 SELECTABLE'),
    ('douyin', 'lead', 5, '遍历视频结果卡片', 'search', 'scroll', 'search_video/cover·desc',
     'search_video', 'card_identity 取到 desc+时长', 'none', 0,
     '按「描述+时长」判重，避免同一视频重复进入'),
    ('douyin', 'lead', 6, '进入视频播放页', 'search_video', 'tap', 'search_video/cover',
     'player', 'page_kind == player', 'none', 0,
     ''),
    ('douyin', 'lead', 7, '打开评论区', 'player', 'tap', 'video_player/comment_entry',
     'comments', 'page_kind == comments', 'none', 0,
     'e9n 死了也要靠文案兜底（评论…按钮）'),
    ('douyin', 'lead', 8, '读取评论并判定线索', 'comments', 'read', 'comments/item',
     'comments', 'read_comments 返回 >= 1 条且能解析昵称+正文', 'none', 0,
     'label 解析优先，id 兜底；判不准就停，不猜'),
    ('douyin', 'lead', 9, '采集作者身份', 'comments', 'tap+read', 'comments/avatar',
     'profile', '主页 tk1 与 50g 同时唯一且昵称一致', 'none', 0,
     '抖音号取不到时走分享链接抓 uid 备选'),
    ('douyin', 'lead', 10, '关闭评论区返回结果页', 'comments', 'tap', 'comments/close',
     'search_video', '回到结果页并保留滚动位置', 'none', 0,
     'back_to_results；退多了会回 feed'),

    # ── 回复链：douyin_dm_task → douyin_extract ──────────────────
    ('douyin', 'dm', 1, '领取线索', '', 'claim', '',
     '', '拿到带 commenter_douyin_id 或 commenter_uid 的线索', 'none', 0,
     '两者都没有的线索跳过（跨机定位不了）'),
    ('douyin', 'dm', 2, '直达本人主页（uid 深链）', '', 'deeplink',
     'profile/douyin_id·nickname',
     'profile', '落地 UserProfileActivity，且 50g+tk1 都能读到', 'none', 0,
     '★第一选择：snssdk1128://user/profile/<uid>。前提 uid 必须是纯数字；'
     '若 uid 字段是字母（其实是抖音号），跳过本步走搜号。落地读不到就回落，不算失败'),
    ('douyin', 'dm', 3, '搜号定位（兜底）', 'feed', 'tap+type+tap',
     'search/input·clear·submit',
     'search', 'page_kind == search 且 is_main_search 为真', 'none', 0,
     '进主搜索页→输入抖音号→提交。SplashActivity 绝不按 BACK（会退出应用）；'
     '先清「继续编辑」残留弹窗'),
    ('douyin', 'dm', 4, '切「用户」tab', 'search', 'tap', 'search_user/tab_user',
     'search_user', '用户列表出现关注按钮', 'none', 0,
     '找不到时向左滑一次再找'),
    ('douyin', 'dm', 5, '逐个候选进主页核号', 'search_user', 'tap', 'search_user/关注按钮',
     'profile', '50g 文本与预期抖音号逐字相等', 'none', 0,
     '点击点须离关注按钮左边界 >= MIN_SIDE_GAP，否则拒绝（防误关注）'),
    ('douyin', 'dm', 6, '打开私信', 'profile', 'tap', 'profile/dm',
     'chat', '进入会话页且 未互相关注 提示正常', 'none', 0,
     'zma；点不到会退到关注，属失败'),
    ('douyin', 'dm', 7, '确认会话页', 'chat', 'read', 'chat/send·chat/text_input',
     'chat', 'is_chat_page 为真且发送键唯一', 'none', 0,
     ''),
    ('douyin', 'dm', 8, '填入草稿', 'chat', 'tap+type', 'chat/text_input',
     'chat', '输入框回读 == 草稿内容', 'none', 0,
     ''),
    ('douyin', 'dm', 9, '停：不点发送（停点）', 'chat', 'await', '',
     'chat', '草稿已就位，发送键未被点击', 'send', 1,
     '★ 回复链的终点就是这里，绝不点 jan'),

    # ── 小红书发布链：xiaohongshu_publish ─────────────────
    ('xiaohongshu', 'publish', 1, '素材推入相册并核验', '', 'push', '',
     '', 'content://media 可用', 'none', 0,
     'MediaPusher.ensure_video'),
    ('xiaohongshu', 'publish', 2, '分享入口打开小红书', '', 'intent', '',
     'post_page', '进入发布笔记页（不走底栏「+」）', 'none', 0,
     '实测日志：正在通过分享入口打开小红书'),
    ('xiaohongshu', 'publish', 3, '认发布页', 'post_page', 'select',
     'post_page/title_input·body_input·publish',
     'post_page', '两个 EditText + 发布笔记按钮都在', 'none', 0,
     '标题框与正文框靠 y 坐标区分，见 pick_inputs'),
    ('xiaohongshu', 'publish', 4, '填标题', 'post_page', 'tap+type',
     'post_page/title_input', 'post_page', '回读 text == 标题', 'none', 0, ''),
    ('xiaohongshu', 'publish', 5, '填正文', 'post_page', 'tap+type',
     'post_page/body_input', 'post_page', '回读 text == 正文', 'none', 0, ''),
    ('xiaohongshu', 'publish', 6, '人工确认（停点）', 'post_page', 'await', '',
     'post_page', 'pending 未过期', 'none', 1,
     '★ 主人确认前绝不点「发布笔记」；该键空内容时也不带 disabled，必须自校验'),
    ('xiaohongshu', 'publish', 7, '点发布并核验', 'post_page', 'tap',
     'post_page/publish', '', '笔记出现在自己主页', 'send', 0,
     '只点一次，结果未知转人工'),

    # ── 小红书采集链：xiaohongshu_lead ────────────────────
    ('xiaohongshu', 'lead', 1, '启动并搜索关键词', '', 'launch+type',
     'search_result/keyword', 'search_result', '结果页出现笔记卡片', 'none', 0,
     '日志：搜索「测试」'),
    ('xiaohongshu', 'lead', 2, '进入笔记详情', 'search_result', 'tap', '',
     'note_detail', '标题/正文可读（content 区或 noteContentText）', 'none', 0,
     '结果卡片无稳定 label，只能按封面/描述/作者相对位置关联'),
    ('xiaohongshu', 'lead', 3, '打开评论区', 'note_detail', 'tap',
     'note_detail/comment_btn', 'comments', '出现「共 N 条评论」', 'none', 0,
     '点「说点什么」只会弹输入面板，必须找「评论 N」入口'),
    ('xiaohongshu', 'lead', 4, '读评论并判线索', 'comments', 'read',
     'comments/nickname·content', 'comments', '昵称+正文+时间地区能对上', 'none', 0,
     '三段是独立 TextView，按容器与 y 关联；作者本人评论要跳过'),
    ('xiaohongshu', 'lead', 5, '评论者主页取小红书号', 'comments', 'tap',
     'comments/avatar', 'other_profile', 'xhs_id 匹配 ^小红书号[：:]\\d+$', 'none', 0,
     '★ 唯一键是小红书号，昵称重名率极高（实测搜同名返回 4+ 个）'),
    ('xiaohongshu', 'lead', 6, '写入中转站', '', 'save', '', '',
     'lead_store 落库', 'none', 0, '日志：已入中转站 N 条'),

    # ── 小红书回复链：xiaohongshu_dm ─────────────────────
    ('xiaohongshu', 'dm', 1, '领取线索', '', 'claim', '', '',
     '拿到带小红书号的线索', 'none', 0, ''),
    ('xiaohongshu', 'dm', 2, '按小红书号定位主页', '', 'search',
     'other_profile/xhs_id', 'other_profile', '小红书号逐字相等', 'none', 0,
     '绝不用昵称定位'),
    ('xiaohongshu', 'dm', 3, '打开私信', 'other_profile', 'tap',
     'other_profile/dm', 'chat', '进入会话页且输入框可见', 'none', 0,
     '空输入时发送键不存在，有内容才出现'),
    ('xiaohongshu', 'dm', 4, '填入草稿', 'chat', 'tap+type', 'chat/input',
     'chat', '输入框 value 回读 == 草稿；发送键此时出现', 'none', 0,
     '实测草稿 22 字通过；首次私信有严格配额（1 条）'),
    ('xiaohongshu', 'dm', 5, '停：不点发送（停点）', 'chat', 'await', '',
     'chat', '发送键可读但未被点击', 'send', 1,
     '★ 日志原话：草稿已就绪 + 发送键可读，未发送'),
]


def seed(conn) -> dict:
    """把三链流程写进 flow_step（幂等 upsert）。"""
    for row in FLOWS:
        plat, chain, step_no, name, from_page, action, target, to_page, \
            expect_ok, risk, stop_point, note = row
        conn.execute(
            'INSERT INTO flow_step(plat,chain,step_no,name,from_page,action,'
            'target,to_page,expect_ok,risk,stop_point,evidence,note) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?) '
            'ON CONFLICT(plat,chain,step_no) DO UPDATE SET '
            'name=excluded.name, from_page=excluded.from_page, '
            'action=excluded.action, target=excluded.target, '
            'to_page=excluded.to_page, expect_ok=excluded.expect_ok, '
            'risk=excluded.risk, stop_point=excluded.stop_point, '
            'note=excluded.note',
            (plat, chain, step_no, name, from_page, action, target, to_page,
             expect_ok, risk, stop_point, note))
    conn.commit()
    return {'steps': len(FLOWS)}


def _print_cov(cov, limit=15):
    print(cov.summary())
    bad = [i for i in cov.items if i['verdict'] != 'ok']
    for item in bad[:limit]:
        print('  [%s] %s/%s　%s' % (item['verdict'], item['page'],
                                    item['name'], item['detail']))
    if len(bad) > limit:
        print('  ...另 %d 条非 ok，见 --md 报告' % (len(bad) - limit))
    print('  可用：%s' % ('是' if cov.usable else '否（硬依赖缺失）'))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='ui_db_cli',
                                     description='抖音三链信息数据库')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('init', help='建库')
    sub.add_parser('seed', help='写入三链流程步骤')

    p_in = sub.add_parser('ingest', help='把 JSONL 灌进库')
    p_in.add_argument('--file', default='')
    p_in.add_argument('--dir', default='')
    p_in.add_argument('--chain', default='unknown')
    p_in.add_argument('--version', default='')
    p_in.add_argument('--plat', default='douyin')
    p_in.add_argument('--force', action='store_true', help='重灌（先清同文件旧数据）')
    p_in.add_argument('--since', type=float, default=None, help='只入该时间戳之后的事件')
    p_in.add_argument('--until', type=float, default=None, help='只入该时间戳之前的事件')
    p_in.add_argument('--suffix', default='', help='run_key 后缀（同一文件切多段用）')

    p_pr = sub.add_parser('propose', help='从实测反推期望')
    p_pr.add_argument('--run', type=int, required=True)

    p_pm = sub.add_parser('promote', help='proposed → active')
    p_pm.add_argument('--version', default='')
    p_pm.add_argument('--plat', default='douyin')

    p_mu = sub.add_parser('multi', help='标记列表型控件（允许多命中）')
    p_mu.add_argument('--page', required=True)
    p_mu.add_argument('--name', required=True)
    p_mu.add_argument('--on', type=int, default=1)
    p_mu.add_argument('--version', default='')
    p_mu.add_argument('--plat', default='douyin')

    p_ck = sub.add_parser('check', help='校对')
    p_ck.add_argument('--run', type=int, default=0)
    p_ck.add_argument('--serial', default='')
    p_ck.add_argument('--plat', default='douyin')
    p_ck.add_argument('--version', default='')
    p_ck.add_argument('--md', default='', help='同时写 markdown 报告到该路径')

    sub.add_parser('status', help='库概况')

    p_au = sub.add_parser('audit', help='扫单点依赖（新机最先断的控件）')
    p_au.add_argument('--plat', default='douyin')
    p_au.add_argument('--version', default='')
    p_au.add_argument('--md', default='')

    args = parser.parse_args(argv)
    conn = ui_db.connect()
    try:
        if args.cmd == 'init':
            print('库已就绪：%s' % ui_db.DB_PATH)
            return 0
        if args.cmd == 'seed':
            print('流程步骤已写入：%s' % json.dumps(seed(conn), ensure_ascii=False))
            return 0
        if args.cmd == 'ingest':
            if args.file:
                out = ui_db.ingest(args.file, conn=conn, chain=args.chain,
                                   app_version=args.version, plat=args.plat,
                                   force=args.force, since=args.since,
                                   until=args.until, key_suffix=args.suffix)
                print(json.dumps(out, ensure_ascii=False))
                return 0 if out.get('ok') else 1
            if args.dir:
                ui_db.RECORD_DIR = Path(args.dir)
            outs = ui_db.ingest_dir(conn=conn, chain=args.chain,
                                    app_version=args.version, plat=args.plat)
            ok = sum(1 for o in outs if o.get('ok'))
            print('入库 %d/%d 个文件' % (ok, len(outs)))
            for out in outs:
                print('  %s' % json.dumps(out, ensure_ascii=False))
            return 0
        if args.cmd == 'propose':
            print(json.dumps(ui_db.propose(args.run, conn=conn), ensure_ascii=False))
            return 0
        if args.cmd == 'promote':
            print(json.dumps(ui_db.promote(args.plat, args.version or None,
                                           conn=conn), ensure_ascii=False))
            return 0
        if args.cmd == 'multi':
            sql = 'UPDATE expect SET multi=? WHERE plat=? AND page=? AND name=?'
            params = [args.on, args.plat, args.page, args.name]
            if args.version:
                sql += ' AND app_version=?'
                params.append(args.version)
            cur = conn.execute(sql, params)
            conn.commit()
            print('已标记 %d 条为 multi=%d' % (cur.rowcount, args.on))
            return 0
        if args.cmd == 'audit':
            report = ui_db.audit_dependencies(conn, args.plat, args.version or None)
            lines = ['# 单点依赖审计（%s）' % args.plat, '',
                     '判据只有「一个混淆 id」撑着、或备选判据实测不唯一的控件——'
                     '这些就是新机上最先断的一批。', '']
            head = '## 一、纯 id 依赖（id 一死就废）：%d 条' % len(report['single'])
            lines += [head, '', '| 控件 | 硬依赖 | 实测次数 |', '|---|---|---|']
            for item in report['single']:
                lines.append('| %s | %s | %d |' % (
                    item['want'], '是' if item['hard'] else '', item['probes']))
            lines += ['', '## 二、备选判据不可靠（换了 id 也未必救得回来）：%d 条'
                      % len(report['weak']), '',
                      '| 控件 | 去id后命中数 | 说明 |', '|---|---|---|']
            for item in report['weak']:
                hn = item['hits_noid']
                why = ('多命中，不唯一' if hn and hn > 1
                       else ('实测 0 命中' if hn == 0 else '无实测数据'))
                lines.append('| %s | %s | %s |' % (item['want'], hn if hn is not None else '-', why))
            lines += ['', '## 三、可安全降级：%d 条' % len(report['safe']), '']
            lines.append('、'.join(i['want'] for i in report['safe']) or '（无）')
            text = '\n'.join(lines)
            print(text)
            if args.md:
                Path(args.md).write_text(text, encoding='utf-8')
                print('\n报告已写：%s' % args.md)
            return 0
        if args.cmd == 'check':
            if args.run:
                cov = ui_db.check_run(args.run, conn=conn)
            elif args.serial:
                cov = ui_db.check_device(args.serial, args.plat,
                                         args.version, conn=conn,
                                         log=lambda m: print('  ' + m))
            else:
                print('需要 --run 或 --serial')
                return 2
            _print_cov(cov)
            if args.md:
                Path(args.md).write_text(ui_db.markdown_report(cov), encoding='utf-8')
                print('报告已写：%s' % args.md)
            return 0 if cov.usable else 1
        if args.cmd == 'status':
            q = lambda sql: conn.execute(sql).fetchone()[0]
            print('库文件：%s' % ui_db.DB_PATH)
            print('  采集批次 run      : %d' % q('SELECT COUNT(*) FROM run'))
            print('  UI 快照 snapshot  : %d' % q('SELECT COUNT(*) FROM snapshot'))
            print('  元素 element      : %d' % q('SELECT COUNT(*) FROM element'))
            print('  控件探测 probe    : %d' % q('SELECT COUNT(*) FROM probe'))
            print('  期望 expect       : %d（active %d / proposed %d）' % (
                q('SELECT COUNT(*) FROM expect'),
                q("SELECT COUNT(*) FROM expect WHERE status='active'"),
                q("SELECT COUNT(*) FROM expect WHERE status='proposed'")))
            print('  流程步骤 flow_step: %d' % q('SELECT COUNT(*) FROM flow_step'))
            print('  校对批次 check_run: %d' % q('SELECT COUNT(*) FROM check_run'))
            for row in conn.execute(
                    'SELECT chain, COUNT(*) c, SUM(stop_point) s FROM flow_step '
                    'GROUP BY chain ORDER BY chain'):
                print('    · %-8s %2d 步，其中人工停点 %d 个'
                      % (row['chain'], row['c'], row['s'] or 0))
            return 0
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
