r"""打出 update.zip（配合客户机上的“一键更新.bat”使用）。

用法：
    python -X utf8 build/make_update.py --src "C:/Users/Administrator/Desktop/AI" \
        --ver 2026-11-07-r1 --out dist/update_2026-11-07-r1.zip

规则：
  · 打包程序文件（exe / _internal / bin / 汇城AI安装引导 等）；
  · 排除 data/（用户数据库与配置）、runtime/（已装环境）、backup_*/、
    日志与 llm.json（含 API Key，绝不进更新包）；
  · 在包根写 版本.txt，客户机更新器据此判断是否已是最新。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import zipfile
from pathlib import Path

EXCLUDE_DIRS = {'data', 'runtime'}
EXCLUDE_TOP_PREFIX = ('backup_',)
EXCLUDE_NAMES = {'llm.json', 'update.zip'}
EXCLUDE_SUFFIX = {'.log'}


def should_skip(rel: Path) -> bool:
    parts = rel.parts
    if parts and parts[0] in EXCLUDE_DIRS:
        return True
    if parts and parts[0].startswith(EXCLUDE_TOP_PREFIX):
        return True
    if rel.name in EXCLUDE_NAMES:
        return True
    if rel.suffix.lower() in EXCLUDE_SUFFIX:
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='已打好并验证过的交付目录（AI/）')
    ap.add_argument('--ver', default=_dt.date.today().strftime('%Y-%m-%d-r1'),
                    help='版本号，默认今天-r1')
    ap.add_argument('--out', default='', help='输出 zip 路径，默认 dist/update_<ver>.zip')
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_dir():
        print('src 不存在：%s' % src)
        return 1
    out = Path(args.out) if args.out else src.parent / 'dist' / ('update_%s.zip' % args.ver)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = [p for p in src.rglob('*') if p.is_file() and not should_skip(p.relative_to(src))]
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr('版本.txt', args.ver + '\n')
        for p in sorted(files):
            z.write(p, p.relative_to(src).as_posix())
    size = out.stat().st_size / 1024 / 1024
    print('update.zip：%s（%d 文件，%.1f MB，版本 %s）' % (out, len(files), size, args.ver))
    print('用法：把此 zip 改名 update.zip 拷到客户机 AI/ 目录，双击 一键更新.bat')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
