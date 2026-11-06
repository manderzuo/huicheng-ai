"""串行的 Mobile MCP stdio 客户端；传输失败后可重连，不重放调用。

close 是对象终态；默认使用本机固定运行时，可注入可执行文件及启动脚本。
返回值为 MCPResult；不记录请求、响应、子进程 stderr 或异常原文。
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

DEFAULT_NODE_PATH = __import__("fishfleet.paths", fromlist=["node_path"]).node_path()
DEFAULT_SERVER_PATH = __import__("fishfleet.paths", fromlist=["mobile_mcp_entry"]).mobile_mcp_entry()
PROTOCOL_VERSION = "2024-11-05"


@dataclass
class MCPResult:
    ok: bool
    result: Any = None
    error: str | None = None
    # 发送后的传输失败无法确定工具是否已执行。
    outcome_unknown: bool = False

    def to_dict(self) -> dict:
        return {"ok": self.ok, "result": self.result, "error": self.error,
                "outcome_unknown": self.outcome_unknown}


class _Failure(Exception):
    def __init__(self, code: str, outcome_unknown: bool = False):
        self.code = code
        self.outcome_unknown = outcome_unknown


@dataclass(eq=False)
class _Connection:
    process: Any
    messages: queue.Queue
    generation: int


class MobileMCPAdapter:
    def __init__(self, node_path: str = DEFAULT_NODE_PATH,
                 server_path: str = DEFAULT_SERVER_PATH, timeout: float = 30.0):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.node_path, self.server_path, self.timeout = node_path, server_path, timeout
        self._lock = threading.Lock()  # 只串行业务，close 永不等待它。
        self._state_lock = threading.Lock()  # 不在此锁内启动进程、读写管道或等待退出。
        self._closed = False
        self._connection = None
        self._generation = 0
        self._next_id = 0

    @staticmethod
    def _reader(stream, messages):
        try:
            for line in stream:
                try:
                    messages.put(json.loads(line))
                except (ValueError, UnicodeError):
                    messages.put(_Failure("invalid_response"))
                    return
        except (OSError, UnicodeError, ValueError):
            messages.put(_Failure("connection_lost"))
        finally:
            messages.put(_Failure("connection_lost"))
            # stdout 由读取者自己关闭；其他线程 close TextIOWrapper 会等待读锁。
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _dispose(connection):
        """先唤醒请求，再杀进程；管道清理由后台完成，避免读写缓冲锁死锁。"""
        connection.messages.put(_Failure("connection_lost"))
        process = connection.process
        try:
            process.kill()
        except OSError:
            pass

        def reap():
            try:
                process.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                pass
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

        threading.Thread(target=reap, daemon=True).start()

    def _disconnect(self, connection):
        # 旧请求只能摘除自己持有的代际，不能清掉后来发布的连接。
        with self._state_lock:
            if self._connection is not connection:
                return
            self._connection = None
        self._dispose(connection)

    def close(self):
        """终态关闭：原子禁止重连/发送，不获取业务锁，也不关闭读取中的管道。"""
        with self._state_lock:
            self._closed = True
            connection, self._connection = self._connection, None
        if connection is not None:
            self._dispose(connection)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _check_active(self, connection):
        with self._state_lock:
            if self._closed:
                raise _Failure("closed")
            if self._connection is not connection:
                raise _Failure("connection_lost")

    def _send(self, connection, message):
        self._check_active(connection)
        # 发送在状态锁外；close 可杀掉阻塞写入的进程，不能等待写入锁。
        try:
            connection.process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            connection.process.stdin.flush()
        except (OSError, ValueError):
            raise _Failure("connection_lost", message.get("method") == "tools/call") from None

    def _request(self, connection, method, params, deadline):
        self._next_id += 1
        request_id = self._next_id
        self._send(connection, {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            while True:
                self._check_active(connection)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _Failure("timeout")
                try:
                    message = connection.messages.get(timeout=remaining)
                except queue.Empty:
                    raise _Failure("timeout") from None
                self._check_active(connection)
                if isinstance(message, _Failure):
                    raise message
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise _Failure("invalid_response")
                if "id" not in message:  # server notifications
                    continue
                if message["id"] != request_id:
                    raise _Failure("invalid_response")
                if "error" in message:
                    raise _Failure("rpc_error")
                if "result" not in message:
                    raise _Failure("invalid_response")
                return message["result"]
        except _Failure as exc:
            exc.outcome_unknown = method == "tools/call" and exc.code != "rpc_error"
            raise

    def _ensure_connected(self, deadline):
        with self._state_lock:
            if self._closed:
                raise _Failure("closed")
            connection = self._connection
        if connection is not None:
            if connection.process.poll() is None:
                return connection
            self._disconnect(connection)
        try:
            process = subprocess.Popen(
                [self.node_path, self.server_path], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1)
        except OSError:
            raise _Failure("start_failed") from None
        with self._state_lock:
            self._generation += 1
            connection = _Connection(process, queue.Queue(), self._generation)
            closed = self._closed
            if not closed:
                self._connection = connection
        threading.Thread(target=self._reader, args=(process.stdout, connection.messages),
                         daemon=True).start()
        if closed:  # close 可能发生于 Popen 尚未返回时；未发布的进程也必须回收。
            self._dispose(connection)
            raise _Failure("closed")
        result = self._request(connection, "initialize", {"protocolVersion": PROTOCOL_VERSION,
            "capabilities": {}, "clientInfo": {"name": "fishfleet", "version": "1.0"}}, deadline)
        if not isinstance(result, dict) or not isinstance(result.get("protocolVersion"), str):
            raise _Failure("invalid_response")
        if result["protocolVersion"] != PROTOCOL_VERSION:
            raise _Failure("unsupported_protocol")
        self._send(connection, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return connection

    def _execute(self, method, params, timeout):
        duration = self.timeout if timeout is None else timeout
        if duration <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + duration
        with self._state_lock:
            if self._closed:
                return MCPResult(False, error="closed")
        if not self._lock.acquire(timeout=duration):
            # 锁都没拿到：请求从未开始写入，可证明未发出。
            with self._state_lock:
                return MCPResult(False, error="closed" if self._closed else "slot_timeout")
        connection = None
        try:
            connection = self._ensure_connected(deadline)
            if time.monotonic() >= deadline:
                raise _Failure("timeout")
            result = self._request(connection, method, params, deadline)
            if not isinstance(result, dict):
                raise _Failure("invalid_response", method == "tools/call")
            if method == "tools/call" and result.get("isError") is True:
                return MCPResult(False, result=result, error="tool_error")
            return MCPResult(True, result=result)
        except _Failure as exc:
            # 初始化失败时局部连接尚未返回，但业务锁保证这里仍是本次创建的代际。
            if connection is None:
                with self._state_lock:
                    connection = self._connection
            if connection is not None:
                self._disconnect(connection)
            return MCPResult(False, error=exc.code, outcome_unknown=exc.outcome_unknown)
        finally:
            self._lock.release()

    def list_tools(self, cursor: str | None = None, timeout: float | None = None) -> MCPResult:
        return self._execute("tools/list", {} if cursor is None else {"cursor": cursor}, timeout)

    def call_tool(self, name: str, arguments: dict | None = None,
                  timeout: float | None = None) -> MCPResult:
        if not isinstance(name, str) or not name or (arguments is not None and not isinstance(arguments, dict)):
            raise ValueError("name must be nonempty and arguments must be a dict")
        # Serialize before connecting so invalid input cannot leave a request half-written.
        params = {"name": name, "arguments": arguments if arguments is not None else {}}
        json.dumps(params, ensure_ascii=False)
        result = self._execute("tools/call", params, timeout)
        # UI 信息录制（默认关闭，开关文件控制）。
        # 挂在这一层而不是 mobile_gateway.call：Facebook 的 facebook_live 直接用
        # device_session 拿 adapter 调 call_tool，绕过 gateway，挂上面会一条都录不到。
        try:
            from . import ui_recorder
            if ui_recorder.on():
                _serial = str((arguments or {}).get('device') or '')
                if result.ok:
                    ui_recorder.record(name, arguments or {}, _serial, result.result)
                else:
                    ui_recorder.record(name, arguments or {}, _serial, None,
                                       RuntimeError(result.error or 'tool_failed'))
        except Exception:
            pass
        return result
