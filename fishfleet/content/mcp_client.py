"""
FishFleet — MCP 视频生成客户端

通过 MCP（Model Context Protocol）调用外部视频生成服务。

设计目标：
  · 协议无关：HTTP / stdio 都支持
  · 服务无关：任何实现了 generate_video 工具的 MCP 服务都能接
  · 失败可重试：网络抖动、任务排队、超时
  · 结果标准化：统一返回结构，上层不用关心底层实现

使用示例：

    client = MCPVideoClient()
    result = client.generate(
        topic="雨夜的橘猫",
        style="赛博朋克",
        duration=15,
    )
    if result.ok:
        print(result.video_path)
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

from ..core.config import get_config


# ---------------------------------------------------------------------------
# 结果
# ---------------------------------------------------------------------------

@dataclass
class VideoResult:
    ok: bool
    video_path: str = ""
    duration: float = 0.0
    resolution: str = ""
    cost: float = 0.0
    provider_job_id: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "video_path": self.video_path,
            "duration": self.duration,
            "resolution": self.resolution,
            "cost": self.cost,
            "provider_job_id": self.provider_job_id,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# MCP 客户端
# ---------------------------------------------------------------------------

class MCPVideoClient:
    """
    MCP 视频生成客户端。

    配置来自 config.yaml 的 video_generation 段：

        video_generation:
          enabled: true
          mcp:
            transport: http
            server: "http://127.0.0.1:8080/mcp"
            tool: "generate_video"
            timeout: 900
    """

    def __init__(self, config: dict | None = None):
        if httpx is None:
            raise RuntimeError("需要 httpx：pip install httpx")

        cfg = config if config is not None else get_config().video_generation
        self.enabled = bool(cfg.get("enabled", False))
        mcp = cfg.get("mcp", {}) or {}
        self.transport = mcp.get("transport", "http")
        self.server = mcp.get("server", "")
        self.tool_name = mcp.get("tool", "generate_video")
        self.timeout = mcp.get("timeout", 900)
        # stdio：command + args 指向本地 MCP 服务脚本（如 Node 桥接）。
        self.command = mcp.get("command", "") or ""
        self.args = list(mcp.get("args", []) or [])

        # 2026-11-07 内置回退：旧配置里的开发机绝对路径在客户机上不存在时，
        # 自动改用程序自带的 runtime/node + 内置 server.mjs（零依赖，无需 npm 装）。
        if self.transport != "http":
            import os as _os
            need_fallback = (not self.command or not _os.path.exists(self.command)
                             or not self.args or not _os.path.exists(self.args[0]))
            if need_fallback:
                try:
                    from fishfleet.paths import node_path as _node, seedance_server as _seed
                    _n, _s = _node(), _seed()
                    if _os.path.exists(_n) and _os.path.exists(_s):
                        self.command, self.args = _n, [_s, *self.args[1:]]
                except Exception:
                    pass

        self.prompt_template = cfg.get("prompt_template", "{topic}")
        self.defaults = cfg.get("defaults", {}) or {}

        self._client: Optional[httpx.Client] = None

    # ---- HTTP 传输 ----

    def _http(self) -> httpx.Client:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            # 可选认证
            token = os.environ.get("MCP_TOKEN", "")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            self._client = httpx.Client(timeout=self.timeout, headers=headers)
        return self._client

    def _call_http(self, tool: str, arguments: dict) -> dict:
        """MCP over HTTP：tools/call"""
        url = self.server.rstrip("/")
        if not url.endswith("/mcp"):
            url = url + "/mcp" if not url.endswith("/") else url + "mcp"

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": arguments,
            },
        }
        r = self._http().post(url, json=payload)
        r.raise_for_status()
        data = r.json()

        if "error" in data:
            raise RuntimeError(f"MCP 错误: {data['error']}")

        return data.get("result", {})

    # ---- stdio 传输 ----

    def _call_stdio(self, tool: str, arguments: dict) -> dict:
        """MCP over stdio：启动本地 MCP 服务进程，按行收发 JSON-RPC。"""
        import subprocess

        if not self.command:
            raise RuntimeError("stdio 传输缺少 command 配置")

        cmd = [self.command, *self.args]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        def send(payload: dict) -> None:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()

        # 管道在 Windows 上不能 select：只开一个读线程，多次等待共用同一队列。
        import queue
        import threading

        messages: "queue.Queue[str]" = queue.Queue()

        def reader() -> None:
            try:
                for line in proc.stdout:
                    messages.put(line)
            except Exception:  # noqa: BLE001
                pass
            finally:
                messages.put("")

        threading.Thread(target=reader, daemon=True).start()

        def wait_for(want_id: int, deadline: float) -> dict:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise RuntimeError("MCP stdio 等待超时")
                try:
                    line = messages.get(timeout=remaining)
                except queue.Empty:
                    raise RuntimeError("MCP stdio 等待超时")
                if not line:
                    raise RuntimeError("MCP stdio 进程提前退出")
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("id") == want_id:
                    if "error" in data:
                        raise RuntimeError(f"MCP 错误: {data['error']}")
                    return data.get("result", {})

        try:
            send({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "FishFleet", "version": "1.0"},
                },
            })
            wait_for(1, time.time() + 120)
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            })
            return wait_for(2, time.time() + self.timeout)
        finally:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    # ---- 调用 ----

    def _call(self, tool: str, arguments: dict) -> dict:
        if self.transport == "http":
            return self._call_http(tool, arguments)
        elif self.transport == "stdio":
            return self._call_stdio(tool, arguments)
        raise ValueError(f"未知传输方式: {self.transport}")

    def list_tools(self) -> list[dict]:
        """列出 MCP 服务提供的工具（调试用）。"""
        url = self.server.rstrip("/")
        if not url.endswith("/mcp"):
            url = url + "/mcp" if not url.endswith("/") else url + "mcp"

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        }
        r = self._http().post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        return data.get("result", {}).get("tools", [])

    # ---- 生成视频 ----

    def build_prompt(self, topic: str, **overrides) -> str:
        """根据模板构造提示词。"""
        params = {
            "topic": topic,
            "style": self.defaults.get("style", "写实"),
            "duration": self.defaults.get("duration", 15),
            "scene": overrides.get("scene", topic),
        }
        params.update(overrides)
        try:
            return self.prompt_template.format(**params)
        except KeyError:
            return topic

    def generate(
        self,
        topic: str = "",
        prompt: str = "",
        style: str = "",
        duration: int = 0,
        resolution: str = "",
        reference_image: str = "",
        extra: dict | None = None,
        retries: int = 2,
    ) -> VideoResult:
        """
        生成视频。

        参数：
          topic:      主题（会用模板转成 prompt）
          prompt:     直接用这个 prompt（优先于 topic）
          style:      风格
          duration:   时长（秒）
          resolution: 分辨率
          reference_image: 参考图（可选）
          extra:      传给 MCP 的额外参数

        返回：VideoResult
        """
        if not self.enabled:
            return VideoResult(ok=False, error="视频生成未启用（config.video_generation.enabled=false）")

        if not self.server:
            return VideoResult(ok=False, error="MCP 服务器未配置")

        final_prompt = prompt or self.build_prompt(topic, style=style or self.defaults.get("style", ""))
        args: dict[str, Any] = {"prompt": final_prompt}
        if duration or self.defaults.get("duration"):
            args["duration"] = duration or self.defaults.get("duration")
        if resolution or self.defaults.get("resolution"):
            args["resolution"] = resolution or self.defaults.get("resolution")
        if style:
            args["style"] = style
        if reference_image and Path(reference_image).exists():
            args["reference_image"] = reference_image
        if extra:
            args.update(extra)

        last_error = ""
        for attempt in range(retries + 1):
            try:
                result = self._call(self.tool_name, args)
                return self._normalize(result)
            except Exception as e:  # noqa: BLE001
                last_error = f"{type(e).__name__}: {e}"
                if attempt < retries:
                    time.sleep(2 ** attempt)   # 指数退避

        return VideoResult(ok=False, error=last_error)

    # ---- 结果标准化 ----

    def _normalize(self, result: dict) -> VideoResult:
        """
        把 MCP 返回的各种格式统一成 VideoResult。

        常见返回格式：
          A. {"content": [{"type": "text", "text": "..."}]}
          B. {"video_path": "...", "duration": 15}
          C. {"url": "...", "status": "done"}
        """
        if not isinstance(result, dict):
            return VideoResult(ok=False, error=f"返回值不是对象: {type(result)}")

        # 格式 A：MCP 标准 content 数组
        content = result.get("content")
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
            joined = "\n".join(texts)
            # 尝试从文本里解析 JSON
            try:
                parsed = json.loads(joined)
                if isinstance(parsed, dict):
                    return self._normalize(parsed)
            except json.JSONDecodeError:
                pass
            # 纯文本 → 当作路径或错误
            if joined.strip().startswith("{"):
                return VideoResult(ok=False, error=f"无法解析: {joined[:200]}")
            if os.path.exists(joined.strip()):
                return VideoResult(ok=True, video_path=joined.strip(), raw=result)
            return VideoResult(ok=False, error=joined[:300], raw=result)

        # 格式 B/C：直接字段
        path = result.get("video_path") or result.get("path") or result.get("file") or ""
        url = result.get("url") or result.get("video_url") or ""
        status = (result.get("status") or "").lower()

        if status and status not in ("done", "success", "completed", "finished", ""):
            return VideoResult(ok=False, error=f"任务状态: {status}", raw=result)

        if path and os.path.exists(path):
            return VideoResult(
                ok=True,
                video_path=path,
                duration=float(result.get("duration", 0) or 0),
                resolution=result.get("resolution", ""),
                cost=float(result.get("cost", 0) or 0),
                provider_job_id=result.get("job_id", ""),
                raw=result,
            )

        if url:
            # 下载到本地
            try:
                local = self._download(url)
                return VideoResult(
                    ok=True,
                    video_path=local,
                    duration=float(result.get("duration", 0) or 0),
                    cost=float(result.get("cost", 0) or 0),
                    provider_job_id=result.get("job_id", ""),
                    raw=result,
                )
            except Exception as e:  # noqa: BLE001
                return VideoResult(ok=False, error=f"下载失败: {e}", raw=result)

        return VideoResult(ok=False, error="返回中没有视频路径或 URL", raw=result)

    def _download(self, url: str) -> str:
        """下载视频到本地。"""
        cfg = get_config()
        media_dir = Path(cfg.storage.get("media_dir", "data/media")) / "generated"
        media_dir.mkdir(parents=True, exist_ok=True)
        out = media_dir / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.mp4"

        with self._http().stream("GET", url) as r:
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_bytes(8192):
                    f.write(chunk)
        return str(out)

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_client: Optional[MCPVideoClient] = None


def get_video_client() -> MCPVideoClient:
    global _client
    if _client is None:
        _client = MCPVideoClient()
    return _client
