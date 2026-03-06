"""
单元测试 ─ config.py
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from file_agent.config import AgentConfig, load_config, save_config, _read_legacy_api_key


class TestAgentConfigDefaults:
    def test_default_values(self):
        cfg = AgentConfig()
        assert cfg.api_key == ""
        assert cfg.model == "glm-5"
        assert cfg.max_content_chars == 500
        assert cfg.dry_run is False
        assert ".git" in cfg.ignore_dirs

    def test_custom_values(self):
        cfg = AgentConfig(api_key="test-key", model="gpt-4", dry_run=True)
        assert cfg.api_key == "test-key"
        assert cfg.model == "gpt-4"
        assert cfg.dry_run is True


class TestLoadSaveConfig:
    def test_round_trip(self, tmp_path):
        cfg_path = str(tmp_path / "test_config.yaml")
        original = AgentConfig(
            api_key="my-secret-key",
            model="glm-5",
            scan_dirs=["./a", "./b"],
            ignore_dirs=[".git"],
            max_content_chars=1000,
        )
        save_config(original, cfg_path)
        # save_config 会把 api_key 替换为占位符
        loaded = load_config(cfg_path)
        assert loaded.model == "glm-5"
        assert loaded.scan_dirs == ["./a", "./b"]
        assert loaded.max_content_chars == 1000
        # api_key 不应被直接写入（安全）
        assert loaded.api_key != "my-secret-key"

    def test_auto_create_default(self, tmp_path):
        cfg_path = str(tmp_path / "nonexist.yaml")
        cfg = load_config(cfg_path)
        assert isinstance(cfg, AgentConfig)
        assert Path(cfg_path).is_file()  # 应自动创建

    def test_env_override(self, tmp_path, monkeypatch):
        cfg_path = str(tmp_path / "test.yaml")
        save_config(AgentConfig(), cfg_path)
        monkeypatch.setenv("FILE_AGENT_API_KEY", "env-key-123")
        cfg = load_config(cfg_path)
        assert cfg.api_key == "env-key-123"


class TestLegacyApiKey:
    def test_read_from_txt(self, tmp_path):
        (tmp_path / "api_key.txt").write_text("legacy-key-abc", encoding="utf-8")
        key = _read_legacy_api_key(str(tmp_path))
        assert key == "legacy-key-abc"

    def test_empty_txt(self, tmp_path):
        (tmp_path / "api_key.txt").write_text("", encoding="utf-8")
        key = _read_legacy_api_key(str(tmp_path))
        assert key == ""

    def test_no_txt(self, tmp_path):
        key = _read_legacy_api_key(str(tmp_path))
        assert key == ""
