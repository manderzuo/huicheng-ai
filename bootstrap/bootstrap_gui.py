r"""引导器图形界面：显示进度、支持重试、装完自动启动主程序。

2026-11-06：主人要求"发给完全没有环境的电脑也能跑"。
命令行版已经跑通（83 秒装完 450 MB），这里补上界面，
让非技术用户看到进度、知道卡在哪一步。
"""
from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# 引导逻辑在同一目录
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap as bs  # noqa: E402


class BootstrapApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.msg_q: queue.Queue = queue.Queue()
        self.done = False
        self.ok = False

        root.title('FishFleet 环境安装')
        root.geometry('680x460')
        root.minsize(600, 400)
        try:
            root.iconbitmap(default='')
        except Exception:
            pass

        # 顶部说明
        head = tk.Frame(root, padx=16, pady=12)
        head.pack(fill='x')
        tk.Label(head, text='首次运行需要安装运行环境',
                 font=('Microsoft YaHei UI', 13, 'bold'), anchor='w').pack(fill='x')
        tk.Label(head, text='会自动下载并安装到本程序目录，不会改动系统；'
                            '装好后下次直接启动。',
                 font=('Microsoft YaHei UI', 9), fg='#666', anchor='w',
                 justify='left').pack(fill='x', pady=(4, 0))

        # 进度条
        bar_frame = tk.Frame(root, padx=16)
        bar_frame.pack(fill='x')
        self.progress = ttk.Progressbar(bar_frame, mode='determinate', maximum=100)
        self.progress.pack(fill='x', pady=(4, 6))
        self.status = tk.Label(bar_frame, text='准备中…', anchor='w',
                               font=('Microsoft YaHei UI', 9))
        self.status.pack(fill='x')

        # 日志区
        log_frame = tk.Frame(root, padx=16, pady=8)
        log_frame.pack(fill='both', expand=True)
        self.log = tk.Text(log_frame, height=12, wrap='word',
                           font=('Consolas', 9), bg='#1e1e1e', fg='#d4d4d4',
                           insertbackground='#d4d4d4', relief='flat')
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')

        # 底部按钮
        foot = tk.Frame(root, padx=16, pady=10)
        foot.pack(fill='x')
        self.btn_retry = tk.Button(foot, text='重试', state='disabled',
                                   command=self.start, width=10)
        self.btn_retry.pack(side='right', padx=(8, 0))
        self.btn_close = tk.Button(foot, text='关闭', command=root.destroy, width=10)
        self.btn_close.pack(side='right')

        root.protocol('WM_DELETE_WINDOW', self.on_close)

        # 开始
        self.root.after(200, self.start)
        self.root.after(120, self.pump)

    # ---------------------------------------------------------- 消息泵
    def pump(self) -> None:
        """把后台线程的消息搬到界面（tkinter 非线程安全）。"""
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == 'log':
                    self.append_log(payload)
                elif kind == 'status':
                    self.status.config(text=payload)
                elif kind == 'progress':
                    self.progress['value'] = max(0, min(100, payload))
                elif kind == 'done':
                    self.finish(payload)
        except queue.Empty:
            pass
        if not self.done:
            self.root.after(120, self.pump)

    def append_log(self, text: str) -> None:
        self.log.insert('end', text + '\n')
        self.log.see('end')

    # ---------------------------------------------------------- 线程
    def start(self) -> None:
        if self.done:
            # 重试：重置状态
            self.done = False
            self.log.delete('1.0', 'end')
            self.progress['value'] = 0
            self.btn_retry.config(state='disabled')
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self) -> None:
        total = len(bs.COMPONENTS)
        counter = {'n': 0}

        def say(msg: str) -> None:
            self.msg_q.put(('log', msg))
            # 从消息里猜进度
            if '===' in msg and '安装' in msg:
                counter['n'] += 1
                self.msg_q.put(('progress', counter['n'] * 100 // max(1, total)))
            self.msg_q.put(('status', msg.strip()[:80]))

        try:
            self.msg_q.put(('log', '程序目录：%s' % bs.BASE))
            status = bs.check_all()
            missing = [k for k, _, _ in bs.COMPONENTS if not status.get(k)]
            if not missing:
                self.msg_q.put(('log', '环境已就绪，直接启动'))
                self.msg_q.put(('progress', 100))
                self.msg_q.put(('done', True))
                return

            names = {k: n for k, n, _ in bs.COMPONENTS}
            self.msg_q.put(('log', '需要安装：%s' % '、'.join(names[k] for k in missing)))
            self.msg_q.put(('log', ''))

            state = bs.load_state()
            for key in missing:
                name = names[key]
                self.msg_q.put(('log', '=== 安装 %s ===' % name))
                try:
                    ok = bs.INSTALLERS[key](progress=say)
                except Exception as exc:                # noqa: BLE001
                    self.msg_q.put(('log', '  ✗ %s：%s: %s'
                                    % (name, type(exc).__name__, exc)))
                    ok = False
                state[key] = {'ok': bool(ok), 'at': time.time()}
                bs.save_state(state)
                self.msg_q.put(('log', ('✓ %s 完成' if ok else '✗ %s 失败') % name))
                self.msg_q.put(('log', ''))
                if not ok:
                    self.msg_q.put(('status', '%s 安装失败' % name))

            final = bs.check_all()
            left = [names[k] for k, v in final.items() if not v]
            self.msg_q.put(('progress', 100))
            if left:
                self.msg_q.put(('log', '未完成：%s' % '、'.join(left)))
                self.msg_q.put(('done', False))
            else:
                self.msg_q.put(('log', '全部就绪'))
                self.msg_q.put(('done', True))
        except Exception as exc:                        # noqa: BLE001
            self.msg_q.put(('log', '引导异常：%s: %s' % (type(exc).__name__, exc)))
            self.msg_q.put(('done', False))

    # ---------------------------------------------------------- 收尾
    def finish(self, ok: bool) -> None:
        self.done = True
        self.ok = ok
        if ok:
            self.status.config(text='安装完成，正在启动…')
            self.btn_retry.config(state='disabled')
            self.root.after(600, self.launch_main)
        else:
            self.status.config(text='安装未完成，请检查网络后点「重试」')
            self.btn_retry.config(state='normal')
            messagebox.showwarning(
                '安装未完成',
                '部分组件没有装好。\n\n请检查网络连接后点「重试」；\n'
                '如果反复失败，把上面日志的最后几行发给技术支持。')

    def launch_main(self) -> None:
        """启动主程序。"""
        # 2026-11-06：主程序定名 HuichengFleetAI（英文，避免 .NET/中文路径问题）；
        # 兼容旧名 FishFleet.exe 和开发态的 desktop.py。
        cands = [
            bs.BASE / 'HuichengFleetAI.exe',
            bs.BASE / 'FishFleet.exe',
        ]
        exe = next((p for p in cands if p.exists()), None)
        if exe is None:
            main_py = bs.BASE / 'fishfleet' / 'desktop.py'
            if main_py.exists():
                import subprocess
                try:
                    subprocess.Popen([sys.executable, str(main_py)],
                                     cwd=str(bs.BASE))
                    self.root.after(1200, self.root.destroy)
                    return
                except Exception as exc:            # noqa: BLE001
                    messagebox.showerror('启动失败', str(exc))
                    return
            messagebox.showwarning(
                '找不到主程序',
                '环境已装好，但没找到 HuichengFleetAI.exe。\n'
                '请确认它与本引导程序在同一个目录。')
            self.root.destroy()
            return
        import subprocess
        try:
            subprocess.Popen([str(exe)], cwd=str(bs.BASE))
        except Exception as exc:                    # noqa: BLE001
            messagebox.showerror('启动失败', str(exc))
            return
        self.root.after(1200, self.root.destroy)

    def on_close(self) -> None:
        if not self.done:
            if not messagebox.askokcancel('确认', '安装还没完成，确定要退出吗？'):
                return
        self.root.destroy()


def main() -> int:
    # 2026-11-06：支持命令行参数，便于自动化测试和排障。
    #   --check    只检查环境并打印（不开界面）
    #   --repair   强制重装全部组件
    #   --console  用命令行模式安装（不开界面），日志直接打到 stdout
    args = set(sys.argv[1:])
    if '--check' in args or '--console' in args or '--repair' in args:
        return bs.main()
    root = tk.Tk()
    BootstrapApp(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
