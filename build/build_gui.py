r"""汇城群控AI版 一键构建工具（GUI）。

2026-11-06 主人要求：构建脚本也要做成 EXE，双击就能用，不用敲命令。

流程：
    1. 检查前置条件（PyInstaller、两个 spec、图标、版本文件）
    2. 打包主程序   → dist/HuichengFleetAI/
    3. 打包引导器   → dist/汇城AI安装引导.exe
    4. 组装成品     → 输出目录（主程序平铺 + 引导器 + 使用说明）

设计要点：
    · 构建在**后台线程**跑，界面不卡；输出实时刷到日志框。
    · 每一步都能失败退出，并明确告诉用户**哪一步、什么错**。
    · 默认输出到桌面 `汇城AI`，可改。
    · 不碰用户的 data/（数据库），只打包代码。
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_TITLE = '汇城群控AI版 · 一键构建'

README_TEXT = '''========================================
  汇城群控AI版  v1.0.0
========================================

【怎么用】
  双击 「汇城AI安装引导.exe」

  第一次运行会：
    1. 检查这台电脑缺什么运行环境
    2. 缺的自动下载安装（需要联网，约 400 MB，5~10 分钟）
    3. 装完自动打开主程序

  以后再打开：
    直接双击 「HuichengFleetAI.exe」，不用再走引导。


【首次运行前请确认】
  · 电脑能上网
  · 安装过程中如果杀毒软件弹提示（会下载 node.exe / adb.exe，
    部分杀软误报），请选「允许」
  · 手机已开启「USB 调试」，用数据线连上电脑
  · 推荐装个 Edge 浏览器（Win10/11 一般自带）；没有会用默认浏览器打开


【出问题了怎么办】
  方式一（推荐）：
    浏览器打开  http://127.0.0.1:8765/api/log/diagnose
    把页面内容复制发回来。

  方式二：点主界面右下角诊断按钮，导出日志 zip。

  日志在这里：
      <本文件夹>\\data\\logs\\app.log        全部日志
      <本文件夹>\\data\\logs\\error.log      只有错误（排查先看这个）
      <本文件夹>\\data\\logs\\events.jsonl   操作记录

  发问题给我时，请把 error.log 和 events.jsonl 一起发，
  这样能还原你当时点了什么、卡在哪一步。


【它装到哪里了】
  全部装在这个文件夹里，不写注册表、不改系统：
      runtime\\python\\     Python 运行时
      runtime\\node\\       Node.js
      runtime\\mobile-mcp\\ 手机通信组件
      bin\\adb.exe         调试工具

  想卸载：直接删掉整个文件夹即可。


【目录说明】
  汇城AI安装引导.exe    第一次装环境用（装完可以留着备查）
  HuichengFleetAI.exe   主程序（以后双击这个）
  _internal\\            程序依赖，不要动
  runtime\\              运行环境（首次运行后自动生成），不要动
  data\\                 你的数据（设备、线索、设置）
                        备份就备份这个目录
'''


class BuildApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.msg_q: queue.Queue = queue.Queue()
        self.building = False

        root.title(APP_TITLE)
        root.geometry('860x640')
        root.minsize(760, 560)

        # ---------- 顶部：项目目录 ----------
        top = tk.Frame(root, padx=14, pady=10)
        top.pack(fill='x')
        tk.Label(top, text='项目目录', font=('Microsoft YaHei UI', 9)).grid(
            row=0, column=0, sticky='w')
        self.var_proj = tk.StringVar(value=str(Path(__file__).resolve().parents[1]))
        tk.Entry(top, textvariable=self.var_proj, width=70).grid(
            row=0, column=1, sticky='we', padx=6)
        tk.Button(top, text='浏览…', command=self.pick_proj).grid(row=0, column=2)

        # ---------- 输出目录 ----------
        tk.Label(top, text='输出目录', font=('Microsoft YaHei UI', 9)).grid(
            row=1, column=0, sticky='w', pady=(6, 0))
        default_out = str(Path(os.path.expanduser('~')) / 'Desktop' / '汇城AI')
        self.var_out = tk.StringVar(value=default_out)
        tk.Entry(top, textvariable=self.var_out, width=70).grid(
            row=1, column=1, sticky='we', padx=6, pady=(6, 0))
        tk.Button(top, text='浏览…', command=self.pick_out).grid(
            row=1, column=2, pady=(6, 0))
        top.columnconfigure(1, weight=1)

        # ---------- 选项 ----------
        opt = tk.Frame(root, padx=14)
        opt.pack(fill='x')
        self.var_clean = tk.BooleanVar(value=True)
        self.var_main = tk.BooleanVar(value=True)
        self.var_boot = tk.BooleanVar(value=True)
        self.var_assemble = tk.BooleanVar(value=True)
        for text, var in (('清理旧产物', self.var_clean),
                          ('打包主程序', self.var_main),
                          ('打包引导器', self.var_boot),
                          ('组装到输出目录', self.var_assemble)):
            tk.Checkbutton(opt, text=text, variable=var).pack(side='left', padx=(0, 14))

        # ---------- 进度 ----------
        bar = tk.Frame(root, padx=14, pady=8)
        bar.pack(fill='x')
        self.progress = ttk.Progressbar(bar, mode='determinate', maximum=100)
        self.progress.pack(fill='x')
        self.status = tk.Label(bar, text='就绪', anchor='w',
                               font=('Microsoft YaHei UI', 9))
        self.status.pack(fill='x', pady=(4, 0))

        # ---------- 日志 ----------
        box = tk.Frame(root, padx=14)
        box.pack(fill='both', expand=True)
        self.log = tk.Text(box, height=18, wrap='word', font=('Consolas', 9),
                           bg='#1e1e1e', fg='#d4d4d4', insertbackground='#d4d4d4',
                           relief='flat')
        sb = ttk.Scrollbar(box, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        # ---------- 按钮 ----------
        foot = tk.Frame(root, padx=14, pady=10)
        foot.pack(fill='x')
        self.btn_build = tk.Button(foot, text='开始构建', width=14, height=2,
                                   command=self.start_build,
                                   bg='#5b4cd6', fg='white',
                                   activebackground='#6d54c8',
                                   font=('Microsoft YaHei UI', 10, 'bold'))
        self.btn_build.pack(side='right')
        self.btn_open = tk.Button(foot, text='打开输出目录', width=12,
                                  command=self.open_out)
        self.btn_open.pack(side='right', padx=(0, 8))
        self.btn_clean = tk.Button(foot, text='打开日志', width=10,
                                   command=self.open_log)
        self.btn_clean.pack(side='left')

        root.protocol('WM_DELETE_WINDOW', self.on_close)
        root.after(120, self.pump)

    # ---------------------------------------------------------- 交互
    def pick_proj(self) -> None:
        d = filedialog.askdirectory(title='选择项目目录（含 build/fishfleet.spec）')
        if d:
            self.var_proj.set(d)

    def pick_out(self) -> None:
        d = filedialog.askdirectory(title='选择输出目录')
        if d:
            self.var_out.set(d)

    def open_out(self) -> None:
        p = Path(self.var_out.get())
        if p.exists():
            os.startfile(str(p))          # noqa: S606
        else:
            messagebox.showinfo('提示', '输出目录还不存在，构建一次才有。')

    def open_log(self) -> None:
        """打开日志目录（构建日志也会写到那里）。"""
        d = self.log_dir()
        if d.exists():
            os.startfile(str(d))          # noqa: S606
        else:
            messagebox.showinfo('提示', '还没有日志。')

    def log_dir(self) -> Path:
        p = Path(os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))) \
            / 'FishFleet' / 'buildlogs'
        p.mkdir(parents=True, exist_ok=True)
        return p

    def on_close(self) -> None:
        if self.building:
            if not messagebox.askokcancel('确认', '正在构建，确定要退出吗？'):
                return
        self.root.destroy()

    # ---------------------------------------------------------- 消息泵
    def pump(self) -> None:
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == 'log':
                    self.log.insert('end', payload + '\n')
                    self.log.see('end')
                elif kind == 'status':
                    self.status.config(text=payload)
                elif kind == 'progress':
                    self.progress['value'] = max(0, min(100, payload))
                elif kind == 'done':
                    self.finish(payload)
        except queue.Empty:
            pass
        self.root.after(120, self.pump)

    # ---------------------------------------------------------- 构建
    def start_build(self) -> None:
        if self.building:
            return
        self.building = True
        self.btn_build.config(state='disabled', text='构建中…')
        self.log.delete('1.0', 'end')
        self.progress['value'] = 0
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self) -> None:
        say = lambda m: self.msg_q.put(('log', str(m)))       # noqa: E731
        st = lambda m: self.msg_q.put(('status', str(m)))     # noqa: E731
        pr = lambda v: self.msg_q.put(('progress', v))        # noqa: E731
        ok = False
        try:
            proj = Path(self.var_proj.get()).resolve()
            out = Path(self.var_out.get()).resolve()
            say('项目目录：%s' % proj)
            say('输出目录：%s' % out)
            say('')

            # ---- 前置检查 ----
            st('检查前置条件…')
            spec_main = proj / 'build' / 'fishfleet.spec'
            spec_boot = proj / 'build' / 'bootstrap.spec'
            need = []
            if self.var_main.get() and not spec_main.exists():
                need.append(str(spec_main))
            if self.var_boot.get() and not spec_boot.exists():
                need.append(str(spec_boot))
            if need:
                say('缺少配置文件：')
                for n in need:
                    say('  ' + n)
                self.msg_q.put(('done', False))
                return
            try:
                ver = subprocess.run(
                    [sys.executable, '-m', 'PyInstaller', '--version'],
                    capture_output=True, text=True, timeout=60)
                vers = (ver.stdout or ver.stderr or '').strip().splitlines()
                say('PyInstaller：%s' % (vers[0] if vers else '未知'))
                if ver.returncode != 0:
                    say('✗ PyInstaller 不可用，请先 pip install pyinstaller')
                    self.msg_q.put(('done', False))
                    return
            except Exception as exc:                # noqa: BLE001
                say('✗ 检查 PyInstaller 失败：%s' % exc)
                self.msg_q.put(('done', False))
                return
            say('')

            steps = []
            if self.var_clean.get():
                steps.append(('清理旧产物', self.step_clean))
            if self.var_main.get():
                steps.append(('打包主程序', self.step_main))
            if self.var_boot.get():
                steps.append(('打包引导器', self.step_boot))
            if self.var_assemble.get():
                steps.append(('组装成品', self.step_assemble))

            total = max(1, len(steps))
            for i, (name, fn) in enumerate(steps):
                st('%s…（%d/%d）' % (name, i + 1, total))
                say('=== %s ===' % name)
                t0 = time.time()
                good = fn(proj, out, say)
                cost = time.time() - t0
                say('--- %s %s，耗时 %.0f 秒' % (name, '完成' if good else '失败', cost))
                say('')
                if not good:
                    self.msg_q.put(('done', False))
                    return
                pr(int((i + 1) * 100 / total))

            # 结果确认
            exe = out / 'HuichengFleetAI.exe'
            if self.var_assemble.get() and exe.exists():
                size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
                say('✓ 构建成功')
                say('  输出：%s' % out)
                say('  大小：%.1f MB' % (size / 1048576))
                ok = True
            else:
                say('构建步骤跑完了，但没找到输出文件，请检查上面的日志。')
                ok = False
            self.msg_q.put(('done', ok))
        except Exception as exc:                    # noqa: BLE001
            import traceback
            say('构建异常：%s' % exc)
            say(traceback.format_exc())
            self.msg_q.put(('done', False))

    # ---- 各步骤 ----
    def step_clean(self, proj: Path, out: Path, say) -> bool:
        for d in (proj / 'dist', proj / 'build' / '_work', proj / 'build' / '_wb'):
            if d.exists():
                sz = sum(f.stat().st_size for f in d.rglob('*') if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
                say('  已清理 %s（%.0f MB）' % (d.name, sz / 1048576))
            else:
                say('  %s 不存在，跳过' % d.name)
        return True

    def step_main(self, proj: Path, out: Path, say) -> bool:
        return self.run_pyi(proj, proj / 'build' / 'fishfleet.spec',
                            proj / 'build' / '_work', say)

    def step_boot(self, proj: Path, out: Path, say) -> bool:
        return self.run_pyi(proj, proj / 'build' / 'bootstrap.spec',
                            proj / 'build' / '_wb', say)

    def run_pyi(self, proj: Path, spec: Path, work: Path, say) -> bool:
        cmd = [sys.executable, '-X', 'utf8', '-m', 'PyInstaller',
               str(spec), '--noconfirm',
               '--distpath', str(proj / 'dist'),
               '--workpath', str(work)]
        say('  命令：%s' % ' '.join(cmd[3:]))
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        try:
            p = subprocess.Popen(cmd, cwd=str(proj), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True,
                                 encoding='utf-8', errors='replace', env=env,
                                 bufsize=1)
        except Exception as exc:                    # noqa: BLE001
            say('  启动失败：%s' % exc)
            return False
        tail = []
        for line in p.stdout or []:
            line = line.rstrip()
            if not line:
                continue
            # 只把关键行刷到界面，避免几千行拖慢 UI
            if re.search(r'ERROR|WARNING: (lib|optional)|Building .*completed', line):
                say('  ' + line[:200])
            tail.append(line)
            if len(tail) > 400:
                tail.pop(0)
        p.wait()
        if p.returncode != 0:
            say('  ✗ PyInstaller 返回码 %d，最后 20 行：' % p.returncode)
            for l in tail[-20:]:
                say('    ' + l[:200])
            return False
        return True

    def step_assemble(self, proj: Path, out: Path, say) -> bool:
        src = proj / 'dist' / 'HuichengFleetAI'
        # 2026-11-06：引导器从 onefile 改成 onedir 后，产物是
        # `dist/汇城AI安装引导/`（目录，含 exe + _internal），
        # 不再是单个 `dist/汇城AI安装引导.exe`。两种都要兼容。
        boot_dir = proj / 'dist' / '汇城AI安装引导'
        boot_exe = proj / 'dist' / '汇城AI安装引导.exe'
        if not src.exists():
            say('  ✗ 主程序产物不存在：%s' % src)
            return False
        say('  目标：%s' % out)
        if out.exists():
            # 只删我们自己的东西，绝不碰用户别的文件
            for name in ('_internal', 'HuichengFleetAI.exe',
                         '汇城AI安装引导.exe', '汇城AI安装引导',
                         '使用说明.txt', '诊断.bat', 'data'):
                p = out / name
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    p.unlink()
            say('  已清理旧成品')
        out.mkdir(parents=True, exist_ok=True)

        # 主程序平铺
        for item in src.iterdir():
            dst = out / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        say('  主程序已复制')

        if boot_dir.exists():
            # onedir：整个目录拷过去，exe 在里面
            dst = out / boot_dir.name
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(boot_dir, dst)
            size = sum(f.stat().st_size for f in dst.rglob('*') if f.is_file())
            say('  引导器已复制（onedir，%.1f MB）' % (size / 1048576))
        elif boot_exe.exists():
            shutil.copy2(boot_exe, out / boot_exe.name)
            say('  引导器已复制（onefile，%.1f MB）'
                % (boot_exe.stat().st_size / 1048576))
        else:
            say('  ! 引导器不存在，跳过（只构建主程序时正常）')

        (out / '使用说明.txt').write_text(README_TEXT, encoding='utf-8')
        say('  使用说明已生成')

        # 诊断脚本
        try:
            diag = proj / 'dist' / '诊断.bat'
            if diag.exists():
                shutil.copy2(diag, out / '诊断.bat')
                say('  诊断.bat 已复制')
        except Exception:
            pass

        # 数据目录不带
        d = out / 'data'
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            say('  已移除 data/（数据不带）')
        return True

    # ---------------------------------------------------------- 收尾
    def finish(self, ok: bool) -> None:
        self.building = False
        self.btn_build.config(state='normal', text='开始构建')
        if ok:
            self.status.config(text='构建成功 ✓')
            if messagebox.askyesno('完成', '构建成功！\n\n要打开输出目录吗？'):
                self.open_out()
        else:
            self.status.config(text='构建失败，看上面日志')
            messagebox.showerror('失败', '构建没有完成，具体原因看日志区。')


def main() -> int:
    root = tk.Tk()
    BuildApp(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
