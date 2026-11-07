"""
FishFleet — 配置加载

支持环境变量替换（${VAR}）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# 环境变量替换
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _app_base_str() -> str:
    """程序根目录字符串，供配置里的 {APP_BASE} 占位用。

    打包态 = exe 所在目录；开发态 = 项目根。失败时回退当前目录，
    绝不抛异常（配置加载不能因为路径探测崩掉）。
    """
    try:
        from fishfleet.paths import app_base
        return str(app_base())
    except Exception:
        return os.getcwd()


def _expand_env(value):
    """递归替换 ${VAR} 环境变量与 {APP_BASE} 程序根占位。"""
    if isinstance(value, str):
        if "{APP_BASE}" in value:
            value = value.replace("{APP_BASE}", _app_base_str())
        def repl(m):
            return os.environ.get(m.group(1), "")
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# LLM 配置
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 120

    def validate(self) -> list[str]:
        problems = []
        if not self.base_url:
            problems.append(f"{self.model}: base_url 为空")
        if not self.api_key:
            problems.append(f"{self.model}: api_key 为空（检查环境变量）")
        if not self.model:
            problems.append("model 为空")
        return problems


# ---------------------------------------------------------------------------
# 设备配置
# ---------------------------------------------------------------------------

@dataclass
class DeviceConfig:
    connection: str = "usb"
    adb_path: str = "adb"
    health_check_interval: int = 60
    max_concurrency: int = 8
    auto_reconnect: bool = True


# ---------------------------------------------------------------------------
# 主配置
# ---------------------------------------------------------------------------

@dataclass
class Config:
    raw: dict = field(default_factory=dict)
    llm_profiles: dict[str, LLMConfig] = field(default_factory=dict)
    routing: dict[str, str] = field(default_factory=dict)
    devices: DeviceConfig = field(default_factory=DeviceConfig)
    video_generation: dict = field(default_factory=dict)
    guard: dict = field(default_factory=dict)
    approval: dict = field(default_factory=dict)
    storage: dict = field(default_factory=dict)

    # ---- 加载 ----

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if yaml is None:
            raise RuntimeError("需要 PyYAML：pip install pyyaml")

        if path is None:
            # 按优先级找配置：环境变量 > fishfleet/config/config.yaml > 当前目录
            env_path = os.environ.get("FISHFLEET_CONFIG", "")
            if env_path:
                path = Path(env_path)
            else:
                pkg_root = Path(__file__).resolve().parent.parent  # fishfleet/
                candidates = [
                    pkg_root / "config" / "config.yaml",
                    pkg_root / "config.yaml",
                    Path.cwd() / "config.yaml",
                ]
                path = next((c for c in candidates if c.exists()), candidates[0])
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")

        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        raw = _expand_env(raw)

        cfg = cls(raw=raw)

        # LLM
        for name, prof in (raw.get("llm") or {}).items():
            cfg.llm_profiles[name] = LLMConfig(
                provider=prof.get("provider", "openai"),
                base_url=prof.get("base_url", ""),
                api_key=prof.get("api_key", ""),
                model=prof.get("model", ""),
                temperature=prof.get("temperature", 0.7),
                max_tokens=prof.get("max_tokens", 4096),
                timeout=prof.get("timeout", 120),
            )

        cfg.routing = raw.get("routing") or {}

        # 设备
        dev = raw.get("devices") or {}
        # 2026-11-06 关键修复（客户机「程序里看不到手机」的根因）：
        #   config.yaml 里 `adb_path` 原本写死了开发机路径
        #   `C:\Program Files (x86)\huicheng_android\tools\adb.exe`，
        #   而 `device.py` 直接用 `cfg.devices.adb_path` 去枚举设备：
        #       self.adb = ADB(adb_path or cfg.devices.adb_path)
        #   客户机没有那个目录 → adb 不存在 → 设备列表永远是空的
        #   （此时 `bin\adb.exe devices` 手动跑是正常的，所以极难排查）。
        #
        #   现在：配置里的路径**不存在就自动兜底**到 fishfleet.paths 的解析结果
        #   （它会依次找 bin/adb.exe、runtime、开发机旧路径、PATH）。
        _adb_cfg = (dev.get("adb_path") or "").strip()
        if _adb_cfg and _adb_cfg.lower() not in ("adb", "adb.exe"):
            try:
                from pathlib import Path as _P
                if not _P(_adb_cfg).exists():
                    from fishfleet.paths import adb_path as _resolve_adb
                    _adb_cfg = _resolve_adb()
            except Exception:
                pass
        if not _adb_cfg:
            try:
                from fishfleet.paths import adb_path as _resolve_adb
                _adb_cfg = _resolve_adb()
            except Exception:
                _adb_cfg = "adb"
        cfg.devices = DeviceConfig(
            connection=dev.get("connection", "usb"),
            adb_path=_adb_cfg,
            health_check_interval=dev.get("health_check_interval", 60),
            max_concurrency=dev.get("max_concurrency", 8),
            auto_reconnect=dev.get("auto_reconnect", True),
        )

        cfg.video_generation = raw.get("video_generation") or {}
        cfg.guard = raw.get("guard") or {}
        cfg.approval = raw.get("approval") or {}
        cfg.storage = raw.get("storage") or {}

        return cfg

    # ---- 查询 ----

    def llm(self, role: str = "default") -> LLMConfig:
        """按角色取 LLM 配置。role 会先查 routing，再查 profiles。"""
        name = self.routing.get(role, role)
        if name not in self.llm_profiles:
            raise KeyError(f"找不到 LLM 配置: {name}（role={role}）")
        return self.llm_profiles[name]

    def validate(self) -> list[str]:
        """检查配置是否可用，返回问题列表。"""
        problems: list[str] = []

        if not self.llm_profiles:
            problems.append("没有配置任何 LLM")

        for name, prof in self.llm_profiles.items():
            for p in prof.validate():
                problems.append(f"[{name}] {p}")

        for role in self.routing:
            if self.routing[role] not in self.llm_profiles:
                problems.append(f"routing.{role} 指向不存在的 profile: {self.routing[role]}")

        adb = self.devices.adb_path
        if adb != "adb" and not Path(adb).exists():
            problems.append(f"adb 路径不存在: {adb}")

        return problems


# ---------------------------------------------------------------------------
# 快捷函数
# ---------------------------------------------------------------------------

_config: Config | None = None


def get_config(reload: bool = False) -> Config:
    global _config
    if _config is None or reload:
        _config = Config.load()
    return _config
