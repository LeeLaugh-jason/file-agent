"""
配置管理模块 ─ 负责读写 config.yaml，并提供 AgentConfig 数据类。

优先级（从高到低）：
    1. 环境变量 FILE_AGENT_API_KEY（仅覆盖 api_key）
    2. config.yaml 中的显式值
    3. 旧版 api_key.txt 兼容读取（当 api_key 为空时）
    4. 内置默认值
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List

import yaml


# --------------- 默认值常量 ---------------

_DEFAULT_API_BASE = "https://open.bigmodel.cn/api/paas/v4/"
_DEFAULT_MODEL = "glm-5"
_DEFAULT_IGNORE_DIRS = [".git", "__pycache__", "node_modules", ".venv", "venv", ".idea"]
_DEFAULT_IGNORE_EXTENSIONS: List[str] = []
_DEFAULT_MAX_CONTENT_CHARS = 500


# --------------- 数据类 ---------------

@dataclass
class AgentConfig:
    """全局配置项的单一来源。"""

    # LLM 相关
    api_key: str = ""
    api_base: str = _DEFAULT_API_BASE
    model: str = _DEFAULT_MODEL

    # 扫描相关
    scan_dirs: List[str] = field(default_factory=lambda: ["./test_folder"])
    ignore_dirs: List[str] = field(default_factory=lambda: list(_DEFAULT_IGNORE_DIRS))
    ignore_extensions: List[str] = field(default_factory=lambda: list(_DEFAULT_IGNORE_EXTENSIONS))

    # 内容提取
    max_content_chars: int = _DEFAULT_MAX_CONTENT_CHARS

    # 运行行为
    dry_run: bool = False


# --------------- 读取 / 写入 ---------------

def _read_legacy_api_key(base_dir: str = ".") -> str:
    """兼容旧版 api_key.txt：如果文件存在且非空，返回其中的 key。"""
    txt_path = Path(base_dir) / "api_key.txt"
    if txt_path.is_file():
        key = txt_path.read_text(encoding="utf-8").strip()
        if key:
            return key
    return ""


def load_config(path: str = "config.yaml") -> AgentConfig:
    """读取配置文件并以优先级合并。

    如果配置文件不存在，则自动写入默认模板。
    """
    cfg = AgentConfig()
    config_path = Path(path)

    # 1. 从 YAML 加载
    if config_path.is_file():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for key, value in data.items():
            if hasattr(cfg, key) and value is not None:
                setattr(cfg, key, value)
    else:
        # 首次运行：写入默认模板（方便用户编辑）
        save_config(cfg, path)

    # 2. 旧版 api_key.txt 兼容
    if not cfg.api_key:
        base_dir = str(config_path.parent) if config_path.parent != Path(".") else "."
        cfg.api_key = _read_legacy_api_key(base_dir)

    # 3. 环境变量覆盖
    env_key = os.environ.get("FILE_AGENT_API_KEY", "").strip()
    if env_key:
        cfg.api_key = env_key

    return cfg


def save_config(cfg: AgentConfig, path: str = "config.yaml") -> None:
    """将当前配置持久化到 YAML 文件。"""
    config_path = Path(path)
    data = asdict(cfg)

    # 不把实际 api_key 落盘（安全起见，写占位符）
    if data.get("api_key"):
        data["api_key"] = "<YOUR_API_KEY_HERE>"

    header = (
        "# File-Agent 配置文件\n"
        "# 详细说明见 README.md\n"
        "#\n"
        "# api_key: 填入你的 API Key，或设置环境变量 FILE_AGENT_API_KEY\n"
        "#          也可以继续使用旧版 api_key.txt\n"
        "\n"
    )

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
